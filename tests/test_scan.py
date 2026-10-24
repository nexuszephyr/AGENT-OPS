"""Branch-wide proactive scan (issue #37): recommends only, ranks deterministically."""
import re
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app import scan
from app.agent.tools import PROFILE_TYPES
from app.api import main
from app.data.seed import BRANCH_CUSTOMERS, build_branch_profiles, build_profiles
from app.privacy import find_full_ids


@pytest.fixture
def client(conn):
    main.set_connection(conn)
    main._latest_scan = None
    yield TestClient(main.app)
    main.set_connection(None)
    main._latest_scan = None


def _row(result, cid):
    return next((c for c in result["call_list"] if c["customer_id"] == cid), None)


def test_personas_ramesh_urgent_and_first():
    out = scan.scan(build_profiles())
    top = out["call_list"][0]
    assert top["customer_id"] == "C001" and top["urgent"]
    assert "PM-KISAN payments will fail" in top["reason"]
    assert out["customers_scanned"] == 6


def test_not_eligible_and_enrolled_products_are_not_opportunities():
    out = scan.scan(build_profiles())
    assert "APY" not in {o["product_id"] for o in _row(out, "C002")["opportunities"]}  # Priya: 45, IT payer
    assert "PMSBY" not in {o["product_id"] for o in _row(out, "C006")["opportunities"]}  # Fathima: enrolled


def test_scan_is_deterministic():
    profiles = build_profiles() + build_branch_profiles()
    assert scan.scan(profiles)["call_list"] == scan.scan(profiles)["call_list"]


def test_ranked_by_score_then_id():
    rows = scan.scan(build_profiles() + build_branch_profiles())["call_list"]
    assert rows == sorted(rows, key=lambda c: (-c["score"], c["customer_id"]))
    assert all(c["opportunities"] for c in rows)
    for c in rows:  # the formula, before #36 adds value_inr
        assert c["score"] == c["value_inr_total"] + 100_000 * c["urgent"] + 1_000 * len(c["opportunities"])


def test_summary_totals_match_call_list():
    out = scan.scan(build_profiles() + build_branch_profiles())
    rows, s = out["call_list"], out["summary"]
    assert s["customers_with_opportunities"] == len(rows)
    assert s["opportunities_by_product"] == dict(Counter(o["product_id"] for c in rows for o in c["opportunities"]))
    assert s["blockers_by_action"] == dict(Counter(b["action_id"] for c in rows for b in c["blockers"]))
    assert s["urgent_count"] == sum(c["urgent"] for c in rows) > 0
    assert s["value_inr_unclaimed"] == sum(c["value_inr_total"] for c in rows)


def test_branch_customers_complete_and_masked():
    profiles = build_branch_profiles()
    assert [p["customer_id"] for p in profiles] == [f"C{n}" for n in range(101, 131)] == \
           [c["customer_id"] for c in BRANCH_CUSTOMERS]
    for p in profiles:
        assert not [k for k, v in p.items() if v is None], p["customer_id"]
        assert set(PROFILE_TYPES) <= set(p), p["customer_id"]  # every rule field has a known value
        assert not find_full_ids(p), p["customer_id"]
        assert re.fullmatch(r"XXXX-XXXX-\d{4}", p["aadhaar_masked"])
        assert re.fullmatch(r"XXXXXX\d{4}", p["mobile_masked"])
        assert p["has_savings_account"] == bool(re.fullmatch(r"XXXXXX\d{4}", p.get("account_no_masked") or ""))
    farmers = sum(p["occupation"] == "farmer" for p in profiles)
    assert 10 <= farmers <= 14 and 2 <= sum(not p["has_savings_account"] for p in profiles) <= 3


def _snapshot(conn):
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()  # noqa: S608 - fixed table names
            for t in ("customers", "sessions", "queue_items")}


def test_scan_only_recommends(client, conn):
    client.post("/sessions", json={"customer_id": "C001", "officer_id": "officer-1"})
    before = [tuple(r) for rows in _snapshot(conn).values() for r in rows]
    out = client.post("/scan").json()
    assert out["call_list"] and out["customers_scanned"] == 6 + len(BRANCH_CUSTOMERS)
    assert [tuple(r) for rows in _snapshot(conn).values() for r in rows] == before
    runs = conn.execute("SELECT actor, session_id, payload_json FROM audit_log WHERE event = 'scan_run'").fetchall()
    assert len(runs) == 1 and runs[0]["actor"] == "agent" and runs[0]["session_id"] is None
    assert "C001" not in runs[0]["payload_json"]  # summary only, no per-customer data


def test_api_latest_scan(client):
    assert client.get("/scan/latest").status_code == 404
    out = client.post("/scan").json()
    latest = client.get("/scan/latest")
    assert latest.status_code == 200 and latest.json() == out


def test_api_customers_segment(client):
    demo = client.get("/customers", params={"segment": "demo"}).json()
    assert [c["customer_id"] for c in demo] == [f"C00{n}" for n in range(1, 7)]
    assert {c["segment"] for c in demo} == {"demo"}
    assert len(client.get("/customers", params={"segment": "branch"}).json()) == len(BRANCH_CUSTOMERS)
    assert len(client.get("/customers").json()) == 6 + len(BRANCH_CUSTOMERS)
    assert client.get("/customers", params={"segment": "vip"}).status_code == 422
