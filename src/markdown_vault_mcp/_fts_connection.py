"""Per-thread SQLite connection registry and SQLITE_LOCKED retry policy.

The thread-safety core extracted from ``fts_index.py`` (#760): every
thread that touches the index opens its own ``sqlite3.Connection`` on
first use, a side registry holds strong refs so ``close()`` can close
every connection — including those opened by threads that have since
exited — and application-level retry covers FTS5's ``SQLITE_LOCKED``
errors that SQLite's C-level busy handler never retries (#560).

:class:`FTSIndex <markdown_vault_mcp.fts_index.FTSIndex>` composes a
:class:`SqliteConnectionRegistry` and keeps thin delegating shims for its
historical private surface. See ``docs/design/design.md`` "Vault
thread-safety contract" for the full contract (#519).
"""

from __future__ import annotations

import contextlib
import functools
import logging
import sqlite3
import threading
import time
import uuid
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Match the busy_timeout setting; SQLITE_LOCKED needs application-level retry
# because sqlite3.OperationalError("...locked...") is error code 6 (LOCKED)
# rather than 5 (BUSY), and Python's sqlite3 / SQLite C-level busy_handler
# only handles BUSY. See https://www.sqlite.org/rescode.html#locked.
_SQLITE_LOCKED_RETRY_TIMEOUT_S = 5.0
_SQLITE_LOCKED_INITIAL_SLEEP_S = 0.01
_SQLITE_LOCKED_MAX_SLEEP_S = 0.5


def retry_on_sqlite_locked(
    operation: Callable[[], _T],
    *,
    timeout: float = _SQLITE_LOCKED_RETRY_TIMEOUT_S,
) -> _T:
    """Retry *operation* on transient SQLite "locked" errors.

    Python's ``sqlite3.Connection``'s ``busy_timeout`` only retries on
    ``SQLITE_BUSY`` (error code 5). FTS5 virtual-table internal locking
    raises ``SQLITE_LOCKED`` (error code 6) which is never retried by the
    SQLite C-level busy handler — see #560. This helper provides
    application-level retry with exponential backoff so transient FTS5
    locks (writer mid-upsert blocking a concurrent reader, or vice
    versa) don't surface as user-visible failures.

    Non-"locked" ``OperationalError``s propagate immediately.

    Args:
        operation: Callable to invoke. Re-invoked on each retry, so the
            caller is responsible for any state reset (e.g. running
            inside a fresh ``with conn:`` transaction that auto-rolls
            back).
        timeout: Maximum total wall-clock retry budget in seconds.

    Returns:
        Whatever *operation* returns on success.

    Raises:
        sqlite3.OperationalError: If the operation continues to raise a
            "locked" error past *timeout*, or if it raises an
            ``OperationalError`` that does not mention "locked".
    """
    deadline = time.monotonic() + timeout
    sleep = _SQLITE_LOCKED_INITIAL_SLEEP_S
    while True:
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            # Cap the sleep to the remaining budget so the contract
            # "retry for at most *timeout* seconds" is honoured even
            # near the deadline — without this, the final sleep could
            # push us up to _SQLITE_LOCKED_MAX_SLEEP_S past *timeout*.
            time.sleep(min(sleep, _SQLITE_LOCKED_MAX_SLEEP_S, remaining))
            sleep *= 2


def retry_on_locked(method: Callable[..., _T]) -> Callable[..., _T]:
    """Method decorator: retry the method body on SQLITE_LOCKED.

    Wraps the method so the entire body (including any ``with conn:``
    transaction) is re-invoked on a locked error. This is safe because
    Python's sqlite3 ``with conn:`` block rolls back the transaction on
    exception before the wrapper sees it — the retry starts from a
    clean state.
    """

    @functools.wraps(method)
    def wrapper(self: object, *args: object, **kwargs: object) -> _T:
        return retry_on_sqlite_locked(lambda: method(self, *args, **kwargs))

    return wrapper


