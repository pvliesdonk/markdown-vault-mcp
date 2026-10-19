"""PostgresVectorBackend / PostgresVectorStore: a vault's vectors in a PostgreSQL schema (#1504).

The backend carries the DSN and pool settings; the location is the vault's
schema, and there is no in-memory form. Opening sorts every failure into one
of the contract's outcomes (``_postgres``), checks the tables through
``pg_catalog`` (``_postgres_tables``) and the stored identity
(``_identity``), and opens the pool. The store reads in ``REPEATABLE READ``,
read-only transactions that start from the live meta row, as the streamed
SQLite store does; nothing per chunk is held in the vault process. Writing
and searching arrive with #1504's later children. What this relies on in
PostgreSQL, pgvector and psycopg is in
``docs/design/reference/postgresql-pgvector.md``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple, TypeVar

try:
    import psycopg
    from psycopg import sql
    from psycopg_pool import PoolTimeout

    from markdown_vault_mcp.stores._postgres import (
        BACKEND_NAME,
        Conninfo,
        Extension,
        PoolSettings,
        check_schema,
        classify_error,
        connect,
        find_extension,
        not_configured,
        open_pool,
        prepare_conninfo,
        statement_timeout_ms,
        unavailable,
    )
except ImportError as exc:  # pragma: no cover - exercised in a subprocess
    raise ImportError(
        "The PostgreSQL vector store needs the postgres extra: "
        "pip install 'markdown-vault-mcp[postgres]'"
    ) from exc

from markdown_vault_mcp.exceptions import ReadOnlyError
from markdown_vault_mcp.stores._identity import served_identity
from markdown_vault_mcp.stores._postgres_lease import LeaseHandle, LeaseRegistry
from markdown_vault_mcp.stores._postgres_tables import (
    Layout,
    Meta,
    check_layout,
    read_meta,
    stored_count,
    stored_rows,
    stored_tokens,
    where,
)
from markdown_vault_mcp.stores.registry import register_backend
from markdown_vault_mcp.types import EmbeddingIdentity, StoreFamily

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from psycopg_pool import ConnectionPool

    from markdown_vault_mcp.types import RevisionToken, VectorRow

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_READ_TRANSACTION = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
_LOCK_TIMEOUT_MS = 10_000  # spec §6.3: waiting for a lock on a store table


class _Connection(NamedTuple):
    """What a store holds to reach PostgreSQL.

    Attributes:
        pool: The store's own pool, closed with the store.
        conninfo: The prepared connection string (hides itself in a ``repr``).
        extension: Where the ``vector`` extension lives.
        lease: A writer's lease on the schema; ``None`` for a reader.
    """

    pool: ConnectionPool[psycopg.Connection[tuple[Any, ...]]]
    conninfo: Conninfo
    extension: Extension
    lease: LeaseHandle | None


class PostgresVectorStore:
    """A vault's vector store in a PostgreSQL schema.

    Construct through :class:`PostgresVectorBackend`. Its ``repr`` names the
    schema only.
    """

    def __init__(
        self,
        *,
        schema: str,
        identity: EmbeddingIdentity,
        read_only: bool,
        connection: _Connection,
        reset_pending: bool = False,
    ) -> None:
        self._schema = schema
        self._identity = identity
        self._read_only = read_only
        self._conn = connection
        self._reset_pending = reset_pending
        self._closed = False

    def __repr__(self) -> str:
        """Name the schema, never the DSN."""
        return (
            f"PostgresVectorStore(schema={self._schema!r}, read_only={self._read_only})"
        )

    @property
    def schema(self) -> str:
        """The vault schema this store lives in."""
        return self._schema

    @property
    def read_only(self) -> bool:
        """Whether this process opened the store without owning it."""
        return self._read_only

    @property
    def identity(self) -> EmbeddingIdentity:
        """The embedding space of the rows, as of open or this store's own publish."""
        return self._identity

    @property
    def generation(self) -> int:
        """Bumped by each generation publish, whichever process published.

        Raises:
            RuntimeError: If the store is closed.
            StoreIncompatibleError: If another process has since published
                another identity.
            StoreCorruptError: If the meta row is malformed.
            StoreUnavailableError: If PostgreSQL cannot be read.
        """
        return self._reading(lambda _conn, meta: meta.generation, 0)

    @property
    def count(self) -> int:
        """The number of stored rows (a query; see :meth:`_reading` for errors)."""
        return self._reading(
            lambda conn, _meta: stored_count(conn, schema=self._schema), 0
        )

    def tokens(self) -> dict[str, RevisionToken | None]:
        """Each stored document's token (a query; see :meth:`_reading` for errors)."""
        return self._reading(
            lambda conn, _meta: stored_tokens(conn, schema=self._schema), {}
        )

    def rows_for(self, path: str) -> list[VectorRow]:
        """*path*'s rows, vectors normalised, in storage order (a query)."""
        return self._reading(
            lambda conn, _meta: stored_rows(conn, schema=self._schema, path=path), []
        )

    def _reading(
        self, read: Callable[[psycopg.Connection[Any], Meta], _T], empty: _T
    ) -> _T:
        """Run *read* in one read-only ``REPEATABLE READ`` transaction.

        *read* gets the meta row the transaction sees. A fresh store whose
        first write has not landed, and a schema without the tables, read as
        *empty*.

        Raises:
            RuntimeError: If the store is closed.
            StoreIncompatibleError: If another process has since published
                another identity.
            StoreCorruptError: If the meta row is malformed.
            StoreUnavailableError: If PostgreSQL cannot be read.
        """
        if self._closed:
            raise RuntimeError("The vector store is closed.")
        if self._reset_pending:
            return empty
        try:
            with self._conn.pool.connection() as conn, conn.transaction():
                conn.execute(_READ_TRANSACTION)
                meta = read_meta(conn, schema=self._schema, action="read")
                return empty if meta is None else self._read_checked(conn, meta, read)
        except PoolTimeout:
            failure = unavailable(
                f"No PostgreSQL connection for schema {self._schema} was free in time.",
                reason="pool",
                schema=self._schema,
            )
        except psycopg.Error as exc:
            failure = classify_error(exc, schema=self._schema, action="read")
        raise failure from None

    def _read_checked(
        self,
        conn: psycopg.Connection[Any],
        meta: Meta,
        read: Callable[[psycopg.Connection[Any], Meta], _T],
    ) -> _T:
        """Refuse a live identity of another space, then run *read*."""
        served_identity(
            meta.identity,
            self._identity,
            backend=BACKEND_NAME,
            where=where(self._schema),
            location=self._schema,
        )
        return read(conn, meta)

    def _ensure_lease(self) -> None:
        """Make sure this writer still holds the schema's lease; writes call it first.

        Raises:
            RuntimeError: If the store is closed.
            ReadOnlyError: If the store was opened read-only.
            StoreUnavailableError: ``reason=lease`` if another writer took it.
        """
        if self._closed:
            raise RuntimeError("The vector store is closed.")
        lease = self._conn.lease
        if lease is None:
            raise ReadOnlyError(
                f"The vector store in schema {self._schema} was opened read-only."
            )
        lease.ensure()

    def checkpoint(self) -> None:
        """Do nothing: every publish is durable when it commits."""

    def close(self) -> None:
        """Close the pool and release the lease. Idempotent; reads afterwards raise."""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.pool.close()
        finally:
            if self._conn.lease is not None:
                self._conn.lease.release()


