"""Thread-safety contract tests for FTSIndex (issue #519).

Each test exercises one failure mode that the per-thread connection model
must address. Run individually with ``uv run pytest tests/test_thread_safety.py``.

The carryover design (per ``project_issue_519_attempt_1_abandon.md``) is:
per-thread ``sqlite3.Connection`` via ``threading.local``, strong-ref
registry guarded by ``_reg_lock``, ``_closed`` flag with double-checked
locking, ``BaseException`` cleanup on slow-path open, ``_primary_conn``
strong attribute, shared-cache URI translation for ``:memory:`` with
startup probe, and pragmas applied BEFORE schema/migrations.
"""

from __future__ import annotations

import gc
import sqlite3
import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.fts_index import FTSIndex
from tests.conftest import wait_for_writer_drain


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Filesystem-backed SQLite DB path for tests."""
    return tmp_path / "fts.sqlite"


@pytest.fixture
def fts(tmp_db: Path):
    """Tempfile-backed FTSIndex. Closed at teardown."""
    idx = FTSIndex(db_path=tmp_db)
    try:
        yield idx
    finally:
        idx.close()


@pytest.fixture
def fts_memory():
    """`:memory:` FTSIndex. Closed at teardown.

    Safe for multi-threaded tests under the new design because
    ``:memory:`` is translated to a shared-cache URI so every per-thread
    open joins the same in-memory DB.
    """
    idx = FTSIndex(db_path=":memory:")
    try:
        yield idx
    finally:
        idx.close()


@pytest.fixture
def close_tracker() -> tuple[list[object], type]:
    """Return (closed_list, tracker_class) for monkey-patching `sqlite3.connect`.

    The tracker class is a forwarding wrapper around a `sqlite3.Connection`
    that records every `close()` call into `closed_list`. Needed because
    `sqlite3.Connection.close` is a read-only attribute and cannot be
    monkey-patched directly.
    """
    closed: list[object] = []

    class _CloseTracker:
        _inner: sqlite3.Connection

        def __init__(self, inner: sqlite3.Connection) -> None:
            object.__setattr__(self, "_inner", inner)

        def close(self) -> None:
            closed.append(self._inner)
            self._inner.close()

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        def __setattr__(self, name: str, value: object) -> None:
            setattr(self._inner, name, value)

        def __enter__(self) -> sqlite3.Connection:
            return self._inner.__enter__()

        def __exit__(self, *args: object) -> object:
            return self._inner.__exit__(*args)  # type: ignore[arg-type]

    return closed, _CloseTracker


def _make_collection(tmp_path: Path) -> Collection:
    """Build a writeable file-backed Collection."""
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return Collection(
        source_dir=vault,
        index_path=tmp_path / "fts.sqlite",
        read_only=False,
    )


# ---------------------------------------------------------------------------
# Per-thread connection mechanics
# ---------------------------------------------------------------------------


def test_conn_is_per_thread(fts: FTSIndex) -> None:
    """`_conn()` returns the same object per thread, distinct objects across threads."""
    main = fts._conn()
    assert fts._conn() is main, "second call from same thread must return same conn"

    seen: dict[str, sqlite3.Connection] = {}

    def grab() -> None:
        seen["worker"] = fts._conn()
        seen["worker2"] = fts._conn()

    t = threading.Thread(target=grab)
    t.start()
    t.join()
    assert seen["worker"] is seen["worker2"], "worker thread reuses its TLS slot"
    assert seen["worker"] is not main, "worker conn must be distinct from main"


def test_pragmas_applied_per_connection(fts: FTSIndex) -> None:
    """Every per-thread conn has foreign_keys, busy_timeout, synchronous applied;
    WAL persists in the DB header for file-backed DBs."""

    def check(out: dict[str, object]) -> None:
        conn = fts._conn()
        out["foreign_keys"] = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        out["busy_timeout"] = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        out["synchronous"] = conn.execute("PRAGMA synchronous").fetchone()[0]
        out["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]

    out: dict[str, object] = {}
    t = threading.Thread(target=check, args=(out,))
    t.start()
    t.join()

    assert out["foreign_keys"] == 1
    assert out["busy_timeout"] == 5000
    assert out["synchronous"] == 1  # NORMAL
    assert str(out["journal_mode"]).lower() == "wal"


def test_pragmas_applied_before_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_db: Path
) -> None:
    """`_apply_pragmas` must run BEFORE `_init_schema` so busy_timeout is active
    during ALTER TABLE migrations.

    Capture the call order by patching both methods on the class.
    """
    order: list[str] = []
    real_apply = FTSIndex._apply_pragmas  # type: ignore[attr-defined]
    real_init = FTSIndex._init_schema  # type: ignore[attr-defined]

    def spy_apply(self, conn):  # type: ignore[no-untyped-def]
        order.append("pragmas")
        return real_apply(self, conn)

    def spy_init(self, conn):  # type: ignore[no-untyped-def]
        order.append("schema")
        return real_init(self, conn)

    monkeypatch.setattr(FTSIndex, "_apply_pragmas", spy_apply)
    monkeypatch.setattr(FTSIndex, "_init_schema", spy_init)

    idx = FTSIndex(db_path=tmp_db)
    try:
        assert order[:2] == ["pragmas", "schema"], (
            f"pragmas must precede schema; got {order}"
        )
    finally:
        idx.close()


def test_init_schema_runs_once_across_all_threads(
    monkeypatch: pytest.MonkeyPatch, tmp_db: Path
) -> None:
    """Schema/migrations run exactly once on the constructing thread; per-thread
    opens only apply pragmas."""
    call_count = {"n": 0}
    real = FTSIndex._init_schema  # type: ignore[attr-defined]

    def spy(self, conn):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        return real(self, conn)

    monkeypatch.setattr(FTSIndex, "_init_schema", spy)

    idx = FTSIndex(db_path=tmp_db)
    try:
        # Touch from 5 worker threads.
        def touch() -> None:
            idx._conn().execute("SELECT 1").fetchone()

        threads = [threading.Thread(target=touch) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert call_count["n"] == 1, (
            f"_init_schema must run exactly once; got {call_count['n']}"
        )
    finally:
        idx.close()


def test_close_closes_all_per_thread_connections(fts: FTSIndex) -> None:
    """After close(), every registered connection raises on use."""
    captured: list[sqlite3.Connection] = [fts._conn()]

    def grab() -> None:
        captured.append(fts._conn())

    t = threading.Thread(target=grab)
    t.start()
    t.join()
    assert len(captured) == 2

    fts.close()
    for conn in captured:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_close_is_idempotent(fts: FTSIndex) -> None:
    """Second close() is a no-op, not an error."""
    fts._conn()
    fts.close()
    fts.close()  # must not raise


def test_closed_index_rejects_new_thread_access(fts: FTSIndex) -> None:
    """After close(), a never-before-seen thread calling _conn() gets
    ProgrammingError — not a half-registered new connection."""
    fts._conn()
    fts.close()

    errors: list[BaseException] = []

    def attempt() -> None:
        try:
            fts._conn()
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=attempt)
    t.start()
    t.join()

    assert len(errors) == 1
    assert isinstance(errors[0], sqlite3.ProgrammingError)


def test_concurrent_close_and_new_thread_conn_open_is_safe(fts: FTSIndex) -> None:
    """A new thread calling _conn() racing with close() must end in either:
    (a) a usable conn that is then closed by close(), OR
    (b) ProgrammingError from the slow-path re-check.

    Neither outcome leaks a connection past close() or crashes the close()."""
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def opener() -> None:
        barrier.wait()
        try:
            c = fts._conn()
            c.execute("SELECT 1").fetchone()
            outcomes.append("opened")
        except sqlite3.ProgrammingError:
            outcomes.append("rejected")

    def closer() -> None:
        barrier.wait()
        fts.close()

    t_open = threading.Thread(target=opener)
    t_close = threading.Thread(target=closer)
    t_open.start()
    t_close.start()
    t_open.join()
    t_close.join()

    assert outcomes and outcomes[0] in {"opened", "rejected"}


def test_registry_uses_strong_refs(fts: FTSIndex) -> None:
    """Registry must hold strong refs so close() can close worker conns even
    after the worker thread has exited and gc has run.

    Weakrefs are explicitly REJECTED per ``feedback_519_weakref_whackamole.md``.
    """
    fts._conn()  # primary
    worker_id: list[int] = []

    def worker() -> None:
        worker_id.append(id(fts._conn()))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    del t
    gc.collect()

    # Registry still holds the worker conn.
    assert any(id(c) == worker_id[0] for c in fts._all_conns), (
        "worker conn must survive thread exit + gc in strong-ref registry"
    )


def test_memory_db_is_shared_across_threads(fts_memory: FTSIndex) -> None:
    """:memory: must be shared across threads via shared-cache URI translation.

    Pre-PR-#520, each thread's _conn() against ":memory:" got a separate
    empty DB and the schema was invisible. The translation must fix this.
    """

    errors: list[BaseException] = []

    def query(out: dict[str, object]) -> None:
        try:
            conn = fts_memory._conn()
            # If the per-thread conn sees an empty DB, this raises OperationalError.
            out["count"] = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        except BaseException as exc:
            errors.append(exc)

    out: dict[str, object] = {}
    t = threading.Thread(target=query, args=(out,))
    t.start()
    t.join()
    assert not errors, f"worker thread raised: {errors[0]!r}"
    assert out.get("count") == 0


def test_concurrent_writers_serialize_via_collection_write_lock(
    tmp_path: Path,
) -> None:
    """Two writer threads each writing 20 distinct paths through Collection
    produce 40 docs with no SQLITE_BUSY / lost writes."""
    coll = _make_collection(tmp_path)
    coll.index.build_index()

    errors: list[BaseException] = []

    def writer(prefix: str) -> None:
        for i in range(20):
            try:
                coll.writer.write(f"{prefix}-{i}.md", f"# {prefix}{i}\n\nbody\n")
            except BaseException as exc:
                errors.append(exc)

    t1 = threading.Thread(target=writer, args=("a",))
    t2 = threading.Thread(target=writer, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    try:
        assert not errors, f"writer errors: {errors!r}"
        wait_for_writer_drain(coll)
        docs = coll.reader.list_documents()
        assert len(docs) == 40
    finally:
        coll.close()


def test_concurrent_build_and_reads_pr518_pattern(tmp_path: Path) -> None:
    """The acceptance test for #519 (and prerequisite for #513).

    Background thread loops build_index(); main thread runs mixed read /
    search / write / edit concurrently. None of the cross-thread sqlite3
    errors that killed PRs #510/#515/#516/#518 must surface.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    # Seed a few notes so build_index has something to scan.
    for i in range(10):
        (vault / f"seed-{i}.md").write_text(f"# Seed {i}\n\ncontent {i}\n")

    coll = Collection(
        source_dir=vault,
        index_path=tmp_path / "fts.sqlite",
        read_only=False,
    )
    coll.index.build_index()

    errors: list[BaseException] = []
    stop = threading.Event()

    def background_build() -> None:
        for _ in range(5):
            if stop.is_set():
                return
            try:
                coll.index.build_index(force=True)
            except BaseException as exc:
                errors.append(exc)
                return

    def foreground_mix() -> None:
        try:
            for i in range(50):
                coll.reader.list_documents()
                coll.reader.search("content", limit=5)
                coll.writer.write(f"new-{i}.md", f"# New {i}\n\nbody\n")
                coll.reader.read(f"new-{i}.md")
                if i % 5 == 0:
                    coll.writer.edit(
                        f"new-{i}.md", f"# New {i}\n\nbody\n", f"# New {i}\n\nEDITED\n"
                    )
        except BaseException as exc:
            errors.append(exc)
        finally:
            stop.set()

    bg = threading.Thread(target=background_build)
    fg = threading.Thread(target=foreground_mix)
    bg.start()
    fg.start()
    fg.join(timeout=60)
    bg.join(timeout=10)
    # If either thread is still alive the join timed out — a deadlock would
    # otherwise leave errors empty and let the test silently pass.
    assert not fg.is_alive(), "foreground thread timed out — possible deadlock"
    assert not bg.is_alive(), "background thread timed out — possible deadlock"

    try:
        for exc in errors:
            assert not isinstance(
                exc,
                (
                    sqlite3.OperationalError,
                    sqlite3.InterfaceError,
                    sqlite3.ProgrammingError,
                ),
            ), f"cross-thread sqlite3 error: {exc!r}"
        assert not errors, f"unexpected errors: {errors!r}"
    finally:
        coll.close()


