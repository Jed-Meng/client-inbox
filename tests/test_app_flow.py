"""End to end with Gmail, Drive and Claude replaced by fakes: new mail arrives, gets filed,
summarized and drafted, and the button creates one Gmail draft."""

import pytest
from fastapi.testclient import TestClient

from client_inbox import app as app_module
from client_inbox import db, drive, gmail, llm, pipeline, voice

from conftest import ME, raw_message, raw_thread

HEADERS = {"X-Client-Inbox": "1"}


@pytest.fixture
def world(monkeypatch):
    state = {
        "threads": {
            "t1": raw_thread("t1", [raw_message(
                "m1", "Pat Lee <pat@acme.com>", "Can you confirm the audit price from the attached PO?",
                attachments=["po.txt"])]),
            "t2": raw_thread("t2", [raw_message("n1", "News <news@shop.com>", "50% off everything")]),
        },
        "llm_calls": [], "created": [],
    }
    monkeypatch.setattr(gmail, "changes_since", lambda history_id: ({"t1", "t2"}, "200"))
    monkeypatch.setattr(gmail, "fetch_thread", lambda thread_id: state["threads"].get(thread_id))
    monkeypatch.setattr(gmail, "attachment_bytes", lambda mid, aid: b"PO 7781: audit price 4200 USD")
    monkeypatch.setattr(voice, "past_replies", lambda client_id, limit=4: [{"subject": "Re: hi", "body": "sounds good - J"}])
    monkeypatch.setattr(drive, "search", lambda keywords, limit=4: [
        {"id": "d1", "name": "Acme SOW", "mimeType": "text/plain", "webViewLink": "https://drive.google.com/d1"}])
    monkeypatch.setattr(drive, "read_text", lambda item: "Statement of work: audit due March 3")

    def fake_create(thread, body, my_address):
        state["created"].append((thread["id"], gmail.build_reply(thread, body, my_address)))
        return "draft-1"

    monkeypatch.setattr(gmail, "create_draft", fake_create)
    monkeypatch.setattr(gmail, "get_draft", lambda draft_id: {"id": draft_id})

    def fake_llm(prompt, *, system, tier="fast", schema=None, timeout=240, attachments=None):
        state["llm_calls"].append({"prompt": prompt, "system": system, "tier": tier})
        fields = schema["properties"] if schema else {}
        if "needs_reply" in fields:
            promo = "50% off" in prompt
            return {"one_line": "Promo" if promo else "Pat asks for the audit price",
                    "summary": "A sale." if promo else "Pat wants the audit price confirmed.",
                    "needs_reply": not promo, "keywords": ["audit", "PO 7781"]}
        if "body" in fields:
            return {"body": "Hi Pat,\n\nThe audit price is 4200 USD.\n\nJ", "sources_used": ["S1", "S3"]}
        return {"summary": "A purchase order for an audit at 4200 USD."}

    monkeypatch.setattr(llm, "run", fake_llm)
    db.set_setting("history_id", "100")
    db.set_setting("voice_profile", "Short, lowercase, signs off with J.")
    return state


def run_queue():
    while not pipeline.WORKER.jobs.empty():
        key, fn, args = pipeline.WORKER.jobs.get()
        try:
            fn(*args)
        finally:
            pipeline.WORKER.pending.discard(key)


