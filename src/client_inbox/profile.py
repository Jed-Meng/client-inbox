"""What is known about each client: headcount, states, pay schedule and so on.

Facts arrive two ways. Claude notes what an email states outright ("we have 38 employees");
the owner can type or correct anything. A value the owner set is never overwritten by Claude.
The profile goes into every draft for that client, so replies can apply the right rules
instead of hedging ("if you have 50 or more employees...").
"""

from __future__ import annotations

import json

from . import db

# (key, label, hint shown in the form)
FIELDS = [
    ("employee_count", "Employees", "e.g. 38, or 'about 120'"),
    ("states", "States with employees", "e.g. California, Nevada"),
    ("industry", "Industry", "e.g. dental practice"),
    ("pay_schedule", "Pay schedule", "e.g. biweekly, Fridays"),
    ("payroll_provider", "Payroll or HR system", ""),
    ("notes", "Notes for Claude", "Anything drafts should always take into account"),
]
AUTO_FIELDS = ("employee_count", "states", "industry", "pay_schedule", "payroll_provider")
_LABELS = {key: label for key, label, _ in FIELDS}

FACTS_SCHEMA = {
    "type": "object",
    "description": "Facts about the sender's own organization that this thread states outright. Leave a "
                   "field as an empty string unless the email says it; never guess or infer from the "
                   "email address.",
    "properties": {
        "employee_count": {"type": "string", "description": "Total employees, as stated (e.g. '38')."},
        "states": {"type": "string", "description": "US states where they have employees, comma separated."},
        "industry": {"type": "string", "description": "What the organization does, in a few words."},
        "pay_schedule": {"type": "string", "description": "How often employees are paid."},
        "payroll_provider": {"type": "string", "description": "Payroll or HR system they use."},
    },
    "required": list(AUTO_FIELDS),
}


def load(client_id: int) -> dict:
    row = db.conn().execute("select profile from clients where id = ?", (client_id,)).fetchone()
    data = json.loads(row["profile"]) if row and row["profile"] else {}
    return {"fields": data.get("fields") or {}, "contacts": data.get("contacts") or []}


def _save(client_id: int, data: dict) -> None:
    db.conn().execute("update clients set profile = ? where id = ?", (json.dumps(data), client_id))


def set_by_user(client_id: int, values: dict) -> dict:
    """Save what the owner typed. Clearing a field lets Claude fill it again later."""
    data = load(client_id)
    for key, _, _ in FIELDS:
        if key not in values:
            continue
        value = str(values[key] or "").strip()[:500]
        current = data["fields"].get(key, {})
        if not value:
            data["fields"].pop(key, None)
        elif value != current.get("value"):
            data["fields"][key] = {"value": value, "by": "user"}
    _save(client_id, data)
    return data


def learn(client_id: int, facts: dict, thread_id: str) -> list[str]:
    """Record facts Claude found in an email. Returns the fields that changed."""
    data = load(client_id)
    changed = []
    for key in AUTO_FIELDS:
        value = str((facts or {}).get(key) or "").strip()[:200]
        current = data["fields"].get(key, {})
        if not value or current.get("by") == "user" or current.get("value") == value:
            continue
        data["fields"][key] = {"value": value, "by": "auto", "thread": thread_id}
        changed.append(key)
    if changed:
        _save(client_id, data)
    return changed


def add_contacts(client_id: int, messages: list[dict], me: set[str]) -> None:
    """Keep a list of the people at this client who have written in."""
    data = load(client_id)
    known = {contact["email"] for contact in data["contacts"]}
    added = False
    for msg in messages:
        email = msg.get("from_email") or ""
        if email and email not in me and email not in known and len(data["contacts"]) < 40:
            data["contacts"].append({"name": msg.get("from_name") or email, "email": email})
            known.add(email)
            added = True
    if added:
        _save(client_id, data)


def as_text(client_id: int | None) -> str:
    """The profile as a block for Claude's instructions. Empty when nothing is known."""
    if not client_id:
        return ""
    row = db.conn().execute("select name from clients where id = ?", (client_id,)).fetchone()
    if row is None:
        return ""
    fields = load(client_id)["fields"]
    lines = [
        f"- {_LABELS[key]}: {fields[key]['value']}"
        + ("" if fields[key].get("by") == "user" else " (picked up from an earlier email, not confirmed)")
        for key, _, _ in FIELDS if key in fields
    ]
    if not lines:
        return ""
    return (
        f"What is known about this client, {row['name']}:\n" + "\n".join(lines)
        + "\nUse these facts to decide which rules apply (for example thresholds by headcount, or state "
          "law) and state the conclusion directly. If a rule depends on a fact that is not listed here "
          "or in the thread, ask for it in the reply."
    )
