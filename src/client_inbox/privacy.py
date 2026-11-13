"""Hide identifiers Claude has no need to see.

Every piece of text is passed through `mask` on this computer before it is sent to Claude:
Social Security numbers, dates of birth, bank and card numbers, driver's licence and
passport numbers become a short placeholder such as "[SSN removed]". The original stays
in the user's own mailbox and local files; only the copy that leaves the machine changes.

Limits, stated plainly:
- This is pattern matching. It catches the common written forms, not every possible one.
- Pictures and scanned PDFs are sent as they are so Claude can read them (OCR). Text that
  comes back from OCR is masked like any other text before it is used again.
- Names, addresses, health details and the substance of a complaint are NOT hidden: the
  reply cannot be written without them.

Set CLIENT_INBOX_MASK=0 to turn masking off.
"""

from __future__ import annotations

import os
import re

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = rf"(?:\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}|\d{{4}}-\d{{2}}-\d{{2}}|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}|\d{{1,2}}\s+{_MONTH}\s+\d{{4}})"
_LABEL_END = r"(?:\s*(?:number|num|no\.?|#))?\s*(?:is|was|are|[:=#-])?\s*"

# (kind, pattern, group holding the value to hide)
_RULES = [
    ("ssn", re.compile(r"(?<![\d-])\d{3}[- ]\d{2}[- ]\d{4}(?![\d-])"), 0),
    ("ssn", re.compile(
        rf"(?i)\b(?:ssn|ss#|social\s+security|social|itin|taxpayer\s+id)\b{_LABEL_END}(\d{{9}})\b"), 1),
    ("dob", re.compile(
        rf"(?i)\b(?:dob|d\.o\.b\.?|date\s+of\s+birth|birth\s*date|birthday|born(?:\s+on)?)\b{_LABEL_END}({_DATE})"), 1),
    ("bank", re.compile(
        rf"(?i)\b(?:routing|aba|iban|swift|(?:bank\s+|checking\s+|savings\s+)?account|acct)\b{_LABEL_END}"
        r"((?:[A-Z]{2}\d{2}[A-Z0-9 ]{10,30})|\d(?:[ -]?\d){5,16})\b"), 1),
    ("id", re.compile(
        rf"(?i)\b(?:driver'?s?\s+licen[sc]e|passport)\b{_LABEL_END}((?=[A-Z0-9-]*\d{{4}})[A-Z0-9][A-Z0-9-]{{5,14}})\b"), 1),
]
_CARD = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")

PLACEHOLDER = {
    "ssn": "[SSN removed]",
    "dob": "[date of birth removed]",
    "bank": "[account number removed]",
    "card": "[card number removed]",
    "id": "[ID number removed]",
}


def enabled() -> bool:
    return os.environ.get("CLIENT_INBOX_MASK", "1") != "0"


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def mask(text: str) -> tuple[str, dict[str, int]]:
    """The text with identifiers replaced, and how many of each kind were found."""
    counts: dict[str, int] = {}
    if not text or not enabled():
        return text, counts

    def hide(kind: str):
        def replace(match: re.Match) -> str:
            counts[kind] = counts.get(kind, 0) + 1
            return PLACEHOLDER[kind]
        return replace

    def hide_card(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            counts["card"] = counts.get("card", 0) + 1
            return PLACEHOLDER["card"]
        return match.group(0)

    text = _CARD.sub(hide_card, text)
    for kind, pattern, group in _RULES:
        if group == 0:
            text = pattern.sub(hide(kind), text)
        else:
            replace = hide(kind)
            text = pattern.sub(
                lambda m, r=replace, g=group: m.group(0)[: m.start(g) - m.start(0)] + r(m)
                + m.group(0)[m.end(g) - m.start(0):],
                text,
            )
    return text, counts


def count(*texts: str) -> dict[str, int]:
    """How many identifiers of each kind are in these texts (what masking would hide)."""
    total: dict[str, int] = {}
    for text in texts:
        for kind, found in mask(text or "")[1].items():
            total[kind] = total.get(kind, 0) + found
    return total
