"""The app must never be able to send mail. Fail the build if a send call appears."""

import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "client_inbox"


def test_source_has_no_send_call():
    offenders = []
    for path in list(SRC.rglob("*.py")) + list(SRC.rglob("*.js")):
        text = path.read_text()
        if re.search(r"\.send\s*\(|messages/send|drafts/send|sendmail|smtplib", text, re.I):
            offenders.append(path.name)
    assert offenders == []


def test_no_send_scope_requested():
    from client_inbox.google_auth import SCOPES

    assert not any(scope.endswith(("gmail.send", "mail.google.com/", "gmail.modify")) for scope in SCOPES)
