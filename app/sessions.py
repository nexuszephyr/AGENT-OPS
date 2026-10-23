"""Session storage and derived SessionState (docs/TOOLS.md §3)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from app import queue
from app.config import MAX_QUESTIONS
from app.db import row_to_dict
from app.planner.documents import plan_actions
from app.planner.questions import BY_ID as QUESTIONS
from app.planner.questions import pick_next_question
from app.rules import engine

INGESTING, EVALUATING, ASKING, PLANNING, ACTING = "INGESTING", "EVALUATING", "ASKING", "PLANNING", "ACTING"
AWAITING_APPROVAL, REVISING, DONE = "AWAITING_APPROVAL", "REVISING", "DONE"
ESCALATED = "ESCALATED"  # an item hit the revision limit and is waiting on the officer

# Key inside profile_overrides listing fields set by completed service requests.
# Those facts can never be undone by conversation (see tools.update_profile).
SYSTEM_FIELDS_KEY = "_system_fields"

# Columns update() may write (keys are never taken from user input).
_UPDATABLE = {"state", "profile_overrides", "asked_questions", "messages", "questions_asked",
              "pending_question"}


class SessionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_customer(conn: sqlite3.Connection, customer_id: str) -> dict:
    row = conn.execute("SELECT profile_json FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    if row is None:
        raise SessionError(f"no customer {customer_id}")
    return json.loads(row["profile_json"])


def list_customers(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute("SELECT profile_json FROM customers ORDER BY customer_id"):
        p = json.loads(r["profile_json"])
        out.append({k: p.get(k) for k in ("customer_id", "name", "age", "occupation", "district",
                                          "preferred_language")})
    return out


def create(conn: sqlite3.Connection, customer_id: str, officer_id: str) -> dict:
    get_customer(conn, customer_id)  # raises if missing
    sid = "S-" + uuid.uuid4().hex[:6].upper()
    conn.execute(
        "INSERT INTO sessions (session_id, customer_id, officer_id, state, updated_at) VALUES (?, ?, ?, ?, ?)",
        (sid, customer_id, officer_id, INGESTING, _now()),
    )
    conn.commit()
    return get(conn, sid)


def get(conn: sqlite3.Connection, session_id: str) -> dict:
    s = row_to_dict(conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone())
    if s is None:
        raise SessionError(f"no session {session_id}")
    return s


def update(conn: sqlite3.Connection, session_id: str, **cols) -> None:
    bad = set(cols) - _UPDATABLE
    if bad:
        raise SessionError(f"cannot update columns {sorted(bad)}")
    sets, vals = [], []
    for k, v in cols.items():
        if k in ("profile_overrides", "asked_questions", "messages"):
            k, v = k + "_json", json.dumps(v, ensure_ascii=False)
        sets.append(f"{k} = ?")
        vals.append(v)
    sets.append("updated_at = ?")
    vals.append(_now())
    conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = ?",  # noqa: S608 - allowlisted
                 (*vals, session_id))
    conn.commit()


def profile(conn: sqlite3.Connection, session: dict) -> dict:
    """Bank record with interview facts and system facts layered on top."""
    overrides = {k: v for k, v in (session.get("profile_overrides") or {}).items() if not k.startswith("_")}
    return {**get_customer(conn, session["customer_id"]), **overrides}


def _ranker(session: dict, res: list[dict]) -> dict:
    return pick_next_question(res, asked=session.get("asked_questions") or [],
                              questions_asked=session["questions_asked"], limit=MAX_QUESTIONS)


def results(conn: sqlite3.Connection, session: dict) -> list[dict]:
    """Rules-engine results, with UNKNOWN → VERIFY_MANUALLY once nothing more can be asked.

    That happens when no question is awaiting an answer AND the ranker has nothing
    left to ask (question limit reached, or every useful question was already
    asked and went unanswered, e.g. "not sure").
    """
    res = engine.evaluate_all(profile(conn, session))
    if session.get("pending_question"):
        return res  # still waiting for the answer to the last question
    if any(r["status"] == engine.UNKNOWN for r in res) and _ranker(session, res)["question"] is None:
        res = engine.finalize_unknowns(res)
    return res


def next_question(conn: sqlite3.Connection, session: dict, res: list[dict] | None = None) -> dict:
    return _ranker(session, res if res is not None else results(conn, session))


def mark_asked(conn: sqlite3.Connection, session_id: str, question_id: str, set_pending: bool = True) -> None:
    """The question has been put to the customer. It stays pending until resolved (see loop._finish)."""
    s = get(conn, session_id)
    asked = s.get("asked_questions") or []
    if question_id and question_id not in asked:
        cols: dict = {"asked_questions": asked + [question_id], "questions_asked": s["questions_asked"] + 1}
        if set_pending:
            cols["pending_question"] = question_id
        update(conn, session_id, **cols)


def clear_pending(conn: sqlite3.Connection, session_id: str) -> None:
    """The pending question has been dealt with (answered, or the customer can't answer)."""
    update(conn, session_id, pending_question=None)


def pending_answered(conn: sqlite3.Connection, session: dict) -> bool:
    """True when every field the pending question asks about is now known."""
    q = QUESTIONS.get(session.get("pending_question") or "")
    if q is None:
        return False
    prof = profile(conn, session)
    return all(prof.get(f) is not None for f in q["fields"])


def pending_view(session: dict, res: list[dict]) -> dict | None:
    """The pending question in the same shape as the ranker's output."""
    q = QUESTIONS.get(session.get("pending_question") or "")
    if q is None:
        return None
    resolves = [r["product_id"] for r in res if r["status"] == engine.UNKNOWN
                and set(r["unknown_fields"]) <= set(q["fields"])]
    return {**q, "resolves": resolves, "pending": True}


def _queued_item(items: list[dict], step: dict) -> dict | None:
    """The queue item that covers a plan step, if any."""
    for i in items:
        if step.get("product_id") and i["product_id"] == step["product_id"]:
            return i
        if step["type"] == "SERVICE_REQUEST" and i["action_id"] == step["action_id"]:
            return i
    return None


# What a completed (approved + mock-submitted) item changes in the customer's profile.
# Recorded as a system fact, so a blocked product can become ELIGIBLE afterwards.
COMPLETION_EFFECTS: dict[str, dict] = {
    "AADHAAR_SEEDING_REQUEST": {"aadhaar_seeded": True},
    "EKYC_REQUEST": {"ekyc_done": True},
    "PMJDY": {"has_savings_account": True},
}


def apply_completion(conn: sqlite3.Connection, item: dict) -> dict:
    """Called after the officer approves an item. Returns the facts applied."""
    effect = dict(COMPLETION_EFFECTS.get(item.get("action_id") or "")
                  or COMPLETION_EFFECTS.get(item.get("product_id") or "") or {})
    s = get(conn, item["session_id"])
    if item.get("product_id"):
        # an approved enrollment means the customer now holds the product
        prof = profile(conn, s)
        effect["enrollments"] = list(dict.fromkeys((prof.get("enrollments") or []) + [item["product_id"]]))
    if not effect:
        return {}
    overrides = {**(s.get("profile_overrides") or {}), **effect}
    overrides[SYSTEM_FIELDS_KEY] = sorted(set(overrides.get(SYSTEM_FIELDS_KEY) or []) | set(effect))
    update(conn, s["session_id"], profile_overrides=overrides)
    from app import audit  # local import avoids a cycle at module load
    audit.log(conn, s["session_id"], "system", "completion_effect", {"item_id": item["item_id"], "facts": effect})
    return effect


def derive_state(conn: sqlite3.Connection, session_id: str) -> str:
    """Compute the state-machine position from data, then persist it."""
    s = get(conn, session_id)
    res = results(conn, s)
    items = queue.list_items(conn, session_id=session_id)
    open_statuses = {i["status"] for i in items}
    plan = plan_actions(res, profile(conn, s))
    pending_steps = [st for st in plan["steps"]
                     if "after" not in st and _queued_item(items, st) is None]

    if queue.REJECTED in open_statuses:
        state = REVISING
    elif queue.NEEDS_OFFICER in open_statuses:
        state = ESCALATED  # never DONE while an escalation waits on the officer
    elif s.get("pending_question") or next_question(conn, s, res)["question"] is not None:
        state = ASKING
    elif pending_steps:
        state = PLANNING if not items else ACTING
    elif queue.PENDING_APPROVAL in open_statuses:
        state = AWAITING_APPROVAL
    else:
        state = DONE
    update(conn, session_id, state=state)
    return state


def state_view(conn: sqlite3.Connection, session_id: str) -> dict:
    derive_state(conn, session_id)
    s = get(conn, session_id)
    res = results(conn, s)
    prof = profile(conn, s)
    return {
        "session_id": s["session_id"],
        "customer_id": s["customer_id"],
        "officer_id": s["officer_id"],
        "state": s["state"],
        "profile": prof,
        "results": res,
        "plan": plan_actions(res, prof),
        # while a question awaits its answer, it is the current question (one at a time)
        "next_question": ({"question": pv} if (pv := pending_view(s, res)) else next_question(conn, s, res)),
        "queue_items": queue.list_items(conn, session_id=session_id),
        "questions_asked": s["questions_asked"],
        "pending_question": s.get("pending_question"),
        "messages": s.get("messages") or [],
    }
