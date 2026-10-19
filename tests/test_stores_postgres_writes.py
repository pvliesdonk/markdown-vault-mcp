"""Writes to the PostgreSQL vector store, and the reads that check them (#1504)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg import sql

from markdown_vault_mcp.stores import _postgres as pg
from markdown_vault_mcp.stores import _postgres_tables as tables
from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend
from markdown_vault_mcp.types import (
    ANY_TOKEN,
    DocumentRows,
    EmbeddingIdentity,
    VectorRow,
)
from tests.fixtures.store_contract import StoreLifecycleContract

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.fixtures.postgres import PostgresSchema

_ID = EmbeddingIdentity("fake", "m", "", "v1", 3)


def _doc(
    path: str,
    vectors: list[list[float]],
    *,
    revision: str | None = "t1",
    folder: str = "",
) -> DocumentRows:
    rows = [
        VectorRow(vector=v, metadata={"folder": folder, "content": f"{path}#{i}"})
        for i, v in enumerate(vectors)
    ]
    return DocumentRows(path=path, token=revision, rows=rows)


@pytest.fixture
def conn(postgres_schema: PostgresSchema) -> Iterator[psycopg.Connection[Any]]:
    conninfo = pg.prepare_conninfo(postgres_schema.dsn, schema=postgres_schema.name)
    with pg.connect(conninfo, schema=postgres_schema.name) as connection:
        yield connection


@pytest.mark.parametrize(
    ("configured", "inside"),
    [
        ("off", "on"),
        ("local", "on"),
        ("remote_write", "on"),
        ("on", "on"),
        ("remote_apply", "remote_apply"),
    ],
)
def test_make_durable_raises_only_weaker_settings(
    conn: psycopg.Connection[Any], configured: str, inside: str
) -> None:
    conn.execute(sql.SQL("SET synchronous_commit = {}").format(sql.Literal(configured)))
    with conn.transaction():
        tables.make_durable(conn, lock_timeout_ms=1234)
        assert conn.execute("SHOW synchronous_commit").fetchone() == (inside,)
        assert conn.execute("SHOW lock_timeout").fetchone() == ("1234ms",)
    assert conn.execute("SHOW synchronous_commit").fetchone() == (configured,)


def test_lock_meta_returns_the_row_and_holds_it(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    ext = pg.find_extension(conn, schema=name)
    with conn.transaction():
        tables.create_tables(conn, schema=name, extension=ext, identity=_ID)
    with conn.transaction():
        meta = tables.lock_meta(conn, schema=name)
        assert meta.identity == _ID
        with psycopg.connect(postgres_schema.dsn, autocommit=True) as other:
            other.execute("SET lock_timeout = 200")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                other.execute(
                    sql.SQL("SELECT 1 FROM {}.vector_meta FOR UPDATE").format(
                        sql.Identifier(name)
                    )
                )


def test_reads_of_an_empty_store(postgres_schema: PostgresSchema) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    try:
        assert store.tokens() == {}
        assert store.rows_for("a.md") == []
        assert store.count == 0
    finally:
        store.close()


@pytest.fixture
def store(postgres_schema: PostgresSchema) -> Iterator[Any]:
    opened = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    yield opened
    opened.close()


def test_the_first_write_creates_the_tables(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    with psycopg.connect(postgres_schema.dsn) as admin:
        before = admin.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = %s",
            (postgres_schema.name,),
        ).fetchone()
    assert before == (0,)
    assert (
        store.replace_document(_doc("a.md", [[3, 4, 0]]), expected_token=ANY_TOKEN)
        == "published"
    )
    assert store.tokens() == {"a.md": "t1"}
    assert store.count == 1
    (row,) = store.rows_for("a.md")
    assert row.vector == pytest.approx([0.6, 0.8, 0.0])
    assert row.metadata == {"folder": "", "content": "a.md#0", "path": "a.md"}


def test_replace_swaps_a_documents_rows(store: Any) -> None:
    store.replace_document(
        _doc("a.md", [[1, 0, 0], [0, 1, 0]], revision="t1"), expected_token=ANY_TOKEN
    )
    store.replace_document(
        _doc("a.md", [[0, 0, 1]], revision="t2"), expected_token="t1"
    )
    assert [r.vector for r in store.rows_for("a.md")] == [pytest.approx([0, 0, 1])]
    assert store.tokens() == {"a.md": "t2"}


@pytest.mark.parametrize(
    ("stored", "expected", "outcome"),
    [
        (None, None, "published"),
        ("t1", "t1", "published"),
        ("t1", "t0", "conflict"),
        ("t1", None, "conflict"),
        (None, "t1", "conflict"),
        ("t1", ANY_TOKEN, "published"),
    ],
)
def test_the_token_condition(
    store: Any, stored: str | None, expected: object, outcome: str
) -> None:
    if stored is not None:
        store.replace_document(
            _doc("a.md", [[1, 0, 0]], revision=stored), expected_token=ANY_TOKEN
        )
    result = store.replace_document(
        _doc("a.md", [[0, 1, 0]], revision="new"), expected_token=expected
    )
    assert result == outcome
    if outcome == "conflict":
        assert store.tokens().get("a.md") == stored


def test_remove_document(store: Any) -> None:
    store.replace_document(
        _doc("a.md", [[1, 0, 0]], revision="ta"), expected_token=ANY_TOKEN
    )
    assert store.remove_document("a.md", expected_token="tx") == "conflict"
    assert store.remove_document("a.md", expected_token="ta") == "published"
    assert (store.tokens(), store.count) == ({}, 0)
    assert store.remove_document("gone.md", expected_token=None) == "published"
    assert store.remove_document("gone.md", expected_token="t") == "conflict"


def test_a_document_with_no_rows_keeps_its_token(store: Any) -> None:
    store.replace_document(DocumentRows("a.md", "ta", []), expected_token=ANY_TOKEN)
    assert (store.tokens(), store.count) == ({"a.md": "ta"}, 0)


@pytest.mark.parametrize(
    "bad",
    [
        _doc("a.md", [[1, 0]]),
        _doc("a.md", [[float("nan"), 0, 0]]),
        _doc("a\x00.md", [[1, 0, 0]]),
        _doc("a.md", [[1, 0, 0]], folder="f\x00"),
        DocumentRows("a.md", "t", [VectorRow([1, 0, 0], {"x": "nul\x00"})]),
        DocumentRows("a.md", "t", [VectorRow([1, 0, 0], {"x": object()})]),
    ],
)
def test_rows_that_cannot_be_stored_change_nothing(
    store: Any, bad: DocumentRows
) -> None:
    store.replace_document(
        _doc("a.md", [[1, 0, 0]], revision="old"), expected_token=ANY_TOKEN
    )
    with pytest.raises(ValueError):
        store.replace_document(bad, expected_token=ANY_TOKEN)
    assert store.tokens() == {"a.md": "old"}


def test_a_write_under_another_identity_is_incompatible(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    from markdown_vault_mcp.exceptions import StoreIncompatibleError

    store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "UPDATE {}.vector_meta SET identity = jsonb_set(identity, '{{model}}', '\"other\"')"
            ).format(sql.Identifier(postgres_schema.name))
        )
    write = _doc("b.md", [[0, 1, 0]])
    with pytest.raises(StoreIncompatibleError):
        store.replace_document(write, expected_token=ANY_TOKEN)
    with psycopg.connect(postgres_schema.dsn) as admin:
        row = admin.execute(
            sql.SQL(
                "SELECT count(*) FROM {}.vector_documents WHERE path = 'b.md'"
            ).format(sql.Identifier(postgres_schema.name))
        ).fetchone()
    assert row == (0,)


def test_a_write_behind_a_held_meta_row_times_out(
    store: Any, postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.stores.postgres_vectors as module
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    monkeypatch.setattr(module, "_WRITE_LOCK_TIMEOUT_MS", 300)
    holder = psycopg.connect(postgres_schema.dsn)
    try:
        holder.execute(
            sql.SQL("SELECT 1 FROM {}.vector_meta FOR UPDATE").format(
                sql.Identifier(postgres_schema.name)
            )
        )
        write = _doc("b.md", [[0, 1, 0]])
        with pytest.raises(StoreUnavailableError, match="reason=timeout"):
            store.replace_document(write, expected_token=ANY_TOKEN)
    finally:
        holder.close()


def test_a_reader_refuses_writes(postgres_schema: PostgresSchema) -> None:
    from markdown_vault_mcp.exceptions import ReadOnlyError

    reader = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, _ID, owns_files=False
    )
    write = _doc("a.md", [[1, 0, 0]])
    try:
        with pytest.raises(ReadOnlyError, match="read-only"):
            reader.replace_document(write, expected_token=ANY_TOKEN)
        with pytest.raises(ReadOnlyError, match="read-only"):
            reader.remove_document("a.md", expected_token=ANY_TOKEN)
    finally:
        reader.close()


def test_writes_reach_another_process(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    store.replace_document(
        _doc("a.md", [[1, 0, 0]], revision="ta"), expected_token=ANY_TOKEN
    )
    reader = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, _ID, owns_files=False
    )
    try:
        assert reader.tokens() == {"a.md": "ta"}
    finally:
        reader.close()


class _LosesTheReply:
    """A pool stand-in whose connections commit, then raise as if the reply was lost."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def connection(self) -> Any:
        import contextlib

        @contextlib.contextmanager
        def lend() -> Iterator[Any]:
            with self._pool.connection() as real:
                yield _CommitThenLose(real)

        return lend()


