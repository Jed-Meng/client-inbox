"""Attachments and links: which links get opened, when OCR is used, and what is never fetched."""

import io

import pytest

from client_inbox import db, drive, extract, files, gmail, llm, pipeline, web

from conftest import ME, raw_message, raw_thread


def parsed(body, html=False, **kwargs):
    return gmail.parse_thread(raw_thread("t1", [raw_message("m1", "Pat <pat@acme.com>", body, html=html, **kwargs)]), {ME})


def test_links_come_from_html_and_plain_text():
    thread = parsed('<p>See <a href="https://acme.com/brief.pdf">the brief</a> and https://acme.com/specs/v2.</p>', html=True)
    urls = [link["url"] for link in thread["messages"][0]["links"]]
    assert urls == ["https://acme.com/brief.pdf", "https://acme.com/specs/v2"]


def test_action_tracking_social_and_homepage_links_are_never_opened():
    thread = parsed(
        '<a href="https://acme.com/proposal/42">Proposal</a>'
        '<a href="https://acme.com/unsubscribe?u=1">Unsubscribe</a>'
        '<a href="https://acme.com/go?x=1">Confirm your email</a>'
        '<a href="https://click.mailer.com/track/abc">here</a>'
        '<a href="https://www.linkedin.com/in/pat">LinkedIn</a>'
        '<a href="https://acme.com/">acme.com</a>'
        '<a href="https://acme.com/account/reset">Reset</a>', html=True)
    assert [link["url"] for link in files.pick_links(thread)] == ["https://acme.com/proposal/42"]


def test_links_in_my_own_messages_are_ignored():
    thread = gmail.parse_thread(raw_thread("t1", [
        raw_message("m1", f"Me <{ME}>", "my deck https://myfirm.com/deck/9", to="pat@acme.com", labels=("SENT",)),
    ]), {ME})
    assert files.pick_links(thread) == []


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8765/api/state", "http://localhost/admin", "http://10.0.0.5/router",
    "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data", "http://[::1]/", "file:///etc/passwd",
    "ftp://example.com/x", "https://user:pw@8.8.8.8/",
])
def test_web_refuses_private_and_odd_addresses(url):
    with pytest.raises(web.FetchError):
        web.check_url(url)


def test_web_allows_a_public_address():
    web.check_url("https://8.8.8.8/page")  # a literal public IP: no DNS lookup needed


def image_pdf():
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (200, 120), (255, 255, 255)).save(out, format="PDF")
    return out.getvalue()


def png():
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 120, 200)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def seen(monkeypatch):
    calls = []

    def fake_llm(prompt, *, system, tier="fast", schema=None, timeout=240, attachments=None):
        calls.append({"prompt": prompt, "attachments": [(kind, len(data)) for kind, data in attachments or []]})
        if "text" in schema["properties"]:
            return {"text": "TOTAL DUE $8,780.00", "summary": "An invoice for $8,780."}
        return {"summary": "A plain summary."}

    monkeypatch.setattr(llm, "run", fake_llm)
    return calls


def test_scanned_pdf_and_picture_go_to_ocr_but_text_files_do_not(seen):
    scanned = files.understand("scan.pdf", image_pdf(), "application/pdf")
    assert scanned["method"] == "ocr" and scanned["text"] == "TOTAL DUE $8,780.00"
    assert seen[-1]["attachments"][0][0] == "application/pdf"

    photo = files.understand("board.png", png(), "image/png")
    assert photo["method"] == "ocr" and seen[-1]["attachments"][0][0] == "image/jpeg"  # resized, re-encoded

    note = files.understand("note.txt", b"Meeting moved to Friday at 2pm.", "text/plain")
    assert note["method"] == "text" and seen[-1]["attachments"] == []
    assert "Meeting moved to Friday" in seen[-1]["prompt"]


def test_unreadable_picture_is_reported_not_guessed(seen):
    with pytest.raises(ValueError):
        files.understand("photo.jpg", b"not really an image", "image/jpeg")
    assert seen == []


def test_thread_files_are_listed_then_read(seen, monkeypatch):
    logo = "logo.png"
    raw = raw_thread("t1", [raw_message(
        "m1", "Pat <pat@acme.com>",
        "Brief: https://acme.com/brief and doc https://docs.google.com/document/d/1AbcDefGhiJklMnoPqrStuVwxYz012345/edit",
        attachments=["scan.pdf", logo])])
    raw["messages"][0]["payload"]["parts"][1]["body"]["size"] = 50_000
    raw["messages"][0]["payload"]["parts"][2].update(mimeType="image/png")
    raw["messages"][0]["payload"]["parts"][2]["body"]["size"] = 900
    monkeypatch.setattr(gmail, "attachment_bytes", lambda mid, aid: image_pdf())
    monkeypatch.setattr(web, "fetch", lambda url: {
        "url": url, "content_type": "text/html",
        "data": ("<title>Acme brief</title><p>" + "Launch the new site by March 14. " * 20 + "</p>").encode()})
    monkeypatch.setattr(drive, "read_by_id", lambda file_id: ("Acme SOW", "Rush fee is $450 flat."))

    pipeline.store_thread(raw, is_new=True)
    listed = {f["name"]: f for f in files.for_thread("t1")}
    assert listed["scan.pdf"]["status"] == "pending"
    assert listed[logo]["status"] == "skipped"  # tiny image: a logo, not worth an OCR call
    assert sum(1 for f in listed.values() if f["kind"] == "link") == 2

    files.read_all("t1", include_links=False)  # mail that needs no reply: links stay unopened
    after = {f["key"]: f for f in files.for_thread("t1")}
    assert after["att-scan.pdf"]["method"] == "ocr"
    assert after["https://acme.com/brief"]["status"] == "pending"

    files.read_all("t1", include_links=True)
    done = {f["key"]: f for f in files.for_thread("t1")}
    assert done["https://acme.com/brief"]["method"] == "web" and done["https://acme.com/brief"]["name"] == "Acme brief"
    google = next(f for f in done.values() if f["method"] == "drive")
    assert google["name"] == "Acme SOW" and "Rush fee" in google["text"]

    # The draft sees all of it as sources.
    thread = pipeline.load_thread("t1")
    monkeypatch.setattr(drive, "search", lambda keywords, limit=4: [])
    sources = pipeline.gather_sources(thread)
    assert {s["kind"] for s in sources} >= {"attachment", "link"}
    assert any("read with OCR" in s["title"] for s in sources)


def test_a_failed_link_is_recorded_and_does_not_stop_the_rest(seen, monkeypatch):
    def refuse(url):
        raise web.FetchError("The site refused the request (it needs a sign-in or blocks automated visits).")

    monkeypatch.setattr(web, "fetch", refuse)
    pipeline.store_thread(raw_thread("t1", [raw_message("m1", "Pat <pat@acme.com>", "see https://acme.com/private/doc")]), True)
    files.read_all("t1")
    row = files.for_thread("t1")[0]
    assert row["status"] == "error" and row["note"] == "The site refused the request (it needs a sign-in or blocks automated visits)."
