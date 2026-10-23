"""Agent orchestrator: one officer message → one agent turn (docs/ARCHITECTURE.md).

If the LLM is unavailable, every entry point degrades to rules-only mode:
statuses, plan and next question still come from the deterministic modules.
"""
from __future__ import annotations

import json
import re
import sqlite3

from pydantic import ValidationError

from app import audit, queue, sessions
from app.agent import llm
from app.agent.prompts import (
    REVISION_PROMPT, TOOL_SCHEMAS, AgentReply, Highlight, Question, context_block, system_prompt,
)
from app.agent.tools import ToolBox
from app.rules import engine

HISTORY_TURNS = 12  # last N messages sent to the model


# --- entry points ------------------------------------------------------------------

def start_session(conn: sqlite3.Connection, customer_id: str, officer_id: str) -> dict:
    """Ingest + first evaluation. Deterministic: no LLM call needed to show statuses."""
    s = sessions.create(conn, customer_id, officer_id)
    audit.log(conn, s["session_id"], "system", "session_start",
              {"customer_id": customer_id, "officer_id": officer_id})
    sessions.update(conn, s["session_id"], state=sessions.EVALUATING)
    return sessions.state_view(conn, s["session_id"])


def handle_message(conn: sqlite3.Connection, session_id: str, text: str) -> dict:
    s = sessions.get(conn, session_id)
    _append(conn, session_id, "user", text)
    audit.log(conn, session_id, "officer", "message", {"text": text})
    reply = _agent_turn(conn, session_id, text)
    return _finish(conn, session_id, reply)


def handle_rejection(conn: sqlite3.Connection, item_id: str) -> dict:
    """After the officer rejects an item, let the agent attempt a revision."""
    item = queue.get(conn, item_id)
    sid = item["session_id"]
    if item["status"] != queue.REJECTED:  # e.g. escalated to NEEDS_OFFICER
        reply = AgentReply(to_officer=f"{item_id} needs the officer: revision limit reached.",
                           next_step="awaiting_approval", mode="rules_only")
        return _finish(conn, sid, reply)
    prompt = REVISION_PROMPT.format(
        item_id=item_id, kind=item["kind"], target=item["product_id"] or item["action_id"],
        reason=item["reject_reason"], payload_json=json.dumps(item["payload"], ensure_ascii=False))
    _append(conn, sid, "user", prompt)
    reply = _agent_turn(conn, sid, prompt)
    return _finish(conn, sid, reply)


def set_facts(conn: sqlite3.Connection, session_id: str, facts: dict, question_id: str | None = None) -> dict:
    """Officer enters facts directly (works without the LLM). Same validation as the agent tool."""
    out = ToolBox(conn, session_id, actor="officer").call(
        "update_profile", {"facts": facts, "evidence": "entered by officer"})
    if question_id:
        sessions.mark_asked(conn, session_id, question_id)
    state = sessions.state_view(conn, session_id)
    return {"result": out, "state": state}


def auto_act(conn: sqlite3.Connection, session_id: str, extra_fields: dict | None = None) -> dict:
    """Rules-only 'act' step: queue every actionable plan step. Used as the no-LLM fallback."""
    tb = ToolBox(conn, session_id, actor="agent")
    extra_fields = extra_fields or {}
    plan = tb.call("plan_actions", {})
    outcomes = []
    for st in plan["steps"]:
        if "after" in st:
            continue  # still blocked; it gets queued once its prerequisites are approved
        if st["type"] == "SERVICE_REQUEST":
            outcomes.append({"step": st["order"], **tb.call("raise_request", {"action_id": st["action_id"]})})
        else:
            pid = st["product_id"]
            outcomes.append({"step": st["order"], "product_id": pid,
                             **tb.call("fill_form", {"product_id": pid, "extra_fields": extra_fields.get(pid, {})})})
    return {"outcomes": outcomes, "state": sessions.state_view(conn, session_id)}


# --- internals ---------------------------------------------------------------------

def _agent_turn(conn: sqlite3.Connection, session_id: str, latest: str) -> AgentReply:
    state = sessions.state_view(conn, session_id)
    tb = ToolBox(conn, session_id)
    history = [m for m in state["messages"][-HISTORY_TURNS:] if m["role"] in ("user", "assistant")]
    while history and history[0]["role"] != "user":  # providers require a user turn first
        history.pop(0)
    # put fresh context in front of the latest user message
    if history and history[-1]["role"] == "user":
        history[-1] = {"role": "user", "content": context_block(state) + "\n\n" + history[-1]["content"]}
    else:
        history.append({"role": "user", "content": context_block(state) + "\n\n" + latest})
    try:
        raw = llm.run_tool_loop(system_prompt(state["profile"].get("preferred_language", "en")),
                                history, TOOL_SCHEMAS, tb.call)
        audit.log(conn, session_id, "agent", "llm_reply", {"raw": raw})
        return _parse_reply(raw)
    except llm.LLMError as e:
        audit.log(conn, session_id, "system", "error", {"llm": str(e), "fallback": "rules_only"})
        return rules_only_reply(conn, session_id, note=str(e))


def _parse_reply(raw: str) -> AgentReply:
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            return AgentReply.model_validate_json(m.group(0))
        except ValidationError:
            pass
    # not valid JSON: show the raw text; statuses still come from the rules engine
    return AgentReply(to_officer=raw or "(empty reply)")


def rules_only_reply(conn: sqlite3.Connection, session_id: str, note: str = "") -> AgentReply:
    state = sessions.state_view(conn, session_id)
    highlights = [Highlight(product_id=r["product_id"], status=r["status"],
                            because=[f["text"] for f in r["failed_rules"]] or
                                    [a for a in r["blocked_by"]] or
                                    [f"missing: {u}" for u in r["unknown_fields"]])
                  for r in state["results"]
                  if r["status"] not in (engine.ALREADY_ENROLLED,)]
    q = state["next_question"]["question"]
    question = Question(question_id=q["question_id"], text=q["text_en"]) if q else None
    msg = "Rules-only mode: statuses are from the rules engine."
    if note:
        msg += f" (LLM unavailable: {note})"
    return AgentReply(to_officer=msg, question=question, highlights=highlights,
                      next_step="awaiting_answer" if question else "awaiting_approval", mode="rules_only")


def _finish(conn: sqlite3.Connection, session_id: str, reply: AgentReply) -> dict:
    if reply.question:
        sessions.mark_asked(conn, session_id, reply.question.question_id)
    _append(conn, session_id, "assistant", reply.model_dump_json())
    return {"reply": reply.model_dump(), "state": sessions.state_view(conn, session_id)}


def _append(conn: sqlite3.Connection, session_id: str, role: str, content: str) -> None:
    s = sessions.get(conn, session_id)
    msgs = (s.get("messages") or []) + [{"role": role, "content": content}]
    sessions.update(conn, session_id, messages=msgs)
