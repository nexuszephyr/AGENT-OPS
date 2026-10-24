"""LLM-callable tools (docs/TOOLS.md §1).

Guardrails live HERE, not in the prompt: fill_form re-runs the rules engine,
raise_request only accepts actions some product is actually blocked by,
update_profile never overwrites the bank record. Errors are returned as
{"error": ...} so the model sees them and can recover.

There is deliberately no approve/submit tool (docs/TOOLS.md §2).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from app import audit, queue, sessions
from app.forms import fill
from app.planner.documents import plan_actions
from app.rules import engine

# Field types the interview may set (docs/DATA_MODEL.md §1).
PROFILE_TYPES: dict[str, type | tuple] = {
    "age": int,
    "gender": str,
    "district": str,
    "preferred_language": str,
    "has_savings_account": bool,
    "aadhaar_seeded": bool,
    "ekyc_done": bool,
    "occupation": str,
    "is_income_tax_payer": bool,
    "is_govt_employee": bool,
    "is_registered_professional": bool,
    "monthly_pension": int,
    "is_landholder": bool,
    "land_acres": (int, float),
    "grows_notified_crop": bool,
    "in_notified_area": bool,
    "girl_children_under_10": int,
    "existing_ssy_accounts": int,
}
OCCUPATIONS = {"farmer", "salaried", "self_employed", "gig_worker", "homemaker", "student",
               "pensioner", "govt_employee"}


def _coerce(value: Any, typ: type | tuple) -> tuple[Any, bool]:
    """Strict type check. bool is never accepted as a number and vice versa."""
    if typ is bool:
        return value, isinstance(value, bool)
    if isinstance(value, bool):
        return value, False
    if typ is int:
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return value, isinstance(value, int)
    return value, isinstance(value, typ)


class ToolBox:
    def __init__(self, conn: sqlite3.Connection, session_id: str, actor: str = "agent"):
        self.conn = conn
        self.session_id = session_id
        self.actor = actor
        self._tools: dict[str, Callable[..., dict]] = {
            "fetch_customer": self.fetch_customer,
            "update_profile": self.update_profile,
            "check_eligibility": self.check_eligibility,
            "pick_next_question": self.pick_next_question,
            "plan_actions": self.plan_actions,
            "fill_form": self.fill_form,
            "raise_request": self.raise_request,
            "revise_item": self.revise_item,
        }

    # -- dispatch -------------------------------------------------------------
    def call(self, name: str, args: dict[str, Any] | None) -> dict:
        args = args or {}
        audit.log(self.conn, self.session_id, self.actor, "tool_call", {"tool": name, "args": args})
        fn = self._tools.get(name)
        if fn is None:
            out = {"error": f"unknown tool {name}"}
        elif not isinstance(args, dict):
            out = {"error": f"arguments for {name} must be an object"}
        else:
            try:
                out = fn(**args)
            except TypeError as e:
                out = {"error": f"bad arguments for {name}: {e}"}
            except (queue.QueueError, sessions.SessionError, ValueError) as e:
                out = {"error": str(e)}
            except Exception as e:  # noqa: BLE001 - a tool bug must not look like "LLM unavailable"
                out = {"error": f"internal error in {name}: {type(e).__name__}: {e}"}
                audit.log(self.conn, self.session_id, "system", "error",
                          {"tool": name, "exception": type(e).__name__, "message": str(e)})
        audit.log(self.conn, self.session_id, self.actor, "tool_result", {"tool": name, "result": out})
        return out

    # -- helpers ----------------------------------------------------------------
    @property
    def session(self) -> dict:
        return sessions.get(self.conn, self.session_id)

    def _profile(self) -> dict:
        return sessions.profile(self.conn, self.session)

    def _results(self) -> list[dict]:
        return sessions.results(self.conn, self.session)

    # -- tools ------------------------------------------------------------------
    def fetch_customer(self, customer_id: str | None = None) -> dict:
        s = self.session
        if customer_id and customer_id != s["customer_id"]:
            return {"error": "this session is for a different customer"}
        return {"profile": self._profile()}

    def update_profile(self, facts: dict, evidence: str = "") -> dict:
        if not isinstance(facts, dict):
            return {"error": "facts must be an object of field -> value"}
        if not isinstance(evidence, str) or not evidence.strip():
            return {"error": "evidence is required: quote what the customer or officer said"}
        s = self.session
        bank = sessions.get_customer(self.conn, s["customer_id"])
        overrides = dict(s.get("profile_overrides") or {})
        system_fields = set(overrides.get(sessions.SYSTEM_FIELDS_KEY) or [])
        rejected = []
        for fname, value in facts.items():
            typ = PROFILE_TYPES.get(fname)
            if typ is None:
                rejected.append({"field": fname, "reason": "unknown field"})
                continue
            if value is None:
                rejected.append({"field": fname, "reason": "cannot set a field to null; leave it out instead"})
                continue
            value, ok = _coerce(value, typ)
            if not ok:
                rejected.append({"field": fname, "reason": "bad type"})
                continue
            if fname == "occupation" and value not in OCCUPATIONS:
                rejected.append({"field": fname, "reason": f"occupation must be one of {sorted(OCCUPATIONS)}"})
                continue
            # Compare against the EFFECTIVE profile, not just the bank record.
            if fname in system_fields:
                if overrides[fname] != value:
                    rejected.append({"field": fname,
                                     "reason": "set by a completed service request; cannot be changed by conversation"})
                continue
            if bank.get(fname) is not None and bank[fname] != value:
                rejected.append({"field": fname, "reason": "conflicts with bank record"})
                continue
            if overrides.get(fname) is not None and overrides[fname] != value and self.actor != "officer":
                # only the officer may correct a fact recorded earlier in the interview
                rejected.append({"field": fname, "reason": "conflicts with recorded fact"})
                continue
            overrides[fname] = value
        sessions.update(self.conn, self.session_id, profile_overrides=overrides)
        audit.log(self.conn, self.session_id, self.actor, "profile_update",
                  {"facts": facts, "evidence": evidence, "rejected": rejected})
        return {"profile": self._profile(), "rejected_fields": rejected}

    def check_eligibility(self) -> dict:
        return {"results": self._results()}

    def pick_next_question(self) -> dict:
        out = sessions.next_question(self.conn, self.session)
        return out

    def plan_actions(self) -> dict:
        return plan_actions(self._results(), self._profile())

    def fill_form(self, product_id: str, extra_fields: dict | None = None) -> dict:
        # Re-run the rules: never trust the model's claim that a product is eligible.
        status = engine.status_of(self._results(), product_id)
        if status is None:
            return {"error": f"unknown product {product_id}"}
        if status != engine.ELIGIBLE:
            return {"error": f"{product_id} is {status}, not ELIGIBLE; no form can be prepared"}
        s = self.session
        payload, missing = fill.build_payload(product_id, self._profile(), extra_fields)
        if missing:
            return {"item_id": None, "status": "NEEDS_INFO", "missing_mandatory": missing,
                    "hint": "Ask for these fields, then call fill_form again with extra_fields."}
        existing = queue.find_open(self.conn, self.session_id, queue.ENROLLMENT_FORM, product_id)
        if existing and existing["status"] == queue.PENDING_APPROVAL:
            # validate + write first, render after (the PDF must always match the DB payload)
            item = queue.replace_pending(self.conn, existing["item_id"], payload, None)
        elif existing:
            return {"error": f"{product_id} already has item {existing['item_id']} in status "
                             f"{existing['status']}; revise that item instead"}
        else:
            item = queue.create(self.conn, session_id=self.session_id, customer_id=s["customer_id"],
                                kind=queue.ENROLLMENT_FORM, product_id=product_id, payload=payload,
                                actor=self.actor)
        pdf = fill.render_pdf(item["item_id"], f"{product_id} enrollment form", item["payload"])
        queue.set_pdf(self.conn, item["item_id"], pdf)
        return {"item_id": item["item_id"], "status": queue.PENDING_APPROVAL, "pdf_path": pdf,
                "missing_mandatory": []}

    def raise_request(self, action_id: str, note: str = "") -> dict:
        res = self._results()
        blocked = {a for r in res for a in r["blocked_by"]}
        if action_id not in blocked:
            return {"error": f"{action_id} is not needed: no product is blocked by it"}
        catalog = engine.load_products()
        via = catalog["actions"].get(action_id, {}).get("product")
        if via:
            return {"error": f"{action_id} is done by enrolling in {via}: call fill_form('{via}') instead"}
        # include SUBMITTED: a request that was already completed is never raised twice
        existing = queue.find_open(self.conn, self.session_id, queue.SERVICE_REQUEST, action_id,
                                   include_submitted=True)
        if existing:
            return {"item_id": existing["item_id"], "status": existing["status"], "note": "already raised"}
        s = self.session
        payload, _ = fill.build_payload(None, self._profile(), {"note": note} if note else None,
                                        action_id=action_id)
        item = queue.create(self.conn, session_id=self.session_id, customer_id=s["customer_id"],
                            kind=queue.SERVICE_REQUEST, action_id=action_id, payload=payload, actor=self.actor)
        pdf = fill.render_pdf(item["item_id"], catalog["actions"][action_id]["label"], item["payload"])
        queue.set_pdf(self.conn, item["item_id"], pdf)
        return {"item_id": item["item_id"], "status": queue.PENDING_APPROVAL}

    def revise_item(self, item_id: str, changes: dict, explanation: str = "") -> dict:
        item = queue.get(self.conn, item_id)
        if item["session_id"] != self.session_id:
            return {"error": "item belongs to a different session"}
        if item["status"] == queue.NEEDS_OFFICER:
            return {"error": "revision limit reached: stop and hand this item to the officer"}
        item = revise_and_render(self.conn, item_id, changes, explanation, actor=self.actor)
        return {"item_id": item_id, "status": item["status"], "revision_count": item["revision_count"]}


def revise_and_render(conn: sqlite3.Connection, item_id: str, changes: dict, explanation: str,
                      actor: str, officer_id: str | None = None) -> dict:
    """Shared by the agent tool and the officer route: validate → write → render.

    queue.revise raises before touching the DB if anything is wrong, and the PDF
    is only re-rendered after the DB write succeeded, so the PDF served at
    /queue/{id}/pdf always matches what the officer is approving.
    """
    item = queue.revise(conn, item_id, changes, explanation, actor=actor, officer_id=officer_id)
    target = item["product_id"] or item["action_id"]
    pdf = fill.render_pdf(item_id, f"{target} (revision {item['revision_count']})", item["payload"])
    return queue.set_pdf(conn, item_id, pdf)
