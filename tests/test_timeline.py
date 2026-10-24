"""Session timeline (issue #18): the audit log as the four CGI pipeline stages, live during a turn."""
import json

import pytest
from fastapi.testclient import TestClient

from app import config, timeline
from app.agent import llm, loop
from app.agent.tools import ToolBox
from app.api import main

NOMINEE = {"nominee_name": "Anjali", "nominee_relation": "daughter"}
CHECKER = {"officer_id": "manager-1"}  # maker-checker (#39): the branch manager approves what officer-1 prepared


@pytest.fixture
def client(conn):
    main.set_connection(conn)
    yield TestClient(main.app)
    main.set_connection(None)


def tl(client, sid):
    r = client.get(f"/sessions/{sid}/timeline")
    assert r.status_code == 200
    return r.json()


def stage_status(t):
    return {s["id"]: s["status"] for s in t["stages"]}


def test_ramesh_moves_through_all_four_stages(client):
    sid = client.post("/sessions", json={"customer_id": "C001"}).json()["session_id"]
    t = tl(client, sid)
    assert t["stage"] == "decide" and not t["running"]                       # asking the crop question
    assert stage_status(t) == {"ingest": "done", "decide": "current", "act": "upcoming", "report": "upcoming"}
    assert [e["kind"] for e in t["events"]] == ["session_start"]

    client.post(f"/sessions/{sid}/facts", json={"facts": {"grows_notified_crop": True, "in_notified_area": True},
                                               "question_id": "Q_NOTIFIED_CROP"})
    t = tl(client, sid)
    rec = next(e for e in t["events"] if e["tool"] == "update_profile")
    assert rec["actor"] == "officer" and rec["stage"] == "ingest" and rec["title"] == "Officer recorded 2 facts"
    assert "Grows notified crop: yes" in rec["summary"]

    extra = {p: NOMINEE for p in ("PMJJBY", "PMSBY")}
    extra["APY"] = {**NOMINEE, "pension_amount": 2000}
    extra["PMFBY"] = {"crop_name": "Ragi", "season": "Kharif 2026"}
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": extra}).json()["state"]
    t = tl(client, sid)
    assert t["stage"] == "act"
    act = next(s for s in t["stages"] if s["id"] == "act")
    assert act["tools"] == {"raise_request": 2, "fill_form": 4}
    assert next(s for s in t["stages"] if s["id"] == "decide")["tools"]["plan_actions"] >= 1
    seeding = next(e for e in t["events"] if e["tool"] == "raise_request")
    assert seeding["title"] == "Agent raised AADHAAR_SEEDING_REQUEST"
    assert "waits for a checker's approval" in seeding["summary"]

    for i in st["queue_items"]:
        client.post(f"/queue/{i['item_id']}/approve", json=CHECKER)
    t = tl(client, sid)
    assert t["state"] == "ACTING" and t["stage"] == "act"  # seeding + eKYC done: PM-KISAN form is next

    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {}}).json()["state"]
    for i in st["queue_items"]:
        if i["status"] == "PENDING_APPROVAL":
            client.post(f"/queue/{i['item_id']}/approve", json=CHECKER)
    t = tl(client, sid)
    assert t["state"] == "DONE" and t["stage"] == "report"
    assert stage_status(t) == {"ingest": "done", "decide": "done", "act": "done", "report": "current"}
    kinds = [e["kind"] for e in t["events"]]
    assert kinds.count("approve") == 7 and kinds.count("submit_mock") == 7
    approved = next(e for e in t["events"] if e["kind"] == "approve")
    assert approved["summary"] == "by Branch Manager"
    effect = next(e for e in t["events"] if e["kind"] == "completion_effect")
    assert effect["stage"] == "report" and "Aadhaar seeded: yes" in effect["summary"]


def test_events_are_in_order_and_tool_calls_are_folded(client):
    sid = client.post("/sessions", json={"customer_id": "C002"}).json()["session_id"]
    client.post(f"/sessions/{sid}/act", json={"extra_fields": {"PMJJBY": NOMINEE}})
    t = tl(client, sid)
    ids = [e["id"] for e in t["events"]]
    assert ids == sorted(ids)
    kinds = {e["kind"] for e in t["events"]}
    assert not kinds & {"tool_result", "profile_update", "queue_create"}
    assert all(e["tone"] != "running" for e in t["events"]) and not t["running"]


def test_guardrail_refusals_are_marked(client, conn):
    sid = client.post("/sessions", json={"customer_id": "C002"}).json()["session_id"]
    tb = ToolBox(conn, sid)  # the agent's toolbox
    tb.call("fill_form", {"product_id": "APY"})                         # Priya: APY is NOT_ELIGIBLE
    tb.call("update_profile", {"facts": {"age": 30}, "evidence": "I am 30"})  # bank record says 45
    events = tl(client, sid)["events"]
    refused = next(e for e in events if e["tool"] == "fill_form")
    assert refused["tone"] == "guardrail" and refused["title"] == "fill_form refused"
    assert "NOT_ELIGIBLE" in refused["summary"]
    kept = next(e for e in events if e["tool"] == "update_profile")
    assert kept["tone"] == "guardrail" and "conflicts with bank record" in kept["summary"]


