"""The PostgreSQL writer lease (#1504)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from markdown_vault_mcp.exceptions import StoreNotConfiguredError, StoreUnavailableError
from markdown_vault_mcp.stores import _postgres as pg
from markdown_vault_mcp.stores import _postgres_lease as leases
from markdown_vault_mcp.stores._postgres_lease import Lease, LeaseRegistry
from tests.fixtures.postgres import lease_is_free

if TYPE_CHECKING:
    from tests.fixtures.postgres import PostgresSchema, PostgresServer


def _conninfo(schema: PostgresSchema, **params: str) -> pg.Conninfo:
    dsn = make_conninfo(schema.dsn, **params) if params else schema.dsn
    return pg.prepare_conninfo(dsn, schema=schema.name)


def _free(schema: PostgresSchema) -> bool:
    return lease_is_free(schema.dsn, schema.name)


def _terminate(schema: PostgresSchema, pid: int) -> None:
    """End backend *pid*, waiting up to 5 s for it to exit (PostgreSQL 14+)."""
    with psycopg.connect(schema.dsn, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,))


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
        _conninfo(postgres_schema, application_name="holder-1504"),
        schema=postgres_schema.name,
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
            _conninfo(postgres_schema, application_name="usurper-1504"),
            schema=postgres_schema.name,
        )
        with pytest.raises(StoreUnavailableError, match=r"reason=lease") as failure:
            lease.ensure()
        assert "usurper-1504" in str(failure.value)
    finally:
        lease.release()
        if other is not None:
            other.release()


def test_another_roles_holder_is_still_named(
    postgres_schema: PostgresSchema, postgres_server: PostgresServer
) -> None:
    """PostgreSQL shows any session's application_name to every role (reference)."""
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
        _conninfo(postgres_schema, application_name="other-role-1504"),
        schema=postgres_schema.name,
    )
    try:
        params = conninfo_to_dict(postgres_schema.dsn)
        params.update(user=role, password="pw")
        conninfo = pg.prepare_conninfo(
            make_conninfo("", **params), schema=postgres_schema.name
        )
        with pytest.raises(StoreUnavailableError, match="reason=lease") as failure:
            Lease.acquire(conninfo, schema=postgres_schema.name)
        assert "other-role-1504" in str(failure.value)
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


class _Failing:
    """A live lease connection stand-in whose queries fail with *exc*."""

    closed = False
    broken = False

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def execute(self, *_args: object) -> object:
        raise self._exc

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("sqlstate", "reason"),
    [("42501", "privilege"), ("57014", "timeout"), ("53300", "connect")],
)
def test_a_failing_check_on_a_live_connection_keeps_the_lease(
    postgres_schema: PostgresSchema, sqlstate: str, reason: str
) -> None:
    """A transient error is reported, not mistaken for a lost lease."""
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    real = lease._conn
    try:
        stand_in = _Failing(psycopg.errors.lookup(sqlstate)("boom"))
        lease._conn = stand_in  # type: ignore[assignment]
        with pytest.raises(StoreUnavailableError, match=f"reason={reason}"):
            lease.ensure()
        assert lease._conn is stand_in  # no reconnect replaced it
        assert not _free(postgres_schema)  # the real connection still holds it
    finally:
        lease._conn = real
        lease.release()


def test_a_lock_attempt_on_a_dead_connection_is_unavailable(
    postgres_schema: PostgresSchema,
) -> None:
    conninfo = _conninfo(postgres_schema)
    closed = pg.connect(conninfo, schema=postgres_schema.name)
    closed.close()
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        leases._try_lock(closed, schema=postgres_schema.name)


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
    assert leases._describe(holder) == text


def test_an_idle_session_timeout_does_not_drop_the_lease(
    postgres_schema: PostgresSchema,
) -> None:
    conninfo = _conninfo(postgres_schema, options="-c idle_session_timeout=300")
    lease = Lease.acquire(conninfo, schema=postgres_schema.name)
    try:
        time.sleep(1.0)
        assert not _free(postgres_schema)
    finally:
        lease.release()


