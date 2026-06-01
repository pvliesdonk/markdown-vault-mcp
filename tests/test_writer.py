"""Tests for IndexWriter and Job dataclasses."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

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


def test_close_drains_pending_jobs():
    """Pending queued jobs run to completion before close() returns (#559)."""
    started = threading.Event()
    can_finish = threading.Event()
    runs: list[str] = []

    def slow_runner(job, ctx):  # noqa: ARG001
        started.set()
        can_finish.wait(timeout=5)
        runs.append("build_index")
        return None

    def fast_runner(job, ctx):  # noqa: ARG001
        runs.append("reindex_all")
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

    # Calling close() while slow is in-flight enqueues the shutdown
    # sentinel.  The worker drains FIFO: finishes slow, runs pending,
    # then pops the sentinel and exits.  Setting can_finish lets
    # slow_runner return so close() can join.
    close_thread = threading.Thread(target=writer.close, kwargs={"timeout": 5})
    close_thread.start()
    threading.Event().wait(0.05)
    can_finish.set()
    close_thread.join(timeout=5)

    slow_future.result(timeout=5)  # completes normally
    pending_future.result(timeout=5)  # also completes normally
    assert runs == ["build_index", "reindex_all"]


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


@dataclass
class _FakeIndexManager:
    build_index_calls: list[bool] = field(default_factory=list)
    reindex_calls: int = 0
    build_embeddings_calls: list[bool] = field(default_factory=list)
    process_paths_calls: list[set[str]] = field(default_factory=list)
    flush_paths_calls: list[set[str]] = field(default_factory=list)

    def build_index(self, *, force: bool):
        self.build_index_calls.append(force)
        return "index_stats"

    def reindex(self):
        self.reindex_calls += 1
        return "reindex_result"

    def build_embeddings(self, *, force: bool):
        self.build_embeddings_calls.append(force)
        return 42

    def process_dirty_paths(self, paths: set[str]) -> None:
        self.process_paths_calls.append(paths)

    def flush_dirty_embeddings(self, paths: set[str]) -> None:
        self.flush_paths_calls.append(paths)


def _make_real_writer(im: _FakeIndexManager) -> IndexWriter:
    from markdown_vault_mcp.writer import (
        WriterContext,
        run_build_embeddings,
        run_build_index,
        run_flush_dirty_embeddings,
        run_process_dirty_paths,
        run_reindex_all,
    )

    ctx = WriterContext(index_manager=im)
    writer = IndexWriter(
        runners={
            "build_index": run_build_index,
            "reindex_all": run_reindex_all,
            "build_embeddings": run_build_embeddings,
            "process_dirty_paths": run_process_dirty_paths,
            "flush_dirty_embeddings": run_flush_dirty_embeddings,
        },
        ctx=ctx,
    )
    ctx.writer = writer
    return writer


def test_build_index_job_calls_index_manager():
    im = _FakeIndexManager()
    writer = _make_real_writer(im)
    writer.start()
    try:
        result = writer.submit(BuildIndex(force=True)).result(timeout=5)
        assert result == "index_stats"
        assert im.build_index_calls == [True]
    finally:
        writer.close(timeout=5)


def test_reindex_all_job_calls_index_manager():
    im = _FakeIndexManager()
    writer = _make_real_writer(im)
    writer.start()
    try:
        result = writer.submit(ReindexAll()).result(timeout=5)
        assert result == "reindex_result"
        assert im.reindex_calls == 1
    finally:
        writer.close(timeout=5)


def test_build_embeddings_job_calls_index_manager():
    im = _FakeIndexManager()
    writer = _make_real_writer(im)
    writer.start()
    try:
        result = writer.submit(BuildEmbeddings(force=True)).result(timeout=5)
        assert result == 42
        assert im.build_embeddings_calls == [True]
    finally:
        writer.close(timeout=5)


def test_process_dirty_paths_drains_and_submits_flush():
    import time

    im = _FakeIndexManager()
    writer = _make_real_writer(im)
    writer.mark_dirty(["a.md", "b.md"])
    writer.start()
    try:
        writer.submit(ProcessDirtyPaths()).result(timeout=5)
        # Flush is auto-submitted; wait for queue drain.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and writer.get_status()["queue_depth"] > 0:
            time.sleep(0.01)
        # Give the worker a brief moment to finish processing the
        # auto-submitted FlushDirtyEmbeddings after the queue drains.
        deadline = time.monotonic() + 5
        while (
            time.monotonic() < deadline and writer.get_status()["in_flight"] is not None
        ):
            time.sleep(0.01)
        assert im.process_paths_calls == [{"a.md", "b.md"}]
        # ProcessDirtyPaths now propagates the FTS-dirty snapshot to the
        # writer's vector-dirty set so the auto-submitted
        # FlushDirtyEmbeddings re-embeds the same paths (#559).
        assert im.flush_paths_calls == [{"a.md", "b.md"}]
    finally:
        writer.close(timeout=5)


def test_process_dirty_paths_empty_set_is_noop():
    import time

    im = _FakeIndexManager()
    writer = _make_real_writer(im)
    writer.start()
    try:
        writer.submit(ProcessDirtyPaths()).result(timeout=5)
        # Wait for follow-up FlushDirtyEmbeddings to complete.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (
            writer.get_status()["queue_depth"] > 0
            or writer.get_status()["in_flight"] is not None
        ):
            time.sleep(0.01)
        # process_dirty_paths is called with an empty set; flush is still
        # submitted (and runs with empty set too).
        assert im.process_paths_calls == [set()]
    finally:
        writer.close(timeout=5)


def test_flush_dirty_embeddings_drains():
    im = _FakeIndexManager()
    writer = _make_real_writer(im)
    writer.mark_embedding_dirty(["x.md"])
    writer.start()
    try:
        writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
        assert im.flush_paths_calls == [{"x.md"}]
    finally:
        writer.close(timeout=5)
