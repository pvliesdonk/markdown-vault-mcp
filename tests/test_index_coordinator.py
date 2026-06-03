"""Unit tests for IndexWriteCoordinator."""

from __future__ import annotations

import logging
import sqlite3
import threading
from concurrent.futures import CancelledError, Future
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
            "documents_indexed_error",
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


def test_wait_until_queryable_build_failed_when_error(tmp_path: Path) -> None:
    # #586: after a build ran and FAILED (error captured), wait_until_queryable
    # must raise reason="build_failed" — distinct from never_built (a build was
    # scheduled and failed, not never started). The error is surfaced.
    coord = make_coordinator(tmp_path)
    try:
        coord._readiness.fail_build(RuntimeError("scan exploded"))
        with pytest.raises(IndexUnavailableError) as ei:
            coord.wait_until_queryable(timeout=0)
        assert ei.value.reason == "build_failed"
        assert "scan exploded" in str(ei.value)
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


def _raise_locked(*args: object, **kwargs: object) -> list[dict[str, object]]:  # noqa: ARG001
    raise sqlite3.OperationalError("database is locked")


def test_get_index_status_list_notes_failure_surfaces_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # #583: when list_notes() raises a sqlite3 error, the count stays 0 but
    # documents_indexed_error carries the reason (distinguishing empty from a
    # failing/locked DB), logged at WARNING (visible at the default level).
    coord = make_coordinator(tmp_path)
    try:
        coord.build_index()
        coord._fts.list_notes = _raise_locked  # type: ignore[method-assign]
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.indexing.coordinator"
        ):
            status = coord.get_index_status()
        assert status["documents_indexed"] == 0
        assert status["documents_indexed_error"] is not None
        assert "database is locked" in status["documents_indexed_error"]
        assert any(r.levelno >= logging.WARNING for r in caplog.records)
    finally:
        coord.close(timeout=5)


def test_get_index_status_documents_indexed_error_none_on_success(
    tmp_path: Path,
) -> None:
    # #583: a successful list_notes() read leaves documents_indexed_error None,
    # so an empty/normal index is distinguishable from a failing query.
    coord = make_coordinator(tmp_path)
    try:
        coord.build_index()
        status = coord.get_index_status()
        assert status["documents_indexed_error"] is None
    finally:
        coord.close(timeout=5)


def _raise_value_error(*args: object, **kwargs: object) -> list[dict[str, object]]:  # noqa: ARG001
    raise ValueError("unexpected bug")


def test_get_index_status_non_sqlite_error_propagates(tmp_path: Path) -> None:
    # #583: only sqlite3 errors are tolerated as "count unavailable". A
    # non-sqlite exception signals a bug and must propagate, not be swallowed.
    coord = make_coordinator(tmp_path)
    try:
        coord.build_index()
        coord._fts.list_notes = _raise_value_error  # type: ignore[method-assign]
        with pytest.raises(ValueError, match="unexpected bug"):
            coord.get_index_status()
    finally:
        coord.close(timeout=5)


def test_build_index_clears_stale_error_during_sync_rebuild(tmp_path: Path) -> None:
    # #587: a prior failed build leaves a stale _error; a sync build_index()
    # retry must report "building" (not the stale "failed") to a concurrent
    # status reader mid-rebuild. Pins the fix through the public build_index
    # entry point — the unit test exercises only ReadinessState directly.
    coord = make_coordinator(tmp_path)
    try:
        coord._readiness.fail_build(RuntimeError("stale async failure"))
        assert coord.get_index_status()["status"] == "failed"

        started = threading.Event()
        proceed = threading.Event()
        original = coord.writer._runners["build_index"]

        def _gated(job: object, ctx: object) -> object:
            started.set()
            proceed.wait(timeout=5)
            return original(job, ctx)

        coord.writer._runners["build_index"] = _gated
        builder = threading.Thread(target=coord.build_index)
        builder.start()
        try:
            assert started.wait(timeout=5)
            # begin_sync_build has run: stale error cleared, done-event cleared.
            assert coord.get_index_status()["status"] == "building"
            # a concurrent waiter blocks for the active build (not a premature
            # never_built) because _done is cleared (#587, Gemini HIGH finding).
            with pytest.raises(IndexUnavailableError) as ei:
                coord.wait_until_queryable(timeout=0.1)
            assert ei.value.reason == "timeout"
        finally:
            proceed.set()
            builder.join(timeout=5)
        assert coord.get_index_status()["status"] == "queryable"
    finally:
        coord.close(timeout=5)


