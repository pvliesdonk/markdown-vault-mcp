"""Unit tests for IndexWriteCoordinator."""

from __future__ import annotations

import threading
from concurrent.futures import Future
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.indexing import IndexWriteCoordinator
from markdown_vault_mcp.managers.index import IndexManager
from markdown_vault_mcp.scanner import HeadingChunker
from markdown_vault_mcp.tracker import ChangeTracker

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.vector_index import VectorIndex


def make_coordinator(tmp_path: Path) -> IndexWriteCoordinator:
    """Build a wired coordinator over a tmp vault (mirrors Collection wiring)."""
    (tmp_path / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    db = tmp_path / "index.db"
    fts = FTSIndex(db_path=db)
    tracker = ChangeTracker(tmp_path / ".state.json")
    holder: dict[str, VectorIndex | None] = {"v": None}
    index_mgr = IndexManager(
        fts=fts,
        tracker=tracker,
        source_dir=tmp_path,
        embeddings_path=None,
        embedding_provider=None,
        chunk_strategy=HeadingChunker(max_chunk_words=400),
        exclude_patterns=None,
        required_frontmatter=None,
        indexed_frontmatter_fields=[],
        get_vectors=lambda: holder["v"],
        set_vectors=lambda v: holder.__setitem__("v", v),
    )
    return IndexWriteCoordinator(
        fts=fts,
        index_mgr=index_mgr,
        index_path=db,
        file_write_lock=threading.RLock(),
    )


def test_build_index_makes_queryable(tmp_path: Path) -> None:
    coord = make_coordinator(tmp_path)
    try:
        coord.build_index()
        assert coord.is_queryable() is True
        status = coord.get_index_status()
        assert status["status"] == "queryable"
        assert set(status) >= {
            "status",
            "documents_indexed",
            "error",
            "last_reindex_error",
            "last_build_embeddings_error",
            "queue_depth",
            "in_flight",
            "dirty_paths",
            "dirty_embeddings",
            "write_generation",
        }
    finally:
        coord.close(timeout=5)


def test_reindex_before_build_raises_never_built(tmp_path: Path) -> None:
    coord = make_coordinator(tmp_path)
    try:
        with pytest.raises(IndexUnavailableError) as ei:
            coord.reindex()
        assert ei.value.reason == "never_built"
    finally:
        coord.close(timeout=5)


def test_build_index_async_warm_restart_short_circuits(tmp_path: Path) -> None:
    coord = make_coordinator(tmp_path)
    try:
        coord.build_index()  # populate + set sentinel
        fut = coord.build_index_async()  # warm: already-resolved, no new job
        assert fut.done()
        assert coord.is_queryable() is True
    finally:
        coord.close(timeout=5)


def test_rebuild_embeddings_bypasses_require_built(tmp_path: Path) -> None:
    # Invariant 2: the search-recovery rebuild path bypasses require_built.
    # Never built + no provider -> must hit the provider ValueError, NOT
    # IndexUnavailableError (which require_built would raise).
    coord = make_coordinator(tmp_path)
    try:
        with pytest.raises(ValueError):
            coord.rebuild_embeddings()
    finally:
        coord.close(timeout=5)


def test_mark_paths_dirty_after_close_is_swallowed(tmp_path: Path) -> None:
    # Invariant 4: marks survive on the writer set; submit-after-close
    # RuntimeError is swallowed iff the writer is closed.
    coord = make_coordinator(tmp_path)
    coord.build_index()
    coord.close(timeout=5)
    coord.mark_paths_dirty(["a.md"])  # must not raise
    assert "a.md" in coord.writer.snapshot_dirty_paths()


def test_get_index_status_failed_when_error_and_not_built(tmp_path: Path) -> None:
    coord = make_coordinator(tmp_path)
    try:
        coord._readiness.fail_build(RuntimeError("scan failed"))
        status = coord.get_index_status()
        assert status["status"] == "failed"
        assert "scan failed" in status["error"]
    finally:
        coord.close(timeout=5)


def _raising_runner(job: object, ctx: object) -> object:  # noqa: ARG001
    raise RuntimeError("scan boom")


def test_sync_build_failure_records_failed_status(tmp_path: Path) -> None:
    # #585: a build-job failure must be recorded via fail_build so the
    # collection reports "failed" (not stay "building" with the error lost).
    coord = make_coordinator(tmp_path)
    try:
        coord.writer._runners["build_index"] = _raising_runner
        with pytest.raises(RuntimeError, match="scan boom"):
            coord.build_index()
        status = coord.get_index_status()
        assert status["status"] == "failed"
        assert status["error"] is not None and "scan boom" in status["error"]
        assert coord.is_queryable() is False
    finally:
        coord.close(timeout=5)


def test_async_build_set_completed_failure_records_failed_status(
    tmp_path: Path,
) -> None:
    # #585: if set_build_completed() raises inside the async done-callback,
    # the failure must be recorded — not silently swallowed by the Future
    # machinery, which would leave the collection stuck reporting "building".
    coord = make_coordinator(tmp_path)
    try:

        def _boom() -> None:
            raise RuntimeError("sentinel boom")

        coord._fts.set_build_completed = _boom  # type: ignore[method-assign]
        fut = coord.build_index_async()
        fut.result(timeout=5)  # the build job itself succeeds
        # The done-callback runs on the writer thread and fires AFTER the
        # Future wakes its waiters; wait for the writer to go idle so
        # _on_build_index_done's fail_build() has completed before we read status.
        coord.wait_for_drain(timeout=5)
        status = coord.get_index_status()
        assert status["status"] == "failed"
        assert status["error"] is not None and "sentinel boom" in status["error"]
        assert coord.is_queryable() is False
    finally:
        coord.close(timeout=5)


def test_sync_build_set_completed_failure_records_failed_status(
    tmp_path: Path,
) -> None:
    # #585: the sync try wraps BOTH submit().result() AND set_build_completed();
    # a sentinel-write failure after a successful build must also record "failed"
    # (guards against a refactor moving set_build_completed out of the try).
    coord = make_coordinator(tmp_path)
    try:

        def _boom() -> None:
            raise RuntimeError("sync sentinel boom")

        coord._fts.set_build_completed = _boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="sync sentinel boom"):
            coord.build_index()
        status = coord.get_index_status()
        assert status["status"] == "failed"
        assert status["error"] is not None and "sync sentinel boom" in status["error"]
        assert coord.is_queryable() is False
    finally:
        coord.close(timeout=5)


