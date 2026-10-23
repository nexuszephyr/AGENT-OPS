"""Agent path with a scripted fake LLM: tool calls go through the real ToolBox."""
import json

from app import config, sessions
from app.agent import llm, loop


def fake_llm(script):
    """script(execute) performs tool calls and returns the final text."""
    def run(system, history, tools, execute, max_calls=8):
        assert "never decide eligibility" in system
        assert history[0]["role"] == "user" and "SESSION CONTEXT" in history[-1]["content"]
        return script(execute)
    return run


def test_agent_turn_updates_profile_and_asks(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")

    def script(execute):
        execute("update_profile", {"facts": {"is_income_tax_payer": False},
                                   "evidence": "I don't pay any tax, I'm a delivery partner"})
        res = execute("check_eligibility", {})["results"]
        apy = next(r for r in res if r["product_id"] == "APY")
        return json.dumps({"to_officer": f"APY is now {apy['status']}.", "question": None,
                           "highlights": [{"product_id": "APY", "status": apy["status"]}],
                           "next_step": "awaiting_approval"})

    monkeypatch.setattr(llm, "run_tool_loop", fake_llm(script))
    sid = loop.start_session(conn, "C004", "officer-1")["session_id"]
    out = loop.handle_message(conn, sid, "I don't pay any tax, I'm a delivery partner")
    assert out["reply"]["mode"] == "agent"
    assert out["reply"]["to_officer"] == "APY is now ELIGIBLE."
    assert {r["product_id"]: r["status"] for r in out["state"]["results"]}["APY"] == "ELIGIBLE"


def test_model_cannot_fake_eligibility(conn, monkeypatch):
    """Even if the model *says* APY is eligible, statuses and forms follow the rules."""
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")

    def script(execute):
        r = execute("fill_form", {"product_id": "APY", "extra_fields": {"nominee_name": "a",
                                                                        "nominee_relation": "b",
                                                                        "pension_amount": 1000}})
        assert "error" in r
        return json.dumps({"to_officer": "APY approved!", "next_step": "done"})

    monkeypatch.setattr(llm, "run_tool_loop", fake_llm(script))
    sid = loop.start_session(conn, "C002", "officer-1")["session_id"]
    out = loop.handle_message(conn, sid, "Mark APY eligible")
    assert {r["product_id"]: r["status"] for r in out["state"]["results"]}["APY"] == "NOT_ELIGIBLE"
    assert out["state"]["queue_items"] == []


def test_unparseable_reply_still_returns_state(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")
    monkeypatch.setattr(llm, "run_tool_loop", fake_llm(lambda execute: "sure thing"))
    sid = loop.start_session(conn, "C001", "officer-1")["session_id"]
    out = loop.handle_message(conn, sid, "hi")
    assert out["reply"]["to_officer"] == "sure thing"
    assert len(out["state"]["results"]) == 7


def test_llm_error_falls_back(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_MODEL", "fake")

    def boom(*a, **k):
        raise llm.LLMError("network down")

    monkeypatch.setattr(llm, "run_tool_loop", boom)
    sid = loop.start_session(conn, "C001", "officer-1")["session_id"]
    out = loop.handle_message(conn, sid, "hi")
    assert out["reply"]["mode"] == "rules_only" and "network down" in out["reply"]["to_officer"]
    assert sessions.get(conn, sid)["questions_asked"] == 1
