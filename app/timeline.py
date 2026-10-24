"""Session timeline for the UI (issue #18): the audit log as CGI's four pipeline stages.

    Data ingestion → Intelligent decision-making → Action execution → Outcome reporting

Pure function over audit rows (already redacted by audit.log) and the session row. No DB, no LLM.
Each tool call and its result are folded into one event; a call with no result yet is "running",
which is how the UI shows a turn in progress.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app import officers, sessions

STAGES: list[dict[str, str]] = [
    {"id": "ingest", "label": "Ingest", "name": "Data ingestion"},
    {"id": "decide", "label": "Decide", "name": "Intelligent decision-making"},
    {"id": "act", "label": "Act", "name": "Action execution"},
    {"id": "report", "label": "Report", "name": "Outcome reporting"},
]
_ORDER = {s["id"]: i for i, s in enumerate(STAGES)}

# Where the session is, from its state machine (docs/ARCHITECTURE.md).
STATE_STAGE: dict[str, str] = {
    sessions.INGESTING: "ingest",
    sessions.EVALUATING: "decide", sessions.ASKING: "decide",
    sessions.PLANNING: "act", sessions.ACTING: "act", sessions.AWAITING_APPROVAL: "act",
    sessions.REVISING: "act", sessions.ESCALATED: "act",
    sessions.DONE: "report",
}

# Which stage each tool belongs to (README "What the agent does" table).
TOOL_STAGE: dict[str, str] = {
    "fetch_customer": "ingest", "update_profile": "ingest",
    "check_eligibility": "decide", "pick_next_question": "decide", "plan_actions": "decide",
    "fill_form": "act", "raise_request": "act", "revise_item": "act",
}
EVENT_STAGE: dict[str, str] = {
    "session_start": "ingest", "message": "ingest",
    "llm_reply": "decide", "question_resolved": "decide", "question_replaced": "decide", "error": "decide",
    "reject": "act", "revise": "act", "escalate": "act", "approve": "act", "approval_denied": "act",
    "submit_mock": "report", "completion_effect": "report",
}
# Covered by the tool event that wrote them, so not shown twice.
_FOLDED = {"profile_update", "queue_create"}

_TEXT_MAX = 160


def _short(text: Any, n: int = _TEXT_MAX) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= n else s[: n - 1] + "…"


def _who(actor: str) -> str:
    return {"agent": "Agent", "officer": "Officer"}.get(actor, "System")


def _officer(officer_id: Any) -> str:
    o = officers.get_officer(str(officer_id) if officer_id else None)
    return o["name"] if o else str(officer_id or "unknown officer")


def _tool_event(call: dict, result: dict | None) -> dict:
    """One tool call (+ its result, if it has arrived) as a timeline event."""
    p = call.get("payload") or {}
    tool, args = p.get("tool", "?"), p.get("args") or {}
    out = (result or {}).get("payload", {}).get("result") if result else None
    who = _who(call["actor"])
    tone, title, summary = "ok", f"{who} called {tool}", ""
    detail: dict[str, Any] = {"args": args}

    if out is None:
        tone, summary = "running", "Running…"
    elif isinstance(out, dict) and out.get("error"):
        tone, title, summary = "guardrail", f"{tool} refused", _short(out["error"])
        detail["result"] = out
    elif tool == "fetch_customer":
        cid = (out.get("profile") or {}).get("customer_id", "")
        title, summary = f"{who} loaded the bank record", f"Customer {cid}"
    elif tool == "update_profile":
        facts = args.get("facts") or {}
        rejected = out.get("rejected_fields") or []
        kept = [k for k in facts if k not in {r.get("field") for r in rejected}]
        title = f"{who} recorded {len(kept)} fact{'s' if len(kept) != 1 else ''}" if kept or not rejected \
            else f"{who} tried to record facts"
        summary = ", ".join(f"{k} = {json.dumps(facts[k])}" for k in kept) or "No new facts"
        if rejected:
            tone = "guardrail"
            summary += "; kept the record for " + ", ".join(f"{r.get('field')} ({r.get('reason')})" for r in rejected)
        detail["result"] = {"rejected_fields": rejected}
    elif tool == "check_eligibility":
        statuses = {r["product_id"]: r["status"] for r in out.get("results", [])}
        counts = Counter(statuses.values())
        title = f"Rules engine checked {len(statuses)} products"
        summary = ", ".join(f"{n} {s.lower().replace('_', ' ')}" for s, n in counts.most_common())
        detail["result"] = statuses  # compact: the full results are in the product cards
    elif tool == "pick_next_question":
        q = out.get("question")
        title = "Ranker picked the next question" if q else "No question left to ask"
        summary = q.get("text_en", q.get("question_id", "")) if q else _short(out.get("reason", ""))
        detail["result"] = {"question_id": q.get("question_id"), "resolves": q.get("resolves")} if q else out
    elif tool == "plan_actions":
        steps = out.get("steps", [])
        title = "Planner ordered the steps"
        summary = f"{len(steps)} step{'s' if len(steps) != 1 else ''}, {len(out.get('checklist', []))} documents"
        detail["result"] = [s.get("action_id") or s.get("product_id") for s in steps]
    elif tool == "fill_form":
        pid = args.get("product_id", "")
        if out.get("status") == "NEEDS_INFO":
            tone, title = "attention", f"{pid} form needs details"
            summary = "Missing: " + ", ".join(out.get("missing_mandatory", []))
        else:
            title, summary = f"{who} prepared the {pid} form", f"{out.get('item_id')} waits for a checker's approval"
        detail["result"] = {k: v for k, v in out.items() if k != "pdf_path"}
    elif tool == "raise_request":
        title = f"{who} raised {args.get('action_id', '')}"
        summary = (f"{out.get('item_id')} already raised" if out.get("note") == "already raised"
                   else f"{out.get('item_id')} waits for a checker's approval")
        detail["result"] = out
    elif tool == "revise_item":
        title = f"{who} revised {args.get('item_id', '')}"
        summary = f"Revision {out.get('revision_count')}: back for a checker's approval"
        detail["result"] = out
    else:
        detail["result"] = out

    return {"id": call["id"], "ts": call["ts"], "stage": TOOL_STAGE.get(tool, "decide"), "actor": call["actor"],
            "kind": "tool", "tool": tool, "tone": tone, "title": title, "summary": summary, "detail": detail}


def _plain_event(row: dict) -> dict | None:
    ev, p, who = row["event"], row.get("payload") or {}, _who(row["actor"])
    tone, title, summary = "ok", ev.replace("_", " ").capitalize(), ""
    if ev == "session_start":
        title, summary = "Session started", f"Customer {p.get('customer_id', '')}, officer {p.get('officer_id', '')}"
    elif ev == "message":
        title, summary = "Officer typed", _short(p.get("text"))
    elif ev == "llm_reply":
        raw = p.get("raw") or ""
        try:
            said = json.loads(raw[raw.index("{"): raw.rindex("}") + 1]).get("to_officer", raw)
        except ValueError:
            said = raw
        title, summary = "Agent replied", _short(said)
    elif ev == "question_resolved":
        title = "Customer doesn't know" if p.get("how") == "customer_does_not_know" else "Question answered"
        summary = p.get("question_id", "")
    elif ev == "question_replaced":
        model_q = (p.get("model_question") or {}).get("question_id")
        tone, title = "guardrail", "Ranker overrode the model's question"
        summary = f"Model asked {model_q}; ranker chose {p.get('ranker_question') or 'no question'}"
    elif ev == "error":
        if "tool" in p:
            return None  # also reported by that tool's result
        if "turn_failed" in p:  # a bug inside the turn (loop.handle_message); the turn is over
            tone, title = "attention", "Agent turn failed"
            summary = _short(p["turn_failed"]) + ". Statuses still come from the rules engine"
        else:
            tone, title = "attention", "LLM unavailable"
            summary = "Switched to rules-only mode: statuses still come from the rules engine"
    elif ev == "approve":
        title, summary = f"Officer approved {p.get('item_id', '')}", f"by {_officer(p.get('officer_id'))}"
    elif ev == "approval_denied":
        tone = "guardrail"
        title = f"Maker-checker blocked {_officer(p.get('officer_id'))} on {p.get('item_id', '')}"
        summary = _short(p.get("reason"))
    elif ev == "submit_mock":
        title, summary = f"Submitted {p.get('item_id', '')}", "Mock submission after approval"
    elif ev == "reject":
        tone, title = "attention", f"{_officer(p.get('officer_id'))} rejected {p.get('item_id', '')}"
        summary = _short(p.get("reason"))
    elif ev == "escalate":
        tone, title = "attention", f"{p.get('item_id', '')} escalated to the officer"
        summary = "Revision limit reached"
    elif ev == "revise":
        title = f"{who} revised {p.get('item_id', '')}"
        summary = _short(p.get("explanation") or ", ".join(p.get("changes") or {}))
    elif ev == "completion_effect":
        facts = p.get("facts") or {}
        title = "Customer record updated after approval"
        summary = ", ".join(f"{k} = {json.dumps(v)}" for k, v in facts.items())
    return {"id": row["id"], "ts": row["ts"], "stage": EVENT_STAGE.get(ev, "decide"), "actor": row["actor"],
            "kind": ev, "tool": None, "tone": tone, "title": title, "summary": summary, "detail": p}


def build(session: dict, rows: list[dict]) -> dict:
    """Timeline for one session. `rows` are audit rows for it, in any order."""
    rows = sorted(rows, key=lambda r: r["id"])
    events: list[dict] = []
    open_calls: list[dict] = []  # tool_call rows still waiting for their result
    pending: dict[int, dict] = {}  # call id -> result row
    for r in rows:
        if r["event"] == "tool_call":
            open_calls.append(r)
        elif r["event"] == "tool_result":
            tool = (r.get("payload") or {}).get("tool")
            for c in reversed(open_calls):
                if (c.get("payload") or {}).get("tool") == tool and c["actor"] == r["actor"]:
                    open_calls.remove(c)
                    pending[c["id"]] = r
                    break
    for r in rows:
        if r["event"] == "tool_call":
            events.append(_tool_event(r, pending.get(r["id"])))
        elif r["event"] not in ("tool_result", *_FOLDED):
            e = _plain_event(r)
            if e:
                events.append(e)

    # A turn is in progress while the last officer message has no reply yet, or a tool is still running.
    last_msg = max((e["id"] for e in events if e["kind"] == "message"), default=None)
    replied = last_msg is not None and any(e["id"] > last_msg and e["kind"] in ("llm_reply", "error")
                                           for e in events)
    running = any(e["tone"] == "running" for e in events) or (last_msg is not None and not replied)

    state = session.get("state") or sessions.INGESTING
    stage = STATE_STAGE.get(state, "decide")
    if running and events:
        stage = events[-1]["stage"]  # follow the agent while it works
    current = _ORDER[stage]
    stages = []
    for s in STAGES:
        mine = [e for e in events if e["stage"] == s["id"]]
        i = _ORDER[s["id"]]
        stages.append({**s, "status": "done" if i < current else "current" if i == current else "upcoming",
                       "events": len(mine),
                       "tools": dict(Counter(e["tool"] for e in mine if e["tool"]))})
    return {"session_id": session["session_id"], "state": state, "stage": stage, "running": running,
            "stages": stages, "events": events}