class PostgresVectorBackend:
    """Open :class:`PostgresVectorStore` instances in the schemas of one database."""

    family: StoreFamily = "vector"
    name = BACKEND_NAME

    def __init__(self, dsn: str, *, pool: PoolSettings | None = None) -> None:
        """Carry the connection settings every store this backend opens uses.

        Args:
            dsn: A libpq URI or key/value connection string; it may carry the
                password, so it is never logged or shown.
            pool: The pool's size and timeouts; ``PoolSettings()`` by default.
        """
        self._dsn = dsn
        self._pool = pool if pool is not None else PoolSettings()
        self._leases = LeaseRegistry()

    def __repr__(self) -> str:
        """Hide the DSN."""
        return "PostgresVectorBackend(dsn=<hidden>)"

    def open(
        self,
        location: Path | str | None,
        identity: object | None = None,
        *,
        owns_files: bool = True,
    ) -> PostgresVectorStore:
        """Open the store in schema *location*.

        Args:
            location: The vault schema; ``None`` is refused.
            identity: The ``EmbeddingIdentity`` the rows must belong to.
            owns_files: ``False`` opens read-only; ``True`` opens a writer,
                which needs ``CREATE`` on a schema without the tables.

        Returns:
            The open store; an empty schema opens as an empty store.

        Raises:
            ValueError: If *identity* is not an ``EmbeddingIdentity``.
            StoreNotConfiguredError: If no schema is given, the DSN is
                invalid, or the schema or ``vector`` extension is missing.
            StoreUnavailableError: If PostgreSQL cannot be reached or read,
                or a writer may not create the tables.
            StoreCorruptError: If the schema holds a malformed store.
            StoreUnsupportedSchemaError: If its format is newer.
            StoreIncompatibleError: If it holds another identity.
        """
        schema, wanted = _checked_request(location, identity)
        return self._open(schema, wanted, read_only=not owns_files, fresh=False)

    def open_fresh(
        self, location: Path | str | None, identity: EmbeddingIdentity
    ) -> PostgresVectorStore:
        """Open schema *location* as an empty store at *identity*, reading none of its tables.

        The connection, the ``vector`` extension, the schema and the right to
        create in it are checked, so a misconfiguration fails here; the
        store's tables are not read, and stay as they are until the fresh
        store's first write replaces them (#1504 child 5), so a rebuild that
        never completes leaves the previous rows for the next open.

        Args:
            location: The vault schema; ``None`` is refused.
            identity: The identity the rebuild publishes under.

        Returns:
            A writable store that reads as empty until its first write.

        Raises:
            ValueError: If *identity* is not an ``EmbeddingIdentity``.
            StoreNotConfiguredError: As :meth:`open`.
            StoreUnavailableError: As :meth:`open`.
        """
        schema, wanted = _checked_request(location, identity)
        return self._open(schema, wanted, read_only=False, fresh=True)

    def _open(
        self, schema: str, wanted: EmbeddingIdentity, *, read_only: bool, fresh: bool
    ) -> PostgresVectorStore:
        """Validate the environment, take a writer's lease, then open the store."""
        conninfo = prepare_conninfo(self._dsn, schema=schema)
        with connect(conninfo, schema=schema) as conn:
            _bound_waits(conn, schema=schema, settings=self._pool)
            extension = find_extension(conn, schema=schema)
            check_schema(conn, schema=schema)
            lease = (
                None
                if read_only
                else self._leases.acquire(
                    conninfo,
                    schema=schema,
                    statement_timeout_ms=statement_timeout_ms(self._pool),
                )
            )
            try:
                return self._open_validated(
                    conn,
                    _Request(schema, wanted, read_only, fresh),
                    conninfo,
                    extension,
                    lease,
                )
            except BaseException:
                if lease is not None:
                    lease.release()
                raise

    def _open_validated(
        self,
        conn: psycopg.Connection[Any],
        request: _Request,
        conninfo: Conninfo,
        extension: Extension,
        lease: LeaseHandle | None,
    ) -> PostgresVectorStore:
        """Read the state (unless fresh), check a writer's CREATE right, open the pool."""
        schema = request.schema
        state = (
            _State(request.wanted, 0, "absent")
            if request.fresh
            else _stored_state(conn, schema, extension, request.wanted)
        )
        if not request.read_only and state.layout == "absent":
            _require_create(conn, schema=schema)
        pool = open_pool(
            conninfo, schema=schema, extension=extension, settings=self._pool
        )
        logger.info(
            "postgres_vector_store_opened schema=%s generation=%d read_only=%s fresh=%s",
            schema,
            state.generation,
            request.read_only,
            request.fresh,
        )
        return PostgresVectorStore(
            schema=schema,
            identity=state.identity,
            read_only=request.read_only,
            connection=_Connection(pool, conninfo, extension, lease),
            reset_pending=request.fresh,
        )


