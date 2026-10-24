"""PR #44 review: the API's one sqlite3 connection is shared by worker threads, and the live timeline polls it
while an agent turn writes. Plain sqlite3.Connection breaks under that (InterfaceError, "another row available",
or another thread's rows); db.SerializedConnection must not."""
import threading

from app import audit, db, sessions, timeline
from app.data import seed


def test_shared_connection_survives_concurrent_reads_and_writes():
    conn = db.connect(":memory:")
    seed.seed(conn)
    sids = [sessions.create(conn, cid, "officer-1")["session_id"] for cid in ("C001", "C002", "C003", "C004")]
    errors: list[str] = []
    start = threading.Barrier(8)

    def writer(sid: str) -> None:  # an agent turn: audit rows + session updates, committed one by one
        start.wait()
        try:
            for i in range(150):
                audit.log(conn, sid, "agent", "tool_call", {"tool": "check_eligibility", "args": {"i": i}})
                sessions.update(conn, sid, questions_asked=i % 5)
        except Exception as e:  # noqa: BLE001 - collected and asserted below
            errors.append(f"writer: {type(e).__name__}: {e}")

    def reader(sid: str) -> None:  # the live timeline + state polls
        start.wait()
        try:
            for _ in range(150):
                s = sessions.get(conn, sid)
                assert s["session_id"] == sid, f"read {s['session_id']} for {sid}"  # never another thread's row
                timeline.build(s, audit.fetch(conn, session_id=sid, limit=500))
        except Exception as e:  # noqa: BLE001 - collected and asserted below
            errors.append(f"reader: {type(e).__name__}: {e}")

    threads = [threading.Thread(target=f, args=(sid,)) for sid in sids for f in (writer, reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert all(len(audit.fetch(conn, session_id=sid, limit=1000)) == 150 for sid in sids)


def test_results_behave_like_a_cursor():
    conn = db.connect(":memory:")
    seed.seed(conn)
    cur = conn.execute("SELECT customer_id FROM customers ORDER BY customer_id")
    assert cur.fetchone()["customer_id"] == "C001"
    assert [r["customer_id"] for r in cur.fetchmany(2)] == ["C002", "C003"]
    assert [r["customer_id"] for r in cur] == ["C004", "C005", "C006"]
    assert cur.fetchone() is None and cur.fetchall() == []
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 6
