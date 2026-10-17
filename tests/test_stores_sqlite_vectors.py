"""The SQLite vector backend (#1497)."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
    StoreCorruptError,
    StoreIncompatibleError,
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
    from collections.abc import Callable, Iterator


def _base(tmp_path: Path) -> Path:
    return tmp_path / "vectors" / "idx-embeddings"


def _db(tmp_path: Path) -> Path:
    return _base(tmp_path).with_suffix(".sqlite3")


@pytest.fixture
def opened() -> Iterator[list[Any]]:
    """The stores a factory fixture opens, closed when the test ends."""
    stores: list[Any] = []
    yield stores
    for store in stores:
        store.close()


def _tracked(stores: list[Any], store: Any) -> Any:
    stores.append(store)
    return store


def _reopened(tmp_path: Path) -> tuple[int, dict[str, Any]]:
    """Open the store again, read its row count and tokens, and close it."""
    store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
    try:
        return store.count, store.tokens()
    finally:
        store.close()


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
        backend = SqliteVectorBackend()
        with pytest.raises(ValueError, match="EmbeddingIdentity"):
            backend.open(None)

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
    def make_store(self, opened: list[Any]) -> Callable[..., Any]:
        return lambda identity: _tracked(
            opened, SqliteVectorBackend().open(None, identity)
        )


class TestFileConformance(
    VectorStoreContract, PersistentVectorStoreContract, SourceNeutralContract
):
    @pytest.fixture
    def make_store(self, tmp_path: Path, opened: list[Any]) -> Callable[..., Any]:
        return lambda identity: _tracked(
            opened, SqliteVectorBackend().open(_base(tmp_path), identity)
        )

    @pytest.fixture
    def reopen_store(self, tmp_path: Path, opened: list[Any]) -> Callable[..., Any]:
        return lambda identity, owns: _tracked(
            opened,
            SqliteVectorBackend().open(_base(tmp_path), identity, owns_files=owns),
        )


def _streamed(**kw: Any) -> SqliteVectorBackend:
    return SqliteVectorBackend(
        search="streamed", workspace_bytes=24, **kw
    )  # 2 rows at dimension 3


class TestStreamedFileConformance(
    VectorStoreContract, PersistentVectorStoreContract, SourceNeutralContract
):
    @pytest.fixture
    def make_store(self, tmp_path: Path, opened: list[Any]) -> Callable[..., Any]:
        return lambda identity: _tracked(
            opened, _streamed().open(_base(tmp_path), identity)
        )

    @pytest.fixture
    def reopen_store(self, tmp_path: Path, opened: list[Any]) -> Callable[..., Any]:
        return lambda identity, owns: _tracked(
            opened, _streamed().open(_base(tmp_path), identity, owns_files=owns)
        )


class TestStreamedFileLifecycle(StoreLifecycleContract):
    @pytest.fixture
    def open_store(self, tmp_path: Path) -> Callable[[], Any]:
        return lambda: _streamed().open(_base(tmp_path), IDENT)

    @pytest.fixture
    def open_store_read_only(self, tmp_path: Path) -> Callable[[], Any]:
        return lambda: _streamed().open(_base(tmp_path), IDENT, owns_files=False)

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


class TestStreamedBackend:
    @pytest.mark.parametrize("kw", [{"search": "lazy"}, {"workspace_bytes": 0}])
    def test_refuses_a_bad_mode_or_workspace(self, kw: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            SqliteVectorBackend(**kw)

    def test_in_memory_stays_resident(self) -> None:
        store = _streamed().open(None, IDENT)
        assert store._streamed is False
        store.close()

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE chunks SET vector = x'00000000'",
            "UPDATE chunks SET metadata = '[1, 2]'",
        ],
        ids=["vector", "metadata"],
    )
    def test_open_validates_every_row(self, tmp_path: Path, statement: str) -> None:
        _seed(tmp_path)
        _sql(tmp_path, statement)
        backend, base = _streamed(), _base(tmp_path)
        with pytest.raises(StoreCorruptError):
            backend.open(base, IDENT)

    @pytest.mark.parametrize(
        ("statement", "read"),
        [
            ("UPDATE chunks SET vector = x'00000000'", "search"),
            ("UPDATE chunks SET vector = x'00000000'", "rows_for"),
            ("UPDATE chunks SET metadata = '[1, 2]'", "search"),
            ("UPDATE chunks SET metadata = '[1, 2]'", "rows_for"),
        ],
    )
    def test_a_row_damaged_after_open_is_corrupt_at_the_read(
        self, tmp_path: Path, statement: str, read: str
    ) -> None:
        _seed(tmp_path)
        store = _streamed().open(_base(tmp_path), IDENT)
        _sql(tmp_path, statement)
        call = {
            "search": lambda: store.search([1, 0, 0]),
            "rows_for": lambda: store.rows_for("a.md"),
        }[read]
        with pytest.raises(StoreCorruptError):
            call()
        store.close()

    def test_a_failing_read_is_unavailable(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        store = _streamed().open(_base(tmp_path), IDENT)

        class _Refusing:
            def execute(self, *_args: Any) -> None:
                raise sqlite3.OperationalError("disk I/O error")

        real = store._registry.conn
        store._registry.conn = _Refusing  # type: ignore[method-assign]
        with pytest.raises(StoreUnavailableError):
            store.search([1, 0, 0])
        store._registry.conn = real  # type: ignore[method-assign]
        store.close()

    def test_a_read_racing_close_reports_the_store_closed(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        store = _streamed().open(_base(tmp_path), IDENT)

        def closing_then_refused() -> sqlite3.Connection:
            store._closed = True  # close() won the race after the check
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

        real = store._registry.conn
        store._registry.conn = closing_then_refused  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="closed"):
            store.search([1, 0, 0])
        store._registry.conn = real  # type: ignore[method-assign]
        store._closed = False
        store.close()  # release the database the staged race left open

    def test_a_fresh_store_reads_empty_until_its_first_write_commits(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        fresh = _streamed().open_fresh(_base(tmp_path), IDENT)
        empty = (0, [], {}, [])
        read = lambda: (  # noqa: E731
            fresh.count,
            fresh.search([1, 0, 0]),
            fresh.tokens(),
            fresh.rows_for("a.md"),
        )
        assert read() == empty
        outcome = fresh.replace_document(
            doc("new.md", [[1, 0, 0]]), expected_token="stale"
        )
        assert outcome == "conflict"
        assert read() == empty
        fresh.replace_document(doc("new.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        assert fresh.tokens() == {"new.md": "t1"}
        fresh.close()

    def test_a_reader_sees_the_owners_later_writes(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        owner = _streamed().open(_base(tmp_path), IDENT)
        reader = _streamed().open(_base(tmp_path), IDENT, owns_files=False)
        owner.replace_document(
            doc("b.md", [[0, 1, 0]], token="tb"), expected_token=None
        )
        assert reader.tokens() == {"a.md": "ta", "b.md": "tb"}
        assert reader.count == 2
        reader.close()
        owner.close()

    @pytest.mark.parametrize("dimension", [3, 4], ids=["same-width", "wider"])
    def test_a_reader_refuses_rows_of_another_identity(
        self, tmp_path: Path, dimension: int
    ) -> None:
        _seed(tmp_path)
        owner = _streamed().open(_base(tmp_path), IDENT)
        reader = _streamed().open(_base(tmp_path), IDENT, owns_files=False)
        other = EmbeddingIdentity("fake", "other", "", "v1", dimension)
        owner.publish_generation(other, [doc("b.md", [[0, 1, 0, 0][:dimension]])])
        with pytest.raises(StoreIncompatibleError):
            reader.search([0, 1, 0])
        with pytest.raises(StoreIncompatibleError):
            reader.similar_to_document("b.md")
        reader.close()
        owner.close()

    def test_a_reader_reports_the_owners_generation(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        owner = _streamed().open(_base(tmp_path), IDENT)
        reader = _streamed().open(_base(tmp_path), IDENT, owns_files=False)
        owner.publish_generation(IDENT, [doc("b.md", [[0, 1, 0]])])
        assert reader.generation == owner.generation == 1
        reader.close()
        owner.close()

    def test_removing_an_absent_path_writes_nothing(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        store = _streamed().open(_base(tmp_path), IDENT)
        wal = Path(f"{_db(tmp_path)}-wal")
        before = wal.stat().st_size if wal.exists() else 0
        assert store.remove_document("nope.md", expected_token=None) == "published"
        assert store.remove_document("nope.md", expected_token="t") == "conflict"
        assert (wal.stat().st_size if wal.exists() else 0) == before
        store.close()


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
        assert _reopened(tmp_path)[1] == {"a.md": "ta"}


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
        backend, base = SqliteVectorBackend(), _base(tmp_path)
        with pytest.raises(StoreUnavailableError):
            backend.open(base, IDENT)

    def test_a_file_that_is_not_a_database_is_corrupt_and_left_alone(
        self, tmp_path: Path
    ) -> None:
        _db(tmp_path).parent.mkdir(parents=True)
        garbage = b"not a database " * 100
        _db(tmp_path).write_bytes(garbage)
        backend, base = SqliteVectorBackend(), _base(tmp_path)
        with pytest.raises(StoreCorruptError):
            backend.open(base, IDENT)
        assert _db(tmp_path).read_bytes() == garbage

    def test_a_database_without_the_vector_tables_is_corrupt(
        self, tmp_path: Path
    ) -> None:
        _db(tmp_path).parent.mkdir(parents=True)
        _sql(tmp_path, "CREATE TABLE other (x)")
        backend, base = SqliteVectorBackend(), _base(tmp_path)
        with pytest.raises(StoreCorruptError, match="tables"):
            backend.open(base, IDENT)

    def test_a_newer_format_is_unsupported(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _sql(tmp_path, "UPDATE meta SET value = '2' WHERE key = 'format_version'")
        backend, base = SqliteVectorBackend(), _base(tmp_path)
        with pytest.raises(StoreUnsupportedSchemaError):
            backend.open(base, IDENT)

    def test_a_vector_of_the_wrong_length_is_corrupt(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _sql(tmp_path, "UPDATE chunks SET vector = x'00000000'")
        backend, base = SqliteVectorBackend(), _base(tmp_path)
        with pytest.raises(StoreCorruptError, match="bytes"):
            backend.open(base, IDENT)

    def test_metadata_that_is_not_an_object_is_corrupt(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _sql(tmp_path, "UPDATE chunks SET metadata = '[1, 2]'")
        backend, base = SqliteVectorBackend(), _base(tmp_path)
        with pytest.raises(StoreCorruptError, match="metadata"):
            backend.open(base, IDENT)

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root ignores permission bits",
    )
    def test_a_refused_read_is_unavailable(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _db(tmp_path).chmod(0)
        try:
            backend, base = SqliteVectorBackend(), _base(tmp_path)
            with pytest.raises(StoreUnavailableError):
                backend.open(base, IDENT)
        finally:
            _db(tmp_path).chmod(0o644)

    def test_a_read_only_open_refuses_writes(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT, owns_files=False)
        with pytest.raises(ReadOnlyError):
            store.remove_document("a.md", expected_token=ANY_TOKEN)
        store.close()
        assert _reopened(tmp_path)[0] == 1


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
        assert _reopened(tmp_path)[1] == {"a.md": "ta"}

    def test_a_file_that_is_not_a_database_is_replaced(self, tmp_path: Path) -> None:
        _db(tmp_path).parent.mkdir(parents=True)
        _db(tmp_path).write_bytes(b"not a database " * 100)
        fresh = SqliteVectorBackend().open_fresh(_base(tmp_path), IDENT)
        fresh.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        fresh.close()
        assert _reopened(tmp_path)[0] == 1

    def test_a_newer_format_is_not_replaced(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _sql(tmp_path, "UPDATE meta SET value = '2' WHERE key = 'format_version'")
        fresh = SqliteVectorBackend().open_fresh(_base(tmp_path), IDENT)
        document = doc("a.md", [[1, 0, 0]])
        with pytest.raises(StoreUnsupportedSchemaError):
            fresh.replace_document(document, expected_token=ANY_TOKEN)
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
        backend, base = SqliteVectorBackend(), _base(tmp_path)
        with pytest.raises(StoreCorruptError):
            backend.open(base, IDENT)
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


def _queued_behind_close(store: Any, call: Callable[[], Any]) -> list[Exception]:
    """Run *call* on a thread while a close holds the store's lock; return its error."""
    import threading
    import time

    errors: list[Exception] = []

    def run() -> None:
        try:
            call()
        except Exception as exc:  # the test inspects whatever it raised
            errors.append(exc)

    store._lock.acquire()
    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.05)
    store._closed = True  # what close() does while it holds the lock
    store._registry.close()
    store._lock.release()
    worker.join()
    return errors


