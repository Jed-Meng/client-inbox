"""Identifiers are replaced before any text is sent to Claude."""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from client_inbox import app as app_module
from client_inbox import llm, pipeline, privacy

from conftest import raw_message, raw_thread


@pytest.mark.parametrize("text, kind, secret", [
    ("Her SSN is 123-45-6789 for the W-2.", "ssn", "123-45-6789"),
    ("SSN: 123 45 6789", "ssn", "123 45 6789"),
    ("social security number 123456789 on file", "ssn", "123456789"),
    ("ITIN 912345678", "ssn", "912345678"),
    ("DOB 04/12/1991", "dob", "04/12/1991"),
    ("Date of birth: March 3, 1988", "dob", "March 3, 1988"),
    ("born on 1990-07-21 in Fresno", "dob", "1990-07-21"),
    ("routing number 021000021", "bank", "021000021"),
    ("Direct deposit account #: 0001 2345 6789", "bank", "0001 2345 6789"),
    ("IBAN GB29 NWBK 6016 1331 9268 19", "bank", "GB29 NWBK 6016 1331 9268 19"),
    ("card 4111 1111 1111 1111 exp 09/27", "card", "4111 1111 1111 1111"),
    ("Driver's license number D1234567", "id", "D1234567"),
    ("passport: 512345678", "id", "512345678"),
])
def test_identifiers_are_hidden(text, kind, secret):
    masked, counts = privacy.mask(text)
    assert secret not in masked and privacy.PLACEHOLDER[kind] in masked
    assert counts == {kind: 1}


@pytest.mark.parametrize("text", [
    "Her last day is 11/20/2026 and the final check is due 11/20/2026.",   # ordinary dates stay
    "She was hired on March 3, 2019 and has 46 hours of vacation.",
    "Call me at 510-555-0142 or 415 555 0199.",                            # phone numbers stay
    "Our EIN is 12-3456789 and we have 38 employees.",                     # employer tax id is not personal
    "Invoice 1042 total $8,780.00, PO 7781, order 4111111111111112.",      # not a valid card number
    "Labor Code 201, 227.3 and 29 CFR 825.300 apply.",
    "Please account for 12 employees on the Friday schedule.",
    "She renewed her driver's license last week.",
])
def test_ordinary_text_is_left_alone(text):
    assert privacy.mask(text) == (text, {})


def test_everything_sent_to_claude_is_masked(monkeypatch):
    sent = {}

    def fake_run(command, input, **kwargs):
        sent["stdin"] = input
        sent["system"] = command[command.index("--system-prompt") + 1]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"is_error": False, "result": "ok"}), stderr="")

    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    llm.run("Employee SSN 123-45-6789, DOB 04/12/1991.", system="Client note: payroll account 000123456789.")
    assert "123-45-6789" not in sent["stdin"] and "04/12/1991" not in sent["stdin"]
    assert "[SSN removed]" in sent["stdin"] and "[date of birth removed]" in sent["stdin"]
    assert "000123456789" not in sent["system"] and "[account number removed]" in sent["system"]

    # Text sent alongside a picture is masked too (the picture itself cannot be).
    llm.run("File for SSN 123-45-6789", system="s", attachments=[("image/jpeg", b"\xff\xd8")])
    assert "123-45-6789" not in sent["stdin"] and "[SSN removed]" in sent["stdin"]

    monkeypatch.setenv("CLIENT_INBOX_MASK", "0")
    llm.run("Employee SSN 123-45-6789", system="s")
    assert "123-45-6789" in sent["stdin"]


def test_the_thread_shows_what_was_hidden():
    pipeline.store_thread(raw_thread("t1", [raw_message(
        "m1", "Maya <maya@harbor.example>",
        "Her SSN is 123-45-6789, DOB 04/12/1991. A second employee's SSN is 987-65-4321.")]), True)
    client = TestClient(app_module.app, base_url="http://127.0.0.1")
    detail = client.get("/api/threads/t1").json()
    assert detail["privacy"] == {"on": True, "hidden": {"ssn": 2, "dob": 1}, "unmasked_files": []}
    assert "123-45-6789" in detail["messages"][0]["body"]  # the owner still sees their own mail in full
