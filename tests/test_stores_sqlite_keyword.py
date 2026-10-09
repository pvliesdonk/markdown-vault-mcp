"""The SQLite keyword backend: open outcomes and lifecycle (#1766)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import (
    StoreCorruptError,
    StoreUnavailableError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.interfaces import StoreBackend
from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend
from markdown_vault_mcp.types import ParsedNote
from tests.fixtures.store_contract import StoreLifecycleContract

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _note(path: str = "a.md") -> ParsedNote:
    return ParsedNote(
        path=path,
        frontmatter={},
        title="A",
        chunks=[],
        content_hash="h",
        modified_at=0.0,
    )


class TestOpenOutcomes:
    def test_satisfies_store_backend_and_names_itself(self) -> None:
        backend = SqliteKeywordBackend()
        assert isinstance(backend, StoreBackend)
        assert (backend.family, backend.name) == ("keyword", "sqlite")

    def test_opens_a_writable_file_index(self, tmp_path: Path) -> None:
        store = SqliteKeywordBackend().open(tmp_path / "index.db")
        try:
            assert isinstance(store, FTSIndex)
            store.upsert_note(_note())
            assert store.count_documents() == 1
        finally:
            store.close()

    def test_none_location_is_in_memory_and_always_writable(self) -> None:
        # The CLI search path: no index path, files not owned (#1758).
        store = SqliteKeywordBackend().open(None, owns_files=False)
        try:
            store.upsert_note(_note())
            assert store.count_documents() == 1
        finally:
            store.close()

    def test_memory_literal_is_in_memory_too(self) -> None:
        store = SqliteKeywordBackend().open(":memory:", owns_files=False)
        try:
            store.upsert_note(_note())
        finally:
            store.close()

    def test_an_identity_is_refused_for_a_keyword_store(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="identity"):
            SqliteKeywordBackend().open(tmp_path / "index.db", identity={"x": "y"})

    def test_a_parent_that_is_a_file_is_unavailable(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        location = blocker / "index.db"
        with pytest.raises(StoreUnavailableError) as info:
            SqliteKeywordBackend().open(location)
        assert info.value.backend == "sqlite"
        assert info.value.location == str(location)
        assert str(location) in str(info.value)
        assert blocker.read_text() == "not a directory"

    def test_a_file_that_is_not_a_database_is_corrupt_and_untouched(
        self, tmp_path: Path
    ) -> None:
        location = tmp_path / "index.db"
        junk = b"definitely not sqlite " * 64
        location.write_bytes(junk)
        with pytest.raises(StoreCorruptError) as info:
            SqliteKeywordBackend().open(location)
        assert info.value.location == str(location)
        assert location.read_bytes() == junk

    def test_a_newer_schema_propagates_as_unsupported(self, tmp_path: Path) -> None:
        location = tmp_path / "index.db"
        SqliteKeywordBackend().open(location).close()
        conn = sqlite3.connect(location)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '99')"
        )
        conn.commit()
        conn.close()
        with pytest.raises(StoreUnsupportedSchemaError):
            SqliteKeywordBackend().open(location, owns_files=False)

    def test_backend_configuration_reaches_the_index(self, tmp_path: Path) -> None:
        backend = SqliteKeywordBackend(
            indexed_frontmatter_fields=["kind"],
            searchable_frontmatter_fields=["summary"],
            fts_weights={"title": 2.0},
        )
        store = backend.open(tmp_path / "index.db")
        try:
            assert store._indexed_fields == ["kind"]
            assert store._searchable_fields == ("summary",)
            assert store._fts_weights == {"title": 2.0}
        finally:
            store.close()


class TestLifecycle(StoreLifecycleContract):
    @pytest.fixture
    def open_store(self, tmp_path: Path) -> Callable[[], Any]:
        location = tmp_path / "index.db"
        return lambda: SqliteKeywordBackend().open(location)

    @pytest.fixture
    def open_store_read_only(self, tmp_path: Path) -> Callable[[], Any]:
        location = tmp_path / "index.db"
        SqliteKeywordBackend().open(location).close()
        return lambda: SqliteKeywordBackend().open(location, owns_files=False)

    @pytest.fixture
    def touch_store(self) -> Callable[[Any], None]:
        return lambda store: store.count_documents()

    @pytest.fixture
    def write_to_store(self) -> Callable[[Any], None]:
        return lambda store: store.upsert_note(_note())

    @pytest.fixture
    def closed_error(self) -> type[Exception]:
        return sqlite3.ProgrammingError
