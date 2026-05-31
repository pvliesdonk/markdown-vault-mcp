"""Tests for IndexWriter and Job dataclasses."""

from __future__ import annotations

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
