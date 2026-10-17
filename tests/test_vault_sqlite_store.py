"""A vault on EMBEDDING_STORE=sqlite (#1497)."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path

if TYPE_CHECKING:
    from pathlib import Path


class _Counting(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dim=16)
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed(texts)


def test_a_vault_on_the_sqlite_store_keeps_its_vectors_across_a_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
    provider = _Counting()

    def vault() -> Vault:
        return Vault(
            source_dir=root,
            settings=VaultSettings(
                index_path=tmp_path / "index.db",
                read_only=False,
                embedding_store="sqlite",
            ),
            embedding_provider=provider,
        )

    first = vault()
    try:
        first.index.build_index()
        first.index.build_embeddings()
        (root / "alpha.md").write_text("# Alpha\n\nQuinces and medlars.\n")
        first.index.reindex()
    finally:
        first.close()
    assert (tmp_path / "index-db-embeddings.sqlite3").is_file()
    assert not (tmp_path / "index-db-embeddings.json").exists()

    calls = provider.calls
    second = vault()
    try:
        second.index.build_index()
        assert second.index.build_embeddings() == 0
        assert provider.calls == calls
        store = second._vectors
        assert store is not None
        contents = [m["content"] for m in metadata_by_path(store)["alpha.md"]]
        assert any("Quinces" in c for c in contents)
        assert second.reader.search("medlars", mode="semantic")
    finally:
        second.close()


def test_importing_the_vault_registers_every_backend() -> None:
    """A fresh interpreter, so no other test module's imports register them."""
    import subprocess
    import sys

    script = (
        "import markdown_vault_mcp.vault\n"
        "from markdown_vault_mcp.stores.registry import backend_names\n"
        "print(backend_names('vector'), backend_names('keyword'))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "('numpy', 'sqlite') ('sqlite',)"


def test_a_failed_vector_write_does_not_stop_the_keyword_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.stores import sqlite_vectors

    root = tmp_path / "vault"
    root.mkdir()
    for name in ("a", "b", "c"):
        (root / f"{name}.md").write_text(f"# {name}\n\nOld words.\n")
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=tmp_path / "index.db", read_only=False, embedding_store="sqlite"
        ),
        embedding_provider=_Counting(),
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        for name in ("a", "b", "c"):
            (root / f"{name}.md").write_text(f"# {name}\n\nFresh{name} words.\n")

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise sqlite3.OperationalError("database or disk is full")

        monkeypatch.setattr(sqlite_vectors.SqliteVectorStore, "_commit", refuse)
        vault.index.reindex()
        for name in ("a", "b", "c"):
            hits = vault.reader.search(f"Fresh{name}", mode="keyword")
            assert [h.path for h in hits] == [f"{name}.md"]
    finally:
        monkeypatch.undo()
        vault.close()


def test_status_counts_the_sqlite_rows_before_the_store_opens(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    for name in ("a", "b", "c"):
        (root / f"{name}.md").write_text(f"# {name}\n\nWords {name}.\n")

    def vault() -> Vault:
        return Vault(
            source_dir=root,
            settings=VaultSettings(
                index_path=tmp_path / "index.db",
                read_only=False,
                embedding_store="sqlite",
            ),
            embedding_provider=_Counting(),
        )

    first = vault()
    try:
        first.index.build_index()
        first.index.build_embeddings()
        built = first.index.embeddings_status()["chunk_count"]
    finally:
        first.close()
    assert built == 3
    # A numpy commit file left from before the switch must not be read.
    (tmp_path / "index-db-embeddings.json").write_text('{"rows": [{}]}')
    second = vault()
    try:
        status = second.index.embeddings_status()
        assert (status["backend"], status["chunk_count"]) == ("sqlite", 3)
    finally:
        second.close()


def _unbuilt(tmp_path: Path, store: str) -> Vault:
    root = tmp_path / "vault"
    root.mkdir(exist_ok=True)
    (root / "a.md").write_text("# a\n\nWords.\n")
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=tmp_path / "index.db", read_only=False, embedding_store=store
        ),
        embedding_provider=_Counting(),
    )


