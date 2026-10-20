"""Writes to the PostgreSQL vector store, and the reads that check them (#1504)."""

from __future__ import annotations

import math
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
        locking = sql.SQL("SELECT 1 FROM {}.vector_meta FOR UPDATE").format(
            sql.Identifier(name)
        )
        with psycopg.connect(postgres_schema.dsn, autocommit=True) as other:
            other.execute("SET lock_timeout = 200")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                other.execute(locking)


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


def test_a_conflict_on_the_first_write_leaves_the_store_writable(store: Any) -> None:
    """The rolled-back first write also rolled back the tables it created."""
    assert store.remove_document("a.md", expected_token="t") == "conflict"
    later = _doc("a.md", [[1, 0, 0]], revision="ta")
    assert store.replace_document(later, expected_token=ANY_TOKEN) == "published"
    assert store.tokens() == {"a.md": "ta"}


def test_tables_dropped_under_a_writer_are_recreated_by_a_later_write(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        for table in ("vector_chunks", "vector_documents", "vector_meta"):
            admin.execute(
                sql.SQL("DROP TABLE {}.{}").format(
                    sql.Identifier(postgres_schema.name), sql.Identifier(table)
                )
            )
    first = _doc("b.md", [[0, 1, 0]], revision="tb")
    with pytest.raises(StoreUnavailableError):
        store.replace_document(first, expected_token=ANY_TOKEN)
    again = _doc("b.md", [[0, 1, 0]], revision="tb")
    assert store.replace_document(again, expected_token=ANY_TOKEN) == "published"
    assert store.tokens() == {"b.md": "tb"}


def test_the_new_token_is_stored_when_published(store: Any) -> None:
    store.replace_document(
        _doc("a.md", [[1, 0, 0]], revision="t1"), expected_token=ANY_TOKEN
    )
    store.replace_document(
        _doc("a.md", [[0, 1, 0]], revision="t2"), expected_token="t1"
    )
    assert store.tokens() == {"a.md": "t2"}


def test_literal_escape_text_in_metadata_is_stored(store: Any) -> None:
    text = 'jsonb rejects "\\u0000" in text'
    write = DocumentRows("a.md", "t", [VectorRow([1, 0, 0], {"content": text})])
    assert store.replace_document(write, expected_token=ANY_TOKEN) == "published"
    assert store.rows_for("a.md")[0].metadata["content"] == text


@pytest.mark.parametrize(
    "meta",
    [
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": -float("inf")},
        {"x": "lone \ud800 surrogate"},
        {"lone \udfff key": 1},
        {"nested": {"deep": ["ok", "nul\x00"]}},
    ],
)
def test_metadata_postgresql_cannot_store_is_refused_before_writing(
    store: Any, meta: dict[str, Any]
) -> None:
    store.replace_document(
        _doc("a.md", [[1, 0, 0]], revision="old"), expected_token=ANY_TOKEN
    )
    bad = DocumentRows("a.md", "new", [VectorRow([1, 0, 0], meta)])
    with pytest.raises(ValueError):
        store.replace_document(bad, expected_token=ANY_TOKEN)
    assert store.tokens() == {"a.md": "old"}


@pytest.mark.parametrize("field", ["path", "folder"])
def test_a_key_too_long_for_postgresql_indexes_is_refused(
    store: Any, field: str
) -> None:
    long = "x" * 3000
    bad = (
        _doc(long + ".md", [[1, 0, 0]])
        if field == "path"
        else _doc("a.md", [[1, 0, 0]], folder=long)
    )
    with pytest.raises(ValueError, match="too long"):
        store.replace_document(bad, expected_token=ANY_TOKEN)
    assert store.tokens() == {}


def test_a_store_of_unknown_dimension_refuses_writes(
    postgres_schema: PostgresSchema,
) -> None:
    unknown = EmbeddingIdentity("fake", "m", "", "v1", 0)
    store = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, unknown
    )
    write = DocumentRows("a.md", "t", [])
    try:
        with pytest.raises(ValueError, match="dimension"):
            store.replace_document(write, expected_token=ANY_TOKEN)
    finally:
        store.close()


