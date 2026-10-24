"""Maker-checker approval (issue #39): the preparer can't approve their own item."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import audit, db, queue, sessions
from app.agent.tools import revise_and_render
from app.api import main

MAKER, CHECKER = "officer-1", "manager-1"
PAYLOAD = {"nominee_name": "Anjeli", "nominee_relation": "daughter"}


def _item(conn, officer_id=MAKER):
    s = sessions.create(conn, "C002", officer_id)
    return queue.create(conn, session_id=s["session_id"], customer_id="C002",
                        kind=queue.ENROLLMENT_FORM, product_id="PMJJBY", payload=PAYLOAD)


def _denials(conn, item_id):
    return [a["payload"] for a in audit.fetch(conn) if a["event"] == "approval_denied"
            and a["payload"]["item_id"] == item_id]


@pytest.fixture
def client(conn):
    main.set_connection(conn)
    yield TestClient(main.app)
    main.set_connection(None)


def test_item_records_its_preparer(conn):
    assert _item(conn)["prepared_by"] == MAKER


def test_existing_db_gets_prepared_by_column(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE queue_items (item_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
                "customer_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL)")
    old.close()
    c = db.connect(path)
    assert "prepared_by" in {r[1] for r in c.execute("PRAGMA table_info(queue_items)")}
    c.close()


def test_maker_cannot_approve_own_item(conn):
    item = _item(conn)
    with pytest.raises(queue.MakerCheckerError):
        queue.approve(conn, item["item_id"], MAKER)
    after = queue.get(conn, item["item_id"])
    assert after["status"] == queue.PENDING_APPROVAL and after["decided_by"] is None
    assert _denials(conn, item["item_id"])[0]["officer_id"] == MAKER


def test_maker_cannot_reject_either(conn):
    item = _item(conn)
    with pytest.raises(queue.MakerCheckerError):
        queue.reject(conn, item["item_id"], MAKER, "Nominee is Anjali")
    assert queue.get(conn, item["item_id"])["status"] == queue.PENDING_APPROVAL


def test_checker_approves_makers_item(conn):
    out = queue.approve(conn, _item(conn)["item_id"], CHECKER)
    assert out["status"] == queue.SUBMITTED and out["decided_by"] == CHECKER


def test_checker_cannot_approve_what_they_prepared(conn):
    item = _item(conn, officer_id=CHECKER)  # the checker started this session
    assert item["prepared_by"] == CHECKER
    with pytest.raises(queue.MakerCheckerError, match="preparer"):
        queue.approve(conn, item["item_id"], CHECKER)
    assert queue.get(conn, item["item_id"])["status"] == queue.PENDING_APPROVAL
    assert _denials(conn, item["item_id"])


def test_officer_revision_makes_the_reviser_the_maker(conn):
    item = _item(conn)
    queue.reject(conn, item["item_id"], CHECKER, "Nominee is Anjali")
    out = revise_and_render(conn, item["item_id"], {"nominee_name": "Anjali"}, "fixed", "officer", CHECKER)
    assert out["prepared_by"] == CHECKER and out["status"] == queue.PENDING_APPROVAL
    with pytest.raises(queue.MakerCheckerError):
        queue.approve(conn, item["item_id"], CHECKER)


def test_agent_revision_keeps_the_session_officer(conn):
    item = _item(conn)
    queue.reject(conn, item["item_id"], CHECKER, "Nominee is Anjali")
    assert queue.revise(conn, item["item_id"], {"nominee_name": "Anjali"}, "fixed")["prepared_by"] == MAKER


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_unknown_officer_refused(conn, action):
    item = _item(conn)
    with pytest.raises(queue.MakerCheckerError, match="unknown officer"):
        if action == "approve":
            queue.approve(conn, item["item_id"], "bc-9")
        else:
            queue.reject(conn, item["item_id"], "bc-9", "x")
    assert queue.get(conn, item["item_id"])["status"] == queue.PENDING_APPROVAL


def test_unknown_officer_cannot_start_session_or_revise(conn):
    with pytest.raises(sessions.SessionError, match="unknown officer"):
        sessions.create(conn, "C002", "bc-9")
    item = _item(conn)
    queue.reject(conn, item["item_id"], CHECKER, "x")
    with pytest.raises(queue.QueueError, match="unknown officer"):
        queue.revise(conn, item["item_id"], {"nominee_name": "Anjali"}, "x", actor="officer", officer_id="bc-9")
    assert queue.get(conn, item["item_id"])["status"] == queue.REJECTED


def test_api_approve_requires_officer_id(client, conn):
    item = _item(conn)
    assert client.post(f"/queue/{item['item_id']}/approve", json={}).status_code == 422
    assert client.post(f"/queue/{item['item_id']}/reject", json={"reason": "x"}).status_code == 422


def test_api_maker_approve_is_403_then_checker_approves(client, conn):
    item = _item(conn)
    r = client.post(f"/queue/{item['item_id']}/approve", json={"officer_id": MAKER})
    assert r.status_code == 403 and "maker" in r.json()["detail"]
    r = client.post(f"/queue/{item['item_id']}/approve", json={"officer_id": CHECKER})
    assert r.status_code == 200 and r.json()["item"]["status"] == queue.SUBMITTED


def test_api_unknown_officer(client, conn):
    assert client.post("/sessions", json={"customer_id": "C002", "officer_id": "bc-9"}).status_code == 400
    item = _item(conn)
    assert client.post(f"/queue/{item['item_id']}/approve", json={"officer_id": "bc-9"}).status_code == 403


def test_api_lists_officers(client):
    assert client.get("/officers").json() == [
        {"officer_id": MAKER, "name": "Banking Correspondent", "role": "maker"},
        {"officer_id": CHECKER, "name": "Branch Manager", "role": "checker"},
    ]