class TestStatusBeforeTheStoreOpens:
    def test_no_sqlite_database_counts_zero(self, tmp_path: Path) -> None:
        vault = _unbuilt(tmp_path, "sqlite")
        try:
            assert vault.index.embeddings_status()["chunk_count"] == 0
        finally:
            vault.close()

    def test_an_empty_sqlite_file_counts_zero(self, tmp_path: Path) -> None:
        (tmp_path / "index-db-embeddings.sqlite3").write_bytes(b"")
        vault = _unbuilt(tmp_path, "sqlite")
        try:
            assert vault.index.embeddings_status()["chunk_count"] == 0
        finally:
            vault.close()

    def test_an_unreadable_sqlite_file_counts_zero_and_says_so(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "index-db-embeddings.sqlite3").write_bytes(b"not a db " * 200)
        vault = _unbuilt(tmp_path, "sqlite")
        try:
            with caplog.at_level(logging.WARNING):
                assert vault.index.embeddings_status()["chunk_count"] == 0
        finally:
            vault.close()
        assert [
            r
            for r in caplog.records
            if r.msg.startswith("embeddings_status_count_failed")
        ]

    def test_a_legacy_numpy_row_list_is_counted(self, tmp_path: Path) -> None:
        (tmp_path / "index-db-embeddings.json").write_text("[{}, {}]")
        vault = _unbuilt(tmp_path, "numpy")
        try:
            assert vault.index.embeddings_status()["chunk_count"] == 2
        finally:
            vault.close()

    def test_a_malformed_numpy_commit_file_counts_zero_and_says_so(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "index-db-embeddings.json").write_text("{not json")
        vault = _unbuilt(tmp_path, "numpy")
        try:
            with caplog.at_level(logging.WARNING):
                assert vault.index.embeddings_status()["chunk_count"] == 0
        finally:
            vault.close()
        assert [
            r
            for r in caplog.records
            if r.msg.startswith("embeddings_status_metadata_read_failed")
        ]


def _streamed_vault(root: Path, tmp_path: Path, **kw: object) -> Vault:
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=tmp_path / "index.db",
            read_only=False,
            embedding_store="sqlite",
            embedding_search="streamed",
            **kw,  # type: ignore[arg-type]
        ),
        embedding_provider=MockEmbeddingProvider(dim=16),
    )


def test_a_vault_on_streamed_search_keeps_no_rows_resident(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    vault = _streamed_vault(root, tmp_path, embedding_search_workspace_bytes=64)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        store = vault._vectors
        assert store is not None
        assert store._streamed is True
        assert store._workspace_bytes == 64
        assert store._snap.rows == ()
        assert vault.reader.search("pears", mode="semantic")
    finally:
        vault.close()


def test_streamed_search_on_the_numpy_store_is_refused(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import ConfigurationError

    root = tmp_path / "vault"
    root.mkdir()
    settings = VaultSettings(
        index_path=tmp_path / "index.db", read_only=False, embedding_search="streamed"
    )
    provider = MockEmbeddingProvider(dim=16)
    with pytest.raises(ConfigurationError, match="EMBEDDING_STORE"):
        Vault(source_dir=root, settings=settings, embedding_provider=provider)


def test_a_bad_workspace_from_a_library_caller_is_refused(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import ConfigurationError

    root = tmp_path / "vault"
    root.mkdir()
    with pytest.raises(ConfigurationError, match="workspace"):
        _streamed_vault(root, tmp_path, embedding_search_workspace_bytes=0)


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_a_failed_streamed_read_is_not_an_empty_result(
    tmp_path: Path, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    vault = _streamed_vault(root, tmp_path)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        store = vault._vectors
        assert store is not None

        class _Refusing:
            def execute(self, *_args: object) -> None:
                raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(store._registry, "conn", _Refusing)
        with pytest.raises(StoreUnavailableError):
            vault.reader.search("pears", mode=mode)
    finally:
        monkeypatch.undo()
        vault.close()
