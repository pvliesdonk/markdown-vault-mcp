"""The SQLite vector backend (#1497)."""

from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
    StoreCorruptError,
    StoreUnavailableError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
from markdown_vault_mcp.types import (
    ANY_TOKEN,
    DocumentRows,
    EmbeddingIdentity,
    VectorRow,
)
from tests.fixtures.source_neutral_contract import SourceNeutralContract
from tests.fixtures.store_contract import StoreLifecycleContract
from tests.fixtures.vector_store_contract import (
    IDENT,
    PersistentVectorStoreContract,
    VectorStoreContract,
    doc,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _base(tmp_path: Path) -> Path:
    return tmp_path / "vectors" / "idx-embeddings"


def _db(tmp_path: Path) -> Path:
    return _base(tmp_path).with_suffix(".sqlite3")


def _seed(tmp_path: Path) -> None:
    store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
    store.replace_document(doc("a.md", [[1, 0, 0]], token="ta"), expected_token=None)
    store.close()


def _sql(tmp_path: Path, statement: str) -> None:
    conn = sqlite3.connect(_db(tmp_path))
    try:
        conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


class TestBackend:
    def test_names_itself_and_is_registered(self) -> None:
        from markdown_vault_mcp.interfaces import StoreBackend, VectorBackend
        from markdown_vault_mcp.stores.registry import get_backend

        backend = SqliteVectorBackend()
        assert isinstance(backend, StoreBackend)
        assert isinstance(backend, VectorBackend)
        assert (backend.family, backend.name) == ("vector", "sqlite")
        assert get_backend("vector", "sqlite") is SqliteVectorBackend

    def test_an_identity_is_required(self) -> None:
        with pytest.raises(ValueError, match="EmbeddingIdentity"):
            SqliteVectorBackend().open(None)

    def test_in_memory_writes_no_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        store = SqliteVectorBackend().open(None, IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        store.checkpoint()
        store.close()
        assert list(tmp_path.iterdir()) == []

    def test_the_database_is_the_base_with_its_own_suffix(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        assert _db(tmp_path).is_file()

    def test_every_connection_commits_with_full_sync(self, tmp_path: Path) -> None:
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        conn = store._registry.conn()  # type: ignore[union-attr]
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        store.close()


class TestInMemoryConformance(VectorStoreContract, SourceNeutralContract):
    @pytest.fixture
    def make_store(self) -> Callable[..., Any]:
        return lambda identity: SqliteVectorBackend().open(None, identity)


class TestFileConformance(
    VectorStoreContract, PersistentVectorStoreContract, SourceNeutralContract
):
    @pytest.fixture
    def make_store(self, tmp_path: Path) -> Callable[..., Any]:
        return lambda identity: SqliteVectorBackend().open(_base(tmp_path), identity)

    @pytest.fixture
    def reopen_store(self, tmp_path: Path) -> Callable[..., Any]:
        return lambda identity, owns: SqliteVectorBackend().open(
            _base(tmp_path), identity, owns_files=owns
        )


class TestFileLifecycle(StoreLifecycleContract):
    @pytest.fixture
    def open_store(self, tmp_path: Path) -> Callable[[], Any]:
        return lambda: SqliteVectorBackend().open(_base(tmp_path), IDENT)

    @pytest.fixture
    def open_store_read_only(self, tmp_path: Path) -> Callable[[], Any]:
        return lambda: SqliteVectorBackend().open(
            _base(tmp_path), IDENT, owns_files=False
        )

    @pytest.fixture
    def touch_store(self) -> Callable[[Any], None]:
        return lambda store: store.count

    @pytest.fixture
    def write_to_store(self) -> Callable[[Any], None]:
        return lambda store: store.replace_document(
            doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN
        )

    @pytest.fixture
    def closed_error(self) -> type[Exception]:
        return RuntimeError


class TestRestart:
    def test_rows_tokens_identity_and_generation_survive_reopening(
        self, tmp_path: Path
    ) -> None:
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        store.publish_generation(
            IDENT, [doc("Ünïcode/ñ.md", [[1, 0, 0]], token="ta", folder="Ünïcode")]
        )
        store.replace_document(
            doc("b.md", [[0, 1, 0]], token="tb", content_extra="snow ☃"),
            expected_token=None,
        )
        store.close()
        again = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        assert (again.identity, again.generation) == (IDENT, 1)
        assert again.tokens() == {"Ünïcode/ñ.md": "ta", "b.md": "tb"}
        assert again.rows_for("Ünïcode/ñ.md")[0].metadata == {
            "path": "Ünïcode/ñ.md",
            "folder": "Ünïcode",
            "content": "Ünïcode/ñ.md#0",
        }
        assert again.rows_for("b.md")[0].metadata["content_extra"] == "snow ☃"
        assert again.search([0, 1, 0], limit=1)[0]["path"] == "b.md"
        again.close()

    def test_an_identity_of_unknown_dimension_takes_the_stored_one(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        unknown = EmbeddingIdentity("fake", "m", "", "v1", 0)
        store = SqliteVectorBackend().open(_base(tmp_path), unknown)
        assert store.identity.dimension == 3
        store.close()

    def test_a_document_without_rows_keeps_its_token_across_reopening(
        self, tmp_path: Path
    ) -> None:
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        store.replace_document(doc("e.md", [], token="te"), expected_token=None)
        store.close()
        again = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        assert (again.count, again.tokens()) == (0, {"e.md": "te"})
        again.close()

    def test_the_folder_column_follows_the_metadata(self, tmp_path: Path) -> None:
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        store.replace_document(
            doc("a.md", [[1, 0, 0]], folder="x/y"), expected_token=None
        )
        store.replace_document(
            DocumentRows("b.md", "t", [VectorRow([0, 1, 0], {"folder": None})]),
            expected_token=None,
        )
        store.replace_document(
            DocumentRows("c.md", "t", [VectorRow([0, 0, 1], {})]), expected_token=None
        )
        store.close()
        conn = sqlite3.connect(_db(tmp_path))
        folders = dict(conn.execute("SELECT path, folder FROM chunks"))
        conn.close()
        assert folders == {"a.md": "x/y", "b.md": "", "c.md": ""}

    def test_metadata_json_cannot_hold_is_refused_and_changes_nothing(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        bad = DocumentRows("a.md", "tb", [VectorRow([0, 1, 0], {"tags": {"x"}})])
        with pytest.raises(ValueError, match="cannot be stored"):
            store.replace_document(bad, expected_token="ta")
        assert store.tokens() == {"a.md": "ta"}
        store.close()
        assert SqliteVectorBackend().open(_base(tmp_path), IDENT).tokens() == {
            "a.md": "ta"
        }


class TestOpenOutcomes:
    def test_no_file_opens_an_empty_store_and_creates_nothing(
        self, tmp_path: Path
    ) -> None:
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        assert store.count == 0
        store.close()
        assert not _base(tmp_path).parent.exists()

    def test_a_parent_that_is_a_file_is_unavailable(self, tmp_path: Path) -> None:
        (tmp_path / "vectors").write_text("not a directory")
        with pytest.raises(StoreUnavailableError):
            SqliteVectorBackend().open(_base(tmp_path), IDENT)

    def test_a_file_that_is_not_a_database_is_corrupt_and_left_alone(
        self, tmp_path: Path
    ) -> None:
        _db(tmp_path).parent.mkdir(parents=True)
        garbage = b"not a database " * 100
        _db(tmp_path).write_bytes(garbage)
        with pytest.raises(StoreCorruptError):
            SqliteVectorBackend().open(_base(tmp_path), IDENT)
        assert _db(tmp_path).read_bytes() == garbage

    def test_a_database_without_the_vector_tables_is_corrupt(
        self, tmp_path: Path
    ) -> None:
        _db(tmp_path).parent.mkdir(parents=True)
        _sql(tmp_path, "CREATE TABLE other (x)")
        with pytest.raises(StoreCorruptError, match="tables"):
            SqliteVectorBackend().open(_base(tmp_path), IDENT)

    def test_a_newer_format_is_unsupported(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _sql(tmp_path, "UPDATE meta SET value = '2' WHERE key = 'format_version'")
        with pytest.raises(StoreUnsupportedSchemaError):
            SqliteVectorBackend().open(_base(tmp_path), IDENT)

    def test_a_vector_of_the_wrong_length_is_corrupt(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _sql(tmp_path, "UPDATE chunks SET vector = x'00000000'")
        with pytest.raises(StoreCorruptError, match="bytes"):
            SqliteVectorBackend().open(_base(tmp_path), IDENT)

    def test_metadata_that_is_not_an_object_is_corrupt(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _sql(tmp_path, "UPDATE chunks SET metadata = '[1, 2]'")
        with pytest.raises(StoreCorruptError, match="metadata"):
            SqliteVectorBackend().open(_base(tmp_path), IDENT)

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root ignores permission bits",
    )
    def test_a_refused_read_is_unavailable(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _db(tmp_path).chmod(0)
        try:
            with pytest.raises(StoreUnavailableError):
                SqliteVectorBackend().open(_base(tmp_path), IDENT)
        finally:
            _db(tmp_path).chmod(0o644)

    def test_a_read_only_open_refuses_writes(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT, owns_files=False)
        with pytest.raises(ReadOnlyError):
            store.remove_document("a.md", expected_token=ANY_TOKEN)
        store.close()
        assert SqliteVectorBackend().open(_base(tmp_path), IDENT).count == 1


class TestOpenFresh:
    def test_the_old_rows_stay_until_the_first_write(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        other = EmbeddingIdentity("fake", "m2", "", "v1", 2)
        fresh = SqliteVectorBackend().open_fresh(_base(tmp_path), other)
        assert (fresh.count, fresh.identity) == (0, other)
        reader = SqliteVectorBackend().open(_base(tmp_path), IDENT, owns_files=False)
        assert reader.count == 1
        reader.close()
        fresh.replace_document(doc("new.md", [[1, 0]]), expected_token=ANY_TOKEN)
        fresh.close()
        again = SqliteVectorBackend().open(_base(tmp_path), other)
        assert again.tokens() == {"new.md": "t1"}
        again.close()

    def test_a_conflicting_first_write_keeps_the_old_rows(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        fresh = SqliteVectorBackend().open_fresh(_base(tmp_path), IDENT)
        outcome = fresh.replace_document(
            doc("new.md", [[1, 0, 0]]), expected_token="stale"
        )
        assert outcome == "conflict"
        fresh.close()
        assert SqliteVectorBackend().open(_base(tmp_path), IDENT).tokens() == {
            "a.md": "ta"
        }

    def test_a_file_that_is_not_a_database_is_replaced(self, tmp_path: Path) -> None:
        _db(tmp_path).parent.mkdir(parents=True)
        _db(tmp_path).write_bytes(b"not a database " * 100)
        fresh = SqliteVectorBackend().open_fresh(_base(tmp_path), IDENT)
        fresh.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        fresh.close()
        assert SqliteVectorBackend().open(_base(tmp_path), IDENT).count == 1

    def test_a_newer_format_is_not_replaced(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _sql(tmp_path, "UPDATE meta SET value = '2' WHERE key = 'format_version'")
        fresh = SqliteVectorBackend().open_fresh(_base(tmp_path), IDENT)
        with pytest.raises(StoreUnsupportedSchemaError):
            fresh.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        fresh.close()

    def test_in_memory(self) -> None:
        store = SqliteVectorBackend().open_fresh(None, IDENT)
        assert store.count == 0
        store.close()


class TestDamagedPages:
    def test_a_rebuild_replaces_a_database_with_damaged_pages(
        self, tmp_path: Path
    ) -> None:
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        store.publish_generation(
            IDENT, [doc(f"n{i}.md", [[1, 0, 0]], pad="x" * 400) for i in range(2000)]
        )
        store.close()
        size = _db(tmp_path).stat().st_size
        with _db(tmp_path).open("r+b") as fh:
            fh.seek(size // 2)
            fh.write(os.urandom(32 * 1024))
        with pytest.raises(StoreCorruptError):
            SqliteVectorBackend().open(_base(tmp_path), IDENT)
        fresh = SqliteVectorBackend().open_fresh(_base(tmp_path), IDENT)
        fresh.publish_generation(IDENT, [doc("new.md", [[0, 1, 0]])])
        fresh.close()
        again = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        assert again.tokens() == {"new.md": "t1"}
        again.close()


class TestWalAfterAGeneration:
    def test_a_generation_publish_leaves_no_store_sized_wal(
        self, tmp_path: Path
    ) -> None:
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        store.publish_generation(
            IDENT, [doc(f"n{i}.md", [[1, 0, 0]], pad="x" * 400) for i in range(2000)]
        )
        wal = _db(tmp_path).with_name(_db(tmp_path).name + "-wal")
        size = wal.stat().st_size if wal.exists() else 0
        assert size < 64 * 1024, size
        store.close()