def test_async_build_job_failure_records_failed_status(tmp_path: Path) -> None:
    # #585: a cold async build-job failure (the runner raises) must be recorded
    # via _on_build_index_done's fut.result() guard -> status "failed", not "building".
    coord = make_coordinator(tmp_path)
    try:
        coord.writer._runners["build_index"] = _raising_runner
        fut = coord.build_index_async()
        with pytest.raises(RuntimeError, match="scan boom"):
            fut.result(timeout=5)
        coord.wait_for_drain(timeout=5)
        status = coord.get_index_status()
        assert status["status"] == "failed"
        assert status["error"] is not None and "scan boom" in status["error"]
        assert coord.is_queryable() is False
    finally:
        coord.close(timeout=5)


class _BaseBoom(BaseException):
    pass


@pytest.mark.filterwarnings(
    # The propagating BaseException intentionally terminates the writer thread.
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)
def test_async_set_completed_base_exception_does_not_strand(
    tmp_path: Path,
) -> None:
    # #585: a BaseException from set_build_completed is NOT recorded as a build
    # failure (that conflation is #584's scope) and propagates so the worker can
    # respond to a real signal — but _on_build_index_done's `finally` sets done so
    # waiters never hang.
    coord = make_coordinator(tmp_path)
    try:

        def _boom() -> None:
            raise _BaseBoom("base sentinel boom")

        coord._fts.set_build_completed = _boom  # type: ignore[method-assign]
        fut = coord.build_index_async()
        fut.result(timeout=5)  # the build job itself succeeds
        coord.wait_for_drain(timeout=5)
        # done-event set by the finally -> wait_until_queryable returns promptly
        # (never_built) instead of hanging to its timeout.
        with pytest.raises(IndexUnavailableError) as ei:
            coord.wait_until_queryable(timeout=2)
        assert ei.value.reason == "never_built"
        # the BaseException is not recorded as a build failure (#584 scope)
        assert coord.get_index_status()["status"] != "failed"
    finally:
        coord.close(timeout=5)


