"""Google Drive: find files that mention the email's keywords and read them as text."""

from __future__ import annotations

import io

from . import extract
from .google_auth import service

_EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
_MAX_DOWNLOAD = 15 * 1024 * 1024


def _drive():
    return service("drive", "v3")


def search(keywords: list[str], limit: int = 4) -> list[dict]:
    """Files whose content matches the most keywords, best first."""
    hits: dict[str, dict] = {}
    for keyword in keywords[:6]:
        term = keyword.replace("\\", " ").replace("'", " ").strip()
        if len(term) < 3:
            continue
        resp = _drive().files().list(
            q=f"fullText contains '{term}' and trashed = false",
            pageSize=8,
            fields="files(id, name, mimeType, webViewLink, modifiedTime, size)",
            corpora="user",
        ).execute()
        for rank, item in enumerate(resp.get("files", [])):
            entry = hits.setdefault(item["id"], {**item, "score": 0.0})
            entry["score"] += 1.0 + (8 - rank) / 16
    ranked = sorted(hits.values(), key=lambda f: (f["score"], f.get("modifiedTime", "")), reverse=True)
    return ranked[:limit]


def read_text(item: dict) -> str:
    """Text content of a Drive file, or '' when it cannot be read as text."""
    from googleapiclient.http import MediaIoBaseDownload

    mime = item.get("mimeType", "")
    if mime in _EXPORTS:
        request = _drive().files().export_media(fileId=item["id"], mimeType=_EXPORTS[mime])
        name = item["name"] + (".csv" if _EXPORTS[mime] == "text/csv" else ".txt")
    elif mime.startswith("application/vnd.google-apps."):
        return ""
    else:
        if int(item.get("size") or 0) > _MAX_DOWNLOAD:
            return ""
        request = _drive().files().get_media(fileId=item["id"])
        name = item["name"]
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return extract.extract_text(name, buffer.getvalue(), mime)
