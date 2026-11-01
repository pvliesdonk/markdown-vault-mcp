"""A build that cannot reuse the index drops notes deleted while it was down (#1965).

A start whose index provenance changed, or whose last build never finished,
runs a full build over the rows already there. That build used to keep
every row and upsert what it found, so a note deleted meanwhile stayed
indexed, and the tracker, recording only what the build saw, never reported
it deleted.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path
from tests.test_index_coordinator import make_coordinator

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.indexing.coordinator import IndexWriteCoordinator


def _indexed(coord: IndexWriteCoordinator) -> list[str]:
    return sorted(n["path"] for n in cast("FTSIndex", coord._fts).list_notes())


def _close(coord: IndexWriteCoordinator) -> None:
    coord.close(timeout=5)
    cast("FTSIndex", coord._fts).close()


def _built_then_deleted(tmp_path: Path) -> None:
    """Build an index of ``a.md`` and ``b.md``, close it, then delete ``b.md``."""
    (tmp_path / "b.md").write_text("# B\n\nbravo\n", encoding="utf-8")
    coord = make_coordinator(tmp_path)
    coord.build_index()
    _close(coord)
    (tmp_path / "b.md").unlink()


def _assert_converged(coord: IndexWriteCoordinator) -> None:
    assert _indexed(coord) == ["a.md"]
    result = coord.reindex()
    assert (result.added, result.modified, result.deleted) == (0, 0, 0)
    assert _indexed(coord) == ["a.md"]


def test_a_build_after_a_provenance_change_drops_a_deleted_note(
    tmp_path: Path,
) -> None:
    _built_then_deleted(tmp_path)
    coord = make_coordinator(tmp_path, title_field="name")
    try:
        coord.build_index()
        _assert_converged(coord)
    finally:
        _close(coord)


def test_a_build_resuming_an_interrupted_one_drops_a_deleted_note(
    tmp_path: Path,
) -> None:
    _built_then_deleted(tmp_path)
    fts = FTSIndex(db_path=tmp_path / "index.db")
    fts.clear_build_completed()
    fts.close()
    coord = make_coordinator(tmp_path)
    try:
        coord.build_index()
        _assert_converged(coord)
    finally:
        _close(coord)


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores permission bits",
)
def test_a_build_keeps_the_notes_of_a_folder_it_cannot_read(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "gamma.md").write_text("# Gamma\n\ngamma\n", encoding="utf-8")
    _built_then_deleted(tmp_path)
    (tmp_path / "sub").chmod(0)
    try:
        coord = make_coordinator(tmp_path, title_field="name")
        try:
            coord.build_index()
            assert _indexed(coord) == ["a.md", "sub/gamma.md"]
        finally:
            _close(coord)
    finally:
        (tmp_path / "sub").chmod(0o755)


def _vault(
    root: Path,
    tmp_path: Path,
    title_field: str,
    *,
    provider: MockEmbeddingProvider | None = None,
    required: list[str] | None = None,
) -> Vault:
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=tmp_path / "index.db",
            state_path=tmp_path / "state.json",
            embeddings_path=tmp_path / "vectors" if provider else None,
            required_frontmatter=required,
            title_field=title_field,
            read_only=False,
        ),
        embedding_provider=provider,
    )


def test_a_build_drops_the_tombstone_of_a_deleted_file(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("---\ntags: [x]\n---\n# A\n\nbody\n", encoding="utf-8")
    (root / "bad.md").write_text("# Bad\n\nno tags\n", encoding="utf-8")
    first = _vault(root, tmp_path, "title", required=["tags"])
    try:
        first.index.build_index()
        assert [t["path"] for t in first._fts.list_tombstones()] == ["bad.md"]
    finally:
        first.close()
    (root / "bad.md").unlink()
    second = _vault(root, tmp_path, "name", required=["tags"])
    try:
        second.index.build_index()
        assert second._fts.list_tombstones() == []
    finally:
        second.close()


def test_the_embedding_build_after_it_drops_the_deleted_notes_vectors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("# A\n\nalpha\n", encoding="utf-8")
    (root / "b.md").write_text("# B\n\nbravo\n", encoding="utf-8")
    provider = MockEmbeddingProvider()
    first = _vault(root, tmp_path, "title", provider=provider)
    try:
        first.index.build_index()
        first.index.build_embeddings()
    finally:
        first.close()
    (root / "b.md").unlink()
    second = _vault(root, tmp_path, "name", provider=provider)
    try:
        second.index.build_index()
        second.index.build_embeddings()
        assert [r.path for r in second.reader.search("bravo", mode="keyword")] == []
        store = second._index_mgr._embeddings._loaded_store()
        assert store is not None
        assert sorted(metadata_by_path(store)) == ["a.md"]
    finally:
        second.close()
