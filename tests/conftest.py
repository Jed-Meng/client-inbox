import base64
import os

import pytest

ME = "owner@myfirm.com"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIENT_INBOX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLIENT_INBOX_NO_BACKGROUND", "1")
    from client_inbox import db

    db.set_setting("me", ME)
    yield


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def raw_message(msg_id, sender, body, *, to=ME, subject="Q3 invoice", date_ms=1_000, labels=("INBOX",),
                attachments=(), html=False, message_id=None, references=""):
    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": message_id or f"<{msg_id}@mail>"},
    ]
    if references:
        headers.append({"name": "References", "value": references})
    parts = [{"mimeType": "text/html" if html else "text/plain", "filename": "", "body": {"data": b64(body)}}]
    for name in attachments:
        parts.append({"mimeType": "text/plain", "filename": name, "body": {"attachmentId": f"att-{name}", "size": 20}})
    return {
        "id": msg_id, "threadId": "t1", "labelIds": list(labels), "internalDate": str(date_ms),
        "payload": {"mimeType": "multipart/mixed", "headers": headers, "parts": parts},
    }


def raw_thread(thread_id, messages):
    return {"id": thread_id, "snippet": "snippet", "messages": messages}