def test_build_index_recovers_after_failure(tmp_path: Path) -> None:
    # #585: after a recorded failure, a successful retry must clear the error
    # and restore "queryable" — mark_built() clears _error on success. Locks
    # the failure->recovery half of the contract.
    coord = make_coordinator(tmp_path)
    try:
        original = coord.writer._runners["build_index"]
        coord.writer._runners["build_index"] = _raising_runner
        with pytest.raises(RuntimeError, match="scan boom"):
            coord.build_index()
        assert coord.get_index_status()["status"] == "failed"

        coord.writer._runners["build_index"] = original
        coord.build_index()
        status = coord.get_index_status()
        assert status["status"] == "queryable"
        assert status["error"] is None
        assert coord.is_queryable() is True
    finally:
        coord.close(timeout=5)


def test_on_reindex_done_ignores_cancellation(tmp_path: Path) -> None:
    # #584: a cancelled reindex Future (e.g. writer-shutdown drain) must NOT be
    # recorded as a reindex failure surfaced via get_index_status.
    coord = make_coordinator(tmp_path)
    try:
        fut: Future[object] = Future()
        assert fut.cancel()
        coord._on_reindex_done(fut)
        assert coord._last_reindex_error is None
        assert coord.get_index_status()["last_reindex_error"] is None
    finally:
        coord.close(timeout=5)


def test_on_build_embeddings_done_ignores_cancellation(tmp_path: Path) -> None:
    # #584: a cancelled build_embeddings Future must NOT be recorded as a failure.
    coord = make_coordinator(tmp_path)
    try:
        fut: Future[object] = Future()
        assert fut.cancel()
        coord._on_build_embeddings_done(fut)
        assert coord._last_build_embeddings_error is None
        assert coord.get_index_status()["last_build_embeddings_error"] is None
    finally:
        coord.close(timeout=5)


def test_on_reindex_done_records_genuine_failure(tmp_path: Path) -> None:
    # #584: a genuine (non-cancellation) reindex failure is still recorded.
    coord = make_coordinator(tmp_path)
    try:
        fut: Future[object] = Future()
        fut.set_exception(RuntimeError("real reindex boom"))
        coord._on_reindex_done(fut)
        assert coord._last_reindex_error is not None
        assert "real reindex boom" in str(coord._last_reindex_error)
    finally:
        coord.close(timeout=5)


class _CallbackBaseBoom(BaseException):
    pass


def test_on_reindex_done_records_baseexception_without_propagating(
    tmp_path: Path,
) -> None:
    # #584: a non-cancellation BaseException (e.g. a runner raising SystemExit)
    # must be recorded AND must not propagate out of the callback. The callback
    # runs on the writer thread inside set_exception (index_writer.py:306, inside
    # the writer's own `except BaseException`); a propagating exception there
    # skips the writer's close-and-drain block, stranding pending futures.
    coord = make_coordinator(tmp_path)
    try:
        fut: Future[object] = Future()
        fut.set_exception(_CallbackBaseBoom("signal in runner"))
        coord._on_reindex_done(fut)  # must NOT raise
        assert isinstance(coord._last_reindex_error, _CallbackBaseBoom)
    finally:
        coord.close(timeout=5)


