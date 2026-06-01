"""Tests for IndexWriter and Job dataclasses."""

from __future__ import annotations

import threading

import pytest

from markdown_vault_mcp.writer import (
    BuildEmbeddings,
    BuildIndex,
    FlushDirtyEmbeddings,
    IndexWriter,
    ProcessDirtyPaths,
    ReindexAll,
)


def test_job_kinds_are_distinct():
    assert BuildIndex.kind == "build_index"
    assert ReindexAll.kind == "reindex_all"
    assert BuildEmbeddings.kind == "build_embeddings"
    assert ProcessDirtyPaths.kind == "process_dirty_paths"
    assert FlushDirtyEmbeddings.kind == "flush_dirty_embeddings"


def test_build_index_carries_force_flag():
    assert BuildIndex(force=False).force is False
    assert BuildIndex(force=True).force is True


def test_build_embeddings_carries_force_flag():
    assert BuildEmbeddings(force=False).force is False
    assert BuildEmbeddings(force=True).force is True


def _identity_runner(job, ctx):  # noqa: ARG001
    return job


def test_submit_returns_future_with_result():
    writer = IndexWriter(runners={"build_index": _identity_runner}, ctx=None)
    writer.start()
    try:
        future = writer.submit(BuildIndex(force=True))
        result = future.result(timeout=5)
        assert isinstance(result, BuildIndex)
        assert result.force is True
    finally:
        writer.close(timeout=5)


def test_jobs_execute_in_fifo_order():
    executed: list[str] = []

    def append_runner(job, ctx):  # noqa: ARG001
        executed.append(job.kind)
        return None

    writer = IndexWriter(
        runners={
            "build_index": append_runner,
            "reindex_all": append_runner,
            "build_embeddings": append_runner,
        },
        ctx=None,
    )
    writer.start()
    try:
        f1 = writer.submit(BuildIndex())
        f2 = writer.submit(ReindexAll())
        f3 = writer.submit(BuildEmbeddings())
        f1.result(timeout=5)
        f2.result(timeout=5)
        f3.result(timeout=5)
    finally:
        writer.close(timeout=5)

    assert executed == ["build_index", "reindex_all", "build_embeddings"]


def test_submit_after_close_raises():
    writer = IndexWriter(runners={"build_index": _identity_runner}, ctx=None)
    writer.start()
    writer.close(timeout=5)

    with pytest.raises(RuntimeError, match="closed"):
        writer.submit(BuildIndex())


def test_worker_survives_job_exception():
    runs: list[str] = []

    def raising_runner(job, ctx):  # noqa: ARG001
        raise ValueError("boom")

    def recording_runner(job, ctx):  # noqa: ARG001
        runs.append(job.kind)
        return None

    writer = IndexWriter(
        runners={
            "build_index": raising_runner,
            "reindex_all": recording_runner,
        },
        ctx=None,
    )
    writer.start()
    try:
        f1 = writer.submit(BuildIndex())
        f2 = writer.submit(ReindexAll())

        with pytest.raises(ValueError, match="boom"):
            f1.result(timeout=5)
        f2.result(timeout=5)
        assert runs == ["reindex_all"]
    finally:
        writer.close(timeout=5)


def test_close_cancels_pending_jobs():
    started = threading.Event()
    can_finish = threading.Event()

    def slow_runner(job, ctx):  # noqa: ARG001
        started.set()
        can_finish.wait(timeout=5)
        return None

    def fast_runner(job, ctx):  # noqa: ARG001
        return None

    writer = IndexWriter(
        runners={
            "build_index": slow_runner,
            "reindex_all": fast_runner,
        },
        ctx=None,
    )
    writer.start()
    slow_future = writer.submit(BuildIndex())
    pending_future = writer.submit(ReindexAll())

    started.wait(timeout=5)
    # Slow job is in-flight; pending_future is queued.

    from concurrent.futures import CancelledError

    # Calling close() while slow is in-flight drains pending under
    # _submit_lock. The drain cancels pending_future before the worker
    # finishes the slow job, so pending_future resolves with
    # CancelledError. Setting can_finish lets slow_runner return so
    # close() can join.
    close_thread = threading.Thread(target=writer.close, kwargs={"timeout": 5})
    close_thread.start()
    # Give close() a brief moment to acquire the lock and drain.
    threading.Event().wait(0.05)
    can_finish.set()
    close_thread.join(timeout=5)

    slow_future.result(timeout=5)  # completes normally
    with pytest.raises(CancelledError):
        pending_future.result(timeout=5)


def test_mark_dirty_adds_paths():
    writer = IndexWriter(runners={}, ctx=None)
    writer.mark_dirty(["a.md", "b.md"])
    writer.mark_dirty(["c.md"])
    assert writer.snapshot_dirty_paths() == {"a.md", "b.md", "c.md"}


def test_drain_dirty_paths_clears_and_returns():
    writer = IndexWriter(runners={}, ctx=None)
    writer.mark_dirty(["a.md", "b.md"])
    drained = writer.drain_dirty_paths()
    assert drained == {"a.md", "b.md"}
    assert writer.snapshot_dirty_paths() == set()


def test_mark_embedding_dirty_adds_paths():
    writer = IndexWriter(runners={}, ctx=None)
    writer.mark_embedding_dirty(["a.md"])
    writer.mark_embedding_dirty(["b.md", "a.md"])
    assert writer.snapshot_dirty_embeddings() == {"a.md", "b.md"}


def test_drain_dirty_embeddings_clears_and_returns():
    writer = IndexWriter(runners={}, ctx=None)
    writer.mark_embedding_dirty(["a.md", "b.md"])
    drained = writer.drain_dirty_embeddings()
    assert drained == {"a.md", "b.md"}
    assert writer.snapshot_dirty_embeddings() == set()


def test_get_status_empty_writer():
    writer = IndexWriter(runners={}, ctx=None)
    status = writer.get_status()
    assert status == {
        "queue_depth": 0,
        "in_flight": None,
        "dirty_paths": 0,
        "dirty_embeddings": 0,
    }


def test_get_status_reports_dirty_counts():
    writer = IndexWriter(runners={}, ctx=None)
    writer.mark_dirty(["a.md", "b.md", "c.md"])
    writer.mark_embedding_dirty(["a.md"])
    status = writer.get_status()
    assert status["dirty_paths"] == 3
    assert status["dirty_embeddings"] == 1


def test_get_status_reports_in_flight():
    started = threading.Event()
    can_finish = threading.Event()

    def slow_runner(job, ctx):  # noqa: ARG001
        started.set()
        can_finish.wait(timeout=5)
        return None

    writer = IndexWriter(
        runners={"build_index": slow_runner},
        ctx=None,
    )
    writer.start()
    try:
        future = writer.submit(BuildIndex())
        started.wait(timeout=5)
        status = writer.get_status()
        assert status["in_flight"] == "build_index"
        can_finish.set()
        future.result(timeout=5)
    finally:
        writer.close(timeout=5)
