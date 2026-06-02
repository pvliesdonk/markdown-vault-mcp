"""Unit tests for IndexWriteCoordinator."""

from __future__ import annotations

import threading
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
