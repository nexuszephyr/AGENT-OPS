"""Audit log helper (AGENTS.md rule 6: everything the agent does is logged)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.privacy import redact_full_ids

ACTORS = {"agent", "officer", "system"}


def log(conn: sqlite3.Connection, session_id: str | None, actor: str, event: str, payload: Any = None) -> None:
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor}")
    # Tool args are logged before validation, so redact here: a rejected full Aadhaar
    # number must not end up in the audit trail either (AGENTS.md rule 5).
    safe = redact_full_ids(json.loads(json.dumps(payload, ensure_ascii=False, default=str)))
    conn.execute(
        "INSERT INTO audit_log (session_id, actor, event, payload_json) VALUES (?, ?, ?, ?)",
        (session_id, actor, event, json.dumps(safe, ensure_ascii=False)),
    )
    conn.commit()


def fetch(conn: sqlite3.Connection, session_id: str | None = None, limit: int = 500) -> list[dict]:
    q = "SELECT id, ts, session_id, actor, event, payload_json FROM audit_log"
    args: tuple = ()
    if session_id:
        q += " WHERE session_id = ?"
        args = (session_id,)
    q += " ORDER BY id DESC LIMIT ?"
    rows = conn.execute(q, (*args, limit)).fetchall()
    return [
        {**{k: r[k] for k in ("id", "ts", "session_id", "actor", "event")},
         "payload": json.loads(r["payload_json"]) if r["payload_json"] else None}
        for r in rows
    ]
