"""Guardrails enforced inside the tools (docs/RESPONSIBLE_AI.md)."""
import pytest

from app import queue, sessions
from app.agent.tools import ToolBox


def toolbox(conn, cid):
    s = sessions.create(conn, cid, "officer-1")
    return ToolBox(conn, s["session_id"])


def test_fill_form_refuses_not_eligible(conn):
    tb = toolbox(conn, "C002")
    out = tb.call("fill_form", {"product_id": "APY"})
    assert "error" in out and "NOT_ELIGIBLE" in out["error"]
    assert queue.list_items(conn) == []


def test_fill_form_refuses_blocked(conn):
    tb = toolbox(conn, "C001")
    assert "error" in tb.call("fill_form", {"product_id": "PMKISAN_DBT"})


def test_fill_form_asks_for_missing_mandatory(conn):
    tb = toolbox(conn, "C002")
    out = tb.call("fill_form", {"product_id": "PMJJBY"})
    assert out["status"] == "NEEDS_INFO"
    assert set(out["missing_mandatory"]) == {"nominee_name", "nominee_relation"}
    out = tb.call("fill_form", {"product_id": "PMJJBY",
                                "extra_fields": {"nominee_name": "Anjali", "nominee_relation": "daughter"}})
    assert out["status"] == queue.PENDING_APPROVAL and out["pdf_path"].endswith(".pdf")


def test_fill_form_rejects_full_aadhaar(conn):
    tb = toolbox(conn, "C002")
    out = tb.call("fill_form", {"product_id": "PMJJBY",
                                "extra_fields": {"nominee_name": "1234 5678 9012", "nominee_relation": "x"}})
    assert "error" in out and "Aadhaar" in out["error"]


def test_raise_request_only_when_needed(conn):
    assert "error" in toolbox(conn, "C002").call("raise_request", {"action_id": "AADHAAR_SEEDING_REQUEST"})
    out = toolbox(conn, "C001").call("raise_request", {"action_id": "AADHAAR_SEEDING_REQUEST"})
    assert out["status"] == queue.PENDING_APPROVAL


def test_update_profile_never_overrides_bank_record(conn):
    tb = toolbox(conn, "C002")
    out = tb.call("update_profile", {"facts": {"is_income_tax_payer": False}, "evidence": "I don't pay tax"})
    assert out["rejected_fields"][0]["reason"] == "conflicts with bank record"
    assert out["profile"]["is_income_tax_payer"] is True


def test_update_profile_validates(conn):
    tb = toolbox(conn, "C004")
    assert "error" in tb.call("update_profile", {"facts": {"is_income_tax_payer": False}, "evidence": ""})
    out = tb.call("update_profile", {"facts": {"is_income_tax_payer": "no", "eligible": True, "age": True},
                                     "evidence": "x"})
    reasons = {r["field"]: r["reason"] for r in out["rejected_fields"]}
    assert reasons == {"is_income_tax_payer": "bad type", "eligible": "unknown field", "age": "bad type"}


@pytest.mark.parametrize("name", ["approve_item", "submit_item", "set_status"])
def test_no_approval_tools_exist(conn, name):
    assert "error" in toolbox(conn, "C001").call(name, {"item_id": "x"})


def test_every_tool_call_is_audited(conn):
    tb = toolbox(conn, "C001")
    tb.call("check_eligibility", {})
    events = [r["event"] for r in conn.execute("SELECT event FROM audit_log").fetchall()]
    assert events.count("tool_call") == 1 and events.count("tool_result") == 1
