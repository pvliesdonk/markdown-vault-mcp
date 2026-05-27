"""Thread-safety tests for Collection / FTSIndex.

Per issue #519: verifies the per-thread connection model. These tests MUST
pass on Python 3.11, 3.12, 3.13, and 3.14 (run via tox; see tox.ini).
"""

from __future__ import annotations

import threading


def test_conn_is_per_thread(tmp_collection):
    """index._conn() returns a different Connection object per thread.

    Same thread → same connection (identity). Different threads → different
    connections. This is the load-bearing invariant of the per-thread
    connection model.
    """
    fts = tmp_collection._fts  # FTSIndex instance
    main_conn_1 = fts._conn()
    main_conn_2 = fts._conn()
    assert main_conn_1 is main_conn_2, "same thread should reuse one connection"

    captured: dict[str, object] = {}

    def worker() -> None:
        captured["worker_conn"] = fts._conn()

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert captured["worker_conn"] is not main_conn_1, (
        "worker thread should get a distinct connection"
    )
