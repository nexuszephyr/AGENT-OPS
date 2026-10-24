"""FastAPI app (docs/TOOLS.md §3).

    uvicorn app.api.main:app --reload

Approve / reject are officer routes. The LLM has no path to them.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import audit, config, db, officers, queue, scan, sessions, timeline
from app.agent import loop
from app.agent.tools import revise_and_render
from app.data import seed
from app.planner.documents import plan_actions
from app.rules import engine

app = FastAPI(title="Yojana Saathi", version="0.1.0")
_conn = None
_latest_scan: dict | None = None  # last POST /scan result, in memory only


def conn():
    global _conn
    if _conn is None:
        _conn = db.connect()
        engine.load_products()  # fail fast on bad rules (AGENTS.md rule 3)
        if _conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            seed.seed(_conn)
    return _conn


def set_connection(c) -> None:
    """Tests inject an in-memory DB."""
    global _conn
    _conn = c


# --- request bodies -------------------------------------------------------------------

class SessionIn(BaseModel):
    customer_id: str
    officer_id: str = "officer-1"


class ScanIn(BaseModel):
    officer_id: str | None = None  # who asked, for the audit trail (optional)


class MessageIn(BaseModel):
    text: str
    lang: str | None = None


class FactsIn(BaseModel):
    facts: dict
    question_id: str | None = None


class ActIn(BaseModel):
    extra_fields: dict[str, dict] = {}


# officer_id is required: under maker-checker a silent default would be refused or misattributed
class ApproveIn(BaseModel):
    officer_id: str


class RejectIn(BaseModel):
    officer_id: str
    reason: str


class ReviseIn(BaseModel):
    officer_id: str = "officer-1"
    changes: dict
    explanation: str = ""


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except queue.MakerCheckerError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except (sessions.SessionError, queue.QueueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# --- routes ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "llm_configured": config.llm_configured(), "provider": config.LLM_PROVIDER}


@app.get("/officers")
def list_officers():
    return [officers.get_officer(oid) for oid in officers.OFFICERS]


@app.get("/customers")
def customers(segment: Literal["demo", "branch"] | None = None):
    return sessions.list_customers(conn(), segment)


@app.post("/scan")
def run_scan(body: ScanIn | None = None):
    """Branch-wide proactive scan. Recommends only: creates no queue items, changes no customer or session."""
    global _latest_scan
    officer_id = body.officer_id if body else None
    if officer_id is not None and officers.get_officer(officer_id) is None:
        raise HTTPException(status_code=400, detail=f"unknown officer {officer_id}")
    _latest_scan = scan.scan(sessions.customer_profiles(conn()))
    # summary only, no per-customer data
    audit.log(conn(), None, "agent", "scan_run", {"officer_id": officer_id, **_latest_scan["summary"]})
    return _latest_scan


@app.get("/scan/latest")
def latest_scan():
    if _latest_scan is None:
        raise HTTPException(status_code=404, detail="no scan has run yet")
    return _latest_scan


@app.post("/sessions")
def create_session(body: SessionIn):
    return _guard(loop.start_session, conn(), body.customer_id, body.officer_id)


@app.post("/sessions/{session_id}/message")
def message(session_id: str, body: MessageIn):
    return _guard(loop.handle_message, conn(), session_id, body.text)


@app.get("/sessions/{session_id}/state")
def state(session_id: str):
    return _guard(sessions.state_view, conn(), session_id)


@app.post("/sessions/{session_id}/facts")
def facts(session_id: str, body: FactsIn):
    """Officer-entered facts. Works with no LLM (rules-only demo fallback)."""
    return _guard(loop.set_facts, conn(), session_id, body.facts, body.question_id)


@app.post("/sessions/{session_id}/act")
def act(session_id: str, body: ActIn):
    """Deterministically queue every actionable plan step (no-LLM fallback)."""
    return _guard(loop.auto_act, conn(), session_id, body.extra_fields)


@app.get("/queue")
def list_queue(status: str | None = None, session_id: str | None = None):
    return queue.list_items(conn(), status=status, session_id=session_id)


@app.post("/queue/{item_id}/approve")
def approve(item_id: str, body: ApproveIn):
    item = _guard(queue.approve, conn(), item_id, body.officer_id)
    applied = sessions.apply_completion(conn(), item)
    return {"item": item, "applied": applied, "state": sessions.state_view(conn(), item["session_id"])}


@app.post("/queue/{item_id}/reject")
def reject(item_id: str, body: RejectIn):
    _guard(queue.reject, conn(), item_id, body.officer_id, body.reason)
    turn = loop.handle_rejection(conn(), item_id)  # agent tries a revision
    return {"item": queue.get(conn(), item_id), **turn}


@app.post("/queue/{item_id}/revise")
def revise(item_id: str, body: ReviseIn):
    """Officer edits a REJECTED or NEEDS_OFFICER item. Works with no LLM (rules-only demo path).

    The item goes back to PENDING_APPROVAL: the officer still approves it explicitly.
    """
    item = _guard(revise_and_render, conn(), item_id, body.changes,
                  body.explanation or f"edited by {body.officer_id}", "officer", body.officer_id)
    return {"item": item, "state": sessions.state_view(conn(), item["session_id"])}


@app.get("/queue/{item_id}/history")
def item_history(item_id: str):
    """What happened to one queue item, and what changed since its latest rejection (issue #19). Read-only."""
    return _guard(queue.history, conn(), item_id)


@app.get("/queue/{item_id}/pdf")
def pdf(item_id: str):
    item = _guard(queue.get, conn(), item_id)
    if not item.get("pdf_path") or not Path(item["pdf_path"]).exists():
        raise HTTPException(status_code=404, detail="no PDF for this item")
    return FileResponse(item["pdf_path"], media_type="application/pdf", filename=f"{item_id}.pdf")


@app.get("/audit")
def audit_log(session_id: str | None = None, limit: int = 200):
    return audit.fetch(conn(), session_id=session_id, limit=limit)


@app.get("/sessions/{session_id}/timeline")
def session_timeline(session_id: str):
    """The session's audit log as UI-ready events grouped by pipeline stage (issue #18).

    Read-only. Safe to poll while an agent turn runs: tool calls show as "running" until their result lands.
    """
    s = _guard(sessions.get, conn(), session_id)
    return timeline.build(s, audit.fetch(conn(), session_id=session_id, limit=2000))


@app.get("/dashboard")
def dashboard():
    c = conn()
    latest = c.execute(
        "SELECT session_id FROM sessions s WHERE created_at = "
        "(SELECT MAX(created_at) FROM sessions WHERE customer_id = s.customer_id) GROUP BY customer_id"
    ).fetchall()
    eligible = 0
    blocked: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    docs: Counter[str] = Counter()
    states: Counter[str] = Counter()
    for row in latest:
        states[sessions.derive_state(c, row["session_id"])] += 1
        s = sessions.get(c, row["session_id"])
        res = sessions.results(c, s)
        eligible += sum(r["status"] in (engine.ELIGIBLE, engine.ELIGIBLE_BLOCKED) for r in res)
        for r in res:
            blocked.update(r["blocked_by"])
            if r["status"] == engine.NOT_ELIGIBLE:
                reasons.update(f["id"] for f in r["failed_rules"])
        for d in plan_actions(res, sessions.profile(c, s))["checklist"]:
            docs[d["doc_id"]] += 1
    items = queue.list_items(c)
    by_status = Counter(i["status"] for i in items)
    return {
        "customers_screened": len(latest),
        "products_eligible": eligible,
        "items_pending": by_status[queue.PENDING_APPROVAL],
        "items_approved": by_status[queue.APPROVED] + by_status[queue.SUBMITTED],
        "items_rejected": by_status[queue.REJECTED],
        "items_escalated": by_status[queue.NEEDS_OFFICER],
        "items_by_status": dict(by_status),
        "sessions_by_state": dict(states),
        "blocked_by_action": dict(blocked),
        "not_eligible_reasons": dict(reasons.most_common()),
        "top_missing_docs": dict(docs.most_common()),
    }