@pytest.mark.filterwarnings(
    # The propagating BaseException intentionally terminates the writer thread.
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)
def test_build_index_records_job_baseexception_as_failed(tmp_path: Path) -> None:
    # #591: a BaseException from the build job is recorded as "failed" and
    # re-raised; fail_build sets the done-event so a waiter unblocks (never hangs).
    coord = make_coordinator(tmp_path)
    try:

        def _base_runner(job: object, ctx: object) -> object:  # noqa: ARG001
            raise _BaseBoom("sync job base boom")

        coord.writer._runners["build_index"] = _base_runner
        with pytest.raises(_BaseBoom, match="sync job base boom"):
            coord.build_index()
        status = coord.get_index_status()
        assert status["status"] == "failed"
        assert status["error"] is not None and "sync job base boom" in status["error"]
        assert coord.is_queryable() is False
        # fail_build set the done-event, so a waiter unblocks immediately — and
        # to build_failed (a build ran and failed), not never_built (#586).
        # timeout=0 is a non-blocking state check.
        with pytest.raises(IndexUnavailableError) as ei:
            coord.wait_until_queryable(timeout=0)
        assert ei.value.reason == "build_failed"
    finally:
        coord.close(timeout=5)


def test_build_index_set_completed_baseexception_does_not_strand(
    tmp_path: Path,
) -> None:
    # #591: a BaseException from set_build_completed (the job already succeeded)
    # is NOT recorded as failed and propagates, but the finally sets the
    # done-event so waiters unblock to never_built (symmetric with async #585).
    coord = make_coordinator(tmp_path)
    try:

        def _boom() -> None:
            raise _BaseBoom("sync set_completed base boom")

        coord._fts.set_build_completed = _boom  # type: ignore[method-assign]
        with pytest.raises(_BaseBoom):
            coord.build_index()
        # not recorded as failed; the finally re-set the cleared done-event, so a
        # waiter unblocks to never_built (the build never completed). timeout=0 is
        # a non-blocking state check.
        assert coord.get_index_status()["status"] == "building"
        with pytest.raises(IndexUnavailableError) as ei:
            coord.wait_until_queryable(timeout=0)
        assert ei.value.reason == "never_built"
    finally:
        coord.close(timeout=5)


def test_build_index_drain_cancellation_not_recorded_as_failed(tmp_path: Path) -> None:
    # #590/#591: a drain CancelledError on the sync BuildIndex future (the writer
    # cancelled a queued build during shutdown) is NOT a build failure — skip
    # fail_build and re-raise, mirroring the async _on_build_index_done carve-out.
    coord = make_coordinator(tmp_path)
    try:
        cancelled: Future[object] = Future()
        cancelled.cancel()
        coord.writer.submit = lambda job: cancelled  # type: ignore[method-assign]  # noqa: ARG005
        with pytest.raises(CancelledError):
            coord.build_index()
        # not recorded as failed; the finally re-set the cleared done-event, so a
        # waiter unblocks to never_built (timeout=0 is a non-blocking state check).
        assert coord.get_index_status()["status"] == "building"
        with pytest.raises(IndexUnavailableError) as ei:
            coord.wait_until_queryable(timeout=0)
        assert ei.value.reason == "never_built"
    finally:
        coord.close(timeout=5)