class _CommitThenLose:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def transaction(self) -> Any:
        import contextlib

        @contextlib.contextmanager
        def tx() -> Iterator[None]:
            with self._conn.transaction():
                yield
            raise psycopg.OperationalError("server closed the connection unexpectedly")

        return tx()


def test_a_commit_whose_reply_is_lost_is_commit_unknown_and_landed(store: Any) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    real = store._conn
    store._conn = real._replace(pool=_LosesTheReply(real.pool))
    write = _doc("a.md", [[1, 0, 0]], revision="ta")
    try:
        with pytest.raises(
            StoreUnavailableError, match="reason=commit_unknown"
        ) as failure:
            store.replace_document(write, expected_token=ANY_TOKEN)
        assert failure.value.__cause__ is None
    finally:
        store._conn = real
    # The commit landed: a reconciler pass reading the tokens finds "ta" and embeds nothing.
    assert store.tokens() == {"a.md": "ta"}


def test_a_failure_before_commit_is_not_commit_unknown(
    store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.stores.postgres_vectors as module
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    def broken(*_a: object, **_k: object) -> None:
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(module, "write_document", broken)
    write = _doc("a.md", [[1, 0, 0]])
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        store.replace_document(write, expected_token=ANY_TOKEN)
    assert store.tokens() == {}


def test_a_write_after_the_lease_was_taken_is_refused(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError
    from markdown_vault_mcp.stores._postgres_lease import LeaseRegistry

    lease = store._conn.lease
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s, 5000)", (lease.pid,))
    usurper = LeaseRegistry().acquire(
        pg.prepare_conninfo(postgres_schema.dsn, schema=postgres_schema.name),
        schema=postgres_schema.name,
    )
    write = _doc("a.md", [[1, 0, 0]])
    try:
        with pytest.raises(StoreUnavailableError, match="reason=lease"):
            store.replace_document(write, expected_token=ANY_TOKEN)
    finally:
        usurper.release()
    assert store.tokens() == {}


class TestPostgresStoreLifecycle(StoreLifecycleContract):
    @pytest.fixture
    def open_store(self, postgres_schema: PostgresSchema) -> Any:
        return lambda: PostgresVectorBackend(postgres_schema.dsn).open(
            postgres_schema.name, _ID
        )

    @pytest.fixture
    def open_store_read_only(self, postgres_schema: PostgresSchema) -> Any:
        return lambda: PostgresVectorBackend(postgres_schema.dsn).open(
            postgres_schema.name, _ID, owns_files=False
        )

    @pytest.fixture
    def touch_store(self) -> Any:
        return lambda store: store.count

    @pytest.fixture
    def write_to_store(self) -> Any:
        return lambda store: store.replace_document(
            _doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN
        )

    @pytest.fixture
    def closed_error(self) -> type[Exception]:
        return RuntimeError


def test_a_write_with_no_free_connection_is_unavailable_pool(store: Any) -> None:
    from psycopg_pool import PoolTimeout

    from markdown_vault_mcp.exceptions import StoreUnavailableError

    class _Exhausted:
        def connection(self) -> Any:
            raise PoolTimeout("couldn't get a connection after 30.00 sec")

    real = store._conn
    store._conn = real._replace(pool=_Exhausted())
    write = _doc("a.md", [[1, 0, 0]])
    try:
        with pytest.raises(StoreUnavailableError, match="reason=pool"):
            store.replace_document(write, expected_token=ANY_TOKEN)
    finally:
        store._conn = real
