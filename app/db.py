"""SQLite connection and schema (docs/DATA_MODEL.md §2)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
  customer_id TEXT PRIMARY KEY,
  profile_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  officer_id TEXT NOT NULL,
  state TEXT NOT NULL,
  profile_overrides_json TEXT DEFAULT '{}',
  questions_asked INTEGER DEFAULT 0,
  asked_questions_json TEXT DEFAULT '[]',
  pending_question TEXT,
  messages_json TEXT DEFAULT '[]',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS queue_items (
  item_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(session_id),
  customer_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  product_id TEXT,
  action_id TEXT,
  payload_json TEXT NOT NULL,
  pdf_path TEXT,
  status TEXT NOT NULL,
  reject_reason TEXT,
  revision_count INTEGER DEFAULT 0,
  decided_by TEXT,
  decided_at TEXT,
  prepared_by TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT CURRENT_TIMESTAMP,
  session_id TEXT,
  actor TEXT NOT NULL,
  event TEXT NOT NULL,
  payload_json TEXT
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


# Columns added after the first release: added in place to an existing bank.db.
_MIGRATIONS = {
    "sessions": {
        "asked_questions_json": "TEXT DEFAULT '[]'",
        "pending_question": "TEXT",
    },
    "queue_items": {
        "prepared_by": "TEXT",  # the maker: session officer, or the officer who last revised it
    },
}


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, cols in _MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in list(d):
        if k.endswith("_json") and isinstance(d[k], str):
            d[k[:-5]] = json.loads(d.pop(k))
    return d
