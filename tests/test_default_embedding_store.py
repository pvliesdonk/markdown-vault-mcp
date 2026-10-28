"""The default embedding store is SQLite, searched streamed (#1900)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.config_sections.indexing import store_default_search
from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path


def _status(tmp_path: Path, **settings: Any) -> dict[str, Any]:
    """Build a two-note vault with *settings* and return its embeddings status."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("# A\n\nApples and pears.\n")
    (root / "b.md").write_text("# B\n\nBoats and harbours.\n")
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, **settings),
        embedding_provider=MockEmbeddingProvider(),
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        return vault.index.embeddings_status()
    finally:
        vault.close()


def test_each_store_has_its_default_search_mode() -> None:
    assert [store_default_search(s) for s in ("numpy", "sqlite", "postgres")] == [
        "resident",
        "streamed",
        "streamed",
    ]


def test_a_library_vault_defaults_to_a_streamed_sqlite_store(tmp_path: Path) -> None:
    status = _status(tmp_path, index_path=tmp_path / "index.db")
    assert (status["backend"], status["search_mode"]) == ("sqlite", "streamed")
    assert (tmp_path / "index-db-embeddings.sqlite3").exists()


def test_a_numpy_library_store_needs_no_search_mode(tmp_path: Path) -> None:
    status = _status(
        tmp_path, index_path=tmp_path / "index.db", embedding_store="numpy"
    )
    assert (status["backend"], status["search_mode"]) == ("numpy", "resident")


def test_an_explicit_resident_sqlite_store_stays_resident(tmp_path: Path) -> None:
    status = _status(
        tmp_path, index_path=tmp_path / "index.db", embedding_search="resident"
    )
    assert (status["backend"], status["search_mode"]) == ("sqlite", "resident")


def test_an_in_memory_default_store_is_resident(tmp_path: Path) -> None:
    status = _status(tmp_path)
    assert (status["backend"], status["search_mode"]) == ("sqlite", "resident")


def test_a_numpy_store_still_refuses_streamed_search(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="resident only"):
        _status(
            tmp_path,
            index_path=tmp_path / "index.db",
            embedding_store="numpy",
            embedding_search="streamed",
        )