def test_a_lease_check_behind_a_catalogue_lock_times_out(
    postgres_schema: PostgresSchema,
) -> None:
    lease = Lease.acquire(
        _conninfo(postgres_schema),
        schema=postgres_schema.name,
        statement_timeout_ms=500,
    )
    holder = psycopg.connect(postgres_schema.dsn)
    try:
        holder.execute("LOCK pg_catalog.pg_namespace IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(StoreUnavailableError, match="reason=timeout"):
            lease.ensure()
    finally:
        holder.close()
        lease.release()


def test_a_dropped_schema_is_not_configured_not_a_lease_conflict(
    postgres_server: PostgresServer,
) -> None:
    from tests.fixtures.postgres import scratch_schema

    with scratch_schema(postgres_server.dsn) as name:
        conninfo = pg.prepare_conninfo(postgres_server.dsn, schema=name)
        lease = Lease.acquire(conninfo, schema=name)
        try:
            with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name))
                )
            with pytest.raises(StoreNotConfiguredError, match="does not exist"):
                lease.ensure()
        finally:
            lease.release()
            with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
                admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))


def test_a_release_during_a_reconnect_wins(
    postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lease released while ensure() reconnects ends released, holding nothing."""
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    real_try = leases._try_lock

    def release_then_lock(conn: psycopg.Connection[object], *, schema: str) -> int:
        lease._released = True  # what a concurrent release() has done by now
        return real_try(conn, schema=schema)

    lease._conn.close()
    monkeypatch.setattr(leases, "_try_lock", release_then_lock)
    with pytest.raises(RuntimeError, match="released"):
        lease.ensure()
    assert _free(postgres_schema)


def test_one_registry_shares_a_schemas_lease_between_its_stores(
    postgres_schema: PostgresSchema,
) -> None:
    registry = LeaseRegistry()
    conninfo = _conninfo(postgres_schema)
    first = registry.acquire(conninfo, schema=postgres_schema.name)
    second = registry.acquire(conninfo, schema=postgres_schema.name)
    try:
        assert first.pid == second.pid
        first.release()
        first.release()  # one handle's release is idempotent
        assert not _free(postgres_schema)
        second.ensure()
    finally:
        second.release()
    assert _free(postgres_schema)


def test_two_registries_conflict_like_two_processes(
    postgres_schema: PostgresSchema,
) -> None:
    conninfo = _conninfo(postgres_schema)
    held = LeaseRegistry().acquire(conninfo, schema=postgres_schema.name)
    other = LeaseRegistry()
    try:
        with pytest.raises(StoreUnavailableError, match="reason=lease"):
            other.acquire(conninfo, schema=postgres_schema.name)
    finally:
        held.release()


def test_a_handle_after_release_refuses_ensure(postgres_schema: PostgresSchema) -> None:
    handle = LeaseRegistry().acquire(
        _conninfo(postgres_schema), schema=postgres_schema.name
    )
    handle.release()
    with pytest.raises(RuntimeError, match="released"):
        handle.ensure()


def test_a_lease_connection_that_cannot_be_set_up_is_unavailable(
    postgres_schema: PostgresSchema,
) -> None:
    conninfo = _conninfo(postgres_schema)
    closed = pg.connect(conninfo, schema=postgres_schema.name)
    closed.close()
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        leases._prepare(closed, schema=postgres_schema.name, statement_timeout_ms=1000)


def test_concurrent_ensures_after_a_loss_both_recover(
    postgres_schema: PostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two writers' ensure() on one lost lease: one reconnects, the other waits for it."""
    import threading

    caplog.set_level(logging.INFO, logger="markdown_vault_mcp.stores._postgres_lease")
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    real_locked = leases._locked_connection
    both_in = threading.Barrier(2, timeout=5)

    def slow_locked(*args: object, **kwargs: object) -> object:
        time.sleep(0.3)  # widen the window in which the second ensure() could race
        return real_locked(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(leases, "_locked_connection", slow_locked)
    lease._conn.close()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            both_in.wait()
            lease.ensure()
        except BaseException as exc:  # collected for the assertion in the main thread
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert errors == []
        assert not _free(postgres_schema)
        reacquired = [
            r
            for r in caplog.records
            if r.msg.startswith("postgres_vector_store_lease_reacquired")
        ]
        assert len(reacquired) == 1
    finally:
        lease.release()


def test_a_dropped_schema_logs_no_lost_lease(
    postgres_server: PostgresServer, caplog: pytest.LogCaptureFixture
) -> None:
    from tests.fixtures.postgres import scratch_schema

    caplog.set_level(logging.INFO, logger="markdown_vault_mcp.stores._postgres_lease")
    with scratch_schema(postgres_server.dsn) as name:
        lease = Lease.acquire(
            pg.prepare_conninfo(postgres_server.dsn, schema=name), schema=name
        )
        try:
            with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name))
                )
            with pytest.raises(StoreNotConfiguredError, match="does not exist"):
                lease.ensure()
            events = [r.msg.split()[0] for r in caplog.records]
            assert "postgres_vector_store_lease_lost" not in events
        finally:
            lease.release()
            with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
                admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))