def resolve_connect_uri(db_path: Path | str) -> tuple[str, bool, bool]:
    """Resolve a db_path into (connect_string, uses_uri, is_memory).

    For ``":memory:"`` returns a shared-cache URI unique to this call so that
    every per-thread ``sqlite3.connect()`` joins the same in-memory database
    (required for the per-thread connection model — see #519). For file paths
    returns the path string directly.

    The shared-cache URI is unique per registry instance (uuid4 token) so
    distinct in-process vaults do not collide.
    """
    if str(db_path) == ":memory:":
        token = uuid.uuid4().hex
        return f"file:fts_{token}?mode=memory&cache=shared", True, True
    return str(db_path), False, False


class SqliteConnectionRegistry:
    """Per-thread ``sqlite3.Connection`` registry with a closeable lifecycle.

    Owns the thread-safety machinery behind ``FTSIndex`` (#519): a
    thread-local fast path, a strong-ref registry of every opened
    connection (guarded by ``reg_lock``) so :meth:`close` reaches
    connections whose threads have exited, and the BaseException-hardened
    bootstrap/open paths that keep the registry consistent under
    interrupts.

    Args:
        db_path: SQLite database file path, or ``":memory:"`` for a
            shared-cache in-memory database.
        owner_name: Class name used in the closed-error message so callers
            see the facade they actually hold.
    """

    def __init__(self, db_path: Path | str, *, owner_name: str = "FTSIndex") -> None:
        self.db_path = db_path
        self._owner_name = owner_name
        # Resolve URI (translates ``:memory:`` to a shared-cache URI so that
        # per-thread opens see the same in-memory DB).
        self.connect_uri, self.uses_uri, self.is_memory = resolve_connect_uri(db_path)
        # Thread-safety state — see #519 and docs/design/design.md.
        self.local = threading.local()
        self.all_conns: list[sqlite3.Connection] = []
        self.reg_lock = threading.Lock()
        self.closed = False
        self.primary_conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        """Open a raw sqlite3 connection to this registry's URI."""
        conn = sqlite3.connect(
            self.connect_uri,
            check_same_thread=False,
            uri=self.uses_uri,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def apply_pragmas(self, conn: sqlite3.Connection) -> None:
        """Apply per-connection pragmas (foreign_keys, busy_timeout, synchronous).

        Called on every ``sqlite3.connect()`` — the primary connection and
        every per-thread open. These are per-connection settings that do NOT
        persist across opens.
        """
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")

    def open_primary(
        self,
        init_schema: Callable[[sqlite3.Connection], None],
        probe_shared_cache: Callable[[], None],
    ) -> None:
        """Open and register the primary connection on the calling thread.

        The whole pragma + *init_schema* + probe sequence runs under one
        BaseException cleanup block so any failure (pragma, ALTER TABLE, or
        the shared-cache probe) closes the primary connection — symmetric
        with the slow-path cleanup in :meth:`conn`.

        Args:
            init_schema: Owner callback that runs DDL/migrations/WAL on the
                primary connection (exactly once per owner instance).
            probe_shared_cache: Owner callback invoked for in-memory
                databases after registration; must raise when a second
                connection cannot see the schema (fail-fast for SQLite
                builds without ``SQLITE_ENABLE_SHARED_CACHE``).
        """
        primary = self.connect()
        try:
            # PRAGMAS FIRST — busy_timeout must be active for ALTER TABLE
            # migrations in init_schema (per #519 carryover).
            self.apply_pragmas(primary)
            init_schema(primary)
            self.local.conn = primary
            self.primary_conn = primary
            self.all_conns.append(primary)
            # Fail-fast probe: if shared-cache ``:memory:`` translation is in
            # use but SQLITE_ENABLE_SHARED_CACHE was disabled at build time, a
            # second connection to the URI will see an empty DB. Surface that
            # immediately instead of letting per-thread reads fail mysteriously
            # downstream.
            if self.is_memory:
                probe_shared_cache()
        except BaseException:
            # Close the primary regardless of how far init progressed (the
            # probe call may fail after append, leaving primary in
            # all_conns; reset both paths to a clean state).
            try:
                primary.close()
            except Exception:
                logger.debug(
                    "connection_registry.open_primary cleanup: error closing primary",
                    exc_info=True,
                )
            # Mirror the conn() slow-path TLS clear: if a partially-built
            # owner's local.conn still pointed at the now-closed primary,
            # a caller holding the instance after __init__ raised would
            # fast-path the closed conn instead of getting ProgrammingError.
            self.local.conn = None
            self.all_conns.clear()
            self.primary_conn = None
            raise

    def conn(self) -> sqlite3.Connection:
        """Return this thread's sqlite3 connection, opening one on first touch.

        Uses double-checked locking: the fast path is lock-free; the slow path
        re-checks ``closed`` under ``reg_lock`` so a concurrent ``close()``
        cannot race with a new-thread open.

        Raises:
            sqlite3.ProgrammingError: If ``close()`` has been called.

        Note on connection accumulation: dead-thread connections remain in
        ``all_conns`` (strong refs) until ``close()``. This is bounded for
        the MCP server's workload (long-lived lifespan thread + bounded
        ``asyncio.to_thread`` pool) and is preferred over weakrefs (see
        ``feedback_519_weakref_whackamole.md``).
        """
        if self.closed:
            raise sqlite3.ProgrammingError(
                f"Cannot operate on a closed {self._owner_name}"
            )
        existing: sqlite3.Connection | None = getattr(self.local, "conn", None)
        if existing is not None:
            return existing
        new_conn = self.connect()
        try:
            self.apply_pragmas(new_conn)
            with self.reg_lock:
                if self.closed:
                    raise sqlite3.ProgrammingError(
                        f"Cannot operate on a closed {self._owner_name}"
                    )
                self.local.conn = new_conn
                self.all_conns.append(new_conn)
        except BaseException:
            # Cover KeyboardInterrupt / SystemExit / asyncio.CancelledError —
            # sqlite3.Error alone would leak the open connection on teardown.
            # Clear the TLS slot first: CPython delivers signals at bytecode
            # boundaries, so an interrupt between the local.conn assignment
            # and the registry append would otherwise leave a closed conn in
            # TLS for the next fast-path call to silently return.
            self.local.conn = None
            # Re-acquire reg_lock for the registry mutation: a concurrent
            # close() iterates all_conns under the lock, so an unguarded
            # remove() here could trigger "list changed size during iteration"
            # in close().
            with self.reg_lock, contextlib.suppress(ValueError):
                self.all_conns.remove(new_conn)
            new_conn.close()
            raise
        return new_conn

    def close(self) -> None:
        """Close every per-thread connection and mark this registry closed.

        Idempotent: a second call finds an empty registry and performs no
        connection closes. After ``close()``, any thread calling
        :meth:`conn` raises ``sqlite3.ProgrammingError``.
        """
        with self.reg_lock:
            self.closed = True
            try:
                for conn in self.all_conns:
                    try:
                        conn.close()
                    except sqlite3.ProgrammingError:
                        logger.debug(
                            "connection_registry.close: connection already closed"
                        )
                    except Exception:
                        # Catch non-sqlite3.Error subclasses too (e.g. OSError
                        # from underlying file handle, RuntimeError from C
                        # wrappers) so the loop never exits mid-iteration and
                        # leaves connections un-closed. KeyboardInterrupt /
                        # SystemExit still propagate.
                        logger.error(
                            "connection_registry.close: error closing connection",
                            exc_info=True,
                        )
            finally:
                # Ensure the registry is cleared even if a BaseException
                # (KeyboardInterrupt / SystemExit) interrupts the loop, so a
                # subsequent close() retry sees a clean state.
                self.all_conns.clear()
                self.primary_conn = None
