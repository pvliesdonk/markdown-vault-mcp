"""What the PostgreSQL vector store reports about itself (#1504)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from markdown_vault_mcp.interfaces import ReportsResources
from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend
from markdown_vault_mcp.types import ANY_TOKEN, StoreResources
from tests.fixtures.vector_store_contract import IDENT, doc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.fixtures.postgres import PostgresSchema


@pytest.fixture
def backend(postgres_schema: PostgresSchema) -> PostgresVectorBackend:
    return PostgresVectorBackend(postgres_schema.dsn)


@pytest.fixture
def store(backend: Any, postgres_schema: PostgresSchema) -> Iterator[Any]:
    opened = backend.open(postgres_schema.name, IDENT)
    yield opened
    opened.close()


def test_the_store_reports_its_resources(store: Any) -> None:
    assert isinstance(store, ReportsResources)
    assert store.resources() == StoreResources("streamed", None, 0, 0)  # no tables yet
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    after = store.resources()
    assert (
        after.search_mode,
        after.search_workspace_bytes,
        after.resident_vector_bytes,
    ) == ("streamed", None, 0)
    assert after.stored_bytes > 0


def test_resources_measure_the_tables_in_the_schema_now(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    old = backend.open(postgres_schema.name, IDENT)
    old.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    measured = old.resources().stored_bytes
    old.close()
    fresh = backend.open_fresh(postgres_schema.name, IDENT)
    try:
        assert (
            fresh.resources().stored_bytes == measured
        )  # the old tables, until the reset
    finally:
        fresh.close()


def test_resources_count_only_our_tables(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    before = store.resources().stored_bytes
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute(
            psycopg.sql.SQL(
                "CREATE TABLE {}.unrelated AS SELECT generate_series(1, 5000) AS n"
            ).format(psycopg.sql.Identifier(postgres_schema.name))
        )
    assert store.resources().stored_bytes == before


def test_resources_after_close_raise(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    closed = backend.open(postgres_schema.name, IDENT)
    closed.close()
    with pytest.raises(RuntimeError, match="closed"):
        closed.resources()


def test_resources_with_no_free_connection_are_unavailable(
    postgres_schema: PostgresSchema,
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError
    from markdown_vault_mcp.stores._postgres import PoolSettings

    busy = PostgresVectorBackend(
        postgres_schema.dsn, pool=PoolSettings(pool_size=1, get_timeout_s=0.3)
    ).open(postgres_schema.name, IDENT)
    try:
        with (
            busy._conn.pool.connection(),
            pytest.raises(StoreUnavailableError, match="reason=pool"),
        ):
            busy.resources()
    finally:
        busy.close()


class _Unreachable:
    def connection(self) -> Any:
        raise psycopg.OperationalError("connection refused")


def test_status_of_an_unreachable_store_raises(store: Any) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    real = store._conn
    store._conn = real._replace(pool=_Unreachable())
    try:
        with pytest.raises(StoreUnavailableError, match="reason=connect"):
            store.resources()
    finally:
        store._conn = real


def test_describe_names_where_the_store_lives(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    from psycopg.conninfo import conninfo_to_dict

    from markdown_vault_mcp.interfaces import DescribesStore

    assert isinstance(store, DescribesStore)
    described = store.describe()
    expected = conninfo_to_dict(postgres_schema.dsn)
    assert described["database"] == expected.get("dbname", described["database"])
    assert described["port"] == int(expected.get("port", 5432))
    assert described["schema"] == postgres_schema.name
    assert described["postgresql_version"].split(".")[0].isdigit()
    assert described["pgvector_version"].count(".") == 2
    assert (described["writer"], described["lease_held"]) == (True, True)
    assert set(described) == {
        "host",
        "port",
        "database",
        "schema",
        "postgresql_version",
        "pgvector_version",
        "writer",
        "lease_held",
    }


def test_a_reader_holds_no_lease(backend: Any, postgres_schema: PostgresSchema) -> None:
    reader = backend.open(postgres_schema.name, IDENT, owns_files=False)
    try:
        described = reader.describe()
    finally:
        reader.close()
    assert (described["writer"], described["lease_held"]) == (False, False)


def test_describe_reports_a_lost_lease_without_retaking_it(
    store: Any, postgres_schema: PostgresSchema
) -> None:
    pid = store._conn.lease.pid
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,))
    assert store.describe()["lease_held"] is False
    assert store._conn.lease.pid == pid  # a status read does not take it back
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    assert store.describe()["lease_held"] is True  # a write did


def test_describe_shows_no_secret(postgres_schema: PostgresSchema) -> None:
    import json

    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(postgres_schema.dsn)
    params.setdefault("password", "pw-not-needed")
    params.update(sslcert="/secret/cert.pem", sslkey="/secret/key.pem")
    params["sslmode"] = "disable"
    store = PostgresVectorBackend(make_conninfo("", **params)).open(
        postgres_schema.name, IDENT
    )
    try:
        shown = json.dumps(store.describe()) + repr(store)
    finally:
        store.close()
    for secret in (
        str(params["password"]),
        "/secret/",
        "sslkey",
        "sslcert",
        "password",
    ):
        assert secret not in shown


def test_describe_after_close_raises(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    closed = backend.open(postgres_schema.name, IDENT)
    closed.close()
    with pytest.raises(RuntimeError, match="closed"):
        closed.describe()


def test_describe_raises_when_the_lease_check_fails_on_a_live_connection(
    store: Any,
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    lease = store._conn.lease._lease
    real, pid = lease._conn, lease.pid

    class _Refusing:
        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise psycopg.errors.LockNotAvailable(
                "canceling statement due to lock timeout"
            )

    lease._conn = _Refusing()
    try:
        with pytest.raises(StoreUnavailableError, match="reason=timeout"):
            store.describe()
    finally:
        lease._conn = real
    assert lease.pid == pid  # a status read never takes the lease again


def test_a_dropped_schema_is_not_configured_for_status_and_reads(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    from markdown_vault_mcp.exceptions import StoreNotConfiguredError

    reader = backend.open(postgres_schema.name, IDENT, owns_files=False)
    try:
        with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
            admin.execute(
                psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(
                    psycopg.sql.Identifier(postgres_schema.name)
                )
            )
        for read in (
            lambda: reader.describe(),  # first: no earlier read may stand in for it
            lambda: reader.resources(),
            lambda: reader.generation,
            lambda: reader.tokens(),
        ):
            with pytest.raises(StoreNotConfiguredError, match="does not exist"):
                read()
    finally:
        reader.close()
