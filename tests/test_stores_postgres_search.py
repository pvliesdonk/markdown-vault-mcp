"""Searching the PostgreSQL vector store, and the shared vector contracts (#1504)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend
from markdown_vault_mcp.types import ANY_TOKEN, EmbeddingIdentity
from tests.fixtures.source_neutral_contract import SourceNeutralContract
from tests.fixtures.vector_store_contract import (
    IDENT,
    PersistentVectorStoreContract,
    VectorStoreContract,
    doc,
    paths,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.fixtures.postgres import PostgresSchema


@pytest.fixture
def opened() -> Iterator[list[Any]]:
    """The stores a factory fixture opens, closed when the test ends."""
    stores: list[Any] = []
    yield stores
    for store in stores:
        store.close()


@pytest.fixture
def backend(postgres_schema: PostgresSchema) -> PostgresVectorBackend:
    return PostgresVectorBackend(postgres_schema.dsn)


def _tracked(stores: list[Any], store: Any) -> Any:
    stores.append(store)
    return store


class TestPostgresConformance(
    VectorStoreContract, PersistentVectorStoreContract, SourceNeutralContract
):
    @pytest.fixture
    def make_store(
        self, backend: Any, postgres_schema: PostgresSchema, opened: list[Any]
    ) -> Callable[..., Any]:
        return lambda identity: _tracked(
            opened, backend.open(postgres_schema.name, identity)
        )

    @pytest.fixture
    def reopen_store(
        self, backend: Any, postgres_schema: PostgresSchema, opened: list[Any]
    ) -> Callable[..., Any]:
        return lambda identity, owns: _tracked(
            opened, backend.open(postgres_schema.name, identity, owns_files=owns)
        )


@pytest.fixture
def store(backend: Any, postgres_schema: PostgresSchema) -> Iterator[Any]:
    opened_store = backend.open(postgres_schema.name, IDENT)
    yield opened_store
    opened_store.close()


def test_a_selective_predicate_reads_past_the_first_batch(store: Any) -> None:
    near = [doc(f"n{i:03}.md", [[1, 0.001 * i, 0]]) for i in range(150)]
    store.publish_generation(IDENT, [*near, doc("far.md", [[0, 0, 1]], folder="t")])
    seen: list[str] = []

    def wanted(row: dict[str, Any]) -> bool:
        seen.append(row["path"])
        return row["folder"] == "t"

    results = store.search([1, 0, 0], limit=1, predicate=wanted)
    assert paths(results) == ["far.md"]
    assert len(seen) == 151  # every row, in order, once
    assert set(results[0]) == {"path", "folder", "content", "score"}


def test_folder_scopes_are_exact_prefixes(store: Any) -> None:
    for name, folder in [
        ("a", "a"),
        ("ab", "ab"),
        ("a_b", "a_b"),
        ("axb", "axb"),
        ("pct", "a%"),
        ("deep", "a/b"),
        ("root", ""),
    ]:
        store.replace_document(
            doc(f"{name}.md", [[1, 0, 0]], folder=folder), expected_token=ANY_TOKEN
        )

    def scope(folder: str) -> list[str]:
        return sorted(paths(store.search([1, 0, 0], limit=20, folder=folder)))

    assert scope("a") == ["a.md", "deep.md"]
    assert scope("a_b") == ["a_b.md"]  # "_" is no wildcard
    assert scope("a%") == ["pct.md"]  # nor is "%"
    assert scope("") == ["root.md"]  # the root only
    assert scope("A") == []  # case-sensitive


def test_a_zero_score_is_positive_zero(store: Any) -> None:
    store.replace_document(doc("z.md", [[0, 0, 0]]), expected_token=ANY_TOKEN)
    (result,) = store.search([1, 0, 0])
    assert math.copysign(1, result["score"]) == 1


def test_results_are_copies(store: Any) -> None:
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    first = store.search([1, 0, 0])
    first[0]["path"] = "mutated"
    assert paths(store.search([1, 0, 0])) == ["a.md"]


def test_a_non_finite_query_is_refused(store: Any) -> None:
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    for bad in ([math.nan, 0, 0], [math.inf, 0, 0]):
        with pytest.raises(ValueError, match="finite"):
            store.search(bad)


def test_equal_scores_order_by_path_then_ordinal(store: Any) -> None:
    store.replace_document(
        doc("b.md", [[1, 0, 0], [1, 0, 0]]), expected_token=ANY_TOKEN
    )
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    results = store.search([1, 0, 0], limit=3)
    assert [r["content"] for r in results] == ["a.md#0", "b.md#0", "b.md#1"]


def test_a_reader_opened_before_any_dimension_searches_at_the_stored_one(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    unknown = EmbeddingIdentity("fake", "m", "", "v1", 0)
    reader = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, unknown, owns_files=False
    )
    writer = backend.open(postgres_schema.name, IDENT)
    try:
        writer.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        assert paths(reader.search([1, 0, 0])) == ["a.md"]
        with pytest.raises(ValueError, match="dimension 3"):
            reader.search([1, 0])
    finally:
        writer.close()
        reader.close()


def test_a_fresh_store_searches_nothing_until_its_first_write(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    old = backend.open(postgres_schema.name, IDENT)
    old.replace_document(doc("old.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    old.close()
    fresh = backend.open_fresh(postgres_schema.name, IDENT)
    try:
        assert fresh.search([1, 0, 0]) == []
        assert fresh.similar_to_document("old.md") == []
    finally:
        fresh.close()


def test_search_after_close_raises(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    closed = backend.open(postgres_schema.name, IDENT)
    closed.close()
    for limit in (5, 0):
        with pytest.raises(RuntimeError, match="closed"):
            closed.search([1, 0, 0], limit=limit)
        with pytest.raises(RuntimeError, match="closed"):
            closed.similar_to_document("a.md", limit=limit)


def test_a_predicate_keeping_fewer_than_the_limit_returns_what_it_keeps(
    store: Any,
) -> None:
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    store.replace_document(
        doc("b.md", [[0, 1, 0]], folder="t"), expected_token=ANY_TOKEN
    )
    results = store.search([1, 0, 0], limit=5, predicate=lambda r: r["folder"] == "t")
    assert paths(results) == ["b.md"]
    assert results[0]["score"] == 0.0


class _BrokenReads:
    """A pool stand-in whose next *failures* connections raise *error* on every statement."""

    def __init__(self, pool: Any, error: Exception, failures: int) -> None:
        self._pool, self._error, self._failures = pool, error, failures
        self.attempts = 0

    def connection(self) -> Any:
        import contextlib

        @contextlib.contextmanager
        def lend() -> Iterator[Any]:
            self.attempts += 1
            with self._pool.connection() as real:
                if self.attempts <= self._failures:
                    yield _Raises(real, self._error)
                else:
                    yield real

        return lend()

    def close(self) -> None:
        self._pool.close()


class _Raises:
    def __init__(self, conn: Any, error: Exception) -> None:
        self._conn, self._error = conn, error

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self._error


def _with_pool(store: Any, pool: Any) -> Any:
    real = store._conn
    store._conn = real._replace(pool=pool)
    return real


def test_a_read_that_loses_its_connection_once_is_retried(
    store: Any, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    broken = _BrokenReads(
        store._conn.pool,
        psycopg.OperationalError("server closed the connection unexpectedly"),
        1,
    )
    real = _with_pool(store, broken)
    try:
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.stores.postgres_vectors"
        ):
            assert paths(store.search([1, 0, 0])) == ["a.md"]
    finally:
        store._conn = real
    assert broken.attempts == 2
    (event,) = [
        r
        for r in caplog.records
        if r.msg.startswith("postgres_vector_store_read_retried ")
    ]
    assert event.args == (store.schema, None)


@pytest.mark.parametrize(
    "error",
    [
        psycopg.OperationalError("server closed the connection unexpectedly"),
        psycopg.errors.AdminShutdown(
            "terminating connection due to administrator command"
        ),
    ],
    ids=["no_sqlstate", "57P01"],
)
def test_a_read_that_loses_its_connection_twice_is_unavailable(
    store: Any, error: Exception
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    broken = _BrokenReads(store._conn.pool, error, 2)
    real = _with_pool(store, broken)
    try:
        with pytest.raises(StoreUnavailableError, match="reason=connect"):
            store.tokens()
    finally:
        store._conn = real
    assert broken.attempts == 2


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            psycopg.errors.QueryCanceled(
                "canceling statement due to statement timeout"
            ),
            "timeout",
        ),
        (psycopg.ProgrammingError("a client-side error"), "connect"),
    ],
    ids=["timeout", "client_side"],
)
def test_a_read_failing_otherwise_is_not_retried(
    store: Any, error: Exception, reason: str
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    broken = _BrokenReads(store._conn.pool, error, 2)
    real = _with_pool(store, broken)
    try:
        with pytest.raises(StoreUnavailableError, match=f"reason={reason}"):
            store.count  # noqa: B018 - the property is the read
    finally:
        store._conn = real
    assert broken.attempts == 1


def test_a_search_whose_connection_dies_midway_is_retried_once(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    near = [doc(f"n{i:03}.md", [[1, 0.001 * i, 0]]) for i in range(150)]
    store.publish_generation(IDENT, [*near, doc("far.md", [[0, 0, 1]], folder="t")])
    killed: list[bool] = []

    def kill_once(row: dict[str, Any]) -> bool:
        if not killed:
            killed.append(True)
            with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity"
                    " WHERE application_name = %s AND state = 'idle in transaction'"
                    " AND pid <> pg_backend_pid()",
                    (f"markdown-vault-mcp:{postgres_schema.name}",),
                )
        return row["folder"] == "t"

    results = store.search([1, 0, 0], limit=2, predicate=kill_once)
    assert paths(results) == ["far.md"]  # whole: no duplicate, nothing lost
    assert store.count == 151  # the pool serves again


def test_similar_to_a_document_without_rows_is_empty(store: Any) -> None:
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    store.replace_document(
        doc("e.md", []), expected_token=ANY_TOKEN
    )  # a token, no rows
    assert store.similar_to_document("e.md") == []
    assert store.similar_to_document("nope.md") == []


def test_a_read_finding_no_free_connection_waits_once_and_is_not_retried(
    postgres_schema: PostgresSchema,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logging

    from markdown_vault_mcp.exceptions import StoreUnavailableError
    from markdown_vault_mcp.stores._postgres import PoolSettings

    settings = PoolSettings(pool_size=1, get_timeout_s=0.5)
    busy = PostgresVectorBackend(postgres_schema.dsn, pool=settings).open(
        postgres_schema.name, IDENT
    )
    pool = busy._conn.pool
    try:
        with pool.connection():  # the only connection, held
            # Count the read's checkouts instead of timing them: a loaded
            # runner stretches one wait past any wall-clock bound (#1889).
            checkouts: list[object] = []
            checkout = pool.connection

            def counted(*args: object, **kwargs: object) -> object:
                checkouts.append(args)
                return checkout(*args, **kwargs)  # type: ignore[arg-type]

            monkeypatch.setattr(pool, "connection", counted)
            with (
                caplog.at_level(
                    logging.WARNING, logger="markdown_vault_mcp.stores.postgres_vectors"
                ),
                pytest.raises(StoreUnavailableError, match="reason=pool"),
            ):
                busy.count  # noqa: B018 - the property is the read
            monkeypatch.undo()
    finally:
        busy.close()
    assert len(checkouts) == 1  # one wait, not two
    assert not [
        r
        for r in caplog.records
        if r.msg.startswith("postgres_vector_store_read_retried ")
    ]
