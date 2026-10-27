"""A file-backed vault behaves the same on every vector store (#1898).

Subclass :class:`VaultStoreContract` in a store's vault-level test module
and provide ``store_settings``: the :class:`VaultSettings` fields that put
the vault's vectors in that store. Each case builds a real
:class:`~markdown_vault_mcp.vault.Vault` over a folder of notes, the
file-backed counterpart of the dict source in
:mod:`tests.fixtures.source_neutral_contract`. It covers the purges, a
forced rebuild that reads nothing, the conflict re-queue and who closes
the store (decision 30, #1765). The source probe's unavailable answer is
pinned by the dict source, not here: a file-backed vault refuses an empty
rebuild before it asks.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from markdown_vault_mcp.interfaces import EmbeddingStore
    from markdown_vault_mcp.types import DocumentRows


class VaultStoreContract:
    """Vault-level behaviour every vector store must share."""

    @pytest.fixture
    def store_settings(self) -> dict[str, Any]:
        """The settings that select the store under test."""
        raise NotImplementedError

    @pytest.fixture
    def open_vault(
        self, tmp_path: Path, store_settings: dict[str, Any]
    ) -> Iterator[Callable[..., Vault]]:
        """Yield a callable opening a vault over ``tmp_path/vault``.

        The notes are written once. The state file lives outside the vault
        folder, so a test can make the folder unreadable without failing
        the tracker's write instead. Every vault opened is closed at
        teardown, each one even if another's close raises.
        """
        root = tmp_path / "vault"
        root.mkdir()
        (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
        (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")

        with contextlib.ExitStack() as stack:

            def open_vault(
                index: str = "index.db",
                *,
                settings: dict[str, Any] | None = None,
                vector_store: EmbeddingStore | None = None,
            ) -> Vault:
                vault = Vault(
                    source_dir=root,
                    settings=VaultSettings(
                        index_path=tmp_path / index,
                        state_path=tmp_path / f"{index}.state.json",
                        read_only=False,
                        **{**store_settings, **(settings or {})},
                    ),
                    embedding_provider=MockEmbeddingProvider(dim=16),
                    vector_store=vector_store,
                )
                stack.callback(vault.close)
                return vault

            yield open_vault

    @staticmethod
    def _built(vault: Vault) -> EmbeddingStore:
        """Build *vault*'s keyword index and embeddings; return its open store."""
        vault.index.build_index()
        vault.index.build_embeddings()
        store = vault._vectors
        assert store is not None
        return store

    def test_a_deleted_note_loses_its_vectors(
        self, open_vault: Callable[..., Vault]
    ) -> None:
        vault = open_vault()
        store = self._built(vault)
        (vault.source_dir / "beta.md").unlink()
        vault.index.reindex()
        assert vault.index.wait_for_drain(timeout=10)
        assert sorted(metadata_by_path(store)) == ["alpha.md"]

    def test_a_newly_excluded_note_leaves_the_store_on_reindex(
        self, open_vault: Callable[..., Vault]
    ) -> None:
        first = open_vault()
        self._built(first)
        first.close()
        vault = open_vault(settings={"exclude_patterns": ["beta.md"]})
        vault.index.build_index()
        vault.index.reindex()
        assert vault.index.wait_for_drain(timeout=10)
        store = vault._vectors
        assert store is not None
        assert sorted(metadata_by_path(store)) == ["alpha.md"]

    def test_a_forced_rebuild_that_reads_nothing_keeps_every_vector(
        self, open_vault: Callable[..., Vault]
    ) -> None:
        vault = open_vault()
        store = self._built(vault)
        before = metadata_by_path(store)
        vault.source_dir.chmod(0)
        try:
            vault.index.build_index(force=True)
            # Fails as root, which reads the folder anyway.
            assert not vault._fts.is_build_walk_complete()
            vault.index.build_embeddings(force=True)
        finally:
            vault.source_dir.chmod(0o755)
        assert metadata_by_path(store) == before

    def test_a_conflicting_write_is_requeued_and_lands_next(
        self, open_vault: Callable[..., Vault], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = open_vault()
        store = self._built(vault)
        real = store.replace_document
        refused: list[str] = []

        def once(document: DocumentRows, *, expected_token: Any) -> str:
            if document.path == "alpha.md" and not refused:
                refused.append(document.path)
                return "conflict"
            return real(document, expected_token=expected_token)

        monkeypatch.setattr(store, "replace_document", once)
        (vault.source_dir / "alpha.md").write_text("# Alpha\n\nQuinces.\n")
        vault.index.reindex()
        assert vault.index.wait_for_drain(timeout=10)
        assert refused == ["alpha.md"]
        rows = metadata_by_path(store)["alpha.md"]
        assert any("Quinces" in row["content"] for row in rows)

    def test_the_vault_closes_the_store_it_opened(
        self, open_vault: Callable[..., Vault]
    ) -> None:
        vault = open_vault()
        store = self._built(vault)
        vault.close()
        with pytest.raises(RuntimeError, match="closed"):
            store.tokens()

    def test_an_injected_store_outlives_the_vault(
        self, open_vault: Callable[..., Vault]
    ) -> None:
        owner = open_vault()
        store = self._built(owner)
        before = metadata_by_path(store)
        # The guest indexes the same notes, so it has nothing to write: one
        # writer per store; #1812 holds the many-writer case.
        guest = open_vault("guest.db", vector_store=store)
        guest.index.build_index()
        assert guest._vectors is store
        assert guest.reader.search("pears", mode="semantic")
        guest.close()
        assert metadata_by_path(store) == before
        assert owner.reader.search("boats", mode="semantic")
