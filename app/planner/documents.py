"""Prerequisite + document dependency planner (docs/TOOLS.md › plan_actions). Pure function."""
from __future__ import annotations

from app.rules.engine import ELIGIBLE, ELIGIBLE_BLOCKED, load_products

# Fixed order used to break ties between prerequisite actions.
ACTION_ORDER = ["OPEN_SAVINGS_ACCOUNT", "AADHAAR_SEEDING_REQUEST", "EKYC_REQUEST"]

# A bank already holds these for account holders (KYC done at account opening).
DOCS_ON_FILE_WITH_ACCOUNT = {"AADHAAR", "PHOTO", "PASSBOOK", "MOBILE"}


def docs_on_file(profile: dict) -> set[str]:
    return set(DOCS_ON_FILE_WITH_ACCOUNT) if profile.get("has_savings_account") else set()


def plan_actions(results: list[dict], profile: dict, catalog: dict | None = None,
                 mandatory_extra: dict[str, list[str]] | None = None) -> dict:
    """`mandatory_extra` (product -> form fields, from app/forms/fill.py via the caller) lets every
    ENROLLMENT step list `missing_mandatory`: the fields fill_form will still need as extra_fields."""
    catalog = catalog or load_products()
    mandatory_extra = mandatory_extra or {}

    def missing_mandatory(pid: str) -> list[str]:
        # Full list by design: these are form fields, never profile fields, so the plan can't know what the
        # officer said in chat. The agent calls fill_form once the conversation gives all of them.
        return list(mandatory_extra.get(pid, []))

    doc_labels = catalog["documents"]
    actions = catalog["actions"]
    order_idx = {p["id"]: i for i, p in enumerate(catalog["products"])}
    on_file = docs_on_file(profile)

    candidates = [r for r in results if r["status"] in (ELIGIBLE, ELIGIBLE_BLOCKED)]

    # 1) prerequisite actions, scored by how many products each unlocks
    unlocks: dict[str, list[str]] = {}
    for r in candidates:
        for a in r["blocked_by"]:
            unlocks.setdefault(a, []).append(r["product_id"])

    def a_rank(a: str) -> tuple:
        return (-len(unlocks[a]), ACTION_ORDER.index(a) if a in ACTION_ORDER else 99)

    def missing_docs(r: dict) -> list[str]:
        return [d for d in r["required_docs"] if d not in on_file]

    steps: list[dict] = []
    handled: set[str] = set()
    for a in sorted(unlocks, key=a_rank):
        via = actions.get(a, {}).get("product")
        if via:
            # Opening the account IS the PMJDY enrollment: one step, not two.
            via_res = next((r for r in candidates if r["product_id"] == via), None)
            steps.append({
                "type": "ENROLLMENT", "product_id": via, "action_id": a,
                "unlocks": unlocks[a],
                "docs_missing": missing_docs(via_res) if via_res else [],
                "missing_mandatory": missing_mandatory(via),
                "why": f"{actions[a]['label']}: unlocks {', '.join(unlocks[a])}",
            })
            handled.add(via)
        else:
            steps.append({
                "type": "SERVICE_REQUEST", "action_id": a, "unlocks": unlocks[a],
                "why": f"{actions.get(a, {}).get('label', a)}: unlocks {', '.join(unlocks[a])}",
            })

    # 2) ready enrollments (fewest missing docs first), 3) then blocked ones
    ready = [r for r in candidates if r["status"] == ELIGIBLE and r["product_id"] not in handled]
    ready.sort(key=lambda r: (len(missing_docs(r)), order_idx[r["product_id"]]))
    blocked = [r for r in candidates if r["status"] == ELIGIBLE_BLOCKED and r["product_id"] not in handled]
    blocked.sort(key=lambda r: order_idx[r["product_id"]])

    for r in ready + blocked:
        step = {"type": "ENROLLMENT", "product_id": r["product_id"], "docs_missing": missing_docs(r),
                "missing_mandatory": missing_mandatory(r["product_id"])}
        if r["status"] == ELIGIBLE_BLOCKED:
            step["after"] = r["blocked_by"]
        steps.append(step)

    for i, s in enumerate(steps, 1):
        s["order"] = i

    # checklist: deduplicated docs the customer must bring
    checklist: dict[str, dict] = {}
    for r in candidates:
        for d in missing_docs(r):
            item = checklist.setdefault(d, {"doc_id": d, "label": doc_labels.get(d, d), "used_by": []})
            item["used_by"].append(r["product_id"])

    return {"steps": steps, "checklist": list(checklist.values())}
