"""The PostgreSQL writer lease (#1504)."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from markdown_vault_mcp.exceptions import StoreUnavailableError
from markdown_vault_mcp.stores import _postgres as pg
from markdown_vault_mcp.stores._postgres_lease import LEASE_CLASS, Lease

if TYPE_CHECKING:
    from tests.fixtures.postgres import PostgresSchema, PostgresServer


def _conninfo(schema: PostgresSchema, *, app: str | None = None) -> pg.Conninfo:
    dsn = schema.dsn if app is None else make_conninfo(schema.dsn, application_name=app)
    return pg.prepare_conninfo(dsn, schema=schema.name)


def _key(schema: PostgresSchema) -> int:
    with psycopg.connect(schema.dsn) as conn:
        row = conn.execute(
            "SELECT (%s::bigint << 32) | n.oid::bigint FROM pg_namespace n WHERE n.nspname = %s",
            (LEASE_CLASS, schema.name),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _free(schema: PostgresSchema) -> bool:
    """Whether another session can take the lease key right now (and gives it back)."""
    with psycopg.connect(schema.dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT pg_try_advisory_lock(%s)", (_key(schema),)
        ).fetchone()
        taken = bool(row and row[0])
        if taken:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_key(schema),))
    return taken


def test_acquire_holds_the_key_until_release(postgres_schema: PostgresSchema) -> None:
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    try:
        assert not _free(postgres_schema)
        assert lease.pid > 0
    finally:
        lease.release()
    assert _free(postgres_schema)
    lease.release()  # idempotent


def test_a_second_acquire_is_refused_naming_the_holder(
    postgres_schema: PostgresSchema,
) -> None:
    first = Lease.acquire(
        _conninfo(postgres_schema, app="holder-1504"), schema=postgres_schema.name
    )
    try:
        conninfo = _conninfo(postgres_schema)
        with pytest.raises(StoreUnavailableError, match="reason=lease") as failure:
            Lease.acquire(conninfo, schema=postgres_schema.name)
        assert "holder-1504" in str(failure.value)
        assert str(first.pid) in str(failure.value)
    finally:
        first.release()


def test_ensure_keeps_a_held_lease(postgres_schema: PostgresSchema) -> None:
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    try:
        pid = lease.pid
        lease.ensure()
        assert lease.pid == pid
    finally:
        lease.release()


def _terminate(schema: PostgresSchema, pid: int) -> None:
    with psycopg.connect(schema.dsn, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
        admin.execute(
            "SELECT pg_sleep(0.2) FROM pg_stat_activity WHERE pid = %s", (pid,)
        )


def test_ensure_takes_a_lost_lease_back(
    postgres_schema: PostgresSchema, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="markdown_vault_mcp.stores._postgres_lease")
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    try:
        old = lease.pid
        _terminate(postgres_schema, old)
        lease.ensure()
        assert lease.pid != old
        assert not _free(postgres_schema)
        events = [r.msg.split()[0] for r in caplog.records]
        assert "postgres_vector_store_lease_lost" in events
        assert "postgres_vector_store_lease_reacquired" in events
    finally:
        lease.release()


def test_ensure_refuses_when_another_writer_took_the_lease(
    postgres_schema: PostgresSchema,
) -> None:
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    other: Lease | None = None
    try:
        _terminate(postgres_schema, lease.pid)
        other = Lease.acquire(
            _conninfo(postgres_schema, app="usurper-1504"), schema=postgres_schema.name
        )
        with pytest.raises(StoreUnavailableError, match="usurper-1504"):
            lease.ensure()
    finally:
        lease.release()
        if other is not None:
            other.release()


def test_a_holder_this_role_cannot_see_is_another_session(
    postgres_schema: PostgresSchema, postgres_server: PostgresServer
) -> None:
    if not postgres_server.throwaway:
        pytest.skip("creates a role: runs only against the harness's own container")
    role = f"mvm_test_role_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'pw'").format(sql.Identifier(role))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(postgres_schema.name), sql.Identifier(role)
            )
        )
    first = Lease.acquire(
        _conninfo(postgres_schema, app="hidden-1504"), schema=postgres_schema.name
    )
    try:
        params = conninfo_to_dict(postgres_schema.dsn)
        params.update(user=role, password="pw")
        conninfo = pg.prepare_conninfo(
            make_conninfo("", **params), schema=postgres_schema.name
        )
        with pytest.raises(StoreUnavailableError, match="reason=lease") as failure:
            Lease.acquire(conninfo, schema=postgres_schema.name)
        message = str(failure.value)
        assert "hidden-1504" in message or "another session" in message
    finally:
        first.release()
        with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_a_lease_never_shows_the_dsn(postgres_schema: PostgresSchema) -> None:
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    try:
        password = str(conninfo_to_dict(postgres_schema.dsn).get("password"))
        assert password not in repr(lease)
        assert postgres_schema.name in repr(lease)
    finally:
        lease.release()


def test_ensure_after_release_is_refused(postgres_schema: PostgresSchema) -> None:
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    lease.release()
    with pytest.raises(RuntimeError, match="released"):
        lease.ensure()


def test_ensure_replaces_a_closed_lease_connection(
    postgres_schema: PostgresSchema,
) -> None:
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    try:
        lease._conn.close()
        lease.ensure()
        assert not _free(postgres_schema)
    finally:
        lease.release()


class _Refusing:
    """A lease connection stand-in whose queries fail with a refused privilege."""

    closed = False
    broken = False

    def execute(self, *_args: object) -> object:
        raise psycopg.errors.lookup("42501")("permission denied for view pg_locks")

    def close(self) -> None:
        return None


def test_a_lease_check_that_fails_otherwise_is_unavailable(
    postgres_schema: PostgresSchema,
) -> None:
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    real = lease._conn
    try:
        lease._conn = _Refusing()  # type: ignore[assignment]
        with pytest.raises(StoreUnavailableError, match="reason=privilege"):
            lease.ensure()
    finally:
        lease._conn = real
        lease.release()


def test_a_lock_attempt_on_a_dead_connection_is_unavailable(
    postgres_schema: PostgresSchema,
) -> None:
    from markdown_vault_mcp.stores._postgres_lease import _try_lock

    conninfo = _conninfo(postgres_schema)
    closed = pg.connect(conninfo, schema=postgres_schema.name)
    closed.close()
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        _try_lock(closed, schema=postgres_schema.name)


@pytest.mark.parametrize(
    ("holder", "text"),
    [
        (None, "another session"),
        (("", 42), "another session (pid 42)"),
        (("app-x", 7), "application_name='app-x', pid 7"),
    ],
)
def test_the_holder_is_named_as_far_as_it_is_visible(
    holder: tuple[str, int] | None, text: str
) -> None:
    from markdown_vault_mcp.stores._postgres_lease import _describe

    assert _describe(holder) == text
