"""Item history + revision diff (issue #19): what changed since the officer's rejection."""
import pytest
from fastapi.testclient import TestClient

from app.agent.tools import ToolBox
from app.api import main

TYPO = {"nominee_name": "Anjeli", "nominee_relation": "daughter"}
MAKER, CHECKER = {"officer_id": "officer-1"}, {"officer_id": "manager-1"}


@pytest.fixture
def client(conn):
    main.set_connection(conn)
    yield TestClient(main.app)
    main.set_connection(None)


def priya_pmjjby(client):
    sid = client.post("/sessions", json={"customer_id": "C002", **MAKER}).json()["session_id"]
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {"PMJJBY": TYPO}}).json()["state"]
    return sid, next(i for i in st["queue_items"] if i["product_id"] == "PMJJBY")["item_id"]


def history(client, item):
    r = client.get(f"/queue/{item}/history")
    assert r.status_code == 200
    return r.json()


def test_priya_reject_revise_approve_shows_the_diff(client):
    _, item = priya_pmjjby(client)
    h = history(client, item)
    assert h["rejection"] is None and h["diff"] == [] and [e["event"] for e in h["events"]] == ["queue_create"]

    client.post(f"/queue/{item}/reject", json={**CHECKER, "reason": "Nominee name is Anjali, not Anjeli"})
    client.post(f"/queue/{item}/revise", json={**MAKER, "changes": {"nominee_name": "Anjali"}})
    h = history(client, item)
    assert h["status"] == "PENDING_APPROVAL" and h["revision_count"] == 1
    assert h["rejection"]["reason"] == "Nominee name is Anjali, not Anjeli"
    assert h["rejection"]["officer_id"] == "manager-1"
    assert h["diff"] == [{"field": "nominee_name", "before": "Anjeli", "after": "Anjali"}]
    revise = next(e for e in h["events"] if e["event"] == "revise")
    assert revise["before"] == {"nominee_name": "Anjeli"} and revise["changes"] == {"nominee_name": "Anjali"}

    client.post(f"/queue/{item}/approve", json=CHECKER)
    h = history(client, item)
    assert h["status"] == "SUBMITTED"
    assert h["diff"] == [{"field": "nominee_name", "before": "Anjeli", "after": "Anjali"}]  # still explains it
    assert [e["event"] for e in h["events"]] == ["queue_create", "reject", "revise", "approve", "submit_mock"]


def test_two_revisions_show_rejected_value_to_latest(client):
    _, item = priya_pmjjby(client)
    client.post(f"/queue/{item}/reject", json={**CHECKER, "reason": "nominee"})
    client.post(f"/queue/{item}/revise", json={**MAKER, "changes": {"nominee_name": "Anjal"}})
    client.post(f"/queue/{item}/reject", json={**CHECKER, "reason": "still wrong"})
    client.post(f"/queue/{item}/revise", json={**MAKER, "changes": {"nominee_name": "Anjali",
                                                                      "nominee_relation": "daughter"}})
    h = history(client, item)
    assert h["rejection"]["reason"] == "still wrong"                                 # the latest rejection
    assert h["diff"] == [{"field": "nominee_name", "before": "Anjal", "after": "Anjali"}]  # unchanged field left out


def test_agent_revision_records_before_values_too(client, conn):
    sid, item = priya_pmjjby(client)
    client.post(f"/queue/{item}/reject", json={**CHECKER, "reason": "nominee typo"})
    ToolBox(conn, sid).call("revise_item", {"item_id": item, "changes": {"nominee_name": "Anjali"},
                                            "explanation": "fixed the typo"})
    h = history(client, item)
    assert h["diff"] == [{"field": "nominee_name", "before": "Anjeli", "after": "Anjali"}]
    assert next(e for e in h["events"] if e["event"] == "revise")["actor"] == "agent"


def test_maker_checker_refusal_is_in_the_history(client):
    _, item = priya_pmjjby(client)
    assert client.post(f"/queue/{item}/approve", json=MAKER).status_code == 403
    assert "approval_denied" in [e["event"] for e in history(client, item)["events"]]


def test_history_of_other_items_is_not_mixed_in(client):
    sid = client.post("/sessions", json={"customer_id": "C002", **MAKER}).json()["session_id"]
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {"PMJJBY": TYPO, "PMSBY": TYPO}}).json()["state"]
    a, b = (next(i["item_id"] for i in st["queue_items"] if i["product_id"] == p) for p in ("PMJJBY", "PMSBY"))
    client.post(f"/queue/{a}/reject", json={**CHECKER, "reason": "only this one"})
    assert history(client, b)["rejection"] is None
    assert all(e["item_id"] == b for e in history(client, b)["events"])


def test_unknown_item_is_400(client):
    assert client.get("/queue/Q-NOPE/history").status_code == 400


def test_a_long_session_keeps_the_items_first_events(client, conn):
    """PR #45 review: history used to read the newest 5000 audit rows, so a long session lost queue_create."""
    from app import audit

    sid, item = priya_pmjjby(client)
    for i in range(5100):  # e.g. a long agent session: many tool calls after the item was created
        audit.log(conn, sid, "agent", "tool_call", {"tool": "check_eligibility", "args": {"i": i}})
    client.post(f"/queue/{item}/reject", json={**CHECKER, "reason": "nominee"})
    h = history(client, item)
    assert [e["event"] for e in h["events"]] == ["queue_create", "reject"]


def test_a_refilled_draft_after_the_rejection_is_in_the_diff(client, conn):
    """Merge with #46: fill_form refilling a pending draft (queue_replace) after a rejection changes what the
    checker approves, so it must show in "what changed since the rejection"."""
    sid, item = priya_pmjjby(client)
    client.post(f"/queue/{item}/reject", json={**CHECKER, "reason": "Nominee name is Anjali, not Anjeli"})
    client.post(f"/queue/{item}/revise", json={**MAKER, "changes": {"nominee_name": "Anjali"}})
    out = ToolBox(conn, sid).call("fill_form", {"product_id": "PMJJBY", "extra_fields": {
        "nominee_name": "Anjali", "nominee_relation": "sister"}})          # the agent refills the pending draft
    assert out["item_id"] == item
    h = history(client, item)
    assert "queue_replace" in [e["event"] for e in h["events"]]
    assert {d["field"]: (d["before"], d["after"]) for d in h["diff"]} == {
        "nominee_name": ("Anjeli", "Anjali"), "nominee_relation": ("daughter", "sister")}
