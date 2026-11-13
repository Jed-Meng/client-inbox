"""The shared reference library: loading official pages, searching them per topic, linking them."""

import sqlite3

import pytest

from client_inbox import db, drive, library, llm, pipeline, voice, web

from conftest import raw_message, raw_thread

PAGES = {
    "https://agency.example/final-pay": "<title>x</title><nav>Menu Home About</nav><h1>Final wages</h1><p>"
        + "An employee who is discharged must be paid all wages due at the time of termination. " * 12 + "</p>",
    "https://agency.example/vacation": "<h1>Vacation</h1><p>"
        + "Earned and unused vacation must be paid out at the final rate when employment ends. " * 12 + "</p>",
    "https://agency.example/tax": "<h1>Payroll tax</h1><p>"
        + "Deposit federal payroll taxes on the semiweekly or monthly schedule that applies. " * 12 + "</p>",
}
PACK = [
    ("Final wages FAQ", "State Labor Office", "https://agency.example/final-pay", ""),
    ("Vacation pay FAQ", "State Labor Office", "https://agency.example/vacation", ""),
    ("Payroll tax guide", "Tax Office", "https://agency.example/tax", ""),
    ("Blocked handbook", "Other Office", "https://agency.example/blocked", ""),
]


@pytest.fixture
def loaded(monkeypatch):
    def fake_fetch(url, max_bytes=0, timeout=0):
        if url not in PAGES:
            raise web.FetchError("The site refused the request.")
        return {"url": url, "content_type": "text/html", "data": PAGES[url].encode()}

    monkeypatch.setattr(web, "fetch_public", fake_fetch)
    monkeypatch.setattr(library, "HR_PACK", PACK)
    library.load_hr_pack()


def test_pack_loads_what_it_can_and_lists_what_it_could_not(loaded):
    docs = {d["filename"]: d for d in library.documents()}
    assert set(docs) == {"Final wages FAQ", "Vacation pay FAQ", "Payroll tax guide"}
    assert docs["Final wages FAQ"]["source_url"] == "https://agency.example/final-pay"
    assert docs["Final wages FAQ"]["publisher"] == "State Labor Office" and docs["Final wages FAQ"]["fetched_at"]
    assert [f["title"] for f in library.STATUS["failed"]] == ["Blocked handbook"]
    assert library.STATUS["running"] is False and "California" in db.get_setting("practice_context")
    body = db.conn().execute("select body from upload_chunks limit 1").fetchone()["body"]
    assert "Menu Home About" not in body  # site navigation is not indexed


def test_refresh_replaces_instead_of_duplicating(loaded):
    library.load_hr_pack()
    assert len(library.documents()) == 3


def test_each_topic_gets_its_own_lookup(loaded):
    docs = pipeline.library_passages(["final paycheck when discharged", "unused vacation payout"], [])
    assert {d["filename"] for d in docs} >= {"Final wages FAQ", "Vacation pay FAQ"}
    assert pipeline.library_passages(["quarterly marketing newsletter redesign"], []) == []


def test_library_sources_reach_the_draft_with_their_links(loaded, monkeypatch):
    monkeypatch.setattr(drive, "search", lambda keywords, limit=4: [])
    monkeypatch.setattr(voice, "past_replies", lambda client_id, limit=4: [])
    seen = {}

    def fake_llm(prompt, *, system, tier="fast", schema=None, timeout=240, attachments=None):
        if "needs_reply" in schema["properties"]:
            seen["triage"] = prompt
            ids = {d["filename"]: d["id"] for d in library.documents()}
            return {"one_line": "x", "summary": "s", "needs_reply": True, "keywords": ["Luis"],
                    "topics": ["last paycheck timing", "unused vacation payout"],  # "paycheck" is not in the page
                    "reference_documents": [f"L{ids['Final wages FAQ']}", f"L{ids['Vacation pay FAQ']}", "L999"]}
        seen.update(prompt=prompt, system=system)
        return {"body": "Final wages are due at termination.", "sources_used": ["S1"]}

    monkeypatch.setattr(llm, "run", fake_llm)
    pipeline.store_thread(raw_thread("t1", [raw_message("m1", "Luis <luis@clinic.example>", "When is final pay due?")]), True)
    pipeline.process_thread("t1")

    import json
    sources = json.loads(db.conn().execute("select sources from drafts").fetchone()["sources"])
    library_sources = [s for s in sources if s["kind"] == "library"]
    assert "Final wages FAQ (State Labor Office)" in seen["triage"]
    assert {s["url"] for s in library_sources} == {"https://agency.example/final-pay", "https://agency.example/vacation"}
    assert library_sources[0]["used"] is True
    assert 'kind="library"' in seen["prompt"] and "paid all wages due at the time of termination" in seen["prompt"]
    assert "[verify:" in seen["system"] and "HR consulting" in seen["system"]


def test_a_client_file_is_not_shared_with_other_clients(loaded):
    pipeline.add_upload(7, "acme-handbook.txt", b"Acme gives fifteen days of vacation and pays unused vacation out.")
    assert all(hit["filename"] != "acme-handbook.txt"
               for hit in pipeline.search_uploads(0, ["unused vacation"], by_word=True))
    assert pipeline.search_uploads(7, ["vacation"])[0]["filename"] == "acme-handbook.txt"


def test_old_database_gains_the_new_columns(tmp_path, monkeypatch):
    home = tmp_path / "old"
    home.mkdir()
    con = sqlite3.connect(home / "client_inbox.db")
    con.execute("create table uploads (id integer primary key, client_id integer, filename text, path text, "
                "chars integer, created_at real)")
    con.execute("insert into uploads (client_id, filename, chars) values (3, 'old.txt', 10)")
    con.commit(); con.close()
    monkeypatch.setenv("CLIENT_INBOX_HOME", str(home))
    row = db.conn().execute("select filename, source_url, publisher, fetched_at from uploads").fetchone()
    assert dict(row) == {"filename": "old.txt", "source_url": None, "publisher": None, "fetched_at": None}


def test_mail_that_needs_no_reference_material_gets_none(loaded):
    assert pipeline.library_passages(["invoice total"], ["Harbor"], docs=[]) == []
