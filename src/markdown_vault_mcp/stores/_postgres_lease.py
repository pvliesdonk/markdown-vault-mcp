"""The PostgreSQL vector store's writer lease (#1504).

At most one process writes a vault's schema at a time: a writer holds a
session advisory lock, on a connection of its own, for its whole life. The
key is one ``bigint``: ``LEASE_CLASS`` in the high 32 bits and the schema's
OID in the low 32, so two vaults in one database never block each other. A
session-level lock lasts until it is released or its session ends, so a
writer that loses its connection loses the lease too; ``ensure`` checks
before every write and takes it back or refuses, and never assumes it.

The lease is per process: the stores one :class:`LeaseRegistry` (one
backend) opens on a schema share it, so a process never conflicts with
itself when it opens a new store before closing the old one. The lease
connection turns ``idle_session_timeout`` off, since it is idle between
writes by design, and carries the store's statement and lock timeouts.
Every write is also row-locked and token-conditional (#1504 child 5), so
the lease is policy, not the only guard. What this relies on in PostgreSQL
is in ``docs/design/reference/postgresql-pgvector.md``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import psycopg
from psycopg import sql

from markdown_vault_mcp.stores._postgres import (
    Conninfo,
    classify_error,
    connect,
    not_configured,
    unavailable,
)

logger = logging.getLogger(__name__)

LEASE_CLASS = 0x4D564D31  # "MVM1"

_DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
_LOCK_TIMEOUT_MS = 10_000

_KEY = (
    "(%s::bigint << 32)"
    " | (SELECT n.oid::bigint FROM pg_catalog.pg_namespace n WHERE n.nspname = %s)"
)
_TRY = "SELECT pg_try_advisory_lock(" + _KEY + "), pg_backend_pid()"
# (does the schema exist, does this backend hold its key)
_HELD = (
    "SELECT s.oid IS NOT NULL, EXISTS (SELECT 1 FROM pg_catalog.pg_locks l"
    " WHERE l.locktype = 'advisory' AND l.pid = pg_backend_pid() AND l.granted"
    " AND l.objsubid = 1 AND l.classid = %s::oid AND l.objid = s.oid)"
    " FROM (SELECT (SELECT n.oid FROM pg_catalog.pg_namespace n"
    " WHERE n.nspname = %s) AS oid) s"
)
_HOLDER = (
    "SELECT a.application_name, l.pid FROM pg_catalog.pg_locks l"
    " LEFT JOIN pg_catalog.pg_stat_activity a ON a.pid = l.pid"
    " WHERE l.locktype = 'advisory' AND l.granted AND l.objsubid = 1"
    " AND l.classid = %s::oid"
    " AND l.objid = (SELECT n.oid FROM pg_catalog.pg_namespace n WHERE n.nspname = %s)"
    " AND l.database = (SELECT d.oid FROM pg_catalog.pg_database d"
    "   WHERE d.datname = current_database())"
)


class Lease:
    """A held writer lease on one vault schema. Construct with :meth:`acquire`.

    ``ensure`` and ``release`` may be called from different threads: one
    ``ensure`` reconnects while concurrent ones wait for it and find the
    lease held, and a release that lands while ``ensure`` reconnects wins.
    """

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        *,
        conninfo: Conninfo,
        schema: str,
        pid: int,
        statement_timeout_ms: int,
    ) -> None:
        self._conn = conn
        self._conninfo = conninfo
        self._schema = schema
        self._statement_timeout_ms = statement_timeout_ms
        self.pid = pid
        self._released = False
        self._mutex = threading.Lock()  # guards the connection and the flag
        self._reconnecting = threading.Lock()  # one ensure() reconnects at a time

    def __repr__(self) -> str:
        """Name the schema and the backend, never the DSN."""
        return f"Lease(schema={self._schema!r}, pid={self.pid})"

    @classmethod
    def acquire(
        cls,
        conninfo: Conninfo,
        *,
        schema: str,
        statement_timeout_ms: int = _DEFAULT_STATEMENT_TIMEOUT_MS,
    ) -> Lease:
        """Take the lease on *schema*, on a connection of its own.

        Args:
            conninfo: A connection string from ``prepare_conninfo``.
            schema: The vault schema, which must exist.
            statement_timeout_ms: The lease connection's statement timeout;
                0 sets none.

        Returns:
            The held lease.

        Raises:
            StoreUnavailableError: ``reason=lease`` if another session holds
                it, naming that session; another reason if PostgreSQL cannot
                be reached or read.
            StoreNotConfiguredError: If the schema does not exist.
        """
        conn, pid = _locked_connection(
            conninfo, schema=schema, statement_timeout_ms=statement_timeout_ms
        )
        logger.debug(
            "postgres_vector_store_lease_acquired schema=%s pid=%d", schema, pid
        )
        return cls(
            conn,
            conninfo=conninfo,
            schema=schema,
            pid=pid,
            statement_timeout_ms=statement_timeout_ms,
        )

    def ensure(self) -> None:
        """Make sure the lease is still held, taking it back if it was lost.

        Raises:
            RuntimeError: If the lease was released.
            StoreUnavailableError: ``reason=lease`` if another session took it
                meanwhile; another reason if PostgreSQL cannot be reached or
                the check fails on a live connection (which keeps the lease).
            StoreNotConfiguredError: If the schema no longer exists.
        """
        with self._reconnecting:
            with self._mutex:
                self._refuse_released()
                if self._still_held():
                    return
                logger.warning(
                    "postgres_vector_store_lease_lost schema=%s", self._schema
                )
                self._conn.close()
            conn, pid = _locked_connection(
                self._conninfo,
                schema=self._schema,
                statement_timeout_ms=self._statement_timeout_ms,
            )
            with self._mutex:
                if self._released:
                    conn.close()
                    self._refuse_released()
                self._conn, self.pid = conn, pid
        logger.info(
            "postgres_vector_store_lease_reacquired schema=%s pid=%d", self._schema, pid
        )

    def _refuse_released(self) -> None:
        """Raise when the lease has been released."""
        if self._released:
            raise RuntimeError("The writer lease was released.")

    def _still_held(self) -> bool:
        """Whether this lease's connection is alive and still holds the key.

        A failure on a connection that is still alive (a timeout, a refused
        privilege, too many connections) is reported, not taken as a lost
        lease: the lock may well still be held.

        Raises:
            StoreNotConfiguredError: If the schema no longer exists (said
                before any reconnect, so it is never logged as a lost lease).
            StoreUnavailableError: If the check fails on a live connection.
        """
        if self._conn.closed or self._conn.broken:
            return False
        try:
            row = self._conn.execute(_HELD, (LEASE_CLASS, self._schema)).fetchone()
        except psycopg.Error as exc:
            if self._conn.closed or self._conn.broken:
                return False
            failure: Exception = classify_error(
                exc, schema=self._schema, action="written"
            )
        else:
            if row is not None and row[0]:
                return bool(row[1])
            failure = not_configured(
                f"Schema {self._schema} does not exist.", schema=self._schema
            )
        raise failure from None

    def held(self) -> bool:
        """Whether this lease's connection still holds the key; never reconnects.

        Raises:
            RuntimeError: If the lease was released.
            StoreUnavailableError: If the check fails on a live connection.
            StoreNotConfiguredError: If the schema no longer exists.
        """
        with self._mutex:
            self._refuse_released()
            return self._still_held()

    def release(self) -> None:
        """Release the lease by closing its connection. Idempotent."""
        with self._mutex:
            if self._released:
                return
            self._released = True
            self._conn.close()


class LeaseHandle:
    """One store's share of a process's lease on a schema."""

    def __init__(self, registry: LeaseRegistry, schema: str, lease: Lease) -> None:
        self._registry = registry
        self._schema = schema
        self._lease = lease
        self._released = False

    def __repr__(self) -> str:
        """Name the schema and the backend, never the DSN."""
        return f"LeaseHandle(schema={self._schema!r}, pid={self._lease.pid})"

    @property
    def pid(self) -> int:
        """The backend holding the lease."""
        return self._lease.pid

    def ensure(self) -> None:
        """See :meth:`Lease.ensure`; a released handle raises ``RuntimeError``."""
        if self._released:
            raise RuntimeError("The writer lease was released.")
        self._lease.ensure()

    def held(self) -> bool:
        """See :meth:`Lease.held`; a released handle raises ``RuntimeError``."""
        if self._released:
            raise RuntimeError("The writer lease was released.")
        return self._lease.held()

    def release(self) -> None:
        """Give this store's share back; the last one releases the lease. Idempotent."""
        if self._released:
            return
        self._released = True
        self._registry._give_back(self._schema)