def test_agent_turn_events_and_replaced_question(client, conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")

    def run(system, history, tools, execute, max_calls=8):
        execute("check_eligibility", {})
        execute("pick_next_question", {})
        return json.dumps({"to_officer": "Need one more answer.",
                           "question": {"question_id": "Q_MADE_UP", "text": "Anything else?"},
                           "next_step": "awaiting_answer"})

    monkeypatch.setattr(llm, "run_tool_loop", run)
    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    loop.handle_message(conn, sid, "He works as a delivery partner")
    t = tl(client, sid)
    kinds = [e["kind"] for e in t["events"]]
    assert kinds.index("message") < kinds.index("tool") < kinds.index("llm_reply")
    assert next(e for e in t["events"] if e["kind"] == "llm_reply")["summary"] == "Need one more answer."
    replaced = next(e for e in t["events"] if e["kind"] == "question_replaced")
    assert replaced["tone"] == "guardrail" and "Q_MADE_UP" in replaced["summary"]
    assert not t["running"]


def test_turn_in_progress_is_running_and_follows_the_agent():
    session = {"session_id": "S-1", "state": "ASKING"}
    rows = [
        {"id": 1, "ts": "t1", "actor": "system", "event": "session_start", "payload": {"customer_id": "C001"}},
        {"id": 2, "ts": "t2", "actor": "officer", "event": "message", "payload": {"text": "prepare the forms"}},
        {"id": 3, "ts": "t3", "actor": "agent", "event": "tool_call", "payload": {"tool": "plan_actions", "args": {}}},
        {"id": 4, "ts": "t4", "actor": "agent", "event": "tool_result",
         "payload": {"tool": "plan_actions", "result": {"steps": [], "checklist": []}}},
        {"id": 5, "ts": "t5", "actor": "agent", "event": "tool_call",
         "payload": {"tool": "fill_form", "args": {"product_id": "PMJJBY"}}},
    ]
    t = timeline.build(session, rows)
    assert t["running"] and t["stage"] == "act"           # follows the running fill_form, not the ASKING state
    assert t["events"][-1]["tone"] == "running" and t["events"][-1]["summary"] == "Running…"
    rows.append({"id": 6, "ts": "t6", "actor": "agent", "event": "tool_result",
                 "payload": {"tool": "fill_form", "result": {"item_id": "Q-1", "status": "PENDING_APPROVAL"}}})
    rows.append({"id": 7, "ts": "t7", "actor": "agent", "event": "llm_reply", "payload": {"raw": "done"}})
    t = timeline.build(session, rows)
    assert not t["running"] and t["stage"] == "decide"    # back to the session's own state
    assert t["events"][-2]["summary"] == "Q-1 waits for a checker's approval"


def test_llm_failure_shows_the_fallback(client, conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")

    def boom(*a, **k):
        raise llm.LLMError("network down")

    monkeypatch.setattr(llm, "run_tool_loop", boom)
    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    loop.handle_message(conn, sid, "hello")
    t = tl(client, sid)
    err = next(e for e in t["events"] if e["kind"] == "error")
    assert err["title"] == "LLM unavailable" and err["tone"] == "attention" and not t["running"]


def test_full_aadhaar_never_reaches_the_timeline(client):
    full = "".join(["2345", "6789", "0123"])  # built at runtime: no Aadhaar-like literal in the repo
    sid = client.post("/sessions", json={"customer_id": "C004"}).json()["session_id"]
    client.post(f"/sessions/{sid}/message", json={"text": f"my aadhaar is {full}"})
    body = json.dumps(tl(client, sid))
    assert full not in body and "XXXX-XXXX-0123" in body


def test_unknown_session_is_400(client):
    assert client.get("/sessions/S-NOPE/timeline").status_code == 400


def test_maker_checker_refusal_is_a_guardrail_event(client):
    sid = client.post("/sessions", json={"customer_id": "C002"}).json()["session_id"]
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {"PMJJBY": NOMINEE}}).json()["state"]
    item = st["queue_items"][0]["item_id"]
    assert client.post(f"/queue/{item}/approve", json={"officer_id": "officer-1"}).status_code == 403  # the maker
    denied = next(e for e in tl(client, sid)["events"] if e["kind"] == "approval_denied")
    assert denied["tone"] == "guardrail" and denied["stage"] == "act"
    assert denied["title"] == f"Maker-checker blocked Banking Correspondent on {item}"
    assert "only a checker" in denied["summary"]


def test_a_crashed_turn_does_not_stay_running(client, conn, monkeypatch):
    """PR #44 review: a non-LLM exception inside the turn used to leave the timeline "running" forever."""
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")

    def bug(*a, **k):
        raise RuntimeError("unwrapped SDK bug")

    monkeypatch.setattr(llm, "run_tool_loop", bug)
    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    with pytest.raises(RuntimeError):
        loop.handle_message(conn, sid, "hello")
    t = tl(client, sid)
    failed = next(e for e in t["events"] if e["kind"] == "error")
    assert failed["title"] == "Agent turn failed" and "RuntimeError: unwrapped SDK bug" in failed["summary"]
    assert not t["running"] and t["stage"] == "decide"  # back to the session's own state
