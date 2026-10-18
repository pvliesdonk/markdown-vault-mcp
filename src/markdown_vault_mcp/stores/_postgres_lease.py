"""The PostgreSQL vector store's writer lease (#1504).

At most one process writes a vault's schema at a time: a writer holds a
session advisory lock, on a connection of its own, for its whole life. The
key is one ``bigint``: ``LEASE_CLASS`` in the high 32 bits and the schema's
OID in the low 32, so two vaults in one database never block each other. A
session-level lock lasts until it is released or its session ends, so a
writer that loses its connection loses the lease too; ``ensure`` checks
before every write and takes it back or refuses, and never assumes it.
Every write is also row-locked and token-conditional (#1504 child 5), so
the lease is policy, not the only guard. What this relies on in PostgreSQL
is in ``docs/design/reference/postgresql-pgvector.md``.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from markdown_vault_mcp.stores._postgres import (
    Conninfo,
    classify_error,
    connect,
    unavailable,
)

logger = logging.getLogger(__name__)

LEASE_CLASS = 0x4D564D31  # "MVM1"

_KEY = "(%s::bigint << 32) | (SELECT n.oid::bigint FROM pg_catalog.pg_namespace n WHERE n.nspname = %s)"
_TRY = "SELECT pg_try_advisory_lock(" + _KEY + "), pg_backend_pid()"
_HELD = (
    "SELECT 1 FROM pg_catalog.pg_locks l WHERE l.locktype = 'advisory'"
    " AND l.pid = pg_backend_pid() AND l.granted AND l.objsubid = 1"
    " AND l.classid = %s::oid"
    " AND l.objid = (SELECT n.oid FROM pg_catalog.pg_namespace n WHERE n.nspname = %s)"
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
    """A held writer lease on one vault schema. Construct with :meth:`acquire`."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        *,
        conninfo: Conninfo,
        schema: str,
        pid: int,
    ) -> None:
        self._conn = conn
        self._conninfo = conninfo
        self._schema = schema
        self.pid = pid
        self._released = False

    def __repr__(self) -> str:
        """Name the schema and the backend, never the DSN."""
        return f"Lease(schema={self._schema!r}, pid={self.pid})"

    @classmethod
    def acquire(cls, conninfo: Conninfo, *, schema: str) -> Lease:
        """Take the lease on *schema*, on a connection of its own.

        Args:
            conninfo: A connection string from ``prepare_conninfo``.
            schema: The vault schema, which must exist.

        Returns:
            The held lease.

        Raises:
            StoreUnavailableError: ``reason=lease`` if another session holds
                it, naming that session; another reason if PostgreSQL cannot
                be reached or read.
        """
        conn = connect(conninfo, schema=schema)
        try:
            pid = _try_lock(conn, schema=schema)
        except BaseException:
            conn.close()
            raise
        logger.debug(
            "postgres_vector_store_lease_acquired schema=%s pid=%d", schema, pid
        )
        return cls(conn, conninfo=conninfo, schema=schema, pid=pid)

    def ensure(self) -> None:
        """Make sure the lease is still held, taking it back if it was lost.

        Raises:
            RuntimeError: If the lease was released.
            StoreUnavailableError: ``reason=lease`` if another session took it
                meanwhile; another reason if PostgreSQL cannot be reached.
        """
        if self._released:
            raise RuntimeError("The writer lease was released.")
        if self._still_held():
            return
        logger.warning("postgres_vector_store_lease_lost schema=%s", self._schema)
        self._conn.close()
        conn = connect(self._conninfo, schema=self._schema)
        try:
            self.pid = _try_lock(conn, schema=self._schema)
        except BaseException:
            conn.close()
            raise
        self._conn = conn
        logger.info(
            "postgres_vector_store_lease_reacquired schema=%s pid=%d",
            self._schema,
            self.pid,
        )

    def _still_held(self) -> bool:
        """Whether this lease's connection is alive and still holds the key."""
        if self._conn.closed or self._conn.broken:
            return False
        try:
            row = self._conn.execute(_HELD, (LEASE_CLASS, self._schema)).fetchone()
        except psycopg.OperationalError:
            return False
        except psycopg.Error as exc:
            failure = classify_error(exc, schema=self._schema, action="written")
        else:
            return row is not None
        raise failure from None

    def release(self) -> None:
        """Release the lease by closing its connection. Idempotent."""
        if self._released:
            return
        self._released = True
        self._conn.close()


def _try_lock(conn: psycopg.Connection[Any], *, schema: str) -> int:
    """Try the lease key on *conn*; return its backend pid when taken.

    Raises:
        StoreUnavailableError: ``reason=lease`` naming the holder when taken
            elsewhere; another reason when the query fails.
    """
    try:
        row = conn.execute(_TRY, (LEASE_CLASS, schema)).fetchone()
        taken = bool(row and row[0])
        holder = (
            None if taken else conn.execute(_HOLDER, (LEASE_CLASS, schema)).fetchone()
        )
    except psycopg.Error as exc:
        failure = classify_error(exc, schema=schema, action="opened")
    else:
        if taken and row is not None:
            return int(row[1])
        failure = unavailable(
            f"Another writer holds the lease on schema {schema}: {_describe(holder)}.",
            reason="lease",
            schema=schema,
        )
    raise failure from None


def _describe(holder: tuple[Any, ...] | None) -> str:
    """Name the session holding the lease, as far as this role may see it."""
    if holder is None:
        return "another session"
    name, pid = holder
    return (
        f"application_name={name!r}, pid {pid}"
        if name
        else f"another session (pid {pid})"
    )
