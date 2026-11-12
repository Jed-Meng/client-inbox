"""Which client does a thread belong to?

Rule: the sender's domain is the client (everyone at acme.com is "Acme"). Free-mail
domains such as gmail.com would lump strangers together, so those senders become one
client per address. The user can rename a client, merge two, or move a thread by hand.
"""

from __future__ import annotations

import time

from . import db

FREEMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "pm.me", "gmx.com", "gmx.net", "mail.com", "zoho.com", "fastmail.com",
    "hey.com", "comcast.net", "att.net", "verizon.net", "sbcglobal.net",
}
# Second-level labels that sit under a country code (example.co.uk, example.com.au).
_CC_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}


def registrable_domain(domain: str) -> str:
    """mail.acme.com -> acme.com; shop.acme.co.uk -> acme.co.uk."""
    labels = [part for part in domain.lower().strip(".").split(".") if part]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _CC_SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def rule_for(address: str) -> tuple[str, str]:
    """The default rule an address falls under: ('domain', 'acme.com') or ('address', ...)."""
    address = address.strip().lower()
    domain = registrable_domain(address.rpartition("@")[2])
    if not domain or domain in FREEMAIL:
        return ("address", address)
    return ("domain", domain)


def default_name(kind: str, value: str, display_name: str = "") -> str:
    if kind == "address":
        return display_name.strip() or value
    return value.split(".")[0].replace("-", " ").title()


def client_for(address: str, display_name: str = "") -> int:
    """Find the client for an address, creating one from the default rule if needed."""
    con = db.conn()
    address = address.strip().lower()
    kind, value = rule_for(address)
    for candidate in (("address", address), (kind, value)):
        row = con.execute(
            "select client_id from client_rules where kind = ? and value = ?", candidate
        ).fetchone()
        if row:
            return row["client_id"]
    cur = con.execute(
        "insert into clients (name, created_at) values (?, ?)",
        (default_name(kind, value, display_name), time.time()),
    )
    con.execute(
        "insert into client_rules (kind, value, client_id) values (?, ?, ?)",
        (kind, value, cur.lastrowid),
    )
    return cur.lastrowid


def counterpart(messages: list[dict], me: set[str]) -> tuple[str, str] | None:
    """The first person in the thread who is not the user: (address, display name)."""
    for msg in messages:
        if msg["from_email"] and msg["from_email"] not in me:
            return msg["from_email"], msg["from_name"]
    for msg in messages:
        for name, address in msg.get("to", []):
            if address and address not in me:
                return address, name
    return None


def rename(client_id: int, name: str) -> None:
    db.conn().execute("update clients set name = ? where id = ?", (name.strip(), client_id))


def create(name: str) -> int:
    cur = db.conn().execute(
        "insert into clients (name, created_at) values (?, ?)", (name.strip(), time.time())
    )
    return cur.lastrowid


def merge(source_id: int, target_id: int) -> None:
    """Fold one client into another: its rules, threads and uploaded files all move."""
    if source_id == target_id:
        return
    con = db.conn()
    con.execute("begin")
    try:
        con.execute("update client_rules set client_id = ? where client_id = ?", (target_id, source_id))
        con.execute("update threads set client_id = ? where client_id = ?", (target_id, source_id))
        con.execute("update uploads set client_id = ? where client_id = ?", (target_id, source_id))
        con.execute("update upload_chunks set client_id = ? where client_id = ?", (target_id, source_id))
        con.execute("delete from clients where id = ?", (source_id,))
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise


def move_thread(thread_id: str, client_id: int, sender_address: str | None = None) -> None:
    """File one thread under a client by hand. With sender_address, future mail from
    that sender follows it."""
    con = db.conn()
    con.execute(
        "update threads set client_id = ?, client_locked = 1 where id = ?", (client_id, thread_id)
    )
    if sender_address:
        con.execute(
            "insert into client_rules (kind, value, client_id) values ('address', ?, ?) "
            "on conflict (kind, value) do update set client_id = excluded.client_id",
            (sender_address.strip().lower(), client_id),
        )
