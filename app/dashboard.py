"""Branch dashboard numbers (GET /dashboard, docs/TOOLS.md). Pure: rows in, dict out; no DB, no writes.

`sessions` holds one entry per screened customer (their latest session):
    {"session_id", "customer_id", "customer_name", "state", "results", "profile"}
`items` is every queue item. ₹ values are indicative cover / annual benefit (#36): products with
`value_inr: null` are skipped, never counted as ₹0.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from app import queue
from app.planner.documents import docs_on_file, plan_actions
from app.rules import engine

ELIGIBLE_SET = (engine.ELIGIBLE, engine.ELIGIBLE_BLOCKED)
DONE_SET = (queue.APPROVED, queue.SUBMITTED)


def _ts(value: str) -> datetime:
    """SQLite CURRENT_TIMESTAMP is naive UTC; queue decisions are ISO with an offset."""
    t = datetime.fromisoformat(value)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _rule_texts(node: dict, out: dict[str, str]) -> None:
    if "id" in node and "text" in node:
        out[node["id"]] = node["text"]
    for child in node.get("all", node.get("any", [])) or []:
        _rule_texts(child, out)
    if "not" in node:
        _rule_texts(node["not"], out)


def avg_minutes_to_approval(items: list[dict]) -> float | None:
    """Mean minutes from queueing to the checker's decision, over approved/submitted items; None if none."""
    spans = [(_ts(i["decided_at"]) - _ts(i["created_at"])).total_seconds() / 60
             for i in items if i["status"] in DONE_SET and i.get("decided_at") and i.get("created_at")]
    return round(sum(spans) / len(spans), 1) if spans else None


def build(sessions: list[dict], items: list[dict], catalog: dict) -> dict:
    values = {p["id"]: p.get("value_inr") for p in catalog["products"]}
    rule_text: dict[str, str] = {}
    for p in catalog["products"]:
        _rule_texts(p["rules"], rule_text)

    # existing keys (unchanged semantics: counters over latest sessions, item counts over all items)
    unlocked = {(i["customer_id"], i["product_id"]) for i in items if i["kind"] == queue.ENROLLMENT_FORM
                and i["status"] in DONE_SET}  # each product once per customer
    eligible = value_eligible = 0
    blocked: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    docs: Counter[str] = Counter()
    states: Counter[str] = Counter()

    # new keys
    latest_ids = {s["session_id"] for s in sessions}
    forms = {(i["session_id"], i["product_id"]): i for i in items
             if i["kind"] == queue.ENROLLMENT_FORM and i["session_id"] in latest_ids}
    by_product = {p["id"]: {"name": p["name"], "statuses": dict.fromkeys(engine.ALL_STATUSES, 0)}
                  for p in catalog["products"]}
    rows: list[dict] = []
    funnel_eligible: list[str] = []
    funnel_queued: list[str] = []
    funnel_approved: list[str] = []

    for s in sessions:
        res = s["results"]
        states[s["state"]] += 1
        eligible += sum(r["status"] in ELIGIBLE_SET for r in res)
        value_eligible += sum(r.get("value_inr") or 0 for r in res
                              if r["status"] in ELIGIBLE_SET and (s["customer_id"], r["product_id"]) not in unlocked)
        on_file = docs_on_file(s["profile"])
        for r in res:
            pid = r["product_id"]
            blocked.update(r["blocked_by"])
            if r["status"] == engine.NOT_ELIGIBLE:
                reasons.update(f["id"] for f in r["failed_rules"])
            if pid in by_product:
                by_product[pid]["statuses"][r["status"]] = by_product[pid]["statuses"].get(r["status"], 0) + 1
            form = forms.get((s["session_id"], pid))
            # funnel counts customer-product pairs. "Eligible" includes pairs this session enrolled: an approved
            # form makes the product ALREADY_ENROLLED, and the funnel must not shrink because it succeeded.
            if r["status"] in ELIGIBLE_SET or form:
                funnel_eligible.append(pid)
            if form:
                funnel_queued.append(pid)
                if form["status"] in DONE_SET:
                    funnel_approved.append(pid)
            rows.append({
                "customer_id": s["customer_id"], "customer_name": s["customer_name"], "session_id": s["session_id"],
                "product_id": pid, "status": r["status"],
                "failed_rules": [f["id"] for f in r["failed_rules"]], "blocked_by": list(r["blocked_by"]),
                "missing_docs": ([d for d in r["required_docs"] if d not in on_file]
                                 if r["status"] in ELIGIBLE_SET else []),
                "queue_status": form["status"] if form else None, "value_inr": r.get("value_inr"),
            })
        for d in plan_actions(res, s["profile"], catalog)["checklist"]:
            docs[d["doc_id"]] += 1

    def worth(pids: list[str]) -> int:
        return sum(values.get(p) or 0 for p in pids)

    by_status = Counter(i["status"] for i in items)
    return {
        "customers_screened": len(sessions),
        "products_eligible": eligible,
        "items_pending": by_status[queue.PENDING_APPROVAL],
        "items_approved": by_status[queue.APPROVED] + by_status[queue.SUBMITTED],
        "items_rejected": by_status[queue.REJECTED],
        "items_escalated": by_status[queue.NEEDS_OFFICER],
        "value_inr_unlocked": sum(values.get(pid) or 0 for _, pid in unlocked),
        "value_inr_eligible": value_eligible,
        "items_by_status": dict(by_status),
        "sessions_by_state": dict(states),
        "blocked_by_action": dict(blocked),
        "not_eligible_reasons": dict(reasons.most_common()),
        "top_missing_docs": dict(docs.most_common()),
        # --- issue #21 ---
        "avg_minutes_to_approval": avg_minutes_to_approval([i for i in items if i["session_id"] in latest_ids]),
        "funnel": [
            {"stage": "screened", "count": len(sessions)},
            {"stage": "eligible", "count": len(funnel_eligible), "value_inr": worth(funnel_eligible)},
            {"stage": "queued", "count": len(funnel_queued)},
            {"stage": "approved", "count": len(funnel_approved), "value_inr": worth(funnel_approved)},
        ],
        "by_product": by_product,
        "customer_results": rows,
        "rule_labels": {rid: rule_text.get(rid, rid)
                        for rid in {*reasons, *(f for r in rows for f in r["failed_rules"])}},
        "action_labels": {a: catalog["actions"].get(a, {}).get("label", a)
                          for a in {*blocked, *(b for r in rows for b in r["blocked_by"])}},
        "doc_labels": {d: catalog["documents"].get(d, d)
                       for d in {*docs, *(m for r in rows for m in r["missing_docs"])}},
    }
