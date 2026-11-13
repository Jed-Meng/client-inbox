"""Client profile cards and high-risk flags."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from client_inbox import app as app_module
from client_inbox import clients, db, drive, llm, pipeline, profile, voice

from conftest import raw_message, raw_thread

HEADERS = {"X-Client-Inbox": "1"}


def test_claude_fills_gaps_but_never_overwrites_the_owner():
    client = clients.client_for("maya@harbor.example")
    assert profile.learn(client, {"employee_count": "38", "states": "California", "industry": ""}, "t1") == [
        "employee_count", "states"]
    profile.set_by_user(client, {"employee_count": "41", "notes": "Owner prefers phone calls"})
    assert profile.learn(client, {"employee_count": "12", "states": "California, Nevada"}, "t2") == ["states"]
    fields = profile.load(client)["fields"]
    assert fields["employee_count"] == {"value": "41", "by": "user"}
    assert fields["states"]["value"] == "California, Nevada" and fields["states"]["by"] == "auto"

    text = profile.as_text(client)
    assert "- Employees: 41\n" in text
    assert "States with employees: California, Nevada (picked up from an earlier email, not confirmed)" in text
    assert "Owner prefers phone calls" in text

    profile.set_by_user(client, {"employee_count": ""})  # clearing lets Claude fill it again
    assert profile.learn(client, {"employee_count": "44"}, "t3") == ["employee_count"]


def test_empty_profile_adds_nothing_to_the_prompt():
    assert profile.as_text(clients.client_for("x@nobody.example")) == ""
    assert profile.as_text(None) == ""


@pytest.fixture
def hr(monkeypatch):
    monkeypatch.setattr(drive, "search", lambda keywords, limit=4: [])
    monkeypatch.setattr(voice, "past_replies", lambda client_id, limit=4: [])
    calls = {"draft_systems": [], "draft_tiers": [], "triage_tiers": []}

    def fake_llm(prompt, *, system, tier="fast", schema=None, timeout=240, attachments=None):
        if "needs_reply" in schema["properties"]:
            calls["triage_tiers"].append(tier)
            risky = "pregnant" in prompt
            newsletter = "Unsubscribe" in prompt
            return {"one_line": "x", "summary": "s", "needs_reply": not newsletter, "keywords": [], "topics": [],
                    "client_facts": {"employee_count": "38", "states": "California", "industry": "",
                                     "pay_schedule": "biweekly", "payroll_provider": ""},
                    "high_risk": risky or newsletter,
                    "risk_reasons": ["pregnant employee facing termination"] if risky else []}
        calls["draft_tiers"].append(tier)
        if tier == "careful" and calls.get("careful_unavailable"):
            raise llm.LLMError("model not available on this plan")
        calls["draft_systems"].append(system)
        return {"body": "draft", "sources_used": []}

    monkeypatch.setattr(llm, "run", fake_llm)

    def arrive(thread_id, sender, body):
        pipeline.store_thread(raw_thread(thread_id, [raw_message("m-" + thread_id, sender, body)]), True)
        pipeline.process_thread(thread_id)

    calls["arrive"] = arrive
    return calls


def test_profile_is_learned_from_mail_and_used_in_the_draft(hr):
    hr["arrive"]("t1", "Maya <maya@harbor.example>", "We have 38 employees in California, paid biweekly. Question...")
    client = TestClient(app_module.app, base_url="http://127.0.0.1")
    row = next(c for c in client.get("/api/state").json()["clients"] if c["name"] == "Harbor")
    assert row["digest"] == "38 employees · California · biweekly" and row["high_risk"] == 0

    card = client.get(f"/api/clients/{row['id']}/profile").json()
    by_key = {f["key"]: f for f in card["fields"]}
    assert by_key["employee_count"]["value"] == "38" and by_key["employee_count"]["by"] == "auto"
    assert by_key["industry"]["value"] == ""
    assert card["contacts"] == [{"name": "Maya", "email": "maya@harbor.example"}]
    assert "- Employees: 38" in hr["draft_systems"][-1] and "HIGH RISK" not in hr["draft_systems"][-1]

    saved = client.put(f"/api/clients/{row['id']}/profile", headers=HEADERS,
                       json={"fields": {"employee_count": "52"}}).json()
    assert {f["key"]: f for f in saved["fields"]}["employee_count"]["by"] == "user"
    hr["arrive"]("t2", "Maya <maya@harbor.example>", "Another question, we still have 38 employees.")
    assert "- Employees: 52\n" in hr["draft_systems"][-1]


def test_high_risk_mail_gets_a_holding_reply_and_can_be_cleared(hr):
    hr["arrive"]("t1", "Maya <maya@harbor.example>", "She is pregnant and the manager wants to fire her tomorrow.")
    client = TestClient(app_module.app, base_url="http://127.0.0.1")
    detail = client.get("/api/threads/t1").json()
    assert detail["risk"] == {"high": True, "reasons": ["pregnant employee facing termination"],
                              "dismissed": False, "active": True}
    system = hr["draft_systems"][-1]
    assert "HIGH RISK (pregnant employee facing termination)" in system and "holding reply" in system
    harbor = next(c for c in client.get("/api/state").json()["clients"] if c["name"] == "Harbor")
    assert harbor["high_risk"] == 1
    assert client.get(f"/api/clients/{harbor['id']}/threads").json()[0]["high_risk"] == 1

    # The owner clears the flag: the draft is rewritten as a normal reply.
    assert client.post("/api/threads/t1/risk", headers=HEADERS, json={"dismissed": True}).json()["queued"] is True
    key, fn, args = pipeline.WORKER.jobs.get()
    fn(*args); pipeline.WORKER.pending.discard(key)
    assert "HIGH RISK" not in hr["draft_systems"][-1]
    assert client.get("/api/threads/t1").json()["risk"]["active"] is False
    assert client.get("/api/state").json()["clients"][0]["high_risk"] == 0


def test_mail_that_needs_no_reply_is_never_flagged_or_learned_from(hr):
    hr["arrive"]("t9", "News <news@promo.example>", "Big sale. Unsubscribe here.")
    thread = pipeline.load_thread("t9")
    assert pipeline.risk_of(thread)["high"] is False
    assert profile.load(thread["client_id"])["fields"] == {}


def test_old_database_gains_profile_and_risk_columns(tmp_path, monkeypatch):
    home = tmp_path / "old"
    home.mkdir()
    con = sqlite3.connect(home / "client_inbox.db")
    con.execute("create table clients (id integer primary key, name text not null, created_at real)")
    con.execute("create table threads (id text primary key, client_id integer, keywords text, messages_json text)")
    con.execute("insert into clients (name) values ('Acme')")
    con.execute("insert into threads (id, client_id, keywords) values ('t1', 1, '[\"audit\"]')")
    con.commit(); con.close()
    monkeypatch.setenv("CLIENT_INBOX_HOME", str(home))
    assert profile.load(1) == {"fields": {}, "contacts": []}
    row = dict(db.conn().execute("select risk, keywords from threads").fetchone())
    assert pipeline.risk_of(row)["active"] is False
    assert pipeline.search_terms(row) == (["audit"], [], None)  # old keyword format still reads


def test_each_job_goes_to_its_own_model(hr):
    hr["arrive"]("t1", "Maya <maya@harbor.example>", "Routine question about our PTO policy.")
    hr["arrive"]("t2", "Maya <maya@harbor.example>", "She is pregnant and the manager wants to fire her tomorrow.")
    assert hr["triage_tiers"] == ["triage", "triage"]
    assert hr["draft_tiers"] == ["smart", "careful"]
    models = {r["thread_id"]: r["model"] for r in db.conn().execute("select thread_id, model from drafts")}
    assert models == {"t1": llm.MODELS["smart"], "t2": llm.MODELS["careful"]}
    assert (llm.MODELS["fast"], llm.MODELS["triage"], llm.MODELS["smart"], llm.MODELS["careful"]) == (
        "haiku", "sonnet", "sonnet", "opus")


def test_high_risk_draft_falls_back_when_the_careful_model_is_unavailable(hr):
    hr["careful_unavailable"] = True
    hr["arrive"]("t1", "Maya <maya@harbor.example>", "She is pregnant and the manager wants to fire her tomorrow.")
    assert hr["draft_tiers"] == ["careful", "smart"]
    draft = db.conn().execute("select body, model from drafts where thread_id = 't1'").fetchone()
    assert draft["body"] == "draft" and draft["model"] == llm.MODELS["smart"]
    assert "HIGH RISK" in hr["draft_systems"][-1]  # still written as a holding reply
    assert pipeline.load_thread("t1")["status"] == "idle"
