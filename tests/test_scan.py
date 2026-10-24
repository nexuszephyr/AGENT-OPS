"""Branch-wide proactive scan (issue #37): recommends only, ranks deterministically."""
import json
import re
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app import db, scan, sessions
from app.agent.tools import PROFILE_TYPES
from app.api import main
from app.data import seed
from app.data.seed import BRANCH_CUSTOMERS, build_branch_profiles, build_profiles
from app.privacy import find_full_ids
from app.rules import engine


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
    assert top["customer_id"] == "C001" and top["urgent"] and top["rank"] == 1
    # both prerequisites are missing for Ramesh, so the reason names both
    assert top["reason"].endswith("Aadhaar not seeded + eKYC pending → PM-KISAN payments will fail")
    assert out["customers_scanned"] == 6


def test_not_eligible_and_enrolled_products_are_not_opportunities():
    out = scan.scan(build_profiles())
    assert "APY" not in {o["product_id"] for o in _row(out, "C002")["opportunities"]}  # Priya: 45, IT payer
    assert "PMSBY" not in {o["product_id"] for o in _row(out, "C006")["opportunities"]}  # Fathima: enrolled


def test_scan_is_deterministic():
    profiles = build_profiles() + build_branch_profiles()
    assert scan.scan(profiles)["call_list"] == scan.scan(profiles)["call_list"]


def test_ranked_urgent_first_then_value_breadth_id():
    rows = scan.scan(build_profiles() + build_branch_profiles())["call_list"]
    key = [(not c["urgent"], -c["value_inr_total"], -len(c["opportunities"]), c["customer_id"]) for c in rows]
    assert key == sorted(key) and [c["rank"] for c in rows] == list(range(1, len(rows) + 1))
    assert all(c["opportunities"] for c in rows)
    for c in rows:  # display score only, not used for ordering
        assert c["score"] == c["value_inr_total"] + 1_000 * len(c["opportunities"])


def test_single_blocker_reason_keeps_its_wording():
    rows = {c["customer_id"]: c for c in scan.scan(build_branch_profiles())["call_list"]}
    one = [c for c in rows.values() if c["urgent"] and len([b for b in c["blockers"]
                                                          if b["action_id"] in scan.URGENT_ACTIONS]) == 1]
    assert one and all("+" not in c["reason"] and "→ PM-KISAN payments will fail" in c["reason"] for c in one)


def test_urgency_beats_more_and_pricier_opportunities(monkeypatch):
    """Once #36 adds ₹ values (₹2 lakh per insurance), urgency must still win."""
    def priced(profile, catalog=None):
        return [{**r, "value_inr": 200_000} for r in engine.evaluate_all(profile, catalog)]

    monkeypatch.setattr(scan, "evaluate_all", priced)
    ramesh = next(p for p in build_profiles() if p["customer_id"] == "C001")  # urgent, 4 opportunities
    rich = {**seed._DEFAULTS, "customer_id": "C999", "name": "Test Farmer", "age": 30, "gender": "F",
            "occupation": "farmer", "is_landholder": True, "land_acres": 2.0, "grows_notified_crop": True,
            "in_notified_area": True, "girl_children_under_10": 1}  # not urgent, 6 opportunities
    rows = scan.scan([rich, ramesh])["call_list"]
    by_id = {c["customer_id"]: c for c in rows}
    assert not by_id["C999"]["urgent"] and by_id["C001"]["urgent"]
    assert by_id["C999"]["value_inr_total"] > by_id["C001"]["value_inr_total"]
    assert len(by_id["C999"]["opportunities"]) > len(by_id["C001"]["opportunities"])
    assert [c["customer_id"] for c in rows] == ["C001", "C999"]


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


def test_scan_records_who_asked(client, conn):
    assert client.post("/scan", json={"officer_id": "bc-9"}).status_code == 400
    assert client.post("/scan", json={"officer_id": "officer-1"}).status_code == 200
    row = conn.execute("SELECT payload_json FROM audit_log WHERE event = 'scan_run'").fetchone()
    assert '"officer_id": "officer-1"' in row["payload_json"]


def test_db_seeded_before_segment_still_splits_demo_and_branch(tmp_path):
    c = db.connect(str(tmp_path / "old.db"))
    for p in build_profiles() + build_branch_profiles():  # rows without the `segment` key
        c.execute("INSERT INTO customers (customer_id, profile_json) VALUES (?, ?)",
                  (p["customer_id"], json.dumps(p)))
    assert [r["customer_id"] for r in sessions.list_customers(c, "demo")] == [f"C00{n}" for n in range(1, 7)]
    assert len(sessions.list_customers(c, "branch")) == len(BRANCH_CUSTOMERS)
    c.close()


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


def test_urgent_still_first_with_real_values():
    """With #36's real ₹ values a non-urgent customer worth more still ranks below an urgent one."""
    ramesh = next(p for p in build_profiles() if p["customer_id"] == "C001")
    urgent = {**ramesh, "customer_id": "C998", "age": 55}  # unseeded; too old for PMJJBY / APY
    rich = {**seed._DEFAULTS, "customer_id": "C997", "name": "Test Farmer", "age": 30, "gender": "F",
            "occupation": "farmer", "is_landholder": True, "land_acres": 2.0, "grows_notified_crop": True,
            "in_notified_area": True}  # seeded, eKYC done: not urgent
    rows = {c["customer_id"]: c for c in scan.scan([rich, urgent])["call_list"]}
    assert rows["C998"]["urgent"] and not rows["C997"]["urgent"]
    assert rows["C997"]["value_inr_total"] > rows["C998"]["value_inr_total"] > 0
    assert rows["C998"]["rank"] == 1 and rows["C997"]["rank"] == 2


def test_branch_scan_totals_are_filled_in():
    out = scan.scan(build_profiles() + build_branch_profiles())
    assert out["summary"]["value_inr_unclaimed"] > 0
    assert out["call_list"][0]["urgent"]
