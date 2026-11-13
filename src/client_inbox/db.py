"""SQLite storage. Everything lives in ~/.client-inbox/ on the user's machine."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
create table if not exists settings (key text primary key, value text);

create table if not exists clients (
    id integer primary key,
    name text not null,
    created_at real
);

-- kind is 'domain' (acme.com) or 'address' (pat@gmail.com); address rules win.
create table if not exists client_rules (
    kind text not null,
    value text not null,
    client_id integer not null,
    primary key (kind, value)
);

create table if not exists threads (
    id text primary key,
    client_id integer,
    client_locked integer default 0,   -- 1 once the user files the thread by hand
    subject text,
    snippet text,
    last_from text,
    last_date real,
    message_count integer,
    awaiting_me integer default 0,     -- 1 when the newest message is not from the user
    last_inbound_id text,
    is_new integer default 0,          -- 1 when it arrived after install (auto-processed)
    needs_reply integer,               -- null until triaged
    one_line text,
    summary text,
    keywords text,
    triaged_for text,                  -- last_inbound_id the summary was written for
    status text default 'idle',        -- idle | summarizing | reading | drafting | error
    error text,
    messages_json text
);

create table if not exists drafts (
    thread_id text primary key,
    body text,
    sources text,
    for_message_id text,
    gmail_draft_id text,
    updated_at real
);

create table if not exists uploads (
    id integer primary key,
    client_id integer,
    filename text,
    path text,
    chars integer,
    created_at real
);

-- Attachments and links found in a thread's emails, with what Claude read from them.
create table if not exists thread_files (
    id integer primary key,
    thread_id text not null,
    message_id text,
    kind text not null,                -- attachment | link
    key text not null,                 -- attachment id or url, unique within the thread
    name text,
    url text,
    mime text,
    size integer,
    path text,                         -- local copy of an attachment, for viewing
    method text,                       -- text | ocr | web | drive
    text text,
    summary text,
    status text default 'pending',     -- pending | reading | ready | skipped | error
    note text,
    unique (thread_id, key)
);

create virtual table if not exists upload_chunks using fts5(body, upload_id unindexed, client_id unindexed);
"""

_local = threading.local()


def home() -> Path:
    path = Path(os.environ.get("CLIENT_INBOX_HOME", "~/.client-inbox")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def conn() -> sqlite3.Connection:
    """One connection per thread; the poller, worker and web requests each get their own."""
    path = str(home() / "client_inbox.db")
    existing = getattr(_local, "conn", None)
    if existing is not None and getattr(_local, "path", None) == path:
        return existing
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("pragma journal_mode = wal")
    con.execute("pragma busy_timeout = 30000")
    con.executescript(SCHEMA)
    _migrate(con)
    _local.conn, _local.path = con, path
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Add columns that newer versions need to a database made by an older one."""
    wanted = {
        "uploads": ("source_url text", "publisher text", "fetched_at real"),
        "clients": ("profile text",),   # JSON: what is known about the client (see profile.py)
        "threads": ("risk text",),      # JSON: {"high": bool, "reasons": [...], "dismissed": bool}
        "drafts": ("model text",),      # which model wrote the draft
    }
    for table, columns in wanted.items():
        have = {row["name"] for row in con.execute(f"pragma table_info({table})")}
        for column in columns:
            if column.split()[0] not in have:
                con.execute(f"alter table {table} add column {column}")


def get_setting(key: str, default: str | None = None) -> str | None:
    row = conn().execute("select value from settings where key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn().execute(
        "insert into settings (key, value) values (?, ?) "
        "on conflict (key) do update set value = excluded.value",
        (key, value),
    )