def test_a_dimension_only_identity_change_is_incompatible(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    from markdown_vault_mcp.exceptions import StoreIncompatibleError

    store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "UPDATE {}.vector_meta SET identity = jsonb_set(identity, '{{dimension}}', '4')"
            ).format(sql.Identifier(postgres_schema.name))
        )
    write = _doc("b.md", [[0, 1, 0]])
    with pytest.raises(StoreIncompatibleError):
        store.replace_document(write, expected_token=ANY_TOKEN)


class _FailsAtCommit:
    """A pool stand-in whose transactions roll back at COMMIT with a given error."""

    def __init__(self, pool: Any, exc: Exception) -> None:
        self._pool = pool
        self._exc = exc

    def connection(self) -> Any:
        import contextlib

        @contextlib.contextmanager
        def lend() -> Iterator[Any]:
            with self._pool.connection() as real:
                yield _RollsBackAtCommit(real, self._exc)

        return lend()


class _RollsBackAtCommit:
    def __init__(self, conn: Any, exc: Exception) -> None:
        self._conn = conn
        self._exc = exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def transaction(self) -> Any:
        import contextlib

        @contextlib.contextmanager
        def tx() -> Iterator[None]:
            with self._conn.transaction():
                yield
                raise psycopg.Rollback
            raise self._exc

        return tx()


@pytest.mark.parametrize(
    ("sqlstate", "reason"),
    [("40001", "error"), ("57014", "timeout"), ("53100", "connect")],
)
def test_a_commit_postgresql_rolled_back_is_not_commit_unknown(
    store: Any, sqlstate: str, reason: str
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    real = store._conn
    store._conn = real._replace(
        pool=_FailsAtCommit(real.pool, psycopg.errors.lookup(sqlstate)("rolled back"))
    )
    write = _doc("a.md", [[1, 0, 0]], revision="ta")
    try:
        with pytest.raises(StoreUnavailableError, match=f"reason={reason}"):
            store.replace_document(write, expected_token=ANY_TOKEN)
    finally:
        store._conn = real
    assert store.tokens() == {}


def test_a_backend_killed_before_commit_rolled_back_and_the_pool_recovers(
    store: Any, postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.stores.postgres_vectors as module
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    store.replace_document(
        _doc("a.md", [[1, 0, 0]], revision="ta"), expected_token=ANY_TOKEN
    )
    real_write = module.write_document

    def write_then_die(conn: Any, **kwargs: Any) -> None:
        real_write(conn, **kwargs)
        pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
        with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
            admin.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,))

    monkeypatch.setattr(module, "write_document", write_then_die)
    write = _doc("a.md", [[0, 1, 0]], revision="tb")
    # PostgreSQL answers the COMMIT with 57P01 (admin_shutdown): it reported
    # an error for the command, so the write rolled back; not commit_unknown.
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        store.replace_document(write, expected_token=ANY_TOKEN)
    monkeypatch.undo()
    assert store.tokens() == {"a.md": "ta"}  # nothing landed; the pool still serves


def test_ordinary_nested_metadata_is_stored(store: Any) -> None:
    meta = {
        "n": 1,
        "f": 0.5,
        "flag": True,
        "none": None,
        "pair": ("a", "b"),
        "deep": {"k": [1, "x"]},
        "big": 1e20,
        "negzero": -0.0,
        "keys": {7: "seven"},
    }
    write = DocumentRows("a.md", "t", [VectorRow([1, 0, 0], meta)])
    assert store.replace_document(write, expected_token=ANY_TOKEN) == "published"
    stored = store.rows_for("a.md")[0].metadata
    assert stored["pair"] == ["a", "b"]  # jsonb has no tuples
    assert stored["deep"] == {"k": [1, "x"]}
    assert stored["big"] == 10**20
    assert isinstance(stored["big"], int)  # jsonb numeric loses the float
    assert math.copysign(1, stored["negzero"]) == 1  # and the sign of zero
    assert stored["keys"] == {"7": "seven"}


_OLD = EmbeddingIdentity("fake", "old", "", "v1", 2)


def _planted_old(schema: PostgresSchema) -> None:
    """A store at _OLD holding old.md, as a previous model left it."""
    old = PostgresVectorBackend(schema.dsn).open(schema.name, _OLD)
    try:
        old.replace_document(
            _doc("old.md", [[1, 0]], revision="to"), expected_token=ANY_TOKEN
        )
    finally:
        old.close()