class LeaseRegistry:
    """The leases one process (one backend) holds, shared per schema.

    Taking or giving back a schema's lease holds that schema's own lock for
    the whole step (so a last release finishes before the next acquire
    starts), never the registry's, so schemas never wait on each other.
    """

    def __init__(self) -> None:
        self._held: dict[str, tuple[Lease, int]] = {}
        self._schema_locks: dict[str, threading.Lock] = {}
        self._mutex = threading.Lock()  # guards the two dictionaries only

    def _schema_lock(self, schema: str) -> threading.Lock:
        """Return *schema*'s own lock, creating it once."""
        with self._mutex:
            return self._schema_locks.setdefault(schema, threading.Lock())

    def acquire(
        self,
        conninfo: Conninfo,
        *,
        schema: str,
        statement_timeout_ms: int = _DEFAULT_STATEMENT_TIMEOUT_MS,
    ) -> LeaseHandle:
        """Share this process's lease on *schema*, taking it if none is held.

        Raises:
            StoreUnavailableError: As :meth:`Lease.acquire`.
            StoreNotConfiguredError: As :meth:`Lease.acquire`.
        """
        with self._schema_lock(schema):
            with self._mutex:
                held = self._held.get(schema)
            if held is None:
                lease = Lease.acquire(
                    conninfo, schema=schema, statement_timeout_ms=statement_timeout_ms
                )
                held = (lease, 0)
            with self._mutex:
                self._held[schema] = (held[0], held[1] + 1)
            return LeaseHandle(self, schema, held[0])

    def _give_back(self, schema: str) -> None:
        """Drop one share of *schema*'s lease; release it with the last."""
        with self._schema_lock(schema):
            with self._mutex:
                lease, count = self._held[schema]
                if count > 1:
                    self._held[schema] = (lease, count - 1)
                    return
                del self._held[schema]
            lease.release()


