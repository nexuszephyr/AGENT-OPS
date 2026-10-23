"""Approval queue state machine (AGENTS.md rule 2, docs/DATA_MODEL.md §2).

Only `approve()` moves an item past PENDING_APPROVAL, and only the officer API
calls it. The LLM has no tool that reaches approve() or submit().
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app import audit
from app.config import MAX_REVISIONS
from app.db import row_to_dict
from app.privacy import UnmaskedIdentifierError, assert_no_full_ids

DRAFT = "DRAFT"
PENDING_APPROVAL = "PENDING_APPROVAL"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
REVISED = "REVISED"
SUBMITTED = "SUBMITTED"
NEEDS_OFFICER = "NEEDS_OFFICER"

ENROLLMENT_FORM = "ENROLLMENT_FORM"
SERVICE_REQUEST = "SERVICE_REQUEST"

TRANSITIONS: dict[str, set[str]] = {
    DRAFT: {PENDING_APPROVAL},
    PENDING_APPROVAL: {APPROVED, REJECTED},
    REJECTED: {REVISED, NEEDS_OFFICER},
    REVISED: {PENDING_APPROVAL},
    APPROVED: {SUBMITTED},
    SUBMITTED: set(),
    NEEDS_OFFICER: {REVISED},  # only the officer may revise an escalated item
}
OPEN_STATUSES = (DRAFT, PENDING_APPROVAL, REJECTED, REVISED, NEEDS_OFFICER)


class QueueError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(conn: sqlite3.Connection, item_id: str) -> dict:
    item = row_to_dict(conn.execute("SELECT * FROM queue_items WHERE item_id = ?", (item_id,)).fetchone())
    if item is None:
        raise QueueError(f"no queue item {item_id}")
    return item


def list_items(conn: sqlite3.Connection, status: str | None = None, session_id: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM queue_items WHERE 1=1", []
    if status:
        q += " AND status = ?"
        args.append(status)
    if session_id:
        q += " AND session_id = ?"
        args.append(session_id)
    q += " ORDER BY created_at, item_id"
    return [row_to_dict(r) for r in conn.execute(q, args).fetchall()]


def _move(conn: sqlite3.Connection, item: dict, new: str, **cols) -> dict:
    if new not in TRANSITIONS[item["status"]]:
        raise QueueError(f"illegal transition {item['status']} → {new} for {item['item_id']}")
    sets = ["status = ?"] + [f"{k} = ?" for k in cols]
    conn.execute(f"UPDATE queue_items SET {', '.join(sets)} WHERE item_id = ?",
                 (new, *cols.values(), item["item_id"]))
    conn.commit()
    return get(conn, item["item_id"])


def find_open(conn: sqlite3.Connection, session_id: str, kind: str, key: str,
              include_submitted: bool = False) -> dict | None:
    col = "product_id" if kind == ENROLLMENT_FORM else "action_id"
    for r in list_items(conn, session_id=session_id):
        if r["kind"] == kind and r[col] == key and (include_submitted or r["status"] != SUBMITTED):
            return r
    return None


def set_pdf(conn: sqlite3.Connection, item_id: str, pdf_path: str) -> dict:
    conn.execute("UPDATE queue_items SET pdf_path = ? WHERE item_id = ?", (pdf_path, item_id))
    conn.commit()
    return get(conn, item_id)


def create(conn: sqlite3.Connection, *, session_id: str, customer_id: str, kind: str,
           payload: dict, product_id: str | None = None, action_id: str | None = None,
           pdf_path: str | None = None, actor: str = "agent") -> dict:
    """Create an item and put it straight into PENDING_APPROVAL (DRAFT → PENDING)."""
    _check_payload(payload)
    item_id = "Q-" + uuid.uuid4().hex[:6].upper()
    conn.execute(
        "INSERT INTO queue_items (item_id, session_id, customer_id, kind, product_id, action_id, "
        "payload_json, pdf_path, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, session_id, customer_id, kind, product_id, action_id,
         json.dumps(payload, ensure_ascii=False), pdf_path, DRAFT),
    )
    conn.commit()
    item = _move(conn, get(conn, item_id), PENDING_APPROVAL)
    audit.log(conn, session_id, actor, "queue_create",
              {"item_id": item_id, "kind": kind, "product_id": product_id, "action_id": action_id})
    return item


def replace_pending(conn: sqlite3.Connection, item_id: str, payload: dict, pdf_path: str | None) -> dict:
    """Refill a still-pending draft (fill_form called again before the officer decided)."""
    item = get(conn, item_id)
    if item["status"] != PENDING_APPROVAL:
        raise QueueError(f"{item_id} is {item['status']}; only pending drafts can be replaced")
    _check_payload(payload)
    conn.execute("UPDATE queue_items SET payload_json = ?, pdf_path = ? WHERE item_id = ?",
                 (json.dumps(payload, ensure_ascii=False), pdf_path, item_id))
    conn.commit()
    return get(conn, item_id)


def approve(conn: sqlite3.Connection, item_id: str, officer_id: str) -> dict:
    """Officer-only. Approve, then mock-submit."""
    item = _move(conn, get(conn, item_id), APPROVED, decided_by=officer_id, decided_at=_now())
    audit.log(conn, item["session_id"], "officer", "approve", {"item_id": item_id, "officer_id": officer_id})
    item = _move(conn, item, SUBMITTED)
    audit.log(conn, item["session_id"], "system", "submit_mock", {"item_id": item_id})
    return item


def reject(conn: sqlite3.Connection, item_id: str, officer_id: str, reason: str) -> dict:
    """Officer-only. After MAX_REVISIONS revisions, a further rejection escalates to NEEDS_OFFICER."""
    if not reason.strip():
        raise QueueError("a rejection needs a reason")
    item = _move(conn, get(conn, item_id), REJECTED,
                 reject_reason=reason, decided_by=officer_id, decided_at=_now())
    audit.log(conn, item["session_id"], "officer", "reject",
              {"item_id": item_id, "officer_id": officer_id, "reason": reason})
    if item["revision_count"] >= MAX_REVISIONS:
        item = _move(conn, item, NEEDS_OFFICER)
        audit.log(conn, item["session_id"], "system", "escalate", {"item_id": item_id})
    return item


def _check_payload(payload: dict) -> None:
    try:
        assert_no_full_ids(payload)
    except UnmaskedIdentifierError as e:
        raise QueueError(str(e)) from e


def validate_revision(item: dict, changes: dict, actor: str) -> dict:
    """All checks, no side effects. Returns the would-be payload. Call BEFORE rendering anything."""
    allowed = {REJECTED} | ({NEEDS_OFFICER} if actor == "officer" else set())
    if item["status"] not in allowed:
        who = "the officer" if item["status"] == NEEDS_OFFICER else "anyone"
        raise QueueError(f"{item['item_id']} is {item['status']}; it cannot be revised by {actor} "
                         f"(only REJECTED items, or NEEDS_OFFICER items by {who})")
    if not isinstance(changes, dict) or not changes:
        raise QueueError("changes must be a non-empty object")
    unknown = set(changes) - set(item["payload"])
    if unknown:
        raise QueueError(f"cannot add new payload fields: {sorted(unknown)}")
    _check_payload(changes)
    return {**item["payload"], **changes}


def revise(conn: sqlite3.Connection, item_id: str, changes: dict, explanation: str,
           actor: str = "agent") -> dict:
    """REJECTED (or NEEDS_OFFICER, officer only) → REVISED → PENDING_APPROVAL.

    Validates fully before writing anything. The caller renders the new PDF only
    after this succeeds (see agent.tools.revise_and_render).
    """
    item = get(conn, item_id)
    payload = validate_revision(item, changes, actor)
    conn.execute(
        "UPDATE queue_items SET payload_json = ?, revision_count = revision_count + 1 WHERE item_id = ?",
        (json.dumps(payload, ensure_ascii=False), item_id),
    )
    conn.commit()
    item = _move(conn, get(conn, item_id), REVISED)
    audit.log(conn, item["session_id"], actor, "revise",
              {"item_id": item_id, "changes": changes, "explanation": explanation})
    return _move(conn, item, PENDING_APPROVAL)