def test_acquiring_one_schema_does_not_wait_for_another(
    postgres_server: PostgresServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow first acquire on schema A does not block schema B in the same registry."""
    import threading

    from tests.fixtures.postgres import scratch_schema

    registry = LeaseRegistry()
    entered_a = threading.Event()
    finish_a = threading.Event()
    real_acquire = Lease.acquire

    def gated(conninfo: pg.Conninfo, *, schema: str, **kwargs: object) -> Lease:
        if schema == slow_name:
            entered_a.set()
            assert finish_a.wait(10)
        return real_acquire(conninfo, schema=schema, **kwargs)  # type: ignore[arg-type]

    with (
        scratch_schema(postgres_server.dsn) as slow_name,
        scratch_schema(postgres_server.dsn) as fast_name,
    ):
        monkeypatch.setattr(Lease, "acquire", staticmethod(gated))
        handles: list[object] = []
        slow = threading.Thread(
            target=lambda: handles.append(
                registry.acquire(
                    pg.prepare_conninfo(postgres_server.dsn, schema=slow_name),
                    schema=slow_name,
                )
            )
        )
        slow.start()
        try:
            assert entered_a.wait(5)
            fast_done = threading.Event()

            def acquire_fast() -> None:
                registry.acquire(
                    pg.prepare_conninfo(postgres_server.dsn, schema=fast_name),
                    schema=fast_name,
                ).release()
                fast_done.set()

            fast = threading.Thread(target=acquire_fast)
            fast.start()
            # Schema B completes while schema A's acquire is still held open.
            assert fast_done.wait(3)
            fast.join(timeout=3)
        finally:
            finish_a.set()
            slow.join(timeout=10)
            for handle in handles:
                handle.release()  # type: ignore[attr-defined]


def test_a_release_to_zero_finishes_before_the_next_acquire(
    postgres_schema: PostgresSchema,
) -> None:
    registry = LeaseRegistry()
    conninfo = _conninfo(postgres_schema)
    for _ in range(5):
        handle = registry.acquire(conninfo, schema=postgres_schema.name)
        handle.release()
    assert _free(postgres_schema)


def test_held_checks_without_reconnecting(postgres_schema: PostgresSchema) -> None:
    lease = Lease.acquire(_conninfo(postgres_schema), schema=postgres_schema.name)
    try:
        assert lease.held()
        lease._conn.close()
        assert not lease.held()
        assert lease._conn.closed  # not replaced: only ensure() reconnects
    finally:
        lease.release()
    with pytest.raises(RuntimeError, match="released"):
        lease.held()


def test_a_handle_after_release_refuses_held(postgres_schema: PostgresSchema) -> None:
    handle = LeaseRegistry().acquire(
        _conninfo(postgres_schema), schema=postgres_schema.name
    )
    handle.release()
    with pytest.raises(RuntimeError, match="released"):
        handle.held()