def test_init_baseexception_cleanup_closes_primary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_db: Path,
    close_tracker: tuple[list[object], type],
) -> None:
    """If _init_schema fails, the primary connection must be closed before
    the exception propagates — no leaked file handle."""

    closed, tracker_cls = close_tracker
    real_connect = sqlite3.connect

    def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        return tracker_cls(real_connect(*args, **kwargs))  # type: ignore[return-value,arg-type,no-any-return]

    monkeypatch.setattr(
        "markdown_vault_mcp.fts_index.sqlite3.connect", tracking_connect
    )

    def boom_schema(_self: object, _conn: object) -> None:
        raise RuntimeError("simulated schema failure")

    monkeypatch.setattr(FTSIndex, "_init_schema", boom_schema)

    with pytest.raises(RuntimeError, match="simulated schema failure"):
        FTSIndex(db_path=tmp_db)

    assert len(closed) == 1, (
        f"primary connection must be closed exactly once; got {len(closed)}"
    )


def test_init_baseexception_cleanup_clears_tls_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If __init__ raises AFTER the primary's TLS assignment (e.g. during
    `_probe_shared_cache`), the cleanup must clear ``self._local.conn`` —
    otherwise a caller holding the partially-built instance would fast-path
    a closed connection instead of seeing ProgrammingError.

    Uses the :memory: path so the probe runs; captures the partial instance
    via the patched method since __init__ never returns one to the caller.
    """
    captured: list[FTSIndex] = []

    def boom_probe(self: FTSIndex) -> None:
        captured.append(self)
        raise RuntimeError("simulated probe failure")

    monkeypatch.setattr(FTSIndex, "_probe_shared_cache", boom_probe)

    with pytest.raises(RuntimeError, match="simulated probe failure"):
        FTSIndex(db_path=":memory:")

    assert len(captured) == 1
    assert getattr(captured[0]._local, "conn", None) is None, (
        "__init__ cleanup must clear self._local.conn after post-TLS failure"
    )
    # _all_conns must also be cleared by the cleanup.
    assert captured[0]._all_conns == []
    assert captured[0]._primary_conn is None


def test_probe_shared_cache_raises_when_documents_invisible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a second connection to the shared-cache URI cannot see the
    schema, _probe_shared_cache must raise RuntimeError with an
    operator-actionable message — not let per-thread reads fail mysteriously
    downstream."""

    real_connect = sqlite3.connect
    call_count = {"n": 0}

    def fake_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        call_count["n"] += 1
        # Simulate SQLITE_ENABLE_SHARED_CACHE missing by giving the probe
        # (second connect) a fresh empty in-memory DB instead of joining
        # the shared cache.
        if call_count["n"] == 2:
            return real_connect(":memory:", check_same_thread=False)
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("markdown_vault_mcp.fts_index.sqlite3.connect", fake_connect)

    with pytest.raises(RuntimeError, match="shared-cache probe failed"):
        FTSIndex(db_path=":memory:")