def _locked_connection(
    conninfo: Conninfo, *, schema: str, statement_timeout_ms: int
) -> tuple[psycopg.Connection[Any], int]:
    """Open a lease connection, set it up, and take the key on it.

    Raises:
        StoreUnavailableError: As :func:`_try_lock`, or if the connection
            cannot be made or set up.
        StoreNotConfiguredError: If the schema does not exist.
    """
    conn = connect(conninfo, schema=schema)
    try:
        _prepare(conn, schema=schema, statement_timeout_ms=statement_timeout_ms)
        pid = _try_lock(conn, schema=schema)
    except BaseException:
        conn.close()
        raise
    return conn, pid


def _prepare(
    conn: psycopg.Connection[Any], *, schema: str, statement_timeout_ms: int
) -> None:
    """Keep the idle lease connection alive and bound its waits."""
    settings = (
        sql.SQL("SET idle_session_timeout = 0"),
        sql.SQL("SET statement_timeout = {}").format(sql.Literal(statement_timeout_ms)),
        sql.SQL("SET lock_timeout = {}").format(sql.Literal(_LOCK_TIMEOUT_MS)),
    )
    try:
        for statement in settings:
            conn.execute(statement)
    except psycopg.Error as exc:
        failure = classify_error(exc, schema=schema, action="opened")
    else:
        return
    raise failure from None


def _try_lock(conn: psycopg.Connection[Any], *, schema: str) -> int:
    """Try the lease key on *conn*; return its backend pid when taken.

    Raises:
        StoreUnavailableError: ``reason=lease`` naming the holder when taken
            elsewhere; another reason when the query fails.
        StoreNotConfiguredError: If the schema does not exist (the key is NULL).
    """
    try:
        row = conn.execute(_TRY, (LEASE_CLASS, schema)).fetchone()
        missing = row is None or row[0] is None
        taken = bool(row and row[0])
        holder = (
            None
            if taken or missing
            else conn.execute(_HOLDER, (LEASE_CLASS, schema)).fetchone()
        )
    except psycopg.Error as exc:
        failure: Exception = classify_error(exc, schema=schema, action="opened")
    else:
        if taken and row is not None:
            return int(row[1])
        failure = (
            not_configured(f"Schema {schema} does not exist.", schema=schema)
            if missing
            else unavailable(
                f"Another writer holds the lease on schema {schema}: "
                f"{_describe(holder)}.",
                reason="lease",
                schema=schema,
            )
        )
    raise failure from None


def _describe(holder: tuple[Any, ...] | None) -> str:
    """Name the session holding the lease, as far as this role may see it."""
    if holder is None:
        return "another session"
    name, pid = holder
    if name:
        return f"application_name={name!r}, pid {pid}"
    return f"another session (pid {pid})"