@pytest.fixture
def fresh(postgres_schema: PostgresSchema) -> Iterator[Any]:
    _planted_old(postgres_schema)
    opened = PostgresVectorBackend(postgres_schema.dsn).open_fresh(
        postgres_schema.name, _ID
    )
    yield opened
    opened.close()


def _stored(schema: PostgresSchema) -> tuple[Any, ...]:
    """What another session sees: the identity's model, the generation, the paths."""
    with psycopg.connect(schema.dsn) as admin:
        s = sql.Identifier(schema.name)
        meta = admin.execute(
            sql.SQL("SELECT identity->>'model', generation FROM {}.vector_meta").format(
                s
            )
        ).fetchone()
        paths = admin.execute(
            sql.SQL("SELECT path FROM {}.vector_documents ORDER BY path").format(s)
        ).fetchall()
    return (meta, [p for (p,) in paths])


def test_a_fresh_store_reads_empty_and_the_old_rows_stay_until_its_first_write(
    fresh: Any, postgres_schema: PostgresSchema
) -> None:
    assert (fresh.tokens(), fresh.count) == ({}, 0)
    assert _stored(postgres_schema) == (("old", 0), ["old.md"])


def test_the_first_fresh_write_replaces_an_intact_store(
    fresh: Any, postgres_schema: PostgresSchema
) -> None:
    assert (
        fresh.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=None)
        == "published"
    )
    assert fresh.tokens() == {"a.md": "t1"}
    assert _stored(postgres_schema) == (("m", 0), ["a.md"])
    assert (
        fresh.replace_document(_doc("b.md", [[0, 1, 0]]), expected_token=None)
        == "published"
    )
    assert fresh.tokens() == {"a.md": "t1", "b.md": "t1"}  # reset once only


def test_a_first_fresh_remove_also_resets(
    fresh: Any, postgres_schema: PostgresSchema
) -> None:
    assert fresh.remove_document("old.md", expected_token=ANY_TOKEN) == "published"
    assert _stored(postgres_schema) == (("m", 0), [])


def test_the_first_fresh_write_recreates_damaged_tables(
    postgres_schema: PostgresSchema,
) -> None:
    _planted_old(postgres_schema)
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP TABLE {}.vector_chunks").format(
                sql.Identifier(postgres_schema.name)
            )
        )
    store = PostgresVectorBackend(postgres_schema.dsn).open_fresh(
        postgres_schema.name, _ID
    )
    try:
        store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=None)
        assert store.rows_for("a.md")[0].vector == pytest.approx([1, 0, 0])
    finally:
        store.close()
    assert _stored(postgres_schema) == (("m", 0), ["a.md"])


def test_a_conflict_on_the_first_fresh_write_resets_nothing(
    fresh: Any, postgres_schema: PostgresSchema
) -> None:
    assert (
        fresh.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token="stale")
        == "conflict"
    )
    assert _stored(postgres_schema) == (("old", 0), ["old.md"])
    assert fresh.tokens() == {}  # still reads empty: the reset is still pending