def test_new_mail_to_gmail_draft(world):
    client = TestClient(app_module.app, base_url="http://127.0.0.1")
    assert sorted(pipeline.poll_once()) == ["t1", "t2"]
    run_queue()

    state = client.get("/api/state").json()
    by_name = {c["name"]: c for c in state["clients"]}
    assert by_name["Acme"]["needs_reply"] == 1
    assert by_name["Shop"]["needs_reply"] == 0

    acme = by_name["Acme"]["id"]
    upload = client.post(f"/api/clients/{acme}/files", headers=HEADERS,
                         files={"file": ("rates.txt", b"Standard audit rate card: 4200 USD flat", "text/plain")})
    assert upload.status_code == 200

    # The promo got a summary but no draft; the client email got both.
    promo = client.get("/api/threads/t2").json()
    assert promo["summary"] == "A sale." and promo["draft"] is None
    detail = client.get("/api/threads/t1").json()
    assert detail["summary"] == "Pat wants the audit price confirmed."
    assert detail["draft"]["body"].startswith("Hi Pat,")
    kinds = {s["kind"]: s for s in detail["draft"]["sources"]}
    assert kinds["attachment"]["title"] == "po.txt" and kinds["attachment"]["used"] is True
    assert kinds["drive"]["url"] == "https://drive.google.com/d1"
    assert "past_reply" in kinds

    # The draft prompt carried the attachment text, the Drive file and the voice profile, on the smart model.
    # (Threads are processed in no fixed order, so pick the draft call by its model tier.)
    draft_call = next(call for call in world["llm_calls"] if call["tier"] == "smart")
    # Fast-model calls: two triage summaries plus one summary of the attachment.
    assert [call["tier"] for call in world["llm_calls"]].count("fast") == 3
    files = {f["name"]: f for f in detail["files"]}
    assert files["po.txt"]["status"] == "ready" and files["po.txt"]["method"] == "text"
    assert files["po.txt"]["summary"] == "A purchase order for an audit at 4200 USD."
    assert client.get(f"/api/thread-files/{files['po.txt']['id']}/content").content == b"PO 7781: audit price 4200 USD"
    assert "audit price 4200 USD" in draft_call["prompt"] and "audit due March 3" in draft_call["prompt"]
    assert "signs off with J" in draft_call["system"]

    # Regenerate with an instruction now also finds the uploaded file.
    assert client.post("/api/threads/t1/draft", headers=HEADERS, json={"instruction": "shorter"}).json()["queued"]
    run_queue()
    regen = world["llm_calls"][-1]["prompt"]
    assert "Standard audit rate card" in regen and "<instruction_from_owner>\nshorter" in regen

    # Nothing has touched Gmail yet. The button creates exactly one draft, addressed and threaded.
    assert world["created"] == []
    resp = client.post("/api/threads/t1/gmail-draft", headers=HEADERS, json={"body": "Hi Pat,\n\n4200 USD.\n\nJ"})
    assert resp.json() == {"gmail_draft_id": "draft-1"}
    thread_id, mime = world["created"][0]
    assert thread_id == "t1" and mime["To"] == "pat@acme.com" and mime["In-Reply-To"] == "<m1@mail>"
    assert client.get("/api/threads/t1").json()["draft"]["gmail_draft_id"] == "draft-1"


def test_reply_from_owner_clears_needs_reply(world):
    pipeline.poll_once()
    run_queue()
    world["threads"]["t1"]["messages"].append(
        raw_message("m2", f"Owner <{ME}>", "4200 USD.", date_ms=5_000, labels=("SENT",)))
    pipeline.poll_once()
    run_queue()
    row = db.conn().execute("select needs_reply, awaiting_me from threads where id = 't1'").fetchone()
    assert (row["needs_reply"], row["awaiting_me"]) == (0, 0)


def test_other_sites_cannot_drive_the_app(world):
    client = TestClient(app_module.app, base_url="http://127.0.0.1")
    pipeline.poll_once()
    assert client.post("/api/threads/t1/gmail-draft", json={"body": "x"}).status_code == 403  # no app header
    evil = TestClient(app_module.app, base_url="http://evil.example")
    assert evil.get("/api/state").status_code == 403  # DNS rebinding
    assert world["created"] == []


def test_rename_and_move(world):
    client = TestClient(app_module.app, base_url="http://127.0.0.1")
    pipeline.poll_once()
    shop = next(c for c in client.get("/api/state").json()["clients"] if c["name"] == "Shop")
    client.post(f"/api/clients/{shop['id']}/rename", headers=HEADERS, json={"name": "Shop Co"})
    moved = client.post("/api/threads/t2/move", headers=HEADERS, json={"new_name": "Vendors", "apply_to_sender": True})
    names = {c["name"] for c in client.get("/api/state").json()["clients"]}
    assert "Vendors" in names and "Shop Co" not in names  # empty clients drop out of the list
    assert len(client.get(f"/api/clients/{moved.json()['client_id']}/threads").json()) == 1
