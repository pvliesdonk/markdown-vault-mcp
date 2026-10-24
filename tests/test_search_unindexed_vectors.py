"""Search never returns a vector row of a note the keyword index lacks (#1505)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.types import ANY_TOKEN
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vector_store_contract import doc

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

QUERY = "harbour boats sailing"


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    """Three notes built and embedded, two at the root and one in ``sub``."""
    root = tmp_path / "vault"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("# Alpha\n\nApples and pears.\n")
    (root / "b.md").write_text("# Beta\n\nBoats in the harbour.\n")
    (root / "sub" / "c.md").write_text("# Gamma\n\nWaves and tides.\n")
    built = Vault(
        source_dir=root,
        settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
        embedding_provider=MockEmbeddingProvider(),
    )
    built.index.build_index()
    built.index.build_embeddings()
    try:
        yield built
    finally:
        built.close()


def _ghost(vault: Vault, path: str, vector: list[float], folder: str = "") -> None:
    """Store rows for *path*, a note the keyword index has no rows for."""
    store = vault._vectors
    assert store is not None
    store.replace_document(
        doc(path, [vector], token="t-ghost", folder=folder, title="Ghost"),
        expected_token=ANY_TOKEN,
    )


def _query_vector(vault: Vault) -> list[float]:
    provider = vault._index_mgr._embeddings.embedding_provider
    assert provider is not None
    return list(provider.embed_query([QUERY])[0])


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_no_search_returns_an_unindexed_notes_vectors(vault: Vault, mode: str) -> None:
    _ghost(vault, "ghost.md", _query_vector(vault))  # the closest vector there is
    results = vault.reader.search(QUERY, mode=mode, limit=10)
    assert results
    assert "ghost.md" not in {r.path for r in results}


def test_get_similar_never_returns_an_unindexed_note(vault: Vault) -> None:
    store = vault._vectors
    assert store is not None
    _ghost(vault, "ghost.md", list(store.rows_for("a.md")[0].vector))
    similar = vault.reader.get_similar("a.md", limit=10)
    assert "ghost.md" not in {r.path for r in similar}


def test_a_folder_search_skips_only_the_unindexed_note(vault: Vault) -> None:
    _ghost(vault, "sub/ghost.md", _query_vector(vault), folder="sub")
    results = vault.reader.search(QUERY, mode="semantic", folder="sub", limit=10)
    assert [r.path for r in results] == ["sub/c.md"]
