"""Tests for the indexing-readiness policy from issue #525.

Collection methods fall into four buckets:

- Bucket 1 (never block): ``read``, ``write``, ``edit``, ``delete``,
  ``rename``, ``write_attachment``. Disk is the source of truth; the FTS
  index is a downstream consumer.
- Bucket 2 (partial + report): ``search``, ``list``, ``list_folders``,
  ``list_tags``, ``get_recent``, ``get_orphan_notes``,
  ``get_most_linked``, ``get_broken_links``, ``stats``. Query against
  current index state; never implicitly build.
- Bucket 3 (block / raise): ``get_backlinks``, ``get_outlinks``,
  ``get_similar``, ``get_context``, ``get_connection_path``,
  ``get_toc``. Silently wrong on a partial index → raise
  ``IndexUnavailableError`` pre-#513; block on a background-completion
  event post-#513. (``get_toc`` is FTS-backed: on cold start the FTS
  ``documents`` row is absent and the underlying manager would raise
  a misleading ``ValueError("Document not found")``.)
- Bucket 4 (coordinate): ``reindex``, ``build_embeddings``,
  ``build_index``. ``reindex`` and ``build_embeddings`` require a built
  index (raise ``IndexUnavailableError`` otherwise). ``build_index`` is
  the bootstrap; its warm-restart short-circuit uses persisted FTS
  state alone (no ``_initialized`` flag).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.exceptions import IndexUnavailableError
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _seed(vault: Path, name: str = "note.md", body: str = "# Note\n\nhello\n") -> Path:
    f = vault / name
    f.write_text(body)
    return f


# ---------------------------------------------------------------------------
# Bucket 1 — never block (read/write/edit/delete/rename/write_attachment)
# ---------------------------------------------------------------------------


class TestBucket1NeverBlock:
    def test_read_on_unbuilt_returns_disk_content(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault, "a.md", "# A\n\nbody\n")
        col = Collection(source_dir=vault)

        result = col.read("a.md")

        assert result is not None
        assert "body" in result.content

    def test_write_on_unbuilt_persists_to_disk(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        col = Collection(source_dir=vault, read_only=False)

        col.write("new.md", "# New\n\ncreated\n")

        assert (vault / "new.md").read_text().endswith("created\n")

    def test_edit_on_unbuilt_modifies_disk(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault, "e.md", "# E\n\nfoo\n")
        col = Collection(source_dir=vault, read_only=False)

        col.edit("e.md", old_text="foo", new_text="bar")

        assert "bar" in (vault / "e.md").read_text()

    def test_delete_on_unbuilt_removes_from_disk(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault, "d.md")
        col = Collection(source_dir=vault, read_only=False)

        col.delete("d.md")

        assert not (vault / "d.md").exists()

    def test_rename_on_unbuilt_moves_file(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault, "old.md")
        col = Collection(source_dir=vault, read_only=False)

        col.rename("old.md", "new.md")

        assert not (vault / "old.md").exists()
        assert (vault / "new.md").exists()

    def test_write_attachment_on_unbuilt_persists(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        col = Collection(
            source_dir=vault,
            read_only=False,
            attachment_extensions=["bin"],
        )

        col.write_attachment("blob.bin", b"\x00\x01\x02")

        assert (vault / "blob.bin").read_bytes() == b"\x00\x01\x02"


# ---------------------------------------------------------------------------
# Bucket 2 — partial + report (return empty/no-op on unbuilt; no implicit build)
# ---------------------------------------------------------------------------


class TestBucket2PartialReport:
    def test_search_on_unbuilt_returns_empty(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault, "a.md", "# A\n\nhello world\n")
        col = Collection(source_dir=vault)

        results = col.search("hello")

        assert results == []

    def test_list_on_unbuilt_returns_empty(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault, "a.md")
        _seed(vault, "b.md")
        col = Collection(source_dir=vault)

        notes = col.list_documents()

        assert notes == []

    def test_stats_on_unbuilt_reports_zero_documents(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)

        result = col.stats()

        assert result.document_count == 0


# ---------------------------------------------------------------------------
# Bucket 3 — block (raise IndexUnavailableError on unbuilt pre-#513)
# ---------------------------------------------------------------------------


class TestBucket3Block:
    def test_get_backlinks_on_unbuilt_raises(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.get_backlinks("note.md")
        assert excinfo.value.reason == "never_built"

    def test_get_outlinks_on_unbuilt_raises(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.get_outlinks("note.md")
        assert excinfo.value.reason == "never_built"

    def test_get_similar_on_unbuilt_raises(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(
            source_dir=vault,
            embedding_provider=MockEmbeddingProvider(),
            embeddings_path=tmp_path / "vectors",
        )

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.get_similar("note.md")
        assert excinfo.value.reason == "never_built"

    def test_get_context_on_unbuilt_raises(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.get_context("note.md")
        assert excinfo.value.reason == "never_built"

    def test_get_connection_path_on_unbuilt_raises(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault, "a.md")
        _seed(vault, "b.md")
        col = Collection(source_dir=vault)

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.get_connection_path("a.md", "b.md")
        assert excinfo.value.reason == "never_built"

    def test_get_toc_on_unbuilt_raises(self, tmp_path: Path) -> None:
        """get_toc reads from FTS so cold-start must raise readiness, not a
        misleading ValueError("Document not found") for a file that exists.
        """
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.get_toc("note.md")
        assert excinfo.value.reason == "never_built"


# ---------------------------------------------------------------------------
# Bucket 4 — coordinate (reindex/build_embeddings require built index;
# build_index short-circuit uses FTS state alone)
# ---------------------------------------------------------------------------


class TestBucket4Coordinate:
    def test_reindex_on_unbuilt_raises(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.reindex()
        assert excinfo.value.reason == "never_built"

    def test_build_embeddings_on_unbuilt_raises(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(
            source_dir=vault,
            embedding_provider=MockEmbeddingProvider(),
            embeddings_path=tmp_path / "vectors",
        )

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.build_embeddings()
        assert excinfo.value.reason == "never_built"

    def test_build_index_short_circuits_on_warm_restart(self, tmp_path: Path) -> None:
        """A second Collection on the same persistent FTS DB short-circuits.

        Pre-fix: ``_initialized`` resets to False on every process start,
        so ``build_index()`` re-scans every file even when the FTS DB
        already holds them. Post-fix: short-circuit checks FTS state
        alone, so warm restarts are O(1).
        """
        vault = _vault(tmp_path)
        for i in range(5):
            _seed(vault, f"note_{i}.md", f"# Note {i}\n\nbody {i}\n")

        index_path = tmp_path / "fts.db"

        col1 = Collection(source_dir=vault, index_path=index_path)
        col1.build_index()
        col1.close()

        col2 = Collection(source_dir=vault, index_path=index_path)
        stats = col2.build_index()

        # Short-circuit returns the existing count, indexes zero new chunks.
        assert stats.documents_indexed == 5
        assert stats.chunks_indexed == 0
        col2.close()


# ---------------------------------------------------------------------------
# wait_until_queryable primitive
# ---------------------------------------------------------------------------


class TestWaitUntilQueryable:
    def test_unbuilt_raises(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        col = Collection(source_dir=vault)

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.wait_until_queryable(timeout=0.1)
        assert excinfo.value.reason == "never_built"

    def test_after_build_returns(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)
        col.build_index()

        # Must return without raising.
        col.wait_until_queryable(timeout=0.1)


# ---------------------------------------------------------------------------
# Cross-cutting: readiness flag is only set after a successful build
# ---------------------------------------------------------------------------


class TestWarmRestartCompletenessSentinel:
    """Warm-restart short-circuit must require proof of clean prior build,
    not just "FTS has some rows" (a partial-write crash leaves rows
    without the proof).
    """

    def test_warm_restart_with_partial_index_rebuilds(self, tmp_path: Path) -> None:
        """A persistent FTS with rows but no completeness sentinel — the
        residue of a process that crashed mid-build_index — must trigger
        a full rebuild on the next process, not be treated as ready.
        """
        from markdown_vault_mcp.fts_index import FTSIndex
        from markdown_vault_mcp.scanner import scan_directory

        vault = _vault(tmp_path)
        for i in range(3):
            _seed(vault, f"n_{i}.md", f"# N{i}\n\nbody {i}\n")
        index_path = tmp_path / "fts.db"

        # Simulate a crash-mid-build: upsert one note directly into the
        # FTS DB (so list_notes() is non-empty) without going through
        # Collection.build_index() (so no sentinel is set).
        fts = FTSIndex(db_path=index_path)
        for note in scan_directory(vault):
            fts.upsert_note(note)
            break  # Stop after one — simulating a mid-loop crash.
        fts.close()

        # Next process opens the same DB and calls build_index().
        col = Collection(source_dir=vault, index_path=index_path)
        col.build_index()

        # Must have rebuilt fully — not short-circuited on the 1 stale row.
        assert {row["path"] for row in col._fts.list_notes()} == {
            "n_0.md",
            "n_1.md",
            "n_2.md",
        }
        col.close()

    def test_clean_warm_restart_short_circuits(self, tmp_path: Path) -> None:
        """A persistent FTS that was the result of a successful prior
        build MUST short-circuit — the perf win the PR is for.
        """
        vault = _vault(tmp_path)
        for i in range(3):
            _seed(vault, f"n_{i}.md", f"# N{i}\n\nbody {i}\n")
        index_path = tmp_path / "fts.db"

        col1 = Collection(source_dir=vault, index_path=index_path)
        col1.build_index()
        col1.close()

        col2 = Collection(source_dir=vault, index_path=index_path)
        stats = col2.build_index()

        # Short-circuit: zero chunks reindexed.
        assert stats.documents_indexed == 3
        assert stats.chunks_indexed == 0
        col2.close()


class TestReadinessFlagSemantics:
    def test_failed_force_rebuild_clears_flag(self, tmp_path: Path) -> None:
        """A failed build_index(force=True) on a previously-built Collection
        must clear ``_index_built`` — otherwise bucket-3 queries proceed
        against a cleared / partially rebuilt index.
        """
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)
        col.build_index()  # Sets _index_built = True.

        def boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("simulated rebuild failure")

        col._index_mgr.build_index = boom  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            col.build_index(force=True)

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.get_backlinks("note.md")
        assert excinfo.value.reason == "never_built"

    def test_failed_build_index_leaves_unready(self, tmp_path: Path) -> None:
        """If build_index() raises, subsequent bucket-3 calls still raise."""
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)

        # Force the underlying index manager to fail.
        def boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("simulated build failure")

        col._index_mgr.build_index = boom  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            col.build_index()

        with pytest.raises(IndexUnavailableError) as excinfo:
            col.get_backlinks("note.md")
        assert excinfo.value.reason == "never_built"


# ---------------------------------------------------------------------------
# Lifespan: submits jobs and yields immediately (#559)
# ---------------------------------------------------------------------------


def test_lifespan_yields_quickly_on_cold_start(tmp_path: Path) -> None:
    """Cold-start lifespan yields immediately after submitting BuildIndex (#559).

    The lifespan submits ``BuildIndex`` and (when configured)
    ``BuildEmbeddings`` jobs to the :class:`IndexWriter` and yields
    without waiting for completion (true fire-and-forget per spec).
    This test bounds the handshake on a 50-file cold vault — the
    yield must complete in well under a second regardless of vault
    size.  The FTS index is *not* required to be queryable on yield;
    bucket-3 tools block on ``@needs_queryable`` until the writer
    drains.
    """
    import asyncio
    import time

    from markdown_vault_mcp._server_deps import make_collection_lifespan
    from markdown_vault_mcp.config import CollectionConfig

    # Construct a cold vault (many files, no existing DB).
    for i in range(50):
        (tmp_path / f"n{i}.md").write_text(f"# n{i}\n\nhello", encoding="utf-8")

    config = CollectionConfig(source_dir=tmp_path, read_only=False)
    lifespan_fn = make_collection_lifespan(config)

    async def _run() -> None:
        start = time.monotonic()
        async with lifespan_fn(None) as ctx:  # type: ignore[arg-type]
            elapsed = time.monotonic() - start
            # Fire-and-forget yield must be sub-second even on a cold vault.
            assert elapsed < 2.0, f"Lifespan took {elapsed:.2f}s to yield"
            assert ctx["collection"] is not None

    asyncio.run(_run())


def test_get_index_status_includes_writer_keys(tmp_path):
    """get_index_status() returns writer state in addition to legacy keys (#559)."""
    from markdown_vault_mcp.collection import Collection

    col = Collection(source_dir=tmp_path, read_only=False)
    try:
        col.build_index()
        status = col.get_index_status()
        assert "status" in status
        assert "documents_indexed" in status
        assert "error" in status
        # New writer keys:
        assert "queue_depth" in status
        assert "in_flight" in status
        assert "dirty_paths" in status
        assert "dirty_embeddings" in status
    finally:
        col.close()
