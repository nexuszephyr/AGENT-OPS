"""Rules engine tests: RULES_SPEC.md §7 plus the persona outcomes in DATA_MODEL.md §3."""
import copy
import json

import pytest

from app.data.seed import build_profiles
from app.rules import engine
from app.rules.engine import (
    ALREADY_ENROLLED,
    ELIGIBLE,
    ELIGIBLE_BLOCKED,
    NOT_ELIGIBLE,
    UNKNOWN,
    VERIFY_MANUALLY,
)

PROFILES = {p["customer_id"]: p for p in build_profiles()}


def result(profile, pid):
    return next(r for r in engine.evaluate_all(profile) if r["product_id"] == pid)


def statuses(profile):
    return {r["product_id"]: r["status"] for r in engine.evaluate_all(profile)}


def with_(base_id, **changes):
    p = copy.deepcopy(PROFILES[base_id])
    p.update(changes)
    return p


# --- RULES_SPEC.md §7 ---------------------------------------------------------

def test_priya_apy_rejected_two_reasons():
    r = result(PROFILES["C002"], "APY")
    assert r["status"] == NOT_ELIGIBLE
    assert {f["id"] for f in r["failed_rules"]} == {"R-APY-AGE-MAX", "R-APY-NOT-ITP"}


def test_priya_pmjjby_eligible():
    assert result(PROFILES["C002"], "PMJJBY")["status"] == ELIGIBLE


def test_ramesh_pmkisan_blocked_on_seeding():
    r = result(PROFILES["C001"], "PMKISAN_DBT")
    assert r["status"] == ELIGIBLE_BLOCKED
    assert "AADHAAR_SEEDING_REQUEST" in r["blocked_by"]
    assert "EKYC_REQUEST" in r["blocked_by"]


def test_missing_it_status_is_unknown():
    r = result(PROFILES["C004"], "APY")
    assert r["status"] == UNKNOWN
    assert r["unknown_fields"] == ["is_income_tax_payer"]


def test_unknown_does_not_mask_failure():
    p = with_("C004", age=55)
    r = result(p, "APY")
    assert r["status"] == NOT_ELIGIBLE  # F wins over U
    assert [f["id"] for f in r["failed_rules"]] == ["R-APY-AGE-MAX"]


def test_no_account_blocks_insurance():
    p = with_("C003", age=30)
    assert result(p, "PMJDY")["status"] == ELIGIBLE
    r = result(p, "PMJJBY")
    assert r["status"] == ELIGIBLE_BLOCKED
    assert r["blocked_by"] == ["OPEN_SAVINGS_ACCOUNT"]


def test_already_enrolled():
    assert result(PROFILES["C006"], "PMSBY")["status"] == ALREADY_ENROLLED


def test_pensioner_excluded_from_pmkisan():
    r = result(PROFILES["C005"], "PMKISAN_DBT")
    assert r["status"] == NOT_ELIGIBLE
    assert {"R-PMK-PENSION", "R-PMK-NOT-GOVT"} <= {f["id"] for f in r["failed_rules"]}


def test_ssy_limit():
    assert result(with_("C006", existing_ssy_accounts=2), "SSY")["status"] == NOT_ELIGIBLE


def test_validation_rejects_missing_source(tmp_path):
    cat = copy.deepcopy(engine.load_products())
    del cat["products"][0]["source_url"]
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(cat))
    with pytest.raises(engine.RulesValidationError):
        engine.load_products(f)


# --- extra validation + semantics -------------------------------------------------

@pytest.mark.parametrize("mutate", [
    lambda c: c["products"][1]["rules"]["all"].append(dict(c["products"][1]["rules"]["all"][0])),  # dup id
    lambda c: c["products"][1]["rules"]["all"][0].update(op="~="),                                # bad op
    lambda c: c["products"][1]["rules"]["all"][0].pop("text"),                                    # no text
    lambda c: c["products"][1]["required_docs"].append("NOPE"),                                   # unknown doc
    lambda c: c["products"][1].update(last_verified=""),                                          # no date
])
def test_validation_rejects_bad_catalog(tmp_path, mutate):
    cat = copy.deepcopy(engine.load_products())
    mutate(cat)
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(cat))
    with pytest.raises(engine.RulesValidationError):
        engine.load_products(f)


def test_finalize_unknowns():
    res = engine.finalize_unknowns(engine.evaluate_all(PROFILES["C004"]))
    assert engine.status_of(res, "APY") == VERIFY_MANUALLY


def test_every_result_cites_source():
    for r in engine.evaluate_all(PROFILES["C001"]):
        assert r["source_url"].startswith("https://")


# --- persona outcomes (DATA_MODEL.md §3) ------------------------------------------

def test_persona_ramesh():
    s = statuses(PROFILES["C001"])
    assert s["PMKISAN_DBT"] == ELIGIBLE_BLOCKED
    assert s["PMFBY"] == UNKNOWN
    assert s["PMJJBY"] == s["PMSBY"] == s["APY"] == ELIGIBLE
    assert s["SSY"] == s["PMJDY"] == NOT_ELIGIBLE
    # after the officer confirms the notified crop
    s2 = statuses(with_("C001", grows_notified_crop=True, in_notified_area=True))
    assert s2["PMFBY"] == ELIGIBLE


def test_persona_priya():
    s = statuses(PROFILES["C002"])
    assert s["PMJJBY"] == s["PMSBY"] == s["SSY"] == ELIGIBLE
    assert s["APY"] == s["PMJDY"] == NOT_ELIGIBLE


def test_persona_lakshmamma():
    s = statuses(PROFILES["C003"])
    assert s["PMJDY"] == ELIGIBLE
    assert s["PMSBY"] == ELIGIBLE_BLOCKED
    assert s["PMJJBY"] == s["APY"] == NOT_ELIGIBLE


def test_persona_arjun_resolves_after_answer():
    assert statuses(with_("C004", is_income_tax_payer=False))["APY"] == ELIGIBLE


def test_persona_suresh():
    s = statuses(PROFILES["C005"])
    assert s["PMKISAN_DBT"] == NOT_ELIGIBLE
    assert s["PMSBY"] == ELIGIBLE


def test_persona_fathima():
    s = statuses(PROFILES["C006"])
    assert s["SSY"] == ELIGIBLE
    assert s["PMSBY"] == ALREADY_ENROLLED
    assert s["PMJJBY"] == s["APY"] == ELIGIBLE


def test_only_deliberate_unknowns():
    """A stray null would change demo outcomes (DATA_MODEL.md §3)."""
    unknown = {
        cid: sorted(r["product_id"] for r in engine.evaluate_all(p) if r["status"] == UNKNOWN)
        for cid, p in PROFILES.items()
    }
    assert unknown == {"C001": ["PMFBY"], "C002": [], "C003": [], "C004": ["APY"], "C005": [], "C006": []}
