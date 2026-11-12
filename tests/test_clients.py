from client_inbox import clients, db, pipeline

from conftest import ME, raw_message, raw_thread


def test_company_domain_groups_everyone():
    first = clients.client_for("pat@acme.com", "Pat Lee")
    second = clients.client_for("sam@mail.acme.com", "Sam")
    assert first == second
    assert db.conn().execute("select name from clients where id = ?", (first,)).fetchone()["name"] == "Acme"


def test_freemail_is_one_client_per_address():
    pat = clients.client_for("pat@gmail.com", "Pat Lee")
    sam = clients.client_for("sam@gmail.com", "Sam Wu")
    assert pat != sam
    assert db.conn().execute("select name from clients where id = ?", (pat,)).fetchone()["name"] == "Pat Lee"


def test_registrable_domain():
    assert clients.registrable_domain("shop.acme.co.uk") == "acme.co.uk"
    assert clients.registrable_domain("a.b.acme.com") == "acme.com"


def test_counterpart_skips_the_owner():
    msgs = [
        {"from_email": ME, "from_name": "Me", "to": [["Pat", "pat@acme.com"]]},
        {"from_email": "pat@acme.com", "from_name": "Pat", "to": [["Me", ME]]},
    ]
    assert clients.counterpart(msgs, {ME}) == ("pat@acme.com", "Pat")
    assert clients.counterpart(msgs[:1], {ME}) == ("pat@acme.com", "Pat")


def test_merge_moves_rules_and_threads():
    acme = clients.client_for("pat@acme.com")
    acme_uk = clients.client_for("lee@acme.co.uk")
    pipeline.store_thread(raw_thread("t9", [raw_message("m1", "Lee <lee@acme.co.uk>", "hello")]), is_new=False)
    clients.merge(acme_uk, acme)
    assert clients.client_for("new.person@acme.co.uk") == acme
    assert db.conn().execute("select client_id from threads where id = 't9'").fetchone()["client_id"] == acme
    assert db.conn().execute("select count(*) as n from clients").fetchone()["n"] == 1


def test_manual_move_survives_new_mail_and_can_follow_the_sender():
    pipeline.store_thread(raw_thread("t1", [raw_message("m1", "Pat <pat@gmail.com>", "hello")]), is_new=False)
    acme = clients.client_for("boss@acme.com")
    clients.move_thread("t1", acme, "pat@gmail.com")
    pipeline.store_thread(raw_thread("t1", [
        raw_message("m1", "Pat <pat@gmail.com>", "hello"),
        raw_message("m2", "Pat <pat@gmail.com>", "again", date_ms=2_000),
    ]), is_new=True)
    assert db.conn().execute("select client_id from threads where id = 't1'").fetchone()["client_id"] == acme
    assert clients.client_for("pat@gmail.com") == acme
