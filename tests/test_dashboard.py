"""Branch dashboard (issue #21): the pure helper, the extended /dashboard, /audit filters, read-only, privacy."""
import json

import pytest
from fastapi.testclient import TestClient

from app import dashboard, queue
from app.api import main
from app.data.seed import build_profiles
from app.privacy import find_full_ids
from app.rules import engine

BC, BM = {"officer_id": "officer-1"}, {"officer_id": "manager-1"}
NOMINEE = {"nominee_name": "Savitha", "nominee_relation": "wife"}


@pytest.fixture
def client(conn):
    main.set_connection(conn)
    yield TestClient(main.app)
    main.set_connection(None)


def _session(cid, sid="S-1", state="ASKING"):
    p = next(x for x in build_profiles() if x["customer_id"] == cid)
    return {"session_id": sid, "customer_id": cid, "customer_name": p["name"], "state": state,
            "results": engine.evaluate_all(p), "profile": p}


def _item(pid, status, sid="S-1", cid="C001", created="2026-10-24 09:00:00", decided=None, kind=None):
    return {"item_id": f"Q-{pid}", "session_id": sid, "customer_id": cid, "kind": kind or queue.ENROLLMENT_FORM,
            "product_id": pid if kind != queue.SERVICE_REQUEST else None,
            "action_id": pid if kind == queue.SERVICE_REQUEST else None,
            "status": status, "created_at": created, "decided_at": decided}


# --- pure helper --------------------------------------------------------------------------

def test_funnel_counts_and_value_skip_nulls():
    d = dashboard.build([_session("C001")], [_item("PMJJBY", queue.SUBMITTED), _item("PMFBY", queue.PENDING_APPROVAL)],
                        engine.load_products())
    f = {x["stage"]: x for x in d["funnel"]}
    # Ramesh: PMJJBY, PMSBY, APY eligible + PM-KISAN blocked (PMFBY UNKNOWN but queued, so it counts as eligible)
    assert [x["stage"] for x in d["funnel"]] == ["screened", "eligible", "queued", "approved"]
    assert [f[k]["count"] for k in ("screened", "eligible", "queued", "approved")] == [1, 5, 2, 1]
    assert f["eligible"]["value_inr"] == 200_000 + 200_000 + 12_000 + 6_000  # PMFBY is null: skipped
    assert f["approved"]["value_inr"] == 200_000


def test_by_product_has_all_seven_with_zero_filled_statuses():
    d = dashboard.build([_session("C002")], [], engine.load_products())
    assert len(d["by_product"]) == 7
    for p in d["by_product"].values():
        assert set(p["statuses"]) == set(engine.ALL_STATUSES) and sum(p["statuses"].values()) == 1
    assert d["by_product"]["APY"]["statuses"][engine.NOT_ELIGIBLE] == 1


def test_avg_minutes_to_approval():
    assert dashboard.build([_session("C001")], [_item("PMJJBY", queue.PENDING_APPROVAL)],
                           engine.load_products())["avg_minutes_to_approval"] is None
    items = [_item("PMJJBY", queue.SUBMITTED, created="2026-10-24 09:00:00", decided="2026-10-24T09:04:00+00:00"),
             _item("PMSBY", queue.APPROVED, created="2026-10-24 09:00:00", decided="2026-10-24T09:10:00+00:00"),
             _item("APY", queue.REJECTED, created="2026-10-24 09:00:00", decided="2026-10-24T11:00:00+00:00")]
    assert dashboard.build([_session("C001")], items, engine.load_products())["avg_minutes_to_approval"] == 7.0


def test_labels_cover_every_id():
    d = dashboard.build([_session("C002"), _session("C003", "S-3")], [], engine.load_products())
    assert set(d["not_eligible_reasons"]) <= set(d["rule_labels"])
    assert d["rule_labels"]["R-APY-NOT-ITP"].startswith("Income-tax payers")
    assert set(d["blocked_by_action"]) <= set(d["action_labels"])
    assert set(d["top_missing_docs"]) <= set(d["doc_labels"])
    assert all(r["product_id"] for r in d["customer_results"]) and len(d["customer_results"]) == 14


# --- over HTTP ----------------------------------------------------------------------------