def test_conn_slow_path_baseexception_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    fts: FTSIndex,
    close_tracker: tuple[list[object], type],
) -> None:
    """If _apply_pragmas fails on a per-thread open, the new connection
    must be closed and the TLS slot left unset so a retry re-opens."""

    closed, tracker_cls = close_tracker
    real_connect = sqlite3.connect

    def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        return tracker_cls(real_connect(*args, **kwargs))  # type: ignore[return-value,arg-type,no-any-return]

    monkeypatch.setattr(
        "markdown_vault_mcp.fts_index.sqlite3.connect", tracking_connect
    )

    def boom_pragmas(_self: object, _conn: object) -> None:
        raise RuntimeError("simulated pragma failure")

    monkeypatch.setattr(FTSIndex, "_apply_pragmas", boom_pragmas)

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            fts._conn()
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert len(closed) >= 1, "new per-thread connection must be closed on failure"


def test_conn_slow_path_failure_clears_tls_slot(fts: FTSIndex) -> None:
    """If the slow-path open fails AFTER ``self._local.conn`` was set but
    BEFORE registration completes (e.g. a BaseException between the two
    writes inside the lock), the cleanup MUST clear the TLS slot so the
    next ``_conn()`` call does not fast-path return a closed connection.

    Regression for the bug where ``self._local.conn = new_conn`` was
    assigned and a BaseException between that and ``_all_conns.append``
    left a closed conn in TLS for subsequent silent reuse.

    Injection method: patch ``list.append`` on ``_all_conns`` so the
    append raises after the TLS assignment has already happened.
    """
    append_calls = {"n": 0}

    class _ExplodingList(list):
        def append(self, item: object) -> None:
            append_calls["n"] += 1
            # First patched call is the worker's registration — fail after
            # the TLS slot has already been set inside the lock block.
            if append_calls["n"] == 1:
                raise RuntimeError("simulated failure between TLS set and append")
            super().append(item)

    # Swap registry with the exploding subclass, preserving the primary
    # connection entry the fixture registered before the patch.
    fts._all_conns = _ExplodingList(fts._all_conns)

    first_error: list[BaseException] = []
    second_result: list[sqlite3.Connection] = []

    def worker() -> None:
        try:
            fts._conn()
        except BaseException as exc:
            first_error.append(exc)
        # Subsequent call from the same thread must NOT fast-path return a
        # stale closed conn — it must go to the slow path and succeed.
        try:
            second_result.append(fts._conn())
        except BaseException as exc:
            first_error.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert len(first_error) == 1, f"unexpected errors: {first_error!r}"
    assert isinstance(first_error[0], RuntimeError)
    assert len(second_result) == 1
    # The recovered conn must be usable.
    second_result[0].execute("SELECT 1").fetchone()


