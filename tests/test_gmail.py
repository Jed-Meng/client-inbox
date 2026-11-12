from client_inbox import gmail

from conftest import ME, raw_message, raw_thread


def thread():
    return gmail.parse_thread(raw_thread("t1", [
        raw_message("m1", "Pat Lee <pat@acme.com>", "Can you send the Q3 invoice?", attachments=["po.txt"]),
        raw_message("m2", f"Owner <{ME}>", "Sure, tomorrow.\n\nOn Mon, Pat wrote:\n> Can you send", date_ms=2_000,
                    labels=("SENT",)),
        raw_message("m3", "Pat Lee <pat@acme.com>", "<p>Thanks &amp; one more <b>question</b></p>", date_ms=3_000,
                    html=True, references="<m1@mail> <m2@mail>"),
        raw_message("m4", f"Owner <{ME}>", "unsent draft", date_ms=4_000, labels=("DRAFT",)),
    ]), {ME})


def test_parse_thread():
    parsed = thread()
    assert [m["id"] for m in parsed["messages"]] == ["m1", "m2", "m3"]  # the draft is ignored
    assert parsed["awaiting_me"] is True
    assert parsed["last_inbound_id"] == "m3"
    assert parsed["messages"][0]["attachments"][0]["filename"] == "po.txt"
    assert parsed["messages"][2]["body"] == "Thanks & one more question"
    assert parsed["messages"][1]["is_me"] is True


def test_strip_quoted():
    assert gmail.strip_quoted("Sure, tomorrow.\n\nOn Mon, Pat wrote:\n> Can you send") == "Sure, tomorrow."
    assert gmail.strip_quoted("Yes.\n> old line\nSee you") == "Yes.\nSee you"


def test_reply_is_addressed_and_threaded():
    mime = gmail.build_reply(thread(), "Here you go.", ME)
    assert mime["To"] == "pat@acme.com"
    assert mime["Subject"] == "Re: Q3 invoice"
    assert mime["In-Reply-To"] == "<m3@mail>"
    assert mime["References"] == "<m1@mail> <m2@mail> <m3@mail>"
    assert mime.get_content().strip() == "Here you go."


def test_no_reply_when_only_the_owner_wrote():
    parsed = gmail.parse_thread(raw_thread("t2", [raw_message("m1", f"Owner <{ME}>", "hi", to="x@acme.com")]), {ME})
    assert gmail.build_reply(parsed, "x", ME) is None
