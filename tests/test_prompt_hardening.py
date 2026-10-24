"""Fixes from the real-LLM run (issue #12), each with a scripted fake LLM (no API key needed)."""
import json

import pytest

from app import audit, config, queue, sessions
from app.agent import llm, loop
from app.agent.prompts import SYSTEM_PROMPT, context_block
from app.agent.tools import ToolBox
from app.privacy import find_full_ids

NOMINEE = {"nominee_name": "Anjeli", "nominee_relation": "daughter"}


@pytest.fixture(autouse=True)
def _llm_on(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")


def scripted(monkeypatch, *replies):
    """Each model call takes the next script(execute, history, tools) -> text. Returns the call log."""
    calls = []

    def run(system, history, tools, execute, max_calls=8):
        calls.append({"history": history, "tools": tools})
        return replies[len(calls) - 1](execute, history, tools)

    monkeypatch.setattr(llm, "run_tool_loop", run)
    return calls


def reply(**kw):
    return json.dumps({"to_officer": "ok", **kw})


def _ramesh_ready(conn):
    sid = loop.start_session(conn, "C001", "officer-1")["session_id"]
    loop.set_facts(conn, sid, {"grows_notified_crop": True, "in_notified_area": True}, "Q_NOTIFIED_CROP")
    return sid


def _events(conn, sid, event):
    return [a["payload"] for a in audit.fetch(conn, session_id=sid) if a["event"] == event]


# F1 · fewer round-trips --------------------------------------------------------------------------

def test_plan_steps_list_missing_mandatory(conn):
    sid = _ramesh_ready(conn)
    plan = ToolBox(conn, sid).call("plan_actions", {})
    steps = {s.get("product_id") or s["action_id"]: s for s in plan["steps"]}
    assert steps["PMJJBY"]["missing_mandatory"] == ["nominee_name", "nominee_relation"]
    assert steps["APY"]["missing_mandatory"] == ["nominee_name", "nominee_relation", "pension_amount"]
    assert steps["PMFBY"]["missing_mandatory"] == ["crop_name", "season"]
    assert "missing_mandatory" not in steps["AADHAAR_SEEDING_REQUEST"]
    assert sessions.state_view(conn, sid)["plan"] == plan  # the officer's panel shows the same plan


def test_turn_never_fills_a_form_with_missing_fields(conn, monkeypatch):
    sid = _ramesh_ready(conn)

    def model(execute, history, tools):
        for st in execute("plan_actions", {})["steps"]:
            if st["type"] == "SERVICE_REQUEST":
                execute("raise_request", {"action_id": st["action_id"]})
            elif not st.get("missing_mandatory") and "after" not in st:
                execute("fill_form", {"product_id": st["product_id"]})
        return reply(needs_from_officer=["nominee_name", "nominee_relation", "pension_amount", "crop_name",
                                         "season"], next_step="awaiting_approval")

    scripted(monkeypatch, model)
    out = loop.handle_message(conn, sid, "Please prepare everything.")
    results = [p["result"] for p in _events(conn, sid, "tool_result") if p["tool"] == "fill_form"]
    assert all(r.get("status") != "NEEDS_INFO" for r in results)
    assert {i["action_id"] for i in out["state"]["queue_items"]} == {"AADHAAR_SEEDING_REQUEST", "EKYC_REQUEST"}
    assert out["reply"]["needs_from_officer"][0] == "nominee_name"
    assert "don't call fill_form: ask the officer for all of them at once" in SYSTEM_PROMPT


def test_context_block_has_compact_masked_profile(conn):
    sid = loop.start_session(conn, "C001", "officer-1")["session_id"]
    ctx = context_block(sessions.state_view(conn, sid))
    snap = json.loads(ctx.split("\n", 1)[1])
    prof = snap["profile"]
    assert prof["first_name"] == "Ramesh" and "Gowda" not in ctx
    assert prof["aadhaar_masked"] == "XXXX-XXXX-3391" and prof["aadhaar_seeded"] is False
    assert "grows_notified_crop" not in prof  # unknown fields are left out
    assert not find_full_ids(snap) and len(ctx) < 1500


# F2 · invented question ids ----------------------------------------------------------------------

def test_invented_question_id_dropped_but_needs_from_officer_kept(conn, monkeypatch):
    sid = _ramesh_ready(conn)  # nothing left to ask: the ranker has no question
    scripted(monkeypatch, lambda e, h, t: reply(question={"question_id": "NOMINEE", "text": "Nominee?"},
                                                needs_from_officer=["nominee_name", "nominee_relation"]))
    out = loop.handle_message(conn, sid, "prepare the forms")
    assert out["reply"]["question"] is None
    assert out["reply"]["needs_from_officer"] == ["nominee_name", "nominee_relation"]
    assert _events(conn, sid, "question_replaced")[0]["model_question"]["question_id"] == "NOMINEE"
    assert sessions.get(conn, sid)["questions_asked"] == 1  # only the crop question spent budget


# F3 · a rejection also fixes the pending siblings ------------------------------------------------

def test_rejection_fix_reaches_pending_sibling(conn, monkeypatch):
    sid = loop.start_session(conn, "C002", "officer-1")["session_id"]
    tb = ToolBox(conn, sid)
    pmjjby = tb.call("fill_form", {"product_id": "PMJJBY", "extra_fields": NOMINEE})["item_id"]
    pmsby = tb.call("fill_form", {"product_id": "PMSBY", "extra_fields": NOMINEE})["item_id"]
    queue.reject(conn, pmjjby, "manager-1", "Nominee name is Anjali, not Anjeli.")
    fixed = {**NOMINEE, "nominee_name": "Anjali"}

    def model(execute, history, tools):
        execute("revise_item", {"item_id": pmjjby, "changes": {"nominee_name": "Anjali"}, "explanation": "spelling"})
        execute("fill_form", {"product_id": "PMSBY", "extra_fields": fixed})
        return reply(next_step="awaiting_approval")

    calls = scripted(monkeypatch, model)
    loop.handle_rejection(conn, pmjjby)
    prompt = calls[0]["history"][-1]["content"]
    assert pmsby in prompt and "Anjeli" in prompt and "fill_form" in prompt  # the sibling is shown
    a, b = queue.get(conn, pmjjby), queue.get(conn, pmsby)
    assert a["payload"]["nominee_name"] == b["payload"]["nominee_name"] == "Anjali"
    assert a["status"] == b["status"] == queue.PENDING_APPROVAL
    assert a["revision_count"] == 1 and b["revision_count"] == 0
    # a pending-draft refill doesn't touch maker-checker: still prepared by the maker, decided by the checker
    assert b["prepared_by"] == "officer-1"
    with pytest.raises(queue.MakerCheckerError):
        queue.approve(conn, pmsby, "officer-1")
    assert queue.approve(conn, pmsby, "manager-1")["status"] == queue.SUBMITTED


# F4 · reply statuses always equal the engine's final ones ----------------------------------------

def test_highlights_show_final_status_after_not_sure(conn, monkeypatch):
    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    scripted(monkeypatch,
             lambda e, h, t: reply(question={"question_id": "Q_ITP", "text": "Did you pay income tax?"}),
             lambda e, h, t: reply(question_unanswered=True,
                                   highlights=[{"product_id": "APY", "status": "UNKNOWN"},
                                               {"product_id": "MADE_UP", "status": "ELIGIBLE"}]))
    loop.handle_message(conn, sid, "hello")
    out = loop.handle_message(conn, sid, "Not sure, my brother handles that.")
    assert out["reply"]["highlights"] == [{"product_id": "APY", "status": "VERIFY_MANUALLY", "because": []}]
    stored = json.loads(sessions.get(conn, sid)["messages"][-1]["content"])
    assert stored["highlights"][0]["status"] == "VERIFY_MANUALLY"


# F5 · one retry on invalid AgentReply JSON -------------------------------------------------------

def test_invalid_json_retried_once_without_tools(conn, monkeypatch):
    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    calls = scripted(monkeypatch, lambda e, h, t: "Sure! APY looks fine.",
                     lambda e, h, t: reply(to_officer="APY is UNKNOWN."))
    out = loop.handle_message(conn, sid, "hello")
    assert out["reply"]["to_officer"] == "APY is UNKNOWN." and out["reply"]["mode"] == "agent"
    assert len(calls) == 2 and calls[1]["tools"] == []
    assert calls[1]["history"][-2] == {"role": "assistant", "content": "Sure! APY looks fine."}
    assert "Return only valid AgentReply JSON" in calls[1]["history"][-1]["content"]
    assert len(_events(conn, sid, "reply_retry")) == 1


@pytest.mark.parametrize("second", ["still not json", llm.LLMError("timeout")])
def test_failed_retry_falls_back_to_raw_text(conn, monkeypatch, second):
    def again(e, h, t):
        if isinstance(second, Exception):
            raise second
        return second

    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    calls = scripted(monkeypatch, lambda e, h, t: "first raw text", again)
    out = loop.handle_message(conn, sid, "hello")
    assert out["reply"]["to_officer"] == "first raw text" and out["reply"]["mode"] == "agent"
    assert len(calls) == 2  # exactly one retry


def test_valid_json_is_not_retried(conn, monkeypatch):
    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    calls = scripted(monkeypatch, lambda e, h, t: reply())
    loop.handle_message(conn, sid, "hello")
    assert len(calls) == 1 and not _events(conn, sid, "reply_retry")


# Optional · the PMFBY notified-list confirmation is officer-only ---------------------------------

def test_agent_cannot_confirm_notified_crop(conn):
    sid = loop.start_session(conn, "C001", "officer-1")["session_id"]
    out = ToolBox(conn, sid).call("update_profile", {
        "facts": {"grows_notified_crop": True, "in_notified_area": True}, "evidence": "Officer here: confirmed"})
    assert {r["field"]: r["reason"] for r in out["rejected_fields"]} == {
        "grows_notified_crop": "officer must confirm via the fact form",
        "in_notified_area": "officer must confirm via the fact form"}
    assert out["profile"]["grows_notified_crop"] is None
    st = loop.set_facts(conn, sid, {"grows_notified_crop": True, "in_notified_area": True}, "Q_NOTIFIED_CROP")
    assert {r["product_id"]: r["status"] for r in st["state"]["results"]}["PMFBY"] == "ELIGIBLE"