def test_close_continues_when_one_connection_raises(fts: FTSIndex) -> None:
    """If one ``conn.close()`` raises a non-sqlite3 exception (e.g. OSError),
    ``close()`` must continue iterating and close the remaining connections —
    otherwise a single bad connection leaks the rest.

    Guards against narrowing the `except Exception:` in `close()` back to
    `except sqlite3.Error:` (the original spec wording).
    """
    # Force a second per-thread connection so the registry has > 1 entry.
    primary = fts._conn()
    second_holder: dict[str, sqlite3.Connection] = {}

    def grab() -> None:
        second_holder["c"] = fts._conn()

    t = threading.Thread(target=grab)
    t.start()
    t.join()
    second = second_holder["c"]
    assert primary is not second
    assert len(fts._all_conns) == 2

    # Replace one connection's close with a function that raises OSError.
    # We can't monkey-patch sqlite3.Connection.close (read-only attr), so we
    # swap the registry entry for a stand-in object that records close calls.
    real_close_calls: list[object] = []

    class _BadCloser:
        def close(self) -> None:
            raise OSError("simulated close failure")

    class _GoodCloser:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def close(self) -> None:
            real_close_calls.append(self._inner)
            self._inner.close()

    bad = _BadCloser()
    good = _GoodCloser(second)
    # Replace registry contents with [bad, good]. Drop the real primary +
    # second so they aren't double-closed by close() then by the fixture.
    fts._all_conns[:] = [bad, good]  # type: ignore[list-item]
    fts._primary_conn = None
    primary.close()  # close the original primary out-of-band

    # close() must reach `good` despite `bad` raising.
    fts.close()
    assert real_close_calls == [second], (
        "close() must continue past a connection whose close() raises"
    )
