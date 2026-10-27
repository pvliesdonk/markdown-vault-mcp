"""A file-backed vault behaves the same on every vector store (#1898).

Subclass :class:`VaultStoreContract` in a store's vault-level test module
and provide ``store_settings``: the :class:`VaultSettings` fields that put
the vault's vectors in that store. Each case builds a real
:class:`~markdown_vault_mcp.vault.Vault` over a folder of notes, the
file-backed counterpart of the dict source in
:mod:`tests.fixtures.source_neutral_contract`, and covers deletion against
an unreadable source, the conflict re-queue and who closes the store
(decision 30, #1765).
"""

from __future__ import annotations

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
        teardown.
        """
        root = tmp_path / "vault"
        root.mkdir()
        (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
        (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
        opened: list[Vault] = []

        def open_vault(index: str = "index.db", **kw: Any) -> Vault:
            settings = VaultSettings(
                index_path=tmp_path / index,
                state_path=tmp_path / f"{index}.state.json",
                read_only=False,
                **{**store_settings, **kw.pop("settings", {})},
            )
            vault = Vault(
                source_dir=root,
                settings=settings,
                embedding_provider=MockEmbeddingProvider(dim=16),
                **kw,
            )
            opened.append(vault)
            return vault

        yield open_vault
        for vault in opened:
            vault.close()

    @staticmethod
    def _built(vault: Vault) -> EmbeddingStore:
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
        vault.index.wait_for_drain(timeout=10)
        assert sorted(metadata_by_path(store)) == ["alpha.md"]

    def test_a_newly_excluded_note_leaves_the_store(
        self, open_vault: Callable[..., Vault]
    ) -> None:
        first = open_vault()
        self._built(first)
        first.close()
        vault = open_vault(settings={"exclude_patterns": ["beta.md"]})
        store = self._built(vault)
        vault.index.reindex()
        vault.index.wait_for_drain(timeout=10)
        assert sorted(metadata_by_path(store)) == ["alpha.md"]

    def test_an_unreadable_source_keeps_every_vector(
        self, open_vault: Callable[..., Vault]
    ) -> None:
        vault = open_vault()
        store = self._built(vault)
        before = metadata_by_path(store)
        vault.source_dir.chmod(0)
        try:
            vault.index.build_index(force=True)
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
        vault.index.wait_for_drain(timeout=10)
        assert refused == ["alpha.md"]
        rows = metadata_by_path(store)["alpha.md"]
        assert any("Quinces" in row["content"] for row in rows)

    def test_the_vault_closes_the_store_it_opened(
        self, open_vault: Callable[..., Vault]
    ) -> None:
        vault = open_vault()
        store = self._built(vault)
        vault.close()
        with pytest.raises(RuntimeError):
            store.tokens()

    def test_an_injected_store_outlives_the_vault(
        self, open_vault: Callable[..., Vault]
    ) -> None:
        owner = open_vault()
        store = self._built(owner)
        before = metadata_by_path(store)
        guest = open_vault("guest.db", vector_store=store)
        guest.index.build_index()
        assert guest.reader.search("pears", mode="semantic")
        guest.close()
        assert metadata_by_path(store) == before
        assert owner.reader.search("boats", mode="semantic")