class _Request(NamedTuple):
    """What an open was asked for."""

    schema: str
    wanted: EmbeddingIdentity
    read_only: bool
    fresh: bool


class _State(NamedTuple):
    """What an open found: the identity to serve, the generation, the layout."""

    identity: EmbeddingIdentity
    generation: int
    layout: Layout


def _checked_request(
    location: Path | str | None, identity: object
) -> tuple[str, EmbeddingIdentity]:
    """Validate an open's arguments; return the schema and the identity.

    Raises:
        ValueError: If *identity* is not an ``EmbeddingIdentity``.
        StoreNotConfiguredError: If *location* is ``None``.
    """
    if not isinstance(identity, EmbeddingIdentity):
        raise ValueError("A vector store opens against an EmbeddingIdentity.")
    if location is None:
        raise not_configured(
            "The PostgreSQL vector store needs a schema; none was given "
            "(set EMBEDDING_POSTGRES_SCHEMA).",
            schema="",
        )
    return str(location), identity


def _stored_state(
    conn: psycopg.Connection[Any],
    schema: str,
    extension: Extension,
    wanted: EmbeddingIdentity,
) -> _State:
    """Read what *schema* holds; an empty schema serves *wanted* at generation 0."""
    if check_layout(conn, schema=schema, extension=extension) == "absent":
        return _State(wanted, 0, "absent")
    meta = read_meta(conn, schema=schema, action="opened")
    if meta is None:  # dropped between the two reads
        return _State(wanted, 0, "absent")
    served = served_identity(
        meta.identity,
        wanted,
        backend=BACKEND_NAME,
        where=where(schema),
        location=schema,
    )
    return _State(served, meta.generation, "present")


