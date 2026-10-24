"""₹ value unlocked (issue #36): indicative product values, dashboard sums, formatting."""
import copy
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.data.seed import build_profiles
from app.rules import engine

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))

import components as ui  # noqa: E402

CHECKER = {"officer_id": "manager-1"}
NOMINEE = {"nominee_name": "Anjali", "nominee_relation": "daughter"}


@pytest.fixture
def client(conn):
    main.set_connection(conn)
    yield TestClient(main.app)
    main.set_connection(None)


def _catalog_with(**changes):
    cat = copy.deepcopy(engine.load_products())
    pmjjby = next(p for p in cat["products"] if p["id"] == "PMJJBY")
    for k, v in changes.items():
        if v is KeyError:
            pmjjby.pop(k)
        else:
            pmjjby[k] = v
    return cat


# --- catalogue -------------------------------------------------------------------------

@pytest.mark.parametrize("changes", [{"value_inr": -1}, {"value_inr": 1.5}, {"value_inr": True},
                                     {"value_inr": "200000"}, {"value_inr": KeyError},
                                     {"value_basis": KeyError}, {"value_basis": "  "}])
def test_bad_values_fail_validation(changes):
    with pytest.raises(engine.RulesValidationError, match="value_"):
        engine.validate_catalog(_catalog_with(**changes))


def test_null_value_and_zero_are_valid():
    engine.validate_catalog(_catalog_with(value_inr=None))
    engine.validate_catalog(_catalog_with(value_inr=0))


def test_every_product_has_a_value_and_basis_and_results_carry_them():
    for p in engine.load_products()["products"]:
        assert "value_inr" in p and p["value_basis"]
    r = engine.evaluate_all(build_profiles()[0])
    assert {x["product_id"]: x["value_inr"] for x in r}["PMJJBY"] == 200_000
    assert all("value_basis" in x for x in r)


def test_products_json_matches_rules_spec():
    spec = (ROOT / "docs" / "RULES_SPEC.md").read_text(encoding="utf-8")
    block = re.search(r"## 6\..*?```json\n(.*?)```", spec, re.S).group(1)
    assert json.loads(block) == json.loads((ROOT / "app/rules/products.json").read_text(encoding="utf-8"))


# --- dashboard sums --------------------------------------------------------------------

def _eligible_value(cid):
    p = next(x for x in build_profiles() if x["customer_id"] == cid)
    return sum(r["value_inr"] or 0 for r in engine.evaluate_all(p)
               if r["status"] in (engine.ELIGIBLE, engine.ELIGIBLE_BLOCKED))


def test_persona_eligible_values():
    # Ramesh: PMJJBY + PMSBY + APY + PM-KISAN (PMFBY still UNKNOWN; nulls skipped)
    assert _eligible_value("C001") == 200_000 + 200_000 + 12_000 + 6_000
    # Priya: APY is NOT_ELIGIBLE and SSY has no single value, so only the two covers count
    assert _eligible_value("C002") == 400_000
    # Fathima: PMSBY is ALREADY_ENROLLED, so it is not counted
    assert _eligible_value("C006") == 200_000 + 12_000


def test_dashboard_eligible_sums_latest_sessions(client):
    assert client.get("/dashboard").json()["value_inr_eligible"] == 0
    for cid in ("C001", "C002", "C006"):
        client.post("/sessions", json={"customer_id": cid, "officer_id": "officer-1"})
    d = client.get("/dashboard").json()
    assert d["value_inr_eligible"] == 418_000 + 400_000 + 212_000 and d["value_inr_unlocked"] == 0


def test_value_unlocked_only_by_a_checker_approving_an_enrollment(client):
    sid = client.post("/sessions", json={"customer_id": "C001", "officer_id": "officer-1"}).json()["session_id"]
    client.post(f"/sessions/{sid}/facts", json={"facts": {"grows_notified_crop": True, "in_notified_area": True},
                                               "question_id": "Q_NOTIFIED_CROP"})
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {
        "PMJJBY": NOMINEE, "PMSBY": NOMINEE, "APY": {**NOMINEE, "pension_amount": 1000},
        "PMFBY": {"crop_name": "Ragi", "season": "Kharif 2026"}}}).json()["state"]
    items = {i["product_id"] or i["action_id"]: i["item_id"] for i in st["queue_items"]}

    def dash():
        return client.get("/dashboard").json()

    assert dash()["value_inr_unlocked"] == 0  # queued is not unlocked
    eligible_before = dash()["value_inr_eligible"]
    client.post(f"/queue/{items['AADHAAR_SEEDING_REQUEST']}/approve", json=CHECKER)
    assert dash()["value_inr_unlocked"] == 0  # a service request carries no value
    assert client.post(f"/queue/{items['PMJJBY']}/approve", json={"officer_id": "officer-1"}).status_code == 403
    assert dash()["value_inr_unlocked"] == 0  # the maker's refused approval changes nothing
    client.post(f"/queue/{items['PMJJBY']}/approve", json=CHECKER)
    d = dash()
    assert d["value_inr_unlocked"] == 200_000
    assert d["value_inr_eligible"] == eligible_before - 200_000  # counted once: unlocked, not also eligible
    client.post(f"/queue/{items['PMFBY']}/approve", json=CHECKER)
    assert dash()["value_inr_unlocked"] == 200_000  # PMFBY has no single value: skipped, not ₹0-counted


# --- formatting ------------------------------------------------------------------------

@pytest.mark.parametrize("n, full, compact", [
    (0, "₹0", "₹0"), (999, "₹999", "₹999"), (1000, "₹1,000", "₹1,000"),
    (100_000, "₹1,00,000", "₹1 lakh"), (1_840_000, "₹18,40,000", "₹18.4 lakh"),
    (12_000_000, "₹1,20,00,000", "₹1.2 crore"), (412_000, "₹4,12,000", "₹4.1 lakh"),
    (9_996_000, "₹99,96,000", "₹1 crore"),  # rounds into the next unit, not "₹100 lakh"
])
def test_format_inr(n, full, compact):
    assert ui.format_inr(n) == full and ui.format_inr(n, compact=True) == compact


@pytest.mark.parametrize("status, shown", [
    ("ELIGIBLE", True), ("ELIGIBLE_BLOCKED", True), ("ALREADY_ENROLLED", True),
    ("NOT_ELIGIBLE", False), ("UNKNOWN", False), ("VERIFY_MANUALLY", False),
])
def test_value_line_only_where_the_value_applies(status, shown):
    """Priya's APY (NOT_ELIGIBLE) must not read as "she gets ₹12,000" (review on #47)."""
    apy = {"status": status, "value_inr": 12_000, "value_basis": "minimum guaranteed pension per year"}
    line = ui.value_line(apy)
    assert (line == "₹12,000 minimum guaranteed pension per year (indicative)") if shown else line is None
    assert ui.value_line({**apy, "value_inr": None}) is None  # no single value: nothing, never ₹0
    assert set(engine.ALL_STATUSES) >= ui.VALUE_STATUSES
