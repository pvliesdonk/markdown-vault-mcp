"""Connections to PostgreSQL for the vector store, and how they fail (#1504).

The store's backend carries a DSN; this module turns it into checked
connections and a pool, and every failure into one of the contract's open
outcomes. None of them is *corrupt* or *incompatible*: only the store's own
checks of rows it read can say that, so an outage never becomes a rebuild.
Connect-time failures carry no SQLSTATE in psycopg, so their ``reason``
comes from psycopg's documented attributes alone; libpq's message is kept
in the error and never matched. The DSN never appears in an error, a
location or a log line. What this relies on in PostgreSQL, libpq and
psycopg is in ``docs/design/reference/postgresql-pgvector.md``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, NamedTuple

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from markdown_vault_mcp.exceptions import StoreNotConfiguredError, StoreUnavailableError

logger = logging.getLogger(__name__)

BACKEND_NAME = "postgres"
FORMAT_VERSION = 1
MIN_PGVECTOR: tuple[int, int, int] = (0, 7, 0)

UnavailableReason = Literal[
    "connect",
    "timeout",
    "no_password",
    "refused",
    "privilege",
    "pool",
    "lease",
    "commit_unknown",
    "error",
]

_CONNECT_CLASSES = frozenset({"08", "53", "57"})
_DEFAULTS = (
    ("connect_timeout", "10"),
    ("keepalives", "1"),
    ("keepalives_idle", "30"),
    ("keepalives_interval", "10"),
    ("keepalives_count", "3"),
)


def unavailable(
    message: str, *, reason: UnavailableReason, schema: str
) -> StoreUnavailableError:
    """Build the *unavailable* outcome for *schema*, logging it once.

    Args:
        message: What failed, without the DSN.
        reason: The coarse tag operators and tests read.
        schema: The vault schema, which is the outcome's location.

    Returns:
        The error, its message ending ``(reason=<reason>)``.
    """
    logger.warning(
        "postgres_vector_store_unavailable schema=%s reason=%s", schema, reason
    )
    return StoreUnavailableError(
        f"{message} (reason={reason})", backend=BACKEND_NAME, location=schema
    )


def not_configured(message: str, *, schema: str) -> StoreNotConfiguredError:
    """Build the *not configured* outcome for *schema*: an operator must act.

    Args:
        message: What to do, without the DSN.
        schema: The vault schema, which is the outcome's location.

    Returns:
        The error.
    """
    return StoreNotConfiguredError(message, backend=BACKEND_NAME, location=schema)


def classify_connect_failure(
    exc: psycopg.OperationalError, *, schema: str
) -> StoreUnavailableError:
    """Return the outcome of a failed connection attempt.

    The order matters: libpq sets ``used_password`` also when a password was
    demanded and none was available, so ``needs_password`` is tested first.

    Args:
        exc: What ``psycopg.connect`` raised.
        schema: The vault schema.

    Returns:
        *Unavailable*, with ``timeout``, ``no_password``, ``refused`` or
        ``connect``.
    """
    message = f"Cannot connect to PostgreSQL for schema {schema}: {exc}"
    if isinstance(exc, psycopg.errors.ConnectionTimeout):
        return unavailable(message, reason="timeout", schema=schema)
    pgconn = exc.pgconn
    if pgconn is not None and pgconn.needs_password:
        return unavailable(message, reason="no_password", schema=schema)
    if pgconn is not None and pgconn.used_password:
        return unavailable(message, reason="refused", schema=schema)
    return unavailable(message, reason="connect", schema=schema)


def classify_error(
    exc: psycopg.Error, *, schema: str, action: str
) -> StoreUnavailableError:
    """Return the outcome of an error raised after connecting.

    Args:
        exc: What psycopg raised.
        schema: The vault schema.
        action: What could not be done to the store, as a past participle
            ("opened", "read", "written").

    Returns:
        *Unavailable* by SQLSTATE class; ``error`` for a class this does
        not name, so no PostgreSQL error ever reads as corrupt.
    """
    state = exc.sqlstate
    message = f"Vector store in schema {schema} cannot be {action}: {exc}"
    if state is None or state[:2] in _CONNECT_CLASSES:
        return unavailable(message, reason="connect", schema=schema)
    if state[:2] == "28":
        return unavailable(message, reason="refused", schema=schema)
    if state == "42501":
        return unavailable(message, reason="privilege", schema=schema)
    return unavailable(f"{message} [SQLSTATE {state}]", reason="error", schema=schema)


def prepare_conninfo(dsn: str, *, schema: str) -> str:
    """Return *dsn* with the store's connection defaults where it sets none.

    Adds ``connect_timeout`` (libpq otherwise waits indefinitely), TCP
    keepalives and ``application_name=markdown-vault-mcp:<schema>``; every
    value the DSN sets is kept.

    Args:
        dsn: The operator's libpq URI or key/value connection string.
        schema: The vault schema.

    Returns:
        The connection string to connect with.

    Raises:
        StoreNotConfiguredError: If *dsn* is not a valid connection string;
            the message gives psycopg's reason, never the DSN.
    """
    try:
        params = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError as exc:
        failure = not_configured(
            f"The PostgreSQL DSN for schema {schema} is not a valid connection "
            f"string: {exc}",
            schema=schema,
        )
    else:
        for key, value in _DEFAULTS:
            params.setdefault(key, value)
        params.setdefault("application_name", f"markdown-vault-mcp:{schema}")
        return make_conninfo("", **params)
    raise failure from None


def connect(conninfo: str, *, schema: str) -> psycopg.Connection[tuple[Any, ...]]:
    """Open one autocommit connection, failing with the classified outcome.

    Args:
        conninfo: A connection string from :func:`prepare_conninfo`.
        schema: The vault schema.

    Returns:
        The open connection.

    Raises:
        StoreUnavailableError: If it cannot connect; psycopg's frames, which
            hold *conninfo*, are not chained to it.
    """
    try:
        return psycopg.connect(conninfo, autocommit=True)
    except psycopg.OperationalError as exc:
        failure = classify_connect_failure(exc, schema=schema)
    raise failure from None


class Extension(NamedTuple):
    """The ``vector`` extension as installed in the database.

    Attributes:
        schema: The schema holding its type and operators.
        version: Its version as three integers.
    """

    schema: str
    version: tuple[int, int, int]


def _version(text: str) -> tuple[int, int, int]:
    """Parse ``0.8.0`` (or ``0.8``) into three integers; missing parts are 0."""
    parts = [int(p) for p in text.split(".")[:3] if p.isdigit()]
    padded = [*parts, 0, 0, 0][:3]
    return padded[0], padded[1], padded[2]


def _dotted(version: tuple[int, int, int]) -> str:
    """Render *version* as ``major.minor.patch``."""
    return ".".join(str(part) for part in version)


def find_extension(conn: psycopg.Connection[Any], *, schema: str) -> Extension:
    """Return the ``vector`` extension, refusing a missing or too-old one.

    Args:
        conn: A connection to the vault's database.
        schema: The vault schema, for the outcome's location.

    Returns:
        The extension's schema and version.

    Raises:
        StoreNotConfiguredError: If it is not installed, or older than
            ``MIN_PGVECTOR``; installing needs a superuser, so the message
            gives the SQL to run.
        StoreUnavailableError: If the catalogue cannot be read.
    """
    try:
        row = conn.execute(
            "SELECT n.nspname, e.extversion FROM pg_catalog.pg_extension e"
            " JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace"
            " WHERE e.extname = 'vector'"
        ).fetchone()
    except psycopg.Error as exc:
        failure = classify_error(exc, schema=schema, action="opened")
    else:
        return _checked_extension(row, schema=schema)
    raise failure from None


def _checked_extension(row: tuple[Any, ...] | None, *, schema: str) -> Extension:
    """Turn the catalogue row into an :class:`Extension`, refusing none or too old."""
    if row is None:
        raise not_configured(
            "The vector extension is not installed in this database; a superuser "
            "runs: CREATE EXTENSION vector;",
            schema=schema,
        )
    found = Extension(str(row[0]), _version(str(row[1])))
    if found.version < MIN_PGVECTOR:
        raise not_configured(
            f"The vector extension is {_dotted(found.version)} installed; this "
            f"version needs {_dotted(MIN_PGVECTOR)} or later: a superuser runs "
            "ALTER EXTENSION vector UPDATE;",
            schema=schema,
        )
    return found


def check_schema(conn: psycopg.Connection[Any], *, schema: str) -> None:
    """Refuse a vault schema that does not exist or that this role cannot use.

    PostgreSQL silently skips a missing schema in ``search_path``, so it is
    looked up in ``pg_namespace``; ``USAGE`` is checked because without it
    the store's tables would be invisible and the schema would read as empty.

    Args:
        conn: A connection to the vault's database.
        schema: The vault schema.

    Raises:
        StoreNotConfiguredError: If the schema does not exist; the message
            gives the ``CREATE SCHEMA`` to run.
        StoreUnavailableError: If the role lacks ``USAGE`` on it
            (``reason=privilege``), or the catalogue cannot be read.
    """
    try:
        role_row = conn.execute("SELECT current_user").fetchone()
        row = conn.execute(
            "SELECT has_schema_privilege(n.oid, 'USAGE')"
            " FROM pg_catalog.pg_namespace n WHERE n.nspname = %s",
            (schema,),
        ).fetchone()
    except psycopg.Error as exc:
        failure = classify_error(exc, schema=schema, action="opened")
    else:
        role = str(role_row[0]) if role_row else "<role>"
        _refuse_unusable_schema(conn, row, schema=schema, role=role)
        return
    raise failure from None


def _refuse_unusable_schema(
    conn: psycopg.Connection[Any],
    row: tuple[Any, ...] | None,
    *,
    schema: str,
    role: str,
) -> None:
    """Raise when the ``pg_namespace`` lookup found no schema, or no ``USAGE``."""
    if row is None:
        statement = (
            sql.SQL("CREATE SCHEMA {} AUTHORIZATION {};")
            .format(sql.Identifier(schema), sql.Identifier(role))
            .as_string(conn)
        )
        raise not_configured(
            f"Schema {schema} does not exist; the operator runs: {statement}",
            schema=schema,
        )
    if not row[0]:
        raise unavailable(
            f"Role {role} has no USAGE on schema {schema}.",
            reason="privilege",
            schema=schema,
        )
