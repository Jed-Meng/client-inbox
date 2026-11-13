"""The reference library: documents searched for every client, not just one.

The HR starter pack is a list of official, free, public pages (California and federal).
Nothing is shipped with the app: each user's copy downloads them from the agency's own
site, so the text is current on the day it is loaded and every source links back to it.
"""

from __future__ import annotations

import re
import time

from . import db, extract, pipeline, web

LIBRARY = 0  # the client_id that marks a file as shared reference material
MAX_BYTES = 30 * 1024 * 1024

HR_CONTEXT = (
    "The owner works in HR consulting. Most of their clients' employees are in California, so apply "
    "California rules together with federal ones unless the thread says the employees are elsewhere, "
    "and say which one a rule comes from."
)

_DIR = "California Labor Commissioner (DIR)"
_CRD = "California Civil Rights Department"
_EDD = "California EDD"
_ECFR = "Code of Federal Regulations"

# (title, publisher, page to link to, address to download from when it differs)
# Not included because their sites refuse automated downloads: US Department of Labor fact sheets
# and OSHA handbooks. Download those in a browser and add them to the library by hand. The federal
# wage, hour and leave rules are covered here by the regulations themselves (eCFR).
HR_PACK = [
    ("California overtime FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_overtime.htm", ""),
    ("California overtime exemptions FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_overtimeexemptions.htm", ""),
    ("California meal periods FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_mealperiods.htm", ""),
    ("California rest periods FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_restperiods.htm", ""),
    ("California paydays, pay periods and final wages FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_paydays.htm", ""),
    ("California vacation pay FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_vacation.htm", ""),
    ("California minimum wage FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_minimumwage.htm", ""),
    ("California independent contractor FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_independentcontractor.htm", ""),
    ("California wage deductions FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_deductions.htm", ""),
    ("California reporting time pay FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_reportingtimepay.htm", ""),
    ("California paid sick leave", _DIR, "https://www.dir.ca.gov/dlse/paid_sick_leave.htm", ""),
    ("California Equal Pay Act and pay transparency FAQ", _DIR, "https://www.dir.ca.gov/dlse/california_equal_pay_act.htm", ""),
    ("California lactation accommodation", _DIR, "https://www.dir.ca.gov/dlse/Lactation_Accommodation.htm", ""),
    ("California right to inspect personnel files FAQ", _DIR, "https://www.dir.ca.gov/dlse/faq_righttoinspectpersonnelfiles.htm", ""),
    ("California retaliation FAQ", _DIR, "https://www.dir.ca.gov/dlse/FAQ_Retaliation.htm", ""),
    ("DLSE Enforcement Policies and Interpretations Manual", _DIR, "https://www.dir.ca.gov/dlse/DLSEManual/dlse_enfcmanual.pdf", ""),
    ("Cal/OSHA workplace violence prevention", "Cal/OSHA", "https://www.dir.ca.gov/dosh/Workplace-Violence.html", ""),
    ("California family, medical and pregnancy leave (CFRA, PDL)", _CRD, "https://calcivilrights.ca.gov/family-medical-pregnancy-leave/", ""),
    ("California employment discrimination (FEHA)", _CRD, "https://calcivilrights.ca.gov/employment/", ""),
    ("California sexual harassment prevention training", _CRD, "https://calcivilrights.ca.gov/shpt/", ""),
    ("California Paid Family Leave", _EDD, "https://edd.ca.gov/en/disability/paid-family-leave/", ""),
    ("California Employer's Guide (DE 44)", _EDD, "https://edd.ca.gov/siteassets/files/pdf_pub_ctr/de44.pdf", ""),
    ("FMLA regulations (29 CFR part 825)", _ECFR, "https://www.ecfr.gov/current/title-29/part-825",
     "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-29?part=825"),
    ("FLSA exemption regulations (29 CFR part 541)", _ECFR, "https://www.ecfr.gov/current/title-29/part-541",
     "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-29?part=541"),
    ("FLSA hours worked regulations (29 CFR part 785)", _ECFR, "https://www.ecfr.gov/current/title-29/part-785",
     "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-29?part=785"),
    ("EEOC: reasonable accommodation and undue hardship under the ADA", "EEOC",
     "https://www.eeoc.gov/laws/guidance/enforcement-guidance-reasonable-accommodation-and-undue-hardship-under-ada", ""),
    ("EEOC: Pregnant Workers Fairness Act", "EEOC",
     "https://www.eeoc.gov/wysk/what-you-should-know-about-pregnant-workers-fairness-act", ""),
    ("EEOC: prohibited employment policies and practices", "EEOC",
     "https://www.eeoc.gov/prohibited-employment-policiespractices", ""),
    ("EEOC: harassment", "EEOC", "https://www.eeoc.gov/harassment", ""),
    ("EEOC: retaliation and related issues", "EEOC",
     "https://www.eeoc.gov/laws/guidance/enforcement-guidance-retaliation-and-related-issues", ""),
    ("IRS Publication 15 (Employer's Tax Guide)", "IRS", "https://www.irs.gov/pub/irs-pdf/p15.pdf", ""),
    ("IRS Publication 15-A (employee or independent contractor)", "IRS", "https://www.irs.gov/pub/irs-pdf/p15a.pdf", ""),
]

STATUS = {"running": False, "done": 0, "total": 0, "failed": [], "finished_at": None}


def documents() -> list[dict]:
    rows = db.conn().execute(
        "select id, filename, chars, publisher, source_url, fetched_at, created_at from uploads "
        "where client_id = ? order by publisher, filename", (LIBRARY,),
    ).fetchall()
    return [dict(row) for row in rows]


def _text_of(page: dict, name: str) -> str:
    kind = page["content_type"]
    if kind in ("text/html", "application/xhtml+xml", ""):
        return extract.html_to_text(page["data"].decode("utf-8", errors="replace"))
    return extract.extract_text(name if "." in name else name + ".pdf", page["data"], kind, max_pages=1500)


def add_from_web(title: str, publisher: str, url: str, download: str = "") -> dict:
    """Download one page or PDF and index it, replacing an earlier copy of the same source."""
    page = web.fetch_public(download or url, max_bytes=MAX_BYTES, timeout=60)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", url.rstrip("/").rsplit("/", 1)[-1]) or "page"
    text = _text_of(page, name)
    if len(text) < 500:
        raise ValueError("The page had almost no readable text.")
    for row in db.conn().execute(
        "select id from uploads where client_id = ? and source_url = ?", (LIBRARY, url)
    ).fetchall():
        pipeline.delete_upload(row["id"])
    return pipeline.add_upload(LIBRARY, title, page["data"], text=text, source_url=url, publisher=publisher)


def load_hr_pack() -> None:
    """Download (or refresh) every document in the HR starter pack. Failures are listed, not fatal."""
    STATUS.update(running=True, done=0, total=len(HR_PACK), failed=[], finished_at=None)
    try:
        for title, publisher, url, download in HR_PACK:
            try:
                add_from_web(title, publisher, url, download)
            except Exception as exc:
                STATUS["failed"].append({"title": title, "url": url, "reason": str(exc)[:200]})
            STATUS["done"] += 1
        db.set_setting("practice_context", HR_CONTEXT)
    finally:
        STATUS.update(running=False, finished_at=time.time())