def test_on_build_embeddings_done_records_baseexception_without_propagating(
    tmp_path: Path,
) -> None:
    # #584: symmetric to the reindex callback — a non-cancellation BaseException
    # must be recorded without propagating (writer-stranding) out of the callback.
    coord = make_coordinator(tmp_path)
    try:
        fut: Future[object] = Future()
        fut.set_exception(_CallbackBaseBoom("signal in runner"))
        coord._on_build_embeddings_done(fut)  # must NOT raise
        assert isinstance(coord._last_build_embeddings_error, _CallbackBaseBoom)
    finally:
        coord.close(timeout=5)


def test_on_build_embeddings_done_records_genuine_failure(tmp_path: Path) -> None:
    # #584: symmetric to test_on_reindex_done_records_genuine_failure — a genuine
    # (non-cancellation) build_embeddings Exception is still recorded.
    coord = make_coordinator(tmp_path)
    try:
        fut: Future[object] = Future()
        fut.set_exception(RuntimeError("real build_embeddings boom"))
        coord._on_build_embeddings_done(fut)
        assert coord._last_build_embeddings_error is not None
        assert "real build_embeddings boom" in str(coord._last_build_embeddings_error)
    finally:
        coord.close(timeout=5)


def test_on_build_index_done_ignores_cancellation(tmp_path: Path) -> None:
    # #590: a cancelled BuildIndex future (writer-shutdown drain) must NOT flip the
    # readiness status to "failed" — unlike the diagnostic-only reindex/embeddings
    # errors (separate last_*_error fields), fail_build() drives the top-level status.
    coord = make_coordinator(tmp_path)
    try:
        fut: Future[object] = Future()
        assert fut.cancel()
        coord._on_build_index_done(fut)  # must NOT raise
        status = coord.get_index_status()
        assert status["status"] != "failed"
        assert status["error"] is None
    finally:
        coord.close(timeout=5)


def test_on_build_index_done_records_genuine_failure(tmp_path: Path) -> None:
    # #590: a genuine (non-cancellation) build failure must still drive
    # fail_build -> status "failed" (guards the carve-out from over-swallowing).
    coord = make_coordinator(tmp_path)
    try:
        fut: Future[object] = Future()
        fut.set_exception(RuntimeError("real build boom"))
        coord._on_build_index_done(fut)
        status = coord.get_index_status()
        assert status["status"] == "failed"
        assert status["error"] is not None and "real build boom" in status["error"]
        assert coord.is_queryable() is False
    finally:
        coord.close(timeout=5)


def test_on_build_index_done_records_baseexception(tmp_path: Path) -> None:
    # #590/#585: a genuine non-cancellation BaseException from the build job must
    # still drive fail_build -> status "failed". Pins the `except BaseException`
    # breadth on the build callback (narrowing it to `except Exception` would let
    # a BaseException escape fail_build and strand the index at "building").
    coord = make_coordinator(tmp_path)
    try:
        fut: Future[object] = Future()
        fut.set_exception(_CallbackBaseBoom("base build boom"))
        coord._on_build_index_done(fut)  # must NOT raise
        status = coord.get_index_status()
        assert status["status"] == "failed"
        assert status["error"] is not None and "base build boom" in status["error"]
        assert coord.is_queryable() is False
    finally:
        coord.close(timeout=5)


def test_on_build_index_done_cancellation_unblocks_waiters(tmp_path: Path) -> None:
    # #590: when a BuildIndex is cancelled mid-drain (done-event cleared by
    # begin_async_build, the realistic pre-drain state), the carve-out's
    # `finally: mark_done()` must still fire so a waiter unblocks to never_built
    # instead of hanging to its timeout. Pins the liveness invariant the
    # ignores_cancellation test misses (it starts from the pre-set done-event).
    coord = make_coordinator(tmp_path)
    try:
        coord._readiness.begin_async_build()  # clears _done
        fut: Future[object] = Future()
        assert fut.cancel()
        coord._on_build_index_done(fut)
        with pytest.raises(IndexUnavailableError) as ei:
            coord.wait_until_queryable(timeout=2)
        assert ei.value.reason == "never_built"
        assert coord.get_index_status()["status"] != "failed"
    finally:
        coord.close(timeout=5)
