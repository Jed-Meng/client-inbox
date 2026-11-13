"""Gmail access: read threads and attachments, watch for new mail, create reply drafts.

This module only ever reads mail and creates drafts. It contains no call that
transmits mail, and tests/test_no_send.py fails the build if one is added.
"""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from . import extract
from .google_auth import service

_QUOTE_MARKERS = (
    re.compile(r"^On .{5,200}wrote:\s*$", re.S),
    re.compile(r"^-{3,}\s*Original Message\s*-{3,}", re.I),
    re.compile(r"^-{3,}\s*Forwarded message\s*-{3,}", re.I),
)


def _gmail():
    return service("gmail", "v1")


# ---------- parsing (pure functions, no network) ----------

def strip_quoted(text: str) -> str:
    """Drop the quoted earlier messages from the bottom of an email body."""
    lines = text.replace("\r\n", "\n").split("\n")
    kept: list[str] = []
    for index, line in enumerate(lines):
        two = line + " " + (lines[index + 1] if index + 1 < len(lines) else "")
        if any(marker.match(line.strip()) for marker in _QUOTE_MARKERS) or _QUOTE_MARKERS[0].match(two.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")


def _walk(part: dict):
    yield part
    for sub in part.get("parts") or []:
        yield from _walk(sub)


def _body(payload: dict) -> tuple[str, list[dict]]:
    """A message's text and the web links in it."""
    plain = html = ""
    for part in _walk(payload):
        data = part.get("body", {}).get("data")
        if not data or part.get("filename"):
            continue
        if part.get("mimeType") == "text/plain" and not plain:
            plain = _decode(data)
        elif part.get("mimeType") == "text/html" and not html:
            html = _decode(data)
    text = plain or (extract.html_to_text(html) if html else "")
    return text, extract.links_in(text, html)


def _body_text(payload: dict) -> str:
    return _body(payload)[0]


def parse_thread(raw: dict, me: set[str]) -> dict | None:
    """Reduce a Gmail thread to what the app needs. Draft messages are ignored."""
    messages = []
    for msg in raw.get("messages", []):
        labels = msg.get("labelIds", [])
        if "DRAFT" in labels:
            continue
        payload = msg.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        from_name, from_email = parseaddr(headers.get("from", ""))
        from_email = from_email.lower()
        reply_name, reply_email = parseaddr(headers.get("reply-to", ""))
        attachments = [
            {
                "filename": part["filename"],
                "mime": part.get("mimeType", ""),
                "attachment_id": part["body"]["attachmentId"],
                "size": part["body"].get("size", 0),
            }
            for part in _walk(payload)
            if part.get("filename") and part.get("body", {}).get("attachmentId")
        ]
        body, links = _body(payload)
        messages.append({
            "id": msg["id"],
            "from_name": from_name or from_email,
            "from_email": from_email,
            "reply_to": reply_email.lower(),
            "to": [[name, addr.lower()] for name, addr in getaddresses([headers.get("to", "")]) if addr],
            "cc": [[name, addr.lower()] for name, addr in getaddresses([headers.get("cc", "")]) if addr],
            "subject": headers.get("subject", ""),
            "date": int(msg.get("internalDate", 0)) / 1000,
            "message_id": headers.get("message-id", ""),
            "references": headers.get("references", ""),
            "body": body.strip(),
            "links": links,
            "attachments": attachments,
            "in_inbox": "INBOX" in labels,
            "is_me": from_email in me,
        })
    if not messages:
        return None
    messages.sort(key=lambda m: m["date"])
    inbound = [m for m in messages if not m["is_me"]]
    last = messages[-1]
    return {
        "id": raw["id"],
        "subject": messages[0]["subject"] or "(no subject)",
        "snippet": raw.get("snippet") or last["body"][:160],
        "messages": messages,
        "last_from": last["from_name"],
        "last_date": last["date"],
        "awaiting_me": not last["is_me"],
        "last_inbound_id": inbound[-1]["id"] if inbound else None,
        "in_inbox": any(m["in_inbox"] for m in messages),
    }


def build_reply(thread: dict, body: str, my_address: str) -> EmailMessage | None:
    """A reply to the newest message that is not from the user, threaded correctly."""
    inbound = [m for m in thread["messages"] if not m["is_me"]]
    if not inbound:
        return None
    target = inbound[-1]
    mime = EmailMessage()
    mime["To"] = target["reply_to"] or target["from_email"]
    mime["From"] = my_address
    subject = thread["subject"]
    mime["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if target["message_id"]:
        mime["In-Reply-To"] = target["message_id"]
        mime["References"] = f"{target['references']} {target['message_id']}".strip()
    mime.set_content(body)
    return mime


# ---------- network ----------

def profile() -> dict:
    return _gmail().users().getProfile(userId="me").execute()


def list_inbox_thread_ids(days: int, limit: int = 300) -> list[str]:
    ids: list[str] = []
    token = None
    while len(ids) < limit:
        resp = _gmail().users().threads().list(
            userId="me", q=f"in:inbox newer_than:{days}d", maxResults=100, pageToken=token
        ).execute()
        ids += [t["id"] for t in resp.get("threads", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    return ids[:limit]


def fetch_thread(thread_id: str) -> dict | None:
    from googleapiclient.errors import HttpError

    try:
        return _gmail().users().threads().get(userId="me", id=thread_id, format="full").execute()
    except HttpError as exc:
        if exc.resp.status == 404:
            return None
        raise


def changes_since(history_id: str) -> tuple[set[str], str] | None:
    """Thread ids with new messages since history_id, plus the new history id.
    None when Gmail no longer has that history (caller should resync)."""
    from googleapiclient.errors import HttpError

    ids: set[str] = set()
    token = None
    latest = history_id
    while True:
        try:
            resp = _gmail().users().history().list(
                userId="me", startHistoryId=history_id, historyTypes=["messageAdded"], pageToken=token
            ).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise
        for record in resp.get("history", []):
            for added in record.get("messagesAdded", []):
                message = added.get("message", {})
                if "DRAFT" not in message.get("labelIds", []):
                    ids.add(message["threadId"])
        latest = resp.get("historyId", latest)
        token = resp.get("nextPageToken")
        if not token:
            return ids, str(latest)


def attachment_bytes(message_id: str, attachment_id: str) -> bytes:
    resp = _gmail().users().messages().attachments().get(
        userId="me", messageId=message_id, id=attachment_id
    ).execute()
    return base64.urlsafe_b64decode(resp["data"].encode("utf-8"))


def sent_bodies(query: str, limit: int) -> list[dict]:
    """The user's own sent emails matching a Gmail search, newest first, quotes removed."""
    out: list[dict] = []
    token = None
    while len(out) < limit:
        resp = _gmail().users().messages().list(
            userId="me", q=f"in:sent {query}".strip(), maxResults=min(100, limit - len(out)), pageToken=token
        ).execute()
        for ref in resp.get("messages", []):
            raw = _gmail().users().messages().get(userId="me", id=ref["id"], format="full").execute()
            payload = raw.get("payload", {})
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
            body = strip_quoted(_body_text(payload))
            if len(body) >= 40:
                out.append({
                    "from_name": parseaddr(headers.get("from", ""))[0],
                    "subject": headers.get("subject", ""),
                    "body": body,
                })
        token = resp.get("nextPageToken")
        if not token:
            break
    return out[:limit]


def create_draft(thread: dict, body: str, my_address: str) -> str:
    """Create a reply draft inside the Gmail thread and return its id. Nothing is sent."""
    mime = build_reply(thread, body, my_address)
    if mime is None:
        raise ValueError("This thread has no message from someone else to reply to.")
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")
    draft = _gmail().users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": thread["id"]}}
    ).execute()
    return draft["id"]


def get_draft(draft_id: str) -> dict:
    return _gmail().users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