def _ramesh(client):
    sid = client.post("/sessions", json={"customer_id": "C001", **BC}).json()["session_id"]
    client.post(f"/sessions/{sid}/facts", json={"facts": {"grows_notified_crop": True, "in_notified_area": True},
                                               "question_id": "Q_NOTIFIED_CROP"})
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {
        "PMJJBY": NOMINEE, "PMSBY": NOMINEE, "APY": {**NOMINEE, "pension_amount": 1000},
        "PMFBY": {"crop_name": "Ragi", "season": "Kharif 2026"}}}).json()["state"]
    assert client.post(f"/queue/{st['queue_items'][2]['item_id']}/approve", json=BC).status_code == 403
    for i in st["queue_items"]:
        client.post(f"/queue/{i['item_id']}/approve", json=BM)
    return sid


def test_after_ramesh_flow(client):
    sid = _ramesh(client)
    d = client.get("/dashboard").json()
    counts = [f["count"] for f in d["funnel"]]
    assert counts[1:] == sorted(counts[1:], reverse=True)  # eligible >= queued >= approved (pairs)
    forms_approved = sum(1 for i in client.get("/queue").json()
                         if i["kind"] == queue.ENROLLMENT_FORM and i["status"] == queue.SUBMITTED)
    assert d["funnel"][3]["count"] == forms_approved == 4
    assert d["items_approved"] == 6  # A6's count also includes the two service requests
    rows = [r for r in d["customer_results"] if r["customer_id"] == "C001"]
    assert len(rows) == 7 and {r["session_id"] for r in rows} == {sid}
    status = {r["product_id"]: r["status"] for r in rows}
    assert status["PMJJBY"] == engine.ALREADY_ENROLLED and status["PMKISAN_DBT"] == engine.ELIGIBLE
    assert status["SSY"] == engine.NOT_ELIGIBLE
    assert {r["product_id"]: r["queue_status"] for r in rows}["PMJJBY"] == queue.SUBMITTED
    assert d["avg_minutes_to_approval"] is not None and d["avg_minutes_to_approval"] >= 0


def test_empty_dashboard(client):
    d = client.get("/dashboard").json()
    assert d["funnel"][0] == {"stage": "screened", "count": 0} and d["avg_minutes_to_approval"] is None
    assert len(d["by_product"]) == 7 and d["customer_results"] == []


def test_dashboard_and_audit_are_read_only(client, conn):
    _ramesh(client)

    def snapshot():
        return (conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
                [tuple(r) for r in conn.execute("SELECT * FROM queue_items ORDER BY item_id")])

    before = snapshot()
    for path in ("/dashboard", "/audit", "/audit/facets", "/audit?q=approve&actor=officer"):
        assert client.get(path).status_code == 200
    assert snapshot() == before


def test_audit_filters(client):
    _ramesh(client)
    rows = client.get("/audit", params={"actor": "officer", "event": "approval_denied"}).json()
    assert len(rows) == 1 and rows[0]["payload"]["officer_id"] == "officer-1"
    both = client.get("/audit", params=[("event", "approve"), ("event", "approval_denied")]).json()
    assert {r["event"] for r in both} == {"approve", "approval_denied"}
    q = client.get("/audit", params={"q": "MAKER"}).json()  # case-insensitive, matches the payload
    assert q and all("maker" in (r["event"] + json.dumps(r["payload"])).lower() for r in q)
    assert all(r["actor"] == "agent" for r in client.get("/audit", params={"actor": "agent"}).json())


@pytest.mark.parametrize("needle", ["%", "'", "_", "%' OR '1'='1", "!"])
def test_audit_search_is_literal_and_safe(client, needle):
    _ramesh(client)
    r = client.get("/audit", params={"q": needle})
    assert r.status_code == 200
    assert all(needle.lower() in (x["event"] + json.dumps(x["payload"], ensure_ascii=False)).lower()
               for x in r.json())  # no wildcard over-match


def test_audit_facets(client):
    _ramesh(client)
    f = client.get("/audit/facets").json()
    assert f["actors"] == sorted(set(f["actors"])) and {"agent", "officer", "system"} >= set(f["actors"])
    assert "approval_denied" in f["events"] and len(f["events"]) == len(set(f["events"]))
    assert len(f["sessions"]) == 1 and f["total"] == len(client.get("/audit", params={"limit": 10_000}).json())


def test_no_full_aadhaar_in_dashboard_or_audit(client):
    _ramesh(client)
    for path in ("/dashboard", "/audit?limit=10000", "/audit/facets"):
        assert not find_full_ids(client.get(path).json()), path
