"""SQLite connection and schema (docs/DATA_MODEL.md §2)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

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


class _FetchedCursor(sqlite3.Cursor):
    """A cursor whose rows were all fetched while the connection lock was held."""

    def __init__(self, conn: sqlite3.Connection, rows: list[Any]):
        super().__init__(conn)
        self._rows = rows
        self._i = 0

    def fetchone(self) -> Any:
        if self._i >= len(self._rows):
            return None
        self._i += 1
        return self._rows[self._i - 1]

    def fetchall(self) -> list[Any]:
        rest, self._i = self._rows[self._i:], len(self._rows)
        return rest

    def fetchmany(self, size: int | None = 1) -> list[Any]:
        n = size or 1
        chunk, self._i = self._rows[self._i:self._i + n], min(self._i + n, len(self._rows))
        return chunk

    def __iter__(self) -> _FetchedCursor:
        return self

    def __next__(self) -> Any:
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class SerializedConnection(sqlite3.Connection):
    """The API's one connection, shared by FastAPI's worker threads.

    SQLite itself is thread-safe here, but a Python `sqlite3.Connection` is not safe for concurrent use:
    two threads interleaving statements on it raise InterfaceError / "another row available", or read
    each other's rows (cached statements are shared). With the live timeline (#18) polling while an agent
    turn writes, that would happen every turn. So each call runs *and fetches* under one lock, and callers
    never hold a live cursor. The lock is per statement, not per request, so the timeline's reads
    interleave with a long agent turn instead of waiting for it.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._lock = threading.RLock()

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        with self._lock:
            rows = super().execute(sql, parameters).fetchall()
            return _FetchedCursor(self, rows)

    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:
        with self._lock:
            rows = super().executemany(sql, parameters).fetchall()
            return _FetchedCursor(self, rows)

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        with self._lock:
            return super().executescript(sql_script)

    def commit(self) -> None:
        with self._lock:
            super().commit()

    def rollback(self) -> None:
        with self._lock:
            super().rollback()


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, factory=SerializedConnection)
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
