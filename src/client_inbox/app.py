"""Local web app. Binds to 127.0.0.1 only and refuses requests that did not come from its own page."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import clients, db, gmail, llm, pipeline

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 20 * 1024 * 1024
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = threading.Event()
    if os.environ.get("CLIENT_INBOX_NO_BACKGROUND") != "1":
        threading.Thread(target=pipeline.WORKER.run, args=(stop,), daemon=True).start()
        threading.Thread(target=pipeline.background, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Client Inbox", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Block other websites from driving the app (cross-site requests and DNS rebinding)."""
    if request.url.path.startswith("/api/"):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in _LOCAL_HOSTS:
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        if request.method != "GET" and request.headers.get("x-client-inbox") != "1":
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def _thread_or_404(thread_id: str) -> dict:
    thread = pipeline.load_thread(thread_id)
    if thread is None:
        raise HTTPException(404, "Thread not found")
    return thread


def _upsert_draft_body(thread_id: str, body: str) -> None:
    """Save the user's edits (or a reply they typed from scratch) without touching its sources."""
    db.conn().execute(
        "insert into drafts (thread_id, body, sources, updated_at) values (?, ?, '[]', ?) "
        "on conflict (thread_id) do update set body = excluded.body",
        (thread_id, body, time.time()),
    )


# ---------- state ----------

@app.get("/api/state")
def state():
    rows = db.conn().execute(
        """
        select clients.id as id, clients.name as name,
               count(threads.id) as thread_count,
               coalesce(sum(case when threads.needs_reply = 1 and threads.awaiting_me = 1 then 1 else 0 end), 0)
                   as needs_reply,
               max(threads.last_date) as last_date,
               (select count(*) from uploads where uploads.client_id = clients.id) as file_count
        from clients
        left join threads on threads.client_id = clients.id
        group by clients.id, clients.name
        having count(threads.id) > 0 or file_count > 0
        order by needs_reply desc, last_date desc
        """
    ).fetchall()
    return {
        "me": db.get_setting("me", ""),
        "claude_ready": llm.available(),
        "sync": pipeline.SYNC,
        "voice_ready": bool(db.get_setting("voice_profile")),
        "voice_error": db.get_setting("voice_error", ""),
        "voice_building": "voice" in pipeline.WORKER.pending,
        "clients": [dict(row) for row in rows],
    }


@app.get("/api/clients/{client_id}/threads")
def client_threads(client_id: int):
    rows = db.conn().execute(
        """
        select threads.id as id, subject, snippet, last_from, last_date, message_count, awaiting_me,
               needs_reply, one_line, status,
               case when drafts.thread_id is null then 0 else 1 end as has_draft
        from threads
        left join drafts on drafts.thread_id = threads.id
        where threads.client_id = ?
        order by (case when needs_reply = 1 and awaiting_me = 1 then 0 else 1 end), last_date desc
        limit 300
        """,
        (client_id,),
    ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/threads/{thread_id}")
def thread_detail(thread_id: str):
    thread = _thread_or_404(thread_id)
    draft = db.conn().execute("select * from drafts where thread_id = ?", (thread_id,)).fetchone()
    for msg in thread["messages"]:
        msg["attachments"] = [a["filename"] for a in msg["attachments"]]
    thread["draft"] = (
        {**dict(draft), "sources": json.loads(draft["sources"] or "[]")} if draft else None
    )
    thread["queued"] = f"thread:{thread_id}" in pipeline.WORKER.pending
    thread["gmail_url"] = f"https://mail.google.com/mail/?authuser={quote(db.get_setting('me', ''))}#all/{thread_id}"
    return thread


# ---------- Claude jobs ----------

@app.post("/api/threads/{thread_id}/summarize")
def summarize(thread_id: str):
    _thread_or_404(thread_id)
    pipeline.WORKER.submit(f"thread:{thread_id}", pipeline.triage, thread_id)
    return {"queued": True}


@app.post("/api/threads/{thread_id}/draft")
def draft(thread_id: str, payload: dict = Body(default={})):
    thread = _thread_or_404(thread_id)
    if not thread["last_inbound_id"]:
        raise HTTPException(400, "There is no message from someone else to reply to.")
    queued = pipeline.WORKER.submit(
        f"thread:{thread_id}", pipeline.write_draft, thread_id, str(payload.get("instruction") or "")
    )
    return {"queued": queued}


@app.put("/api/threads/{thread_id}/draft")
def save_draft(thread_id: str, payload: dict = Body(...)):
    _thread_or_404(thread_id)
    _upsert_draft_body(thread_id, str(payload.get("body") or ""))
    return {"saved": True}


@app.post("/api/threads/{thread_id}/gmail-draft")
def gmail_draft(thread_id: str, payload: dict = Body(...)):
    """The one write to Gmail: a draft in the thread, only when the user clicks the button."""
    thread = _thread_or_404(thread_id)
    body = str(payload.get("body") or "").strip()
    if not body:
        raise HTTPException(400, "The draft is empty.")
    try:
        draft_id = gmail.create_draft(thread, body, db.get_setting("me", ""))
        gmail.get_draft(draft_id)  # read it back: confirms Gmail really stored it
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Gmail did not accept the draft: {str(exc)[:300]}") from exc
    _upsert_draft_body(thread_id, body)
    db.conn().execute("update drafts set gmail_draft_id = ? where thread_id = ?", (draft_id, thread_id))
    return {"gmail_draft_id": draft_id}


@app.post("/api/voice/rebuild")
def rebuild_voice():
    pipeline.queue_voice()
    return {"queued": True}


# ---------- organizing clients ----------

@app.post("/api/clients/{client_id}/rename")
def rename_client(client_id: int, payload: dict = Body(...)):
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name is required.")
    clients.rename(client_id, name)
    return {"ok": True}


@app.post("/api/clients/{client_id}/merge")
def merge_client(client_id: int, payload: dict = Body(...)):
    target = payload.get("into")
    if not isinstance(target, int) or target == client_id:
        raise HTTPException(400, "Pick a different client to merge into.")
    clients.merge(client_id, target)
    return {"ok": True}


@app.post("/api/threads/{thread_id}/move")
def move_thread(thread_id: str, payload: dict = Body(...)):
    thread = _thread_or_404(thread_id)
    target = payload.get("client_id")
    if not isinstance(target, int):
        name = str(payload.get("new_name") or "").strip()
        if not name:
            raise HTTPException(400, "Pick a client or type a new name.")
        target = clients.create(name)
    sender = None
    if payload.get("apply_to_sender"):
        other = clients.counterpart(thread["messages"], pipeline.me())
        sender = other[0] if other else None
    clients.move_thread(thread_id, target, sender)
    return {"client_id": target}


# ---------- uploaded files ----------

@app.get("/api/clients/{client_id}/files")
def list_files(client_id: int):
    rows = db.conn().execute(
        "select id, filename, chars, created_at from uploads where client_id = ? order by created_at desc",
        (client_id,),
    ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/clients/{client_id}/files")
async def upload_file(client_id: int, file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File is larger than 20 MB.")
    try:
        return pipeline.add_upload(client_id, file.filename or "file", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/files/{upload_id}")
def delete_file(upload_id: int):
    pipeline.delete_upload(upload_id)
    return {"ok": True}