@pytest.mark.parametrize(
    ("damage", "sqlstate"),
    [
        (
            "DROP TABLE {s}.vector_chunks; CREATE VIEW {s}.vector_chunks AS SELECT 1 AS x",
            "42809",
        ),
        ("CREATE VIEW {s}.depends_on_ours AS SELECT * FROM {s}.vector_meta", "2BP01"),
    ],
    ids=["view_under_our_name", "dependent_view"],
)
def test_a_reset_that_cannot_drop_changes_nothing(
    postgres_schema: PostgresSchema, damage: str, sqlstate: str
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    _planted_old(postgres_schema)
    s = sql.Identifier(postgres_schema.name)
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        for statement in damage.split("; "):
            admin.execute(sql.SQL(statement).format(s=s))
        if sqlstate == "2BP01":  # make the layout damaged so the reset drops
            admin.execute(
                sql.SQL("ALTER TABLE {s}.vector_documents ADD COLUMN extra int").format(
                    s=s
                )
            )
    store = PostgresVectorBackend(postgres_schema.dsn).open_fresh(
        postgres_schema.name, _ID
    )
    write = _doc("a.md", [[1, 0, 0]])
    try:
        with pytest.raises(
            StoreUnavailableError, match=rf"SQLSTATE {sqlstate}\] \(reason=error\)"
        ):
            store.replace_document(write, expected_token=None)
    finally:
        store.close()
    with psycopg.connect(postgres_schema.dsn) as admin:
        kept = admin.execute(
            sql.SQL("SELECT path FROM {}.vector_documents").format(s)
        ).fetchall()
    assert kept == [("old.md",)]


@pytest.mark.parametrize("other_columns", [False, True], ids=["same", "other"])
def test_a_fresh_store_over_a_newer_format_refuses_its_first_write(
    postgres_schema: PostgresSchema, other_columns: bool
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError

    _planted_old(postgres_schema)
    s = sql.Identifier(postgres_schema.name)
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("UPDATE {}.vector_meta SET format_version = %s").format(s),
            (pg.FORMAT_VERSION + 1,),
        )
        if other_columns:  # judged first, this would be corrupt and dropped
            admin.execute(
                sql.SQL("ALTER TABLE {}.vector_chunks ADD COLUMN later int").format(s)
            )
    store = PostgresVectorBackend(postgres_schema.dsn).open_fresh(
        postgres_schema.name, _ID
    )
    write = _doc("a.md", [[1, 0, 0]])
    try:
        with pytest.raises(StoreUnsupportedSchemaError):
            store.replace_document(write, expected_token=None)
    finally:
        store.close()
    assert _stored(postgres_schema)[1] == ["old.md"]


def test_a_reader_is_not_blocked_by_a_fresh_store_reset(
    fresh: Any, postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from markdown_vault_mcp.stores import postgres_vectors as pv
    from markdown_vault_mcp.stores._postgres import PoolSettings

    reader = PostgresVectorBackend(
        postgres_schema.dsn, pool=PoolSettings(statement_timeout_s=3)
    ).open(postgres_schema.name, _OLD, owns_files=False)
    reset_done, finish = threading.Event(), threading.Event()
    real_reset = pv.reset_tables

    def reset_then_wait(*args: Any, **kwargs: Any) -> Any:
        how = real_reset(*args, **kwargs)
        reset_done.set()
        finish.wait(10)
        return how

    monkeypatch.setattr(pv, "reset_tables", reset_then_wait)
    writer = threading.Thread(
        target=fresh.replace_document,
        args=(_doc("a.md", [[1, 0, 0]]),),
        kwargs={"expected_token": None},
    )
    writer.start()
    try:
        assert reset_done.wait(10)
        assert reader.tokens() == {"old.md": "to"}  # the previous rows, at once
    finally:
        finish.set()
        writer.join(10)
        reader.close()
    assert _stored(postgres_schema) == (("m", 0), ["a.md"])


def test_a_lost_reply_on_the_first_fresh_write_does_not_reset_again(fresh: Any) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    real = fresh._conn
    fresh._conn = real._replace(pool=_LosesTheReply(real.pool))
    write = _doc("a.md", [[1, 0, 0]], revision="ta")
    try:
        with pytest.raises(StoreUnavailableError, match="reason=commit_unknown"):
            fresh.replace_document(write, expected_token=None)
    finally:
        fresh._conn = real
    # It landed: the store now reads the database, and the next write keeps a.md.
    assert fresh.tokens() == {"a.md": "ta"}
    fresh.replace_document(_doc("b.md", [[0, 1, 0]]), expected_token=None)
    assert fresh.tokens() == {"a.md": "ta", "b.md": "t1"}


def test_a_reset_outlasting_the_statement_timeout_completes(
    postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.stores import postgres_vectors as pv
    from markdown_vault_mcp.stores._postgres import PoolSettings

    _planted_old(postgres_schema)
    real_reset = pv.reset_tables

    def slow_reset(conn: Any, **kwargs: Any) -> Any:
        conn.execute("SELECT pg_sleep(0.6)")
        return real_reset(conn, **kwargs)

    monkeypatch.setattr(pv, "reset_tables", slow_reset)
    store = PostgresVectorBackend(
        postgres_schema.dsn, pool=PoolSettings(statement_timeout_s=0.3)
    ).open_fresh(postgres_schema.name, _ID)
    try:
        assert (
            store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=None)
            == "published"
        )
    finally:
        store.close()


def test_the_reset_is_logged_after_it_commits(
    fresh: Any, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(
        logging.INFO, logger="markdown_vault_mcp.stores.postgres_vectors"
    ):
        fresh.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=None)
    events = [
        r for r in caplog.records if r.msg.startswith("postgres_vector_store_reset ")
    ]
    assert [r.args[1] for r in events] == ["emptied"]


_OTHER = EmbeddingIdentity("fake", "m2", "", "v1", 2)


def test_a_generation_replaces_everything(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    store.publish_generation(
        _OTHER, [_doc("c.md", [[0, 2]], revision="tc", folder="f")]
    )
    assert (store.identity, store.generation, store.count) == (_OTHER, 1, 1)
    assert store.tokens() == {"c.md": "tc"}
    (row,) = store.rows_for("c.md")
    assert row.vector == pytest.approx([0, 1])
    assert row.metadata == {"folder": "f", "content": "c.md#0", "path": "c.md"}
    store.replace_document(
        _doc("d.md", [[1, 0]]), expected_token=None
    )  # the new dimension
    assert _stored(postgres_schema) == (("m2", 1), ["c.md", "d.md"])


def test_generations_count_up_and_may_be_empty(store: Any) -> None:
    store.publish_generation(_ID, [_doc("a.md", [[1, 0, 0]]), _doc("b.md", [])])
    store.publish_generation(_ID, [])
    assert (store.generation, store.count, store.tokens()) == (2, 0, {})


def test_a_generation_naming_a_path_twice_changes_nothing(store: Any) -> None:
    store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    twice = [_doc("a.md", [[1, 0, 0]]), _doc("a.md", [[0, 1, 0]])]
    with pytest.raises(ValueError, match=r"a\.md"):
        store.publish_generation(_ID, twice)
    assert (store.generation, store.tokens()) == (0, {"a.md": "t1"})


def test_a_generation_with_a_row_that_cannot_be_stored_changes_nothing(
    store: Any,
) -> None:
    store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    bad = [_doc("b.md", [[1, 0, 0]]), _doc("c.md", [[1, 0]])]  # another dimension
    with pytest.raises(ValueError, match="dimension"):
        store.publish_generation(_ID, bad)
    assert (store.generation, store.tokens()) == (0, {"a.md": "t1"})


def test_a_generation_of_unknown_dimension_is_refused(store: Any) -> None:
    unknown = EmbeddingIdentity("fake", "m", "", "v1", 0)
    with pytest.raises(ValueError, match="dimension"):
        store.publish_generation(unknown, [])


def test_a_generation_is_durable_and_reaches_another_process(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    store.publish_generation(_ID, [_doc("a.md", [[1, 0, 0]])])
    other = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, _ID, owns_files=False
    )
    try:
        assert (other.generation, other.tokens()) == (1, {"a.md": "t1"})
    finally:
        other.close()


def test_a_reader_during_a_generation_sees_the_previous_one(
    store: Any, postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from markdown_vault_mcp.stores import postgres_vectors as pv
    from markdown_vault_mcp.stores._postgres import PoolSettings

    store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    reader = PostgresVectorBackend(
        postgres_schema.dsn, pool=PoolSettings(statement_timeout_s=3)
    ).open(postgres_schema.name, _ID, owns_files=False)
    copied, finish = threading.Event(), threading.Event()
    real = pv.load_generation

    def copy_then_wait(*args: Any, **kwargs: Any) -> int:
        generation = real(*args, **kwargs)
        copied.set()
        finish.wait(10)
        return generation

    monkeypatch.setattr(pv, "load_generation", copy_then_wait)
    writer = threading.Thread(
        target=store.publish_generation, args=(_ID, [_doc("b.md", [[0, 1, 0]])])
    )
    writer.start()
    try:
        assert copied.wait(10)
        assert (reader.generation, reader.tokens()) == (0, {"a.md": "t1"})
    finally:
        finish.set()
        writer.join(10)
    assert (reader.generation, reader.tokens()) == (1, {"b.md": "t1"})
    reader.close()


def test_a_generation_outlasting_the_statement_timeout_publishes(
    postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.stores import postgres_vectors as pv
    from markdown_vault_mcp.stores._postgres import PoolSettings

    real = pv.load_generation

    def slow(conn: Any, **kwargs: Any) -> int:
        conn.execute("SELECT pg_sleep(0.6)")
        return real(conn, **kwargs)

    monkeypatch.setattr(pv, "load_generation", slow)
    store = PostgresVectorBackend(
        postgres_schema.dsn, pool=PoolSettings(pool_size=1, statement_timeout_s=0.3)
    ).open(postgres_schema.name, _ID)
    try:
        store.publish_generation(_ID, [_doc("a.md", [[1, 0, 0]])])
        assert store.generation == 1
        with store._conn.pool.connection() as conn:  # the one pooled connection
            assert conn.execute("SHOW statement_timeout").fetchone() == ("300ms",)
    finally:
        store.close()


def test_a_fresh_store_publishes_its_first_generation(
    fresh: Any, postgres_schema: PostgresSchema
) -> None:
    fresh.publish_generation(_ID, [_doc("a.md", [[1, 0, 0]])])
    assert (fresh.generation, fresh.tokens()) == (1, {"a.md": "t1"})
    assert _stored(postgres_schema) == (("m", 1), ["a.md"])


def test_a_lost_reply_on_a_generation_is_commit_unknown(store: Any) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    real = store._conn
    store._conn = real._replace(pool=_LosesTheReply(real.pool))
    generation = [_doc("a.md", [[1, 0, 0]])]
    try:
        with pytest.raises(StoreUnavailableError, match="reason=commit_unknown"):
            store.publish_generation(_ID, generation)
    finally:
        store._conn = real
    assert (store.generation, store.tokens()) == (1, {"a.md": "t1"})


def test_a_reader_refuses_a_generation(postgres_schema: PostgresSchema) -> None:
    from markdown_vault_mcp.exceptions import ReadOnlyError

    reader = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, _ID, owns_files=False
    )
    try:
        with pytest.raises(ReadOnlyError):
            reader.publish_generation(_ID, [])
    finally:
        reader.close()


def test_a_generation_is_logged(store: Any, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(
        logging.INFO, logger="markdown_vault_mcp.stores.postgres_vectors"
    ):
        store.publish_generation(_ID, [_doc("a.md", [[1, 0, 0], [0, 1, 0]])])
    (event,) = [
        r
        for r in caplog.records
        if r.msg.startswith("postgres_vector_store_generation_published ")
    ]
    assert event.args[1:] == (1, 1, 2)  # generation, documents, rows


def test_a_lost_reply_on_a_new_identity_is_adopted_once_it_shows(store: Any) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    real = store._conn
    store._conn = real._replace(pool=_LosesTheReply(real.pool))
    generation = [_doc("c.md", [[0, 1]], revision="tc")]
    try:
        with pytest.raises(StoreUnavailableError, match="reason=commit_unknown"):
            store.publish_generation(_OTHER, generation)
    finally:
        store._conn = real
    # It landed: the next read finds the attempted identity and adopts it.
    assert store.tokens() == {"c.md": "tc"}
    assert store.identity == _OTHER
    store.replace_document(_doc("d.md", [[1, 0]]), expected_token=None)
    assert store.tokens() == {"c.md": "tc", "d.md": "t1"}


def test_a_lost_reply_identity_is_adopted_by_the_next_write_too(store: Any) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    real = store._conn
    store._conn = real._replace(pool=_LosesTheReply(real.pool))
    try:
        with pytest.raises(StoreUnavailableError, match="reason=commit_unknown"):
            store.publish_generation(_OTHER, [])
    finally:
        store._conn = real
    store.remove_document("gone.md", expected_token=ANY_TOKEN)
    assert store.identity == _OTHER


class _LosesBeforeCommit:
    """A pool stand-in whose connections lose the line before the commit lands."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def connection(self) -> Any:
        import contextlib

        @contextlib.contextmanager
        def lend() -> Iterator[Any]:
            with self._pool.connection() as real:
                yield _RollsBackThenLoses(real)

        return lend()


class _RollsBackThenLoses:
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
                raise psycopg.OperationalError("server closed the connection")

        return tx()


def test_an_attempted_identity_that_did_not_land_is_not_adopted(store: Any) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    store.replace_document(_doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    real = store._conn
    store._conn = real._replace(pool=_LosesBeforeCommit(real.pool))
    try:
        with pytest.raises(StoreUnavailableError, match="reason=commit_unknown"):
            store.publish_generation(_OTHER, [])
    finally:
        store._conn = real
    assert store.tokens() == {"a.md": "t1"}  # it did not land: still _ID
    assert store.identity == _ID
    store.replace_document(_doc("b.md", [[0, 1, 0]]), expected_token=None)
    assert store.identity == _ID
