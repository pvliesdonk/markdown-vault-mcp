"""The vault opens and reads its vectors through the store contract (#1777)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import ConfigurationError, StoreIncompatibleError
from markdown_vault_mcp.stores.numpy_sidecar import (
    NumpySidecarBackend,
    NumpySidecarStore,
)
from markdown_vault_mcp.types import EmbeddingIdentity
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path


def _vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
    return root


def _settings(tmp_path: Path, **kw: object) -> VaultSettings:
    return VaultSettings(index_path=tmp_path / "index.db", read_only=False, **kw)


class TestTheVaultUsesTheNumpyStore:
    def test_semantic_search_reads_the_store_and_writes_the_new_layout(
        self, tmp_path: Path
    ) -> None:
        vault = Vault(
            source_dir=_vault_dir(tmp_path),
            settings=_settings(tmp_path),
            embedding_provider=MockEmbeddingProvider(),
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            results = vault.reader.search("Apples and pears.", limit=1, mode="semantic")
            assert results[0].path == "alpha.md"
            assert isinstance(vault._search_mgr.vectors, NumpySidecarStore)
        finally:
            vault.close()
        names = sorted(p.name for p in tmp_path.iterdir())
        assert "index-db-embeddings.v2.json" in names
        assert any(n.startswith("index-db-embeddings.v2.m") for n in names)
        assert "index-db-embeddings.npy" not in names

    def test_status_reports_backend_identity_and_generation(
        self, tmp_path: Path
    ) -> None:
        vault = Vault(
            source_dir=_vault_dir(tmp_path),
            settings=_settings(tmp_path),
            embedding_provider=MockEmbeddingProvider(),
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            status = vault.index.embeddings_status()
            assert status["backend"] == "numpy"
            assert status["identity"]["model"] == "mock-dim-32"
            assert status["generation"] == 1  # the cold build publishes
        finally:
            vault.close()

    def test_a_model_change_rebuilds_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _vault_dir(tmp_path)
        first = Vault(
            source_dir=source,
            settings=_settings(tmp_path),
            embedding_provider=MockEmbeddingProvider(dim=32),
        )
        first.index.build_index()
        first.index.build_embeddings()
        first.close()
        fresh_calls: list[object] = []
        real_fresh = NumpySidecarBackend.open_fresh

        def counting(
            self: NumpySidecarBackend, location: object, identity: object
        ) -> object:
            fresh_calls.append(location)
            return real_fresh(self, location, identity)  # type: ignore[arg-type]

        monkeypatch.setattr(NumpySidecarBackend, "open_fresh", counting)
        second = Vault(
            source_dir=source,
            settings=_settings(tmp_path),
            embedding_provider=MockEmbeddingProvider(dim=16),
        )
        try:
            second.index.build_index()
            results = second.reader.search(
                "Boats and harbours.", limit=1, mode="semantic"
            )
            assert results[0].path == "beta.md"
            second.reader.search("Apples and pears.", limit=1, mode="semantic")
            assert len(fresh_calls) == 1
            assert second.index.embeddings_status()["identity"]["dimension"] == 16
        finally:
            second.close()

    def test_a_read_only_vault_refuses_a_store_of_another_model(
        self, tmp_path: Path
    ) -> None:
        source = _vault_dir(tmp_path)
        writer = Vault(
            source_dir=source,
            settings=_settings(tmp_path),
            embedding_provider=MockEmbeddingProvider(dim=32),
        )
        writer.index.build_index()
        writer.index.build_embeddings()
        writer.close()
        # Only the embeddings files: a read-only SQLite open may add index.db-shm.
        before = {p.name: p.read_bytes() for p in tmp_path.glob("index-db-embeddings*")}
        reader = Vault(
            source_dir=source,
            settings=_settings(tmp_path, owns_index_files=False),
            embedding_provider=MockEmbeddingProvider(dim=16),
        )
        try:
            with pytest.raises(StoreIncompatibleError):
                reader.reader.search("Boats", limit=1, mode="semantic")
        finally:
            reader.close()
        after = {p.name: p.read_bytes() for p in tmp_path.glob("index-db-embeddings*")}
        assert after == before


class TestInjection:
    def test_an_injected_store_is_used_and_left_open(self, tmp_path: Path) -> None:
        provider = MockEmbeddingProvider()
        store = NumpySidecarBackend().open(None, EmbeddingIdentity.of(provider, "v1"))
        vault = Vault(
            source_dir=_vault_dir(tmp_path),
            settings=_settings(tmp_path),
            embedding_provider=provider,
            vector_store=store,
        )
        vault.index.build_index()
        vault.index.build_embeddings()
        vault.close()
        assert store.count > 0  # still open, filled by the vault

    def test_an_injected_backend_is_used(self, tmp_path: Path) -> None:
        opened: list[object] = []

        class Recording(NumpySidecarBackend):
            def open(self, location, identity=None, *, owns_files=True):  # type: ignore[no-untyped-def]
                opened.append(location)
                return super().open(location, identity, owns_files=owns_files)

        vault = Vault(
            source_dir=_vault_dir(tmp_path),
            settings=_settings(tmp_path),
            embedding_provider=MockEmbeddingProvider(),
            vector_backend=Recording(),
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            assert opened
        finally:
            vault.close()

    def test_a_store_without_a_provider_is_refused(self, tmp_path: Path) -> None:
        store = NumpySidecarBackend().open(
            None, EmbeddingIdentity("p", "m", "", "v1", 3)
        )
        settings = _settings(tmp_path)
        source = _vault_dir(tmp_path)
        with pytest.raises(ValueError, match="embedding_provider"):
            Vault(source_dir=source, settings=settings, vector_store=store)

    def test_an_unknown_store_name_is_a_configuration_error(
        self, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path, embedding_store="milvus")
        source = _vault_dir(tmp_path)
        provider = MockEmbeddingProvider()
        with pytest.raises(ConfigurationError, match="milvus"):
            Vault(source_dir=source, settings=settings, embedding_provider=provider)


class TestInMemory:
    def test_a_search_before_the_build_installs_nothing_and_skips_the_provider(
        self, tmp_path: Path
    ) -> None:
        class DownProvider(MockEmbeddingProvider):
            @property
            def dimension(self) -> int:
                raise RuntimeError("provider is down")

            def embed_query(self, _texts: list[str]) -> list[list[float]]:
                raise RuntimeError("provider is down")

        vault = Vault(
            source_dir=_vault_dir(tmp_path),
            settings=VaultSettings(read_only=False),
            embedding_provider=DownProvider(),
        )
        try:
            vault.index.build_index()
            assert vault.reader.search("Apples", limit=1, mode="semantic") == []
            assert vault._search_mgr.vectors is None
        finally:
            vault.close()


class TestProviderDown:
    def test_get_similar_answers_from_disk_with_the_provider_down(
        self, tmp_path: Path
    ) -> None:
        source = _vault_dir(tmp_path)
        (source / "gamma.md").write_text("# Gamma\n\nApples and boats.\n")
        builder = Vault(
            source_dir=source,
            settings=_settings(tmp_path),
            embedding_provider=MockEmbeddingProvider(),
        )
        builder.index.build_index()
        builder.index.build_embeddings()
        builder.close()

        class DownProvider(MockEmbeddingProvider):
            @property
            def dimension(self) -> int:
                raise ConnectionError("provider is down")

            def embed(self, _texts: list[str]) -> list[list[float]]:
                raise ConnectionError("provider is down")

            def embed_query(self, _texts: list[str]) -> list[list[float]]:
                raise ConnectionError("provider is down")

        vault = Vault(
            source_dir=source,
            settings=_settings(tmp_path),
            embedding_provider=DownProvider(),
        )
        try:
            vault.index.build_index()  # warm: the index from the first vault
            similar = vault.reader.get_similar("alpha.md", limit=2)
            assert {r.path for r in similar} == {"beta.md", "gamma.md"}
        finally:
            vault.close()


class TestASearchBeforeTheFirstBuild:
    def test_the_build_after_it_still_embeds(self, tmp_path: Path) -> None:
        vault = Vault(
            source_dir=_vault_dir(tmp_path),
            settings=_settings(tmp_path),
            embedding_provider=MockEmbeddingProvider(),
        )
        try:
            vault.index.build_index()
            vault.reader.search("Apples and pears.", limit=1, mode="hybrid")
            vault.index.build_embeddings()
            results = vault.reader.search("Apples and pears.", limit=1, mode="semantic")
            assert [r.path for r in results] == ["alpha.md"]
            assert vault.index.embeddings_status()["identity"]["dimension"] == 32
        finally:
            vault.close()


def test_a_search_that_loses_its_store_to_the_first_build_reads_the_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first build closes the store a search took before any vectors existed (#1504)."""
    from markdown_vault_mcp.config_sections import VaultSettings
    from markdown_vault_mcp.managers.search import SearchManager
    from markdown_vault_mcp.vault import Vault
    from tests.conftest import MockEmbeddingProvider

    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
        embedding_provider=MockEmbeddingProvider(dim=16),
    )
    try:
        vault.index.build_index()
        real = SearchManager._load_vectors
        raced: list[bool] = []

        def load_then_build(self: SearchManager) -> object:
            store = real(self)
            if not raced:  # the first build lands between the load and the read
                raced.append(True)
                vault._vector_slot.writable(rebuild=None)
            return store

        monkeypatch.setattr(SearchManager, "_load_vectors", load_then_build)
        for mode in ("semantic", "hybrid"):
            raced.clear()
            assert isinstance(vault.reader.search("pears", mode=mode), list)
    finally:
        vault.close()
