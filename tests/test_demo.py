"""Demo mode runs the whole app on sample data, with only Claude faked here."""

from fastapi.testclient import TestClient

from client_inbox import app as app_module
from client_inbox import db, demo, drive, gmail, llm, pipeline, web

HEADERS = {"X-Client-Inbox": "1"}


def test_demo_inbox_end_to_end(monkeypatch):
    for module, names in ((gmail, ["profile", "list_inbox_thread_ids", "fetch_thread", "changes_since",
                                   "attachment_bytes", "sent_bodies", "create_draft", "get_draft"]),
                          (drive, ["search", "read_text", "read_by_id"]), (web, ["fetch"])):
        for name in names:  # let pytest restore the real functions afterwards
            monkeypatch.setattr(module, name, getattr(module, name))
    monkeypatch.setenv("CLIENT_INBOX_DEMO", "1")
    for store in (demo._THREADS, demo._ATTACHMENTS):
        store.clear()
    for store in (demo._DELIVERED, demo._QUEUE, demo._DRAFTS):
        del store[:]
    demo.install()
    db.set_setting("me", demo.ME)

    ocr_inputs = []

    def fake_llm(prompt, *, system, tier="fast", schema=None, timeout=240, attachments=None):
        fields = schema["properties"] if schema else {}
        if "needs_reply" in fields:
            quiet = any(word in prompt for word in ("Design Weekly", "not monitored", "mailbox has reached"))
            return {"one_line": "x", "summary": "s", "needs_reply": not quiet, "keywords": ["rush fee", "Harbor Coffee"]}
        if "body" in fields:
            return {"body": "hi - on it.\n\n- alex", "sources_used": ["S1"]}
        if "text" in fields:
            ocr_inputs.append(attachments[0][0])
            return {"text": "transcribed", "summary": "ocr summary"}
        if schema:
            return {"summary": "file summary"}
        return "voice profile"

    monkeypatch.setattr(llm, "run", fake_llm)
    pipeline.initial_sync()
    while not pipeline.WORKER.jobs.empty():
        key, fn, args = pipeline.WORKER.jobs.get()
        fn(*args)
        pipeline.WORKER.pending.discard(key)

    client = TestClient(app_module.app, base_url="http://127.0.0.1")
    state = client.get("/api/state").json()
    names = {c["name"]: c for c in state["clients"]}
    assert state["demo"] is True and state["demo_waiting"] == 5
    assert {"Harborcoffee", "Lumendental", "Pat Okafor", "Brightside"} <= set(names)
    assert names["Harborcoffee"]["needs_reply"] == 1 and names["Designweekly"]["needs_reply"] == 0
    assert names["Brightside"]["needs_reply"] == 0  # Alex already replied

    invoice = client.get("/api/threads/d-harbor-invoice").json()
    scan = invoice["files"][0]
    assert scan["method"] == "ocr" and scan["viewable"] and invoice["draft"]["body"].startswith("hi - on it")
    pdf = client.get(f"/api/thread-files/{scan['id']}/content")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"

    lumen = {f["kind"]: f for f in client.get("/api/threads/d-lumen-homepage").json()["files"]}
    assert lumen["attachment"]["method"] == "ocr" and lumen["link"]["method"] == "web"
    assert lumen["link"]["name"] == "Lumen Dental - website brief"
    assert sorted(ocr_inputs) == ["application/pdf", "image/jpeg"]

    bakery = client.get("/api/threads/d-bakery-logo").json()["files"]
    assert [f["method"] for f in bakery] == ["drive"]  # the Instagram link was never opened

    # The newsletter's links stay unopened; the receipt PDF is read as plain text.
    assert all(f["status"] == "pending" for f in client.get("/api/threads/d-newsletter").json()["files"])
    assert client.get("/api/threads/d-receipt").json()["files"][0]["method"] == "text"

    delivered = client.post("/api/demo/deliver", headers=HEADERS).json()
    assert delivered["subject"].startswith("RFP")
    assert client.get("/api/threads/d-kestrel-rfp").status_code == 200
    assert client.get("/api/state").json()["demo_waiting"] == 4
