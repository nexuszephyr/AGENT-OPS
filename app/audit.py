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


def _like(text: str) -> str:
    """A LIKE pattern matching `text` literally: % and _ in the search are not wildcards (escape char: !)."""
    return "%" + text.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def fetch(conn: sqlite3.Connection, session_id: str | None = None, limit: int | None = 500,
          events: tuple[str, ...] | list[str] | None = None, actor: str | None = None,
          search: str | None = None) -> list[dict]:
    """Newest first. Filters are ANDed: `events` (any of them), `actor`, and `search`, a case-insensitive
    substring of the event name or the (already redacted) payload. `limit=None` returns every matching row."""
    q = "SELECT id, ts, session_id, actor, event, payload_json FROM audit_log WHERE 1=1"
    args: list = []
    if session_id:
        q += " AND session_id = ?"
        args.append(session_id)
    if actor:
        q += " AND actor = ?"
        args.append(actor)
    if search:
        q += " AND (event LIKE ? ESCAPE '!' OR payload_json LIKE ? ESCAPE '!')"
        args += [_like(search), _like(search)]
    if events:
        q += f" AND event IN ({', '.join('?' * len(events))})"  # noqa: S608 - placeholders only
        args.extend(events)
    q += " ORDER BY id DESC"
    if limit is not None:
        q += " LIMIT ?"
        args.append(limit)
    rows = conn.execute(q, args).fetchall()
    return [
        {**{k: r[k] for k in ("id", "ts", "session_id", "actor", "event")},
         "payload": json.loads(r["payload_json"]) if r["payload_json"] else None}
        for r in rows
    ]


def facets(conn: sqlite3.Connection) -> dict:
    """Distinct values for the dashboard's audit filters, and the total number of rows."""
    def distinct(col: str) -> list:
        sql = f"SELECT DISTINCT {col} FROM audit_log WHERE {col} IS NOT NULL ORDER BY {col}"  # noqa: S608 - fixed names
        return [r[0] for r in conn.execute(sql)]

    return {"actors": distinct("actor"), "events": distinct("event"), "sessions": distinct("session_id"),
            "total": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]}
