"""Branch-wide proactive scan (issue #37). Pure: no LLM, no DB, no queue writes.

It only RECOMMENDS who to call and why. The officer opens a session from the call
list, and the normal approval gate (including maker-checker) applies from there.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from app.planner.documents import plan_actions
from app.rules.engine import ELIGIBLE, ELIGIBLE_BLOCKED, evaluate_all, load_products

PER_OPPORTUNITY = 1_000  # display score only; the ranking is rank_key()
URGENT_ACTIONS = {"AADHAAR_SEEDING_REQUEST": "Aadhaar not seeded", "EKYC_REQUEST": "eKYC pending"}
BLOCKER_NOTES = {
    "AADHAAR_SEEDING_REQUEST": "not Aadhaar-seeded",
    "EKYC_REQUEST": "eKYC pending",
    "OPEN_SAVINGS_ACCOUNT": "no savings account",
}


def _customer(profile: dict, catalog: dict) -> dict:
    results = evaluate_all(profile, catalog)
    plan = plan_actions(results, profile, catalog)
    opportunities = [{"product_id": r["product_id"], "status": r["status"], "value_inr": r.get("value_inr"),
                      "blocked_by": r["blocked_by"]}
                     for r in results if r["status"] in (ELIGIBLE, ELIGIBLE_BLOCKED)]
    blockers = [{"action_id": s["action_id"], "unlocks": s["unlocks"]}
                for s in plan["steps"] if s.get("action_id")]
    pmkisan = next((r for r in results if r["product_id"] == "PMKISAN_DBT"), None)
    urgent_by = [a for a in URGENT_ACTIONS
                 if pmkisan and pmkisan["status"] == ELIGIBLE_BLOCKED and a in pmkisan["blocked_by"]]
    value = sum(o["value_inr"] or 0 for o in opportunities)
    notes = [" + ".join(URGENT_ACTIONS[a] for a in urgent_by) + " → PM-KISAN payments will fail"] if urgent_by else []
    notes += [BLOCKER_NOTES.get(b["action_id"], b["action_id"]) for b in blockers
              if not urgent_by or b["action_id"] not in URGENT_ACTIONS]
    reason = " · ".join([f"Eligible for {', '.join(o['product_id'] for o in opportunities)}", *notes])
    return {
        "customer_id": profile["customer_id"], "name": profile.get("name"), "district": profile.get("district"),
        "occupation": profile.get("occupation"), "segment": profile.get("segment"),
        "opportunities": opportunities, "blockers": blockers, "urgent": bool(urgent_by),
        "score": value + PER_OPPORTUNITY * len(opportunities),
        "value_inr_total": value, "reason": reason,
    }


def rank_key(c: dict) -> tuple:
    """Urgent first (a failing PM-KISAN credit beats any ₹ value), then ₹ value, breadth, id."""
    return (not c["urgent"], -c["value_inr_total"], -len(c["opportunities"]), c["customer_id"])


def scan(profiles: list[dict], catalog: dict | None = None) -> dict:
    """Rank customers with at least one ELIGIBLE / ELIGIBLE_BLOCKED product not yet held."""
    catalog = catalog or load_products()
    rows = [c for c in (_customer(p, catalog) for p in profiles) if c["opportunities"]]
    rows.sort(key=rank_key)
    for i, c in enumerate(rows, 1):
        c["rank"] = i
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "customers_scanned": len(profiles),
        "call_list": rows,
        "summary": {
            "customers_with_opportunities": len(rows),
            "opportunities_by_product": dict(Counter(o["product_id"] for c in rows for o in c["opportunities"])),
            "blockers_by_action": dict(Counter(b["action_id"] for c in rows for b in c["blockers"])),
            "urgent_count": sum(c["urgent"] for c in rows),
            "value_inr_unclaimed": sum(c["value_inr_total"] for c in rows),
        },
    }
