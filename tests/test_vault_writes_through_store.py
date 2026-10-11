"""Every vector write goes through the store's publisher (#1778)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import ReadOnlyError
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.types import (
    ANY_TOKEN,
    DocumentRows,
    EmbeddingIdentity,
    GraphEdge,
    VectorRow,
)
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path, write_legacy_sidecar

if TYPE_CHECKING:
    from pathlib import Path

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
        legacy_matrix = tmp_path / "index-db-embeddings.npy"
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            assert vault.index.build_embeddings() == 0
            assert provider.calls == 0
            vault._vectors.checkpoint()  # type: ignore[union-attr]
            assert legacy_matrix.exists()  # nothing changed, nothing saved
            (tmp_path / "vault" / "alpha.md").write_text("# Alpha\n\nQuinces now.\n")
            vault.index.reindex()
        finally:
            vault.close()
        names = sorted(p.name for p in tmp_path.glob("index-db-embeddings*"))
        assert "index-db-embeddings.json" in names
        assert "index-db-embeddings.npy" not in names  # the first change moved it


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


class _ModelB(MockEmbeddingProvider):
    @property
    def model_name(self) -> str:
        return "model-b"


class TestAnInjectedStoreOfAnotherModel:
    def test_reindex_writes_nothing_into_it(self, tmp_path: Path) -> None:
        model_a = EmbeddingIdentity.of(MockEmbeddingProvider(), "v1")
        store = NumpySidecarBackend().open(None, model_a)
        store.replace_document(
            DocumentRows("old.md", None, [VectorRow([1.0] + [0.0] * 31, {})]),
            expected_token=ANY_TOKEN,
        )
        vault = Vault(
            source_dir=_source(tmp_path),
            settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
            embedding_provider=_ModelB(),
            vector_store=store,
        )
        try:
            vault.index.build_index()
            (tmp_path / "vault" / "alpha.md").write_text("# Alpha\n\nQuinces now.\n")
            (tmp_path / "vault" / "gamma.md").write_text("# Gamma\n\nGlass.\n")
            vault.index.reindex()
            assert sorted(store.tokens()) == ["old.md"]
            assert store.identity == model_a
        finally:
            vault.close()
            store.close()

    def test_a_newly_excluded_note_still_leaves_the_keyword_index(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        builder = _vault(tmp_path, MockEmbeddingProvider())
        try:
            builder.index.build_index()
        finally:
            builder.close()
        model_a = EmbeddingIdentity.of(MockEmbeddingProvider(), "v1")
        store = NumpySidecarBackend().open(None, model_a)
        store.replace_document(
            DocumentRows("old.md", None, [VectorRow([1.0] + [0.0] * 31, {})]),
            expected_token=ANY_TOKEN,
        )
        vault = Vault(
            source_dir=_source(tmp_path),
            settings=VaultSettings(
                index_path=tmp_path / "index.db",
                read_only=False,
                exclude_patterns=["beta.md"],
            ),
            embedding_provider=_ModelB(),
            vector_store=store,
        )
        try:
            vault.index.build_index()
            with caplog.at_level(logging.WARNING):
                vault.index.reindex()
            assert "beta.md" not in {r["path"] for r in vault._fts.list_notes()}
            assert sorted(store.tokens()) == ["old.md"]
            assert any(
                r.msg.startswith("purge_vectors_skipped") for r in caplog.records
            )
        finally:
            vault.close()
            store.close()


class TestAProcessThatDoesNotOwnTheFiles:
    def test_a_forced_rebuild_is_refused_before_embedding(self, tmp_path: Path) -> None:
        provider = _Switchable()
        writer = _vault(tmp_path, provider)
        try:
            writer.index.build_index()
            writer.index.build_embeddings()
        finally:
            writer.close()
        before = {p.name: p.read_bytes() for p in tmp_path.glob("index-db-embeddings*")}
        provider.calls = 0
        reader = _vault(tmp_path, provider, owns_index_files=False)
        try:
            reader.index.build_index()
            with pytest.raises(ReadOnlyError):
                reader.index.build_embeddings(force=True)
        finally:
            reader.close()
        assert provider.calls == 0
        after = {p.name: p.read_bytes() for p in tmp_path.glob("index-db-embeddings*")}
        assert after == before


class TestRebuildingBeforeAnyRead:
    def test_a_failed_forced_rebuild_keeps_serving_the_stored_rows(
        self, tmp_path: Path
    ) -> None:
        provider = _Switchable()
        writer = _vault(tmp_path, provider)
        try:
            writer.index.build_index()
            writer.index.build_embeddings()
        finally:
            writer.close()
        provider.fail = True
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            assert vault.index.build_embeddings(force=True) == 0
            assert sorted(metadata_by_path(_stored(vault))) == ["alpha.md", "beta.md"]
        finally:
            vault.close()


class TestFloat32:
    def test_a_batch_that_overflows_float32_is_skipped_alone(
        self, tmp_path: Path
    ) -> None:
        class Overflowing(MockEmbeddingProvider):
            def embed(self, texts: list[str]) -> list[list[float]]:
                vectors = super().embed(texts)
                return [
                    [1e39, *v[1:]] if "Apples" in t else v
                    for t, v in zip(texts, vectors, strict=True)
                ]

        vault = _vault(tmp_path, Overflowing(), embedding_batch_size=1)
        try:
            vault.index.build_index()
            assert vault.index.build_embeddings() == 1
            assert sorted(metadata_by_path(_stored(vault))) == ["beta.md"]
        finally:
            vault.close()


class TestANonOwningProcessReading:
    def _reader_of_another_model(self, tmp_path: Path) -> Vault:
        writer = _vault(tmp_path, MockEmbeddingProvider())
        try:
            writer.index.build_index()
            writer.index.build_embeddings()
        finally:
            writer.close()
        # The index is on disk already; a process that does not own it cannot
        # mark it built, so these tests call the managers behind the facets.
        return _vault(tmp_path, _ModelB(), owns_index_files=False)

    def test_get_context_degrades_to_no_similar_notes(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        reader = self._reader_of_another_model(tmp_path)
        try:
            with caplog.at_level(logging.WARNING):
                context = reader._search_mgr.get_context("alpha.md")
            assert context.similar == []
            assert any(
                r.msg.startswith("get_context_similar_failed") for r in caplog.records
            )
        finally:
            reader.close()

    def test_the_graph_leaves_out_semantic_edges_quietly(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        reader = self._reader_of_another_model(tmp_path)
        try:
            with caplog.at_level(logging.WARNING):
                edges: list[GraphEdge] = []
                reader._graph_facet._add_semantic_edges(
                    {"alpha.md": None}, edges, max_nodes=10
                )
            assert edges == []
            assert not any(
                r.msg.startswith("get_similar_failed") for r in caplog.records
            )
        finally:
            reader.close()


class TestAProviderThatIsDown:
    def test_reindex_beside_an_injected_store_still_indexes_keywords(
        self, tmp_path: Path
    ) -> None:
        class Down(MockEmbeddingProvider):
            @property
            def dimension(self) -> int:
                raise ConnectionError("provider down")

            def embed(self, texts: list[str]) -> list[list[float]]:  # noqa: ARG002
                raise ConnectionError("provider down")

        store = NumpySidecarBackend().open(
            None, EmbeddingIdentity.of(MockEmbeddingProvider(), "v1")
        )
        vault = Vault(
            source_dir=_source(tmp_path),
            settings=VaultSettings(read_only=False),  # held in memory
            embedding_provider=Down(),
            vector_store=store,
        )
        try:
            vault.index.build_index()
            (tmp_path / "vault" / "gamma.md").write_text("# Gamma\n\nGlass.\n")
            assert vault.index.reindex().added == 1
        finally:
            vault.close()
            store.close()
