"""Deterministic, three-valued eligibility engine.

This module is the ONLY place eligibility is decided (AGENTS.md rule 1).
Pure Python: no I/O except loading products.json, no LLM.
Spec: docs/RULES_SPEC.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

# --- Status constants (single source of truth) --------------------------------
ELIGIBLE = "ELIGIBLE"
ELIGIBLE_BLOCKED = "ELIGIBLE_BLOCKED"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
UNKNOWN = "UNKNOWN"
ALREADY_ENROLLED = "ALREADY_ENROLLED"
VERIFY_MANUALLY = "VERIFY_MANUALLY"

ALL_STATUSES = (ELIGIBLE, ELIGIBLE_BLOCKED, NOT_ELIGIBLE, UNKNOWN, ALREADY_ENROLLED, VERIFY_MANUALLY)

# Three-valued truth values
T, F, U = "T", "F", "U"

PRODUCTS_PATH = Path(__file__).with_name("products.json")

OPS = {
    "==": lambda v, x: v == x,
    "!=": lambda v, x: v != x,
    "<": lambda v, x: v < x,
    "<=": lambda v, x: v <= x,
    ">": lambda v, x: v > x,
    ">=": lambda v, x: v >= x,
    "in": lambda v, x: v in x,
    "not_in": lambda v, x: v not in x,
    "is_true": lambda v, _x: v is True,
    "is_false": lambda v, _x: v is False,
}


class RulesValidationError(ValueError):
    """products.json is malformed. Raised at startup so bad rules never run."""


# --- Loading and validation ----------------------------------------------------

def validate_catalog(catalog: dict) -> None:
    """Fail loudly on anything RULES_SPEC.md §5 forbids."""
    docs = catalog.get("documents") or {}
    actions = catalog.get("actions") or {}
    seen_ids: set[str] = set()
    errors: list[str] = []

    def walk(node: Any, pid: str) -> None:
        if not isinstance(node, dict):
            errors.append(f"{pid}: condition must be an object, got {node!r}")
            return
        if "all" in node or "any" in node:
            children = node.get("all", node.get("any"))
            if not isinstance(children, list) or not children:
                errors.append(f"{pid}: all/any needs a non-empty list")
                return
            for c in children:
                walk(c, pid)
            return
        if "not" in node:
            _check_id_text(node, pid)
            walk(node["not"], pid)
            return
        _check_id_text(node, pid)
        if node.get("op") not in OPS:
            errors.append(f"{pid}/{node.get('id')}: unsupported op {node.get('op')!r}")
        if "field" not in node:
            errors.append(f"{pid}/{node.get('id')}: leaf has no field")

    def _check_id_text(node: dict, pid: str) -> None:
        rid = node.get("id")
        if not rid or not node.get("text"):
            errors.append(f"{pid}: every leaf/not needs id and text ({node})")
            return
        if rid in seen_ids:
            errors.append(f"duplicate rule id {rid}")
        seen_ids.add(rid)

    products = catalog.get("products")
    if not isinstance(products, list) or not products:
        raise RulesValidationError("products.json has no products")

    product_ids = set()
    for p in products:
        pid = p.get("id", "<no id>")
        if pid in product_ids:
            errors.append(f"duplicate product id {pid}")
        product_ids.add(pid)
        for key in ("name", "rules", "source_url", "last_verified"):
            if not p.get(key):
                errors.append(f"{pid}: missing {key}")
        if p.get("rules"):
            walk(p["rules"], pid)
        for d in p.get("required_docs", []):
            if d not in docs:
                errors.append(f"{pid}: unknown document {d}")
        for pre in p.get("prerequisites", []):
            if pre.get("op") not in OPS or "field" not in pre:
                errors.append(f"{pid}: bad prerequisite {pre}")
            if pre.get("action") not in actions:
                errors.append(f"{pid}: prerequisite action {pre.get('action')} not in actions")

    if errors:
        raise RulesValidationError("; ".join(errors))


def load_products(path: str | Path | None = None) -> dict:
    """Load and validate a catalogue. Cached for the default path."""
    if path is None:
        return _load_default()
    catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_catalog(catalog)
    return catalog


@lru_cache(maxsize=1)
def _load_default() -> dict:
    catalog = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    validate_catalog(catalog)
    return catalog


# --- Evaluation --------------------------------------------------------------

@dataclass
class _Eval:
    value: str
    failed: list[dict] = field(default_factory=list)   # [{"id","text"}]
    passed: list[str] = field(default_factory=list)    # rule ids
    unknown: list[str] = field(default_factory=list)   # field names


def _missing(profile: dict, fname: str) -> bool:
    return profile.get(fname) is None


def _leaf(node: dict, profile: dict) -> _Eval:
    fname = node["field"]
    if _missing(profile, fname):
        return _Eval(U, unknown=[fname])
    try:
        ok = OPS[node["op"]](profile[fname], node.get("value"))
    except TypeError:
        # e.g. comparing a string to an int: treat as unknown, never guess
        return _Eval(U, unknown=[fname])
    if ok:
        return _Eval(T, passed=[node["id"]])
    return _Eval(F, failed=[{"id": node["id"], "text": node["text"]}])


def _eval(node: dict, profile: dict) -> _Eval:
    if "all" in node or "any" in node:
        is_all = "all" in node
        # Evaluate every child: no short-circuit, so ALL failures are reported.
        kids = [_eval(c, profile) for c in node["all" if is_all else "any"]]
        passed = [p for k in kids for p in k.passed]
        if is_all:
            if any(k.value == F for k in kids):
                val = F
            elif any(k.value == U for k in kids):
                val = U
            else:
                val = T
        else:
            if any(k.value == T for k in kids):
                val = T
            elif any(k.value == U for k in kids):
                val = U
            else:
                val = F
        failed = [f for k in kids if k.value == F for f in k.failed] if val == F else []
        unknown = [u for k in kids if k.value == U for u in k.unknown] if val == U else []
        return _Eval(val, failed, passed, _dedupe(unknown))

    if "not" in node:
        inner = _eval(node["not"], profile)
        if inner.value == U:
            return _Eval(U, unknown=inner.unknown)
        if inner.value == T:
            return _Eval(F, failed=[{"id": node["id"], "text": node["text"]}])
        return _Eval(T, passed=[node["id"]])

    return _leaf(node, profile)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def evaluate(product: dict, profile: dict) -> dict:
    """Evaluate one product. Returns a ProductResult dict (RULES_SPEC.md §4)."""
    base = {
        "product_id": product["id"],
        "name": product["name"],
        "benefit": product.get("benefit", ""),
        "failed_rules": [],
        "passed_rules": [],
        "unknown_fields": [],
        "blocked_by": [],
        "required_docs": list(product.get("required_docs", [])),
        "source_url": product["source_url"],
        "last_verified": product["last_verified"],
    }

    if product["id"] in (profile.get("enrollments") or []):
        return {**base, "status": ALREADY_ENROLLED}

    res = _eval(product["rules"], profile)
    base["passed_rules"] = res.passed
    if res.value == F:
        return {**base, "status": NOT_ELIGIBLE, "failed_rules": res.failed}
    if res.value == U:
        return {**base, "status": UNKNOWN, "unknown_fields": res.unknown}

    blocked: list[str] = []
    missing: list[str] = []
    for pre in product.get("prerequisites", []):
        fname = pre["field"]
        if _missing(profile, fname):
            missing.append(fname)
        elif not OPS[pre["op"]](profile[fname], pre.get("value")):
            blocked.append(pre["action"])
    # A known blocker wins over a missing prerequisite field.
    if blocked:
        return {**base, "status": ELIGIBLE_BLOCKED, "blocked_by": _dedupe(blocked)}
    if missing:
        return {**base, "status": UNKNOWN, "unknown_fields": _dedupe(missing)}
    return {**base, "status": ELIGIBLE}


def evaluate_all(profile: dict, catalog: dict | None = None) -> list[dict]:
    catalog = catalog or load_products()
    return [evaluate(p, profile) for p in catalog["products"]]


def finalize_unknowns(results: list[dict]) -> list[dict]:
    """After the question limit, remaining UNKNOWNs become VERIFY_MANUALLY."""
    return [{**r, "status": VERIFY_MANUALLY} if r["status"] == UNKNOWN else r for r in results]


def status_of(results: list[dict], product_id: str) -> str | None:
    for r in results:
        if r["product_id"] == product_id:
            return r["status"]
    return None
