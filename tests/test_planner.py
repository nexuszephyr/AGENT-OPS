import copy

from app.data.seed import build_profiles
from app.planner.documents import plan_actions
from app.planner.questions import pick_next_question
from app.rules.engine import evaluate_all

P = {p["customer_id"]: p for p in build_profiles()}


def plan(cid, **changes):
    prof = copy.deepcopy(P[cid])
    prof.update(changes)
    return plan_actions(evaluate_all(prof), prof)


# --- questions ------------------------------------------------------------------

def test_arjun_asks_itp_first():
    q = pick_next_question(evaluate_all(P["C004"]))["question"]
    assert q["question_id"] == "Q_ITP"
    assert q["resolves"] == ["APY"]


def test_ramesh_one_question_resolves_pmfby():
    q = pick_next_question(evaluate_all(P["C001"]))["question"]
    assert q["question_id"] == "Q_NOTIFIED_CROP"
    assert q["resolves"] == ["PMFBY"]


def test_no_question_when_nothing_unknown():
    out = pick_next_question(evaluate_all(P["C002"]))
    assert out["question"] is None and out["reason"] == "no resolvable unknowns"


def test_question_limit():
    out = pick_next_question(evaluate_all(P["C004"]), questions_asked=5)
    assert out == {"question": None, "reason": "question limit reached"}


def test_already_asked_question_is_skipped():
    out = pick_next_question(evaluate_all(P["C004"]), asked=["Q_ITP"])
    assert out["question"] is None


# --- document planner -------------------------------------------------------------

def test_ramesh_plan_starts_with_seeding_then_ekyc():
    steps = plan("C001")["steps"]
    assert [s.get("action_id") for s in steps[:2]] == ["AADHAAR_SEEDING_REQUEST", "EKYC_REQUEST"]
    assert steps[0]["unlocks"] == ["PMKISAN_DBT"]
    # blocked PM-KISAN enrollment comes after its actions
    pmk = next(s for s in steps if s.get("product_id") == "PMKISAN_DBT")
    assert pmk["order"] > 2 and pmk["after"] == ["AADHAAR_SEEDING_REQUEST", "EKYC_REQUEST"]


def test_ramesh_checklist_after_crop_confirmed():
    out = plan("C001", grows_notified_crop=True, in_notified_area=True)
    docs = {c["doc_id"]: c["used_by"] for c in out["checklist"]}
    assert set(docs["RTC"]) == {"PMKISAN_DBT", "PMFBY"}
    assert "SOWING_DECL" in docs and "NOMINEE" in docs and "AUTODEBIT_CONSENT" in docs
    assert "AADHAAR" not in docs  # on file for account holders


def test_lakshmamma_opens_account_first():
    steps = plan("C003")["steps"]
    first = steps[0]
    assert first["product_id"] == "PMJDY" and first["action_id"] == "OPEN_SAVINGS_ACCOUNT"
    assert "PMSBY" in first["unlocks"]
    assert sum(1 for s in steps if s.get("product_id") == "PMJDY") == 1  # not duplicated
    assert "AADHAAR" in first["docs_missing"]  # no account → nothing on file


def test_plan_orders_are_sequential():
    steps = plan("C006")["steps"]
    assert [s["order"] for s in steps] == list(range(1, len(steps) + 1))
