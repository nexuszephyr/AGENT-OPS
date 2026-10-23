import pytest

from app import queue, sessions
from app.config import MAX_REVISIONS


@pytest.fixture
def item(conn):
    s = sessions.create(conn, "C002", "officer-1")
    return queue.create(conn, session_id=s["session_id"], customer_id="C002",
                        kind=queue.ENROLLMENT_FORM, product_id="PMJJBY",
                        payload={"nominee_name": "Anjeli", "nominee_relation": "daughter"})


def test_new_item_is_pending(item):
    assert item["status"] == queue.PENDING_APPROVAL


def test_approve_then_mock_submit(conn, item):
    out = queue.approve(conn, item["item_id"], "officer-1")
    assert out["status"] == queue.SUBMITTED and out["decided_by"] == "officer-1"


def test_cannot_approve_twice(conn, item):
    queue.approve(conn, item["item_id"], "officer-1")
    with pytest.raises(queue.QueueError):
        queue.approve(conn, item["item_id"], "officer-1")


def test_reject_needs_reason(conn, item):
    with pytest.raises(queue.QueueError):
        queue.reject(conn, item["item_id"], "officer-1", "  ")


def test_reject_revise_requeue(conn, item):
    queue.reject(conn, item["item_id"], "officer-1", "Nominee is Anjali")
    out = queue.revise(conn, item["item_id"], {"nominee_name": "Anjali"}, "fixed spelling")
    assert out["status"] == queue.PENDING_APPROVAL
    assert out["revision_count"] == 1
    assert out["payload"]["nominee_name"] == "Anjali"


def test_revise_cannot_add_fields(conn, item):
    queue.reject(conn, item["item_id"], "officer-1", "x")
    with pytest.raises(queue.QueueError):
        queue.revise(conn, item["item_id"], {"approved": True}, "sneaky")


def test_cannot_revise_pending_item(conn, item):
    with pytest.raises(queue.QueueError):
        queue.revise(conn, item["item_id"], {"nominee_name": "x"}, "x")


def test_escalates_after_max_revisions(conn, item):
    iid = item["item_id"]
    for i in range(MAX_REVISIONS):
        queue.reject(conn, iid, "officer-1", f"still wrong {i}")
        queue.revise(conn, iid, {"nominee_name": f"try{i}"}, "retry")
    out = queue.reject(conn, iid, "officer-1", "still wrong")
    assert out["status"] == queue.NEEDS_OFFICER
    with pytest.raises(queue.QueueError):
        queue.revise(conn, iid, {"nominee_name": "again"}, "x")


def test_audit_records_officer_actions(conn, item):
    queue.approve(conn, item["item_id"], "officer-1")
    events = [r["event"] for r in conn.execute("SELECT event FROM audit_log").fetchall()]
    assert "approve" in events and "queue_create" in events
