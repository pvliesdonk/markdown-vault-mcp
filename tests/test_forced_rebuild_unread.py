"""A forced rebuild keeps the notes of folders it could not read (#1929).

An unreadable directory is not an authoritative absence (#1625, #1785): a
forced build that wiped every row before its walk dropped the notes under it
from keyword search, and the forced embedding build then dropped their vectors,
paid for again once the folder was readable.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores permission bits",
)

STORES = ["numpy", "sqlite"]


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    """A vault with ``alpha.md``, ``sub/gamma.md`` and ``subway/x.md``."""
    vault = tmp_path / "vault"
    for rel, text in (
        ("alpha.md", "# Alpha\n\nalphaword\n"),
        ("sub/gamma.md", "# Gamma\n\ngammaword\n"),
        ("subway/x.md", "# X\n\nxword\n"),
    ):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text(text, encoding="utf-8")
    yield vault
    for path in (vault, vault / "sub"):
        if path.exists():
            path.chmod(0o755)


def _vault(
    root: Path,
    tmp_path: Path,
    *,
    provider: MockEmbeddingProvider | None = None,
    store: str = "numpy",
    required: list[str] | None = None,
) -> Vault:
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=tmp_path / "index.db",
            state_path=tmp_path / "state.json",  # outside the source: chmod-safe
            embeddings_path=tmp_path / "vectors" if provider else None,
            embedding_store=store,
            required_frontmatter=required,
            read_only=False,
        ),
        embedding_provider=provider,
    )


def _paths(vault: Vault, word: str) -> list[str]:
    return [r.path for r in vault.reader.search(word, mode="keyword")]


class TestTheKeywordBuild:
    def test_a_forced_build_keeps_an_unread_folders_notes(
        self, root: Path, tmp_path: Path
    ) -> None:
        vault = _vault(root, tmp_path)
        try:
            vault.index.build_index()
            (root / "sub").chmod(0)
            vault.index.build_index(force=True)
            assert _paths(vault, "gammaword") == ["sub/gamma.md"]
            assert _paths(vault, "alphaword") == ["alpha.md"]
            assert not vault._fts.is_build_walk_complete()
        finally:
            (root / "sub").chmod(0o755)
            vault.close()

    def test_a_deleted_folder_still_loses_its_notes(
        self, root: Path, tmp_path: Path
    ) -> None:
        vault = _vault(root, tmp_path)
        try:
            vault.index.build_index()
            shutil.rmtree(root / "subway")
            (root / "sub").chmod(0)
            vault.index.build_index(force=True)
            assert vault.reader.get_metadata("subway/x.md") is None
            assert vault.reader.get_metadata("sub/gamma.md") is not None
        finally:
            (root / "sub").chmod(0o755)
            vault.close()

    def test_a_prefix_sibling_is_not_kept(self, root: Path, tmp_path: Path) -> None:
        """``sub`` unreadable spares ``sub/…``, never ``subway/…``."""
        vault = _vault(root, tmp_path)
        try:
            vault.index.build_index()
            (root / "subway" / "x.md").unlink()
            (root / "sub").chmod(0)
            vault.index.build_index(force=True)
            assert vault.reader.get_metadata("subway/x.md") is None
        finally:
            (root / "sub").chmod(0o755)
            vault.close()

    def test_an_unreadable_root_keeps_everything(
        self, root: Path, tmp_path: Path
    ) -> None:
        vault = _vault(root, tmp_path)
        try:
            vault.index.build_index()
            root.chmod(0)
            vault.index.build_index(force=True)
            root.chmod(0o755)
            for rel in ("alpha.md", "sub/gamma.md", "subway/x.md"):
                assert vault.reader.get_metadata(rel) is not None, rel
        finally:
            root.chmod(0o755)
            vault.close()

    def test_a_kept_tombstone_survives(self, root: Path, tmp_path: Path) -> None:
        for rel in ("alpha.md", "sub/gamma.md", "subway/x.md"):
            text = (root / rel).read_text(encoding="utf-8")
            (root / rel).write_text("---\ntags: [t]\n---\n" + text, encoding="utf-8")
        (root / "sub" / "skip.md").write_text("# Skip\n\nno tags\n", encoding="utf-8")
        vault = _vault(root, tmp_path, required=["tags"])
        try:
            vault.index.build_index()
            assert [r["path"] for r in vault._fts.list_tombstones()] == ["sub/skip.md"]
            (root / "sub").chmod(0)
            vault.index.build_index(force=True)
            assert [r["path"] for r in vault._fts.list_tombstones()] == ["sub/skip.md"]
            assert vault.reader.get_metadata("sub/gamma.md") is not None
        finally:
            (root / "sub").chmod(0o755)
            vault.close()

    def test_a_kept_note_is_read_again_once_readable(
        self, root: Path, tmp_path: Path
    ) -> None:
        """A kept note re-parses on the next readable scan, picking up an edit."""
        vault = _vault(root, tmp_path)
        try:
            vault.index.build_index()
            (root / "sub").chmod(0)
            vault.index.build_index(force=True)
            (root / "sub").chmod(0o755)
            (root / "sub" / "gamma.md").write_text(
                "# Gamma\n\nnewword\n", encoding="utf-8"
            )
            vault.index.reindex()
            assert _paths(vault, "newword") == ["sub/gamma.md"]
            assert _paths(vault, "gammaword") == []
        finally:
            (root / "sub").chmod(0o755)
            vault.close()


def _stored_paths(
    tmp_path: Path, provider: MockEmbeddingProvider, store: str
) -> set[str]:
    from markdown_vault_mcp.embed_text import EmbedTextBuilder
    from markdown_vault_mcp.stores.registry import get_backend
    from markdown_vault_mcp.types import EmbeddingIdentity

    identity = EmbeddingIdentity.of(provider, EmbedTextBuilder().format_token())
    opened = get_backend("vector", store)().open(
        tmp_path / "vectors", identity, owns_files=False
    )
    try:
        return set(metadata_by_path(opened))
    finally:
        opened.close()


def _track_embeds(provider: MockEmbeddingProvider) -> list[str]:
    texts: list[str] = []
    original = provider.embed

    def tracking(batch: list[str]) -> list[list[float]]:
        texts.extend(batch)
        return original(batch)

    provider.embed = tracking  # type: ignore[method-assign]
    return texts


@pytest.mark.parametrize("store", STORES)
class TestTheVectors:
    def _forced_over_unread_sub(
        self, root: Path, tmp_path: Path, store: str
    ) -> tuple[Vault, MockEmbeddingProvider]:
        provider = MockEmbeddingProvider()
        vault = _vault(root, tmp_path, provider=provider, store=store)
        vault.index.build_index()
        vault.index.build_embeddings()
        (root / "sub").chmod(0)
        vault.index.build_index(force=True)
        vault.index.build_embeddings(force=True)
        (root / "sub").chmod(0o755)
        return vault, provider

    def test_a_forced_rebuild_keeps_the_unread_notes_vectors(
        self, root: Path, tmp_path: Path, store: str
    ) -> None:
        vault, provider = self._forced_over_unread_sub(root, tmp_path, store)
        try:
            embedded = _track_embeds(provider)
            vault.index.reindex()
            vault.index.build_embeddings()
            assert embedded == []
        finally:
            vault.close()
        assert "sub/gamma.md" in _stored_paths(tmp_path, provider, store)

    def test_a_kept_note_deleted_meanwhile_is_removed(
        self, root: Path, tmp_path: Path, store: str
    ) -> None:
        vault, provider = self._forced_over_unread_sub(root, tmp_path, store)
        try:
            (root / "sub" / "gamma.md").unlink()
            vault.index.reindex()
            vault.index.build_embeddings()
            assert vault.reader.get_metadata("sub/gamma.md") is None
        finally:
            vault.close()
        assert "sub/gamma.md" not in _stored_paths(tmp_path, provider, store)
