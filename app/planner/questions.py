"""Next-question ranker (docs/TOOLS.md › pick_next_question). Pure function, no LLM.

The LLM may rephrase the chosen question in the customer's language, but which
question to ask is decided here.
"""
from __future__ import annotations

from app.rules.engine import UNKNOWN

# Catalogue order is the tie-breaker. text_kn is optional: when it is None the
# LLM writes the Kannada version (docs/PROMPTS.md §5).
CATALOGUE: list[dict] = [
    {"question_id": "Q_ITP", "fields": ["is_income_tax_payer"],
     "text_en": "Did you pay income tax last year?"},
    {"question_id": "Q_NOTIFIED_CROP", "fields": ["grows_notified_crop", "in_notified_area"],
     "text_en": "Which crop are you growing this season, and in which village?",
     "officer_note": "Officer confirms the crop and village against the PMFBY notified list."},
    {"question_id": "Q_LAND", "fields": ["is_landholder"],
     "text_en": "Do you own farmland in your name?"},
    {"question_id": "Q_GIRLS", "fields": ["girl_children_under_10"],
     "text_en": "Do you have a daughter younger than 10?"},
    {"question_id": "Q_SSY_EXISTING", "fields": ["existing_ssy_accounts"],
     "text_en": "Does your family already have a Sukanya Samriddhi account? How many?"},
    {"question_id": "Q_PENSION", "fields": ["monthly_pension"],
     "text_en": "Do you receive a pension? About how much per month?"},
    {"question_id": "Q_GOVT", "fields": ["is_govt_employee"],
     "text_en": "Do you or did you work for the government?"},
    {"question_id": "Q_PROF", "fields": ["is_registered_professional"],
     "text_en": "Are you a registered doctor, engineer, lawyer, CA or architect?"},
    {"question_id": "Q_SEEDED", "fields": ["aadhaar_seeded"],
     "text_en": "Is your Aadhaar linked to this bank account?",
     "officer_note": "Officer can check this in the core-banking system."},
    {"question_id": "Q_EKYC", "fields": ["ekyc_done"],
     "text_en": "Have you completed Aadhaar eKYC for this account?",
     "officer_note": "Officer can check this in the core-banking system."},
    {"question_id": "Q_HAS_ACCOUNT", "fields": ["has_savings_account"],
     "text_en": "Do you already have a savings account with any bank?"},
    {"question_id": "Q_AGE", "fields": ["age"], "text_en": "How old are you?"},
    {"question_id": "Q_OCCUPATION", "fields": ["occupation"], "text_en": "What work do you do?"},
]

BY_ID = {q["question_id"]: q for q in CATALOGUE}


def pick_next_question(
    results: list[dict],
    asked: list[str] | None = None,
    questions_asked: int = 0,
    limit: int = 5,
) -> dict:
    """Return {"question": {...}} or {"question": None, "reason": ...}."""
    asked_set = set(asked or [])
    if questions_asked >= limit:
        return {"question": None, "reason": "question limit reached"}

    unknown = [r for r in results if r["status"] == UNKNOWN]
    if not unknown:
        return {"question": None, "reason": "no resolvable unknowns"}

    best, best_key = None, None
    for idx, q in enumerate(CATALOGUE):
        if q["question_id"] in asked_set:
            continue
        qfields = set(q["fields"])
        resolves = [r["product_id"] for r in unknown if set(r["unknown_fields"]) <= qfields]
        # partial coverage is the fallback when no single question resolves a product
        covered = sum(len(set(r["unknown_fields"]) & qfields) for r in unknown)
        if not resolves and not covered:
            continue
        key = (len(resolves), covered, -idx)
        if best_key is None or key > best_key:
            best, best_key = {**q, "resolves": resolves}, key

    if best is None:
        return {"question": None, "reason": "no resolvable unknowns"}
    return {"question": best}
