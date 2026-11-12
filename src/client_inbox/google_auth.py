"""Google sign-in for a desktop app. The token never leaves ~/.client-inbox/."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from . import db

# Read mail, create drafts, read Drive. The app has no code path that sends mail.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
]

_local = threading.local()


def client_secrets_path() -> Path | None:
    """The OAuth client: the user's own file first, then the one shipped with the app."""
    for path in (db.home() / "credentials.json", Path(__file__).parent / "oauth_client.json"):
        if path.exists():
            return path
    return None


def token_path() -> Path:
    return db.home() / "token.json"


def load_credentials():
    """Stored credentials, refreshed if needed. None when the user has not signed in."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path()
    if not path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            return None
        _save(creds)
        return creds
    return None


def login():
    """Open the browser for Google consent and store the token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets = client_secrets_path()
    if secrets is None:
        raise FileNotFoundError("No Google OAuth client found. See 'Google setup' in the README.")
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save(creds)
    return creds


def _save(creds) -> None:
    path = token_path()
    path.write_text(creds.to_json())
    os.chmod(path, 0o600)


def service(name: str, version: str):
    """A Google API client for this thread (the underlying HTTP object is not thread-safe)."""
    from googleapiclient.discovery import build

    cache = getattr(_local, "services", None)
    if cache is None:
        cache = _local.services = {}
    if name not in cache:
        creds = load_credentials()
        if creds is None:
            raise RuntimeError("Not signed in to Google. Restart the app to sign in again.")
        cache[name] = build(name, version, credentials=creds, cache_discovery=False)
    return cache[name]
