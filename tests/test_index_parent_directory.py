"""Opening an index whose parent folders do not exist (#1730)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from markdown_vault_mcp._fts_connection import SqliteConnectionRegistry
from markdown_vault_mcp.exceptions import StoreUnavailableError
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.stores import _writer_lock
from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend
from markdown_vault_mcp.vault import Vault, VaultSettings


@pytest.mark.parametrize("as_string", [False, True])
def test_direct_index_creates_missing_parents(tmp_path: Path, as_string: bool) -> None:
    location = tmp_path / "new folder" / "nested" / "index.db"
    index = FTSIndex(str(location) if as_string else location)
    try:
        assert location.is_file()
        assert index.count_documents() == 0
    finally:
        index.close()


def test_backend_creates_parents_without_a_platform_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_writer_lock, "fcntl", None)
    location = tmp_path / "new" / "nested" / "index.db"
    index = SqliteKeywordBackend().open(location)
    try:
        assert location.is_file()
        assert index.count_documents() == 0
    finally:
        index.close()


def test_vault_builds_and_searches_an_index_in_a_new_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_writer_lock, "fcntl", None)
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("# Airplane\n\nA rendering note.\n", encoding="utf-8")
    location = tmp_path / "new" / "nested" / "index.db"
    vault = Vault(source_dir=source, settings=VaultSettings(index_path=location))
    try:
        vault.index.build_index()
        results = vault.reader.search("rendering", mode="keyword")
        assert [result.path for result in results] == ["a.md"]
        assert location.is_file()
    finally:
        vault.close()


@pytest.mark.parametrize("through_backend", [False, True])
def test_read_only_open_does_not_create_missing_parents(
    tmp_path: Path, through_backend: bool
) -> None:
    location = tmp_path / "missing" / "nested" / "index.db"
    if through_backend:
        with pytest.raises(StoreUnavailableError):
            SqliteKeywordBackend().open(location, owns_files=False)
    else:
        with pytest.raises(sqlite3.OperationalError):
            FTSIndex(location, read_only=True)
    assert not location.parent.parent.exists()


@pytest.mark.parametrize("as_path", [False, True])
def test_in_memory_open_creates_no_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_path: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    registry = SqliteConnectionRegistry(Path(":memory:") if as_path else ":memory:")
    try:
        assert registry.conn().execute("SELECT 1").fetchone()[0] == 1
        assert list(tmp_path.iterdir()) == []
    finally:
        registry.close()


def test_refused_parent_creation_is_unavailable_and_leaves_notes_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_writer_lock, "fcntl", None)
    location = tmp_path / "not a database" / "index.db"
    note = tmp_path / "note.md"
    note.write_text("Keep this note.", encoding="utf-8")
    original_mkdir = Path.mkdir

    def refuse_index_parent(path: Path, *args: object, **kwargs: object) -> None:
        if path == location.parent:
            raise PermissionError(f"directory creation refused: {path}")
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", refuse_index_parent)
    with pytest.raises(StoreUnavailableError) as error:
        SqliteKeywordBackend().open(location)
    assert error.value.backend == "sqlite"
    assert error.value.location == str(location)
    assert isinstance(error.value.__cause__, PermissionError)
    assert note.read_text(encoding="utf-8") == "Keep this note."
    assert not location.parent.exists()