class TestLifecycleRaces:
    def test_a_checkpoint_queued_behind_close_reports_the_store_closed(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        errors = _queued_behind_close(store, store.checkpoint)
        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)

    def test_a_close_queued_behind_close_logs_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _seed(tmp_path)
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        with caplog.at_level(logging.WARNING):
            errors = _queued_behind_close(store, store.close)
        assert errors == []
        assert not [r for r in caplog.records if "truncate_failed" in r.msg]

    def test_a_failed_checkpoint_is_logged_not_raised(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _seed(tmp_path)
        store = SqliteVectorBackend().open(_base(tmp_path), IDENT)

        class _Refusing:
            def execute(self, *_args: Any) -> None:
                raise sqlite3.OperationalError("disk I/O error")

        real = store._registry.conn
        store._registry.conn = _Refusing  # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING):
            store.checkpoint()
        store._registry.conn = real  # type: ignore[method-assign]
        assert [
            r
            for r in caplog.records
            if r.msg.startswith("sqlite_vector_store_checkpoint_failed")
        ]
        store.close()


class TestForeignDatabases:
    def test_a_writable_open_leaves_a_foreign_database_as_it_was(
        self, tmp_path: Path
    ) -> None:
        _db(tmp_path).parent.mkdir(parents=True)
        _sql(tmp_path, "CREATE TABLE other (x)")
        backend, base = SqliteVectorBackend(), _base(tmp_path)
        with pytest.raises(StoreCorruptError):
            backend.open(base, IDENT)
        conn = sqlite3.connect(_db(tmp_path))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "delete"

    def test_a_format_version_that_is_not_ascii_digits_is_corrupt(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        _sql(tmp_path, "UPDATE meta SET value = '²' WHERE key = 'format_version'")
        backend, base = SqliteVectorBackend(), _base(tmp_path)
        with pytest.raises(StoreCorruptError, match="format_version"):
            backend.open(base, IDENT)


class TestDeferredCreation:
    def test_a_first_write_under_a_file_is_unavailable_and_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "vectors").write_text("not a directory")
        fresh = SqliteVectorBackend().open_fresh(_base(tmp_path), IDENT)
        document = doc("a.md", [[1, 0, 0]])
        with (
            caplog.at_level(logging.WARNING),
            pytest.raises(StoreUnavailableError),
        ):
            fresh.replace_document(document, expected_token=ANY_TOKEN)
        assert [
            r
            for r in caplog.records
            if r.msg.startswith("sqlite_vector_store_write_failed")
        ]
        fresh.close()

    def test_an_unusable_file_that_cannot_be_removed_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _db(tmp_path).parent.mkdir(parents=True)
        _db(tmp_path).write_bytes(b"not a database " * 100)
        real_unlink = Path.unlink

        def refuse(self: Path, missing_ok: bool = False) -> None:
            if self == _db(tmp_path):
                raise PermissionError("refused")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", refuse)
        fresh = SqliteVectorBackend().open_fresh(_base(tmp_path), IDENT)
        document = doc("a.md", [[1, 0, 0]])
        with pytest.raises(StoreUnavailableError):
            fresh.replace_document(document, expected_token=ANY_TOKEN)
        fresh.close()

    def test_a_writable_open_of_a_store_takes_no_write_lock(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        holder = sqlite3.connect(_db(tmp_path), isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            store = SqliteVectorBackend().open(_base(tmp_path), IDENT)
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        assert store.count == 1
        store.close()  # its TRUNCATE checkpoint would wait out a held write lock