def _bound_waits(
    conn: psycopg.Connection[Any], *, schema: str, settings: PoolSettings
) -> None:
    """Give the validation connection the pool's statement and lock timeouts.

    Without them, an open would wait forever behind a lock another session
    holds on a store table (a migration, a rebuild's first write).

    Raises:
        ValueError: If *settings* are out of range.
        StoreUnavailableError: If the settings cannot be applied.
    """
    timeouts = (
        sql.SQL("SET statement_timeout = {}").format(
            sql.Literal(statement_timeout_ms(settings))
        ),
        sql.SQL("SET lock_timeout = {}").format(sql.Literal(_LOCK_TIMEOUT_MS)),
    )
    try:
        for statement in timeouts:
            conn.execute(statement)
    except psycopg.Error as exc:
        failure = classify_error(exc, schema=schema, action="opened")
    else:
        return
    raise failure from None


def _require_create(conn: psycopg.Connection[Any], *, schema: str) -> None:
    """Refuse a writer that could not create the tables its first write needs.

    Raises:
        StoreUnavailableError: If the role lacks ``CREATE`` on *schema*
            (``reason=privilege``), or the catalogue cannot be read.
    """
    try:
        row = conn.execute(
            "SELECT has_schema_privilege(n.oid, 'CREATE') FROM pg_catalog.pg_namespace n"
            " WHERE n.nspname = %s",
            (schema,),
        ).fetchone()
    except psycopg.Error as exc:
        failure = classify_error(exc, schema=schema, action="opened")
    else:
        if row and row[0]:
            return
        failure = unavailable(
            f"This role has no CREATE on schema {schema}, which a writer needs to "
            "create the vector store tables.",
            reason="privilege",
            schema=schema,
        )
    raise failure from None


register_backend("vector", BACKEND_NAME, PostgresVectorBackend)
