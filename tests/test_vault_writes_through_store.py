"""Every vector write goes through the store's publisher (#1778)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.types import EmbeddingIdentity
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path, write_legacy_sidecar

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from markdown_vault_mcp.interfaces import EmbeddingStore


class _Switchable(MockEmbeddingProvider):
    """Counts embed calls; can fail, or return vectors one wider than it claims."""

    def __init__(self) -> None:
        super().__init__(dim=32)
        self.calls = 0
        self.fail = False
        self.wide = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        vectors = super().embed(texts)
        return [[*v, 0.5] for v in vectors] if self.wide else vectors


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    if not root.exists():
        root.mkdir()
        (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
        (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
    return root


def _vault(tmp_path: Path, provider: MockEmbeddingProvider, **kw: Any) -> Vault:
    return Vault(
        source_dir=_source(tmp_path),
        settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False, **kw),
        embedding_provider=provider,
    )


def _stored(vault: Vault) -> EmbeddingStore:
    store = vault._vectors
    assert store is not None
    return store


def _reopened(tmp_path: Path, provider: MockEmbeddingProvider) -> EmbeddingStore:
    return NumpySidecarBackend().open(
        tmp_path / "index-db-embeddings", EmbeddingIdentity.of(provider, "v1")
    )


class TestTheColdBuild:
    def test_it_publishes_one_generation(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, MockEmbeddingProvider())
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            store = _stored(vault)
            assert store.generation == 1
            assert sorted(metadata_by_path(store)) == ["alpha.md", "beta.md"]
        finally:
            vault.close()

    def test_a_forced_rebuild_that_embeds_nothing_keeps_an_injected_store(
        self, tmp_path: Path
    ) -> None:
        provider = _Switchable()
        store = NumpySidecarBackend().open(None, EmbeddingIdentity.of(provider, "v1"))
        vault = Vault(
            source_dir=_source(tmp_path),
            settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
            embedding_provider=provider,
            vector_store=store,
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            before = (store.count, store.generation)
            provider.fail = True
            assert vault.index.build_embeddings(force=True) == 0
            assert (store.count, store.generation) == before
        finally:
            vault.close()
            store.close()

    def test_a_forced_rebuild_that_embeds_nothing_keeps_an_owned_store(
        self, tmp_path: Path
    ) -> None:
        provider = _Switchable()
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            provider.fail = True
            vault.index.build_embeddings(force=True)
            assert sorted(metadata_by_path(_stored(vault))) == ["alpha.md", "beta.md"]
        finally:
            vault.close()
        assert sorted(metadata_by_path(_reopened(tmp_path, provider))) == [
            "alpha.md",
            "beta.md",
        ]


class TestARefusedWriteKeepsTheOldRows:
    def test_on_reindex(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        provider = _Switchable()
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            old = metadata_by_path(_stored(vault))["alpha.md"]
            (tmp_path / "vault" / "alpha.md").write_text("# Alpha\n\nQuinces now.\n")
            provider.wide = True
            with caplog.at_level(logging.WARNING):
                vault.index.reindex()
            assert metadata_by_path(_stored(vault))["alpha.md"] == old
            assert any(
                r.msg.startswith("reindex_inline_embed_dim_mismatch")
                for r in caplog.records
            )
        finally:
            vault.close()

    def test_on_convergence(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider = _Switchable()
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            old = metadata_by_path(_stored(vault))["alpha.md"]
            (tmp_path / "vault" / "alpha.md").write_text("# Alpha\n\nQuinces now.\n")
            provider.fail = True
            vault.index.reindex()  # the keyword index moves on, the vectors stay
            provider.fail, provider.wide = False, True
            with caplog.at_level(logging.WARNING):
                assert vault.index.build_embeddings() == 0
            assert metadata_by_path(_stored(vault))["alpha.md"] == old
            assert any(
                r.msg.startswith("build_embeddings_converge_dim_mismatch")
                for r in caplog.records
            )
        finally:
            vault.close()

    def test_on_the_dirty_flush(self, tmp_path: Path) -> None:
        provider = _Switchable()
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            old = metadata_by_path(_stored(vault))["alpha.md"]
            (tmp_path / "vault" / "alpha.md").write_text("# Alpha\n\nQuinces now.\n")
            provider.wide = True
            vault._index_mgr._embeddings.flush_dirty_embeddings({"alpha.md"})
            assert metadata_by_path(_stored(vault))["alpha.md"] == old
        finally:
            vault.close()


class TestUpgradingFromALegacySidecar:
    def test_unchanged_notes_converge_without_embedding(self, tmp_path: Path) -> None:
        provider = _Switchable()
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            store = _stored(vault)
            rows = [r for p in sorted(store.tokens()) for r in store.rows_for(p)]
        finally:
            vault.close()
        for f in tmp_path.glob("index-db-embeddings*"):
            f.unlink()
        write_legacy_sidecar(
            tmp_path / "index-db-embeddings",
            [list(r.vector) for r in rows],
            [dict(r.metadata) for r in rows],
            provider=provider,
        )
        provider.calls = 0
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            assert vault.index.build_embeddings() == 0
            assert provider.calls == 0
        finally:
            vault.close()
        names = sorted(p.name for p in tmp_path.glob("index-db-embeddings*"))
        assert "index-db-embeddings.npy" in names  # nothing changed, nothing saved


class TestPurges:
    def test_a_newly_excluded_note_leaves_the_store(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, MockEmbeddingProvider())
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
        finally:
            vault.close()
        vault = _vault(tmp_path, MockEmbeddingProvider(), exclude_patterns=["beta.md"])
        try:
            vault.index.build_index()  # already built: no rescan
            vault.index.reindex()  # purges the newly excluded note
            assert sorted(metadata_by_path(_stored(vault))) == ["alpha.md"]
        finally:
            vault.close()
        reopened = _reopened(tmp_path, MockEmbeddingProvider())
        assert sorted(metadata_by_path(reopened)) == ["alpha.md"]

    def test_a_deleted_note_leaves_the_store(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path, MockEmbeddingProvider())
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            (tmp_path / "vault" / "beta.md").unlink()
            vault.index.reindex()
            assert sorted(metadata_by_path(_stored(vault))) == ["alpha.md"]
        finally:
            vault.close()
        reopened = _reopened(tmp_path, MockEmbeddingProvider())
        assert sorted(metadata_by_path(reopened)) == ["alpha.md"]
