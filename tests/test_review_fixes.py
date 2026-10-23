"""Regression tests for the PR #2 review comments (one section per comment)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, queue, sessions
from app.agent import llm, loop
from app.agent.tools import ToolBox
from app.api import main
from app.privacy import find_full_ids, redact_full_ids
from app.rules import engine

NOMINEE = {"nominee_name": "Anjeli", "nominee_relation": "daughter"}


def new_tb(conn, cid):
    s = loop.start_session(conn, cid, "officer-1")
    return ToolBox(conn, s["session_id"])


def queued_form(conn, cid="C002", pid="PMJJBY"):
    tb = new_tb(conn, cid)
    out = tb.call("fill_form", {"product_id": pid, "extra_fields": NOMINEE})
    return tb, out["item_id"]


def audit_text(conn):
    return " ".join(r["payload_json"] or "" for r in conn.execute("SELECT payload_json FROM audit_log"))


@pytest.fixture
def client(conn):
    main.set_connection(conn)
    yield TestClient(main.app)
    main.set_connection(None)


# 1 · PDF must not be rewritten before the revision is validated ----------------------------

@pytest.mark.parametrize("approve_first", [False, True])
def test_failed_revision_leaves_pdf_untouched(conn, approve_first):
    tb, iid = queued_form(conn)
    if approve_first:
        queue.approve(conn, iid, "officer-1")  # SUBMITTED
    pdf = Path(queue.get(conn, iid)["pdf_path"])
    before = pdf.read_bytes()
    out = tb.call("revise_item", {"item_id": iid, "changes": {"nominee_name": "Hacked"}, "explanation": "x"})
    assert "error" in out
    assert pdf.read_bytes() == before
    assert queue.get(conn, iid)["payload"]["nominee_name"] == "Anjeli"


def test_successful_revision_rerenders_pdf(conn):
    tb, iid = queued_form(conn)
    queue.reject(conn, iid, "officer-1", "Nominee is Anjali")
    out = tb.call("revise_item", {"item_id": iid, "changes": {"nominee_name": "Anjali"}, "explanation": "fix"})
    assert out["status"] == queue.PENDING_APPROVAL
    assert queue.get(conn, iid)["pdf_path"].endswith(f"{iid}.pdf")


# 2 · revision path must refuse full Aadhaar (and nothing leaks to the audit log) -----------------

def test_revision_refuses_full_aadhaar(conn):
    tb, iid = queued_form(conn)
    queue.reject(conn, iid, "officer-1", "Aadhaar is wrong")
    out = tb.call("revise_item", {"item_id": iid, "changes": {"aadhaar_masked": "2345 6789 0123"},
                                  "explanation": "x"})
    assert "error" in out and "Aadhaar" in out["error"]
    item = queue.get(conn, iid)
    assert item["status"] == queue.REJECTED and item["payload"]["aadhaar_masked"].startswith("XXXX")
    assert not find_full_ids(audit_text(conn))


def test_officer_revise_route_refuses_full_aadhaar(client, conn):
    _, iid = queued_form(conn)
    queue.reject(conn, iid, "officer-1", "x")
    r = client.post(f"/queue/{iid}/revise", json={"changes": {"aadhaar_masked": "2345-6789-0123"}})
    assert r.status_code == 400


# 3 · regex covers common formats, recursively ----------------------------------------------------

@pytest.mark.parametrize("value", [
    "234567890123", "2345 6789 0123", "2345-6789-0123", "2345.6789.0123", "id: 2345-6789-0123 ok",
    {"nested": {"deep": "2345-6789-0123"}}, ["a", "2345.6789.0123"], 234567890123,
])
def test_full_aadhaar_detected(value):
    assert find_full_ids(value)


@pytest.mark.parametrize("value", [
    "XXXX-XXXX-1234", "2020-03-14", "98450 12345", "1234567890123", "XXXXXX4521", 2000, True,
])
def test_masked_and_other_numbers_allowed(value):
    assert not find_full_ids(value)


def test_fill_form_refuses_nested_full_aadhaar(conn):
    tb = new_tb(conn, "C002")
    out = tb.call("fill_form", {"product_id": "PMJJBY",
                                "extra_fields": {**NOMINEE, "nominee_kyc": {"aadhaar": "2345-6789-0123"}}})
    assert "error" in out
    assert queue.list_items(conn) == []


def test_redaction_keeps_last_four():
    assert redact_full_ids({"a": ["2345-6789-0123"]}) == {"a": ["XXXX-XXXX-0123"]}


# 4 · the fact form must not record guesses: an empty submission changes nothing --------------------

def test_empty_facts_record_nothing(client):
    sid = client.post("/sessions", json={"customer_id": "C001"}).json()["session_id"]
    st = client.post(f"/sessions/{sid}/facts", json={"facts": {}}).json()["state"]
    assert {r["product_id"]: r["status"] for r in st["results"]}["PMFBY"] == engine.UNKNOWN


# 5 · rules-only mode: a rejected item can be fixed by the officer -----------------------------------

def test_rules_only_reject_revise_approve(client):
    sid = client.post("/sessions", json={"customer_id": "C002"}).json()["session_id"]
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {
        "PMJJBY": NOMINEE, "PMSBY": NOMINEE,
        "SSY": {"girl_child_name": "Anjali", "girl_child_dob": "2020-03-14"}}}).json()["state"]
    iid = next(i["item_id"] for i in st["queue_items"] if i["product_id"] == "PMJJBY")

    out = client.post(f"/queue/{iid}/reject", json={"reason": "Nominee is Anjali"}).json()
    assert out["reply"]["mode"] == "rules_only" and out["item"]["status"] == "REJECTED"
    assert out["state"]["state"] == "REVISING"

    # "Prepare all" no longer errors on the rejected item
    act = client.post(f"/sessions/{sid}/act", json={}).json()
    assert not any("error" in o for o in act["outcomes"])

    r = client.post(f"/queue/{iid}/revise", json={"changes": {"nominee_name": "Anjali"}}).json()
    assert r["item"]["status"] == "PENDING_APPROVAL" and r["item"]["payload"]["nominee_name"] == "Anjali"
    for i in client.get("/queue", params={"session_id": sid, "status": "PENDING_APPROVAL"}).json():
        client.post(f"/queue/{i['item_id']}/approve", json={})
    assert client.get(f"/sessions/{sid}/state").json()["state"] == "DONE"


# 6 · an unanswered question ends as VERIFY_MANUALLY, not UNKNOWN forever ----------------------------

def test_unanswered_question_becomes_verify_manually(conn):
    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    out = loop.handle_message(conn, sid, "hello")              # rules-only: asks Q_ITP
    assert out["reply"]["question"]["question_id"] == "Q_ITP"
    assert engine.status_of(out["state"]["results"], "APY") == engine.UNKNOWN  # still waiting
    out = loop.handle_message(conn, sid, "not sure")           # nothing recorded, nothing left to ask
    assert out["reply"]["question"] is None
    assert engine.status_of(out["state"]["results"], "APY") == engine.VERIFY_MANUALLY


def test_question_limit_finalizes(conn):
    s = sessions.create(conn, "C004", "officer-1")
    sessions.update(conn, s["session_id"], questions_asked=5)
    res = sessions.results(conn, sessions.get(conn, s["session_id"]))
    assert engine.status_of(res, "APY") == engine.VERIFY_MANUALLY


# 7 · the model's question id is checked against the ranker ------------------------------------------

def _fake(monkeypatch, text):
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")
    monkeypatch.setattr(llm, "run_tool_loop", lambda *a, **k: text)


def test_invented_question_id_is_replaced(conn, monkeypatch):
    _fake(monkeypatch, json.dumps({"to_officer": "ok", "question": {"question_id": "Q_CROP_DETAILS", "text": "?"},
                                   "to_customer": {"lang": "kn", "text": "..."}}))
    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    out = loop.handle_message(conn, sid, "hi")
    assert out["reply"]["question"]["question_id"] == "Q_ITP"
    assert out["reply"]["to_customer"] is None
    s = sessions.get(conn, sid)
    assert s["questions_asked"] == 1 and s["asked_questions"] == ["Q_ITP"]
    assert "question_replaced" in [r["event"] for r in conn.execute("SELECT event FROM audit_log")]


def test_invented_ids_cannot_burn_the_budget(conn, monkeypatch):
    _fake(monkeypatch, json.dumps({"to_officer": "ok", "question": {"question_id": "Q_MADE_UP", "text": "?"}}))
    sid = loop.start_session(conn, "C001", "officer-1")["session_id"]
    for _ in range(6):
        loop.handle_message(conn, sid, "hi")
    assert sessions.get(conn, sid)["questions_asked"] <= 1


# 8 · recorded facts can't be silently overwritten -------------------------------------------------

def test_conversation_cannot_undo_completed_request(client, conn):
    sid = client.post("/sessions", json={"customer_id": "C001"}).json()["session_id"]
    st = client.post(f"/sessions/{sid}/act", json={}).json()["state"]
    seed = next(i for i in st["queue_items"] if i["action_id"] == "AADHAAR_SEEDING_REQUEST")
    client.post(f"/queue/{seed['item_id']}/approve", json={})

    tb = ToolBox(conn, sid)
    out = tb.call("update_profile", {"facts": {"aadhaar_seeded": False}, "evidence": "I think it isn't linked"})
    assert out["rejected_fields"][0]["reason"].startswith("set by a completed service request")
    assert out["profile"]["aadhaar_seeded"] is True
    pmk = next(r for r in tb.call("check_eligibility", {})["results"] if r["product_id"] == "PMKISAN_DBT")
    assert "AADHAAR_SEEDING_REQUEST" not in pmk["blocked_by"]
    # and no duplicate request even if asked directly
    dup = tb.call("raise_request", {"action_id": "AADHAAR_SEEDING_REQUEST"})
    assert "error" in dup or dup.get("note") == "already raised"
    assert len([i for i in queue.list_items(conn, session_id=sid) if i["action_id"] == "AADHAAR_SEEDING_REQUEST"]) == 1


def test_recorded_fact_conflict_and_officer_correction(conn):
    tb = new_tb(conn, "C004")
    tb.call("update_profile", {"facts": {"is_income_tax_payer": False}, "evidence": "no tax"})
    out = tb.call("update_profile", {"facts": {"is_income_tax_payer": True}, "evidence": "maybe I did"})
    assert out["rejected_fields"] == [{"field": "is_income_tax_payer", "reason": "conflicts with recorded fact"}]
    officer = ToolBox(conn, tb.session_id, actor="officer")
    out = officer.call("update_profile", {"facts": {"is_income_tax_payer": True}, "evidence": "checked Form 16"})
    assert out["rejected_fields"] == [] and out["profile"]["is_income_tax_payer"] is True


# 9 · tool exceptions come back to the model as tool errors, not "LLM unavailable" ---------------------

def test_bad_tool_args_are_tool_errors(conn):
    tb = new_tb(conn, "C001")
    assert "error" in tb.call("update_profile", {"facts": "is_income_tax_payer=false", "evidence": "x"})
    assert "error" in tb.call("fill_form", "PMJJBY")


def test_unexpected_tool_exception_is_contained(conn, monkeypatch):
    monkeypatch.setattr(ToolBox, "plan_actions", lambda self: {}["boom"])  # before init: methods bind there
    tb = new_tb(conn, "C001")
    out = tb.call("plan_actions", {})
    assert out["error"].startswith("internal error in plan_actions: KeyError")
    assert "error" in [r["event"] for r in conn.execute("SELECT event FROM audit_log")]


def test_tool_bug_does_not_trigger_rules_only(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")

    def run(system, history, tools, execute, max_calls=8):
        r = execute("update_profile", {"facts": "oops", "evidence": "x"})
        assert "error" in r
        return json.dumps({"to_officer": "I made a bad call and recovered."})

    monkeypatch.setattr(llm, "run_tool_loop", run)
    sid = loop.start_session(conn, "C001", "officer-1")["session_id"]
    assert loop.handle_message(conn, sid, "hi")["reply"]["mode"] == "agent"


# 10 · an escalated item keeps the session open --------------------------------------------------

def test_needs_officer_is_escalated_not_done(client, conn):
    sid = client.post("/sessions", json={"customer_id": "C006"}).json()["session_id"]
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {
        "PMJJBY": NOMINEE, "APY": {**NOMINEE, "pension_amount": 1000},
        "SSY": {"girl_child_name": "Zoya", "girl_child_dob": "2023-01-02"}}}).json()["state"]
    items = st["queue_items"]
    target = next(i["item_id"] for i in items if i["product_id"] == "PMJJBY")
    for i in items:
        if i["item_id"] != target:
            client.post(f"/queue/{i['item_id']}/approve", json={})
    for n in range(config.MAX_REVISIONS):
        client.post(f"/queue/{target}/reject", json={"reason": f"wrong {n}"})
        client.post(f"/queue/{target}/revise", json={"changes": {"nominee_name": f"try{n}"}})
    out = client.post(f"/queue/{target}/reject", json={"reason": "still wrong"}).json()
    assert out["item"]["status"] == "NEEDS_OFFICER"
    assert out["state"]["state"] == "ESCALATED"
    d = client.get("/dashboard").json()
    assert d["items_escalated"] == 1 and d["sessions_by_state"].get("ESCALATED") == 1

    # the officer can fix it; it goes back to pending approval
    r = client.post(f"/queue/{target}/revise", json={"changes": {"nominee_name": "Zoya"}}).json()
    assert r["item"]["status"] == "PENDING_APPROVAL"
    client.post(f"/queue/{target}/approve", json={})
    assert client.get(f"/sessions/{sid}/state").json()["state"] == "DONE"
