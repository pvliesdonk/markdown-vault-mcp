"""Thread-safety tests for Collection / FTSIndex.

Per issue #519: verifies the per-thread connection model. These tests MUST
pass on Python 3.11, 3.12, 3.13, and 3.14 (run via tox; see tox.ini).
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

import pytest


@pytest.fixture
def multi_thread_collection(tmp_collection_path):
    """Tempfile-backed Collection for multi-thread tests (:memory: is per-connection; issue #519)."""
    from markdown_vault_mcp.collection import Collection

    coll = Collection(
        source_dir=tmp_collection_path,
        index_path=tmp_collection_path.parent / "index.db",
        read_only=False,
    )
    coll.build_index()
    for i in range(5):
        (tmp_collection_path / f"seed_{i}.md").write_text(f"# Seed {i}\n\nbody {i}\n")
    coll.reindex()
    yield coll
    coll.close()


def test_conn_is_per_thread(tmp_collection):
    """_conn() returns identical connection within a thread, distinct across threads."""
    fts = tmp_collection._fts  # FTSIndex instance
    main_conn_1 = fts._conn()
    main_conn_2 = fts._conn()
    assert main_conn_1 is main_conn_2, "same thread should reuse one connection"

    captured: dict[str, Any] = {}

    def worker() -> None:
        captured["worker_conn"] = fts._conn()

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert captured["worker_conn"] is not main_conn_1, (
        "worker thread should get a distinct connection"
    )


def test_registry_tracks_per_thread_connections(tmp_collection):
    """_all_conns contains one entry per thread that touched _conn()."""
    fts = tmp_collection._fts
    initial = len(fts._all_conns)

    def worker() -> None:
        fts._conn()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(fts._all_conns) == initial + 3


def test_pragmas_applied_per_connection(multi_thread_collection):
    """Worker-thread connections see foreign_keys, busy_timeout, synchronous, and persisted WAL."""
    fts = multi_thread_collection._fts
    captured: dict[str, Any] = {}

    def worker() -> None:
        conn = fts._conn()
        captured["foreign_keys"] = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        captured["busy_timeout"] = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        captured["synchronous"] = conn.execute("PRAGMA synchronous").fetchone()[0]
        captured["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert captured["foreign_keys"] == 1, "foreign_keys must be ON"
    assert captured["busy_timeout"] == 5000, "busy_timeout must be 5000ms"
    assert captured["synchronous"] == 1, "synchronous must be NORMAL (1)"
    assert captured["journal_mode"].lower() == "wal", "WAL must persist across opens"


def test_migrations_dont_rerun_on_per_thread_open(tmp_collection):
    """Per-thread open applies pragmas only — no ALTER/CREATE TABLE on worker connections."""
    fts = tmp_collection._fts
    traced: list[str] = []

    def worker() -> None:
        conn = fts._conn()
        conn.set_trace_callback(traced.append)
        conn.execute("SELECT 1").fetchone()
        conn.set_trace_callback(None)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    for stmt in traced:
        assert "ALTER TABLE" not in stmt.upper(), (
            f"per-thread open must not re-run migrations; saw: {stmt!r}"
        )
        assert "CREATE TABLE" not in stmt.upper(), (
            f"per-thread open must not re-run schema; saw: {stmt!r}"
        )


def test_close_closes_all_per_thread_connections(tmp_collection_path):
    """After close(), every per-thread connection raises on next op."""
    from markdown_vault_mcp.collection import Collection

    coll = Collection(source_dir=tmp_collection_path)
    coll.build_index()
    fts = coll._fts

    captured: dict[str, sqlite3.Connection] = {}

    def worker() -> None:
        captured["worker_conn"] = fts._conn()

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    main_conn = fts._conn()
    coll.close()

    with pytest.raises(sqlite3.ProgrammingError):
        main_conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        captured["worker_conn"].execute("SELECT 1")

    assert fts._all_conns == []


def test_close_is_idempotent(tmp_collection_path):
    """Calling close() twice is safe."""
    from markdown_vault_mcp.collection import Collection

    coll = Collection(source_dir=tmp_collection_path)
    coll.build_index()
    coll.close()
    coll.close()  # must not raise


def test_concurrent_build_and_reads_pr518_pattern(multi_thread_collection):
    """PR #518 failure pattern: background reindex + main thread mixed ops, no errors (issue #519)."""
    coll = multi_thread_collection
    errors: list[BaseException] = []
    stop = threading.Event()

    def background_indexer() -> None:
        try:
            for _ in range(10):
                if stop.is_set():
                    return
                coll.reindex()
        except BaseException as exc:
            errors.append(exc)

    def main_thread_ops() -> None:
        try:
            for i in range(100):
                coll.search("seed", limit=5)
                coll.list()
                if i % 10 == 0:
                    path = f"main_{i}.md"
                    coll.write(path, f"# main {i}\n\ncontent\n")
                if i % 20 == 10 and i > 10:
                    # Edit a path we wrote earlier this run.
                    coll.edit(
                        f"main_{i - 10}.md",
                        old_text="content",
                        new_text=f"content {i}",
                    )
        except BaseException as exc:
            errors.append(exc)

    t_bg = threading.Thread(target=background_indexer)
    t_main = threading.Thread(target=main_thread_ops)
    t_bg.start()
    t_main.start()
    t_main.join(timeout=60)
    stop.set()
    t_bg.join(timeout=30)

    assert not t_bg.is_alive(), "background indexer thread did not terminate"
    assert not t_main.is_alive(), "main-ops thread did not terminate"
    assert errors == [], f"concurrent ops raised errors: {errors}"


def test_concurrent_writers_serialize_via_write_lock(multi_thread_collection):
    """Two worker threads writing distinct paths concurrently — no losses, no errors."""
    coll = multi_thread_collection
    errors: list[BaseException] = []

    def writer(prefix: str, n: int) -> None:
        try:
            for i in range(n):
                coll.write(f"{prefix}_{i}.md", f"# {prefix} {i}\n\nbody\n")
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=writer, args=("a", 20))
    t2 = threading.Thread(target=writer, args=("b", 20))
    t1.start()
    t2.start()
    t1.join(timeout=60)
    t2.join(timeout=60)

    assert errors == [], f"concurrent writes raised: {errors}"
    docs = {d.path for d in coll.list()}
    expected = {f"a_{i}.md" for i in range(20)} | {f"b_{i}.md" for i in range(20)}
    assert expected.issubset(docs), f"missing writes: {expected - docs}"
