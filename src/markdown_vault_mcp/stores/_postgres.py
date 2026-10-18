"""Connections to PostgreSQL for the vector store, and how they fail (#1504).

The store's backend carries a DSN; this module turns it into checked
connections and a pool, and every failure into one of the contract's open
outcomes. None of them is *corrupt* or *incompatible*: only the store's own
checks of rows it read can say that, so an outage never becomes a rebuild.
Connect-time failures carry no SQLSTATE in psycopg, so their ``reason``
comes from psycopg's documented attributes alone. libpq's message is kept,
never matched, and redacted: a quoted token in it that is not exactly the
parsed host, port, database, user or a connection option name is hidden,
because libpq quotes what it fails on, which can be a piece of a mistyped
password. The DSN never appears in an error, a location or a log line, and
a :class:`Conninfo` hides itself from a traceback's argument display. What this relies on in PostgreSQL, libpq and
psycopg is in ``docs/design/reference/postgresql-pgvector.md``.
"""

from __future__ import annotations

import logging
import math
import os
import re
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool, PoolTimeout

from markdown_vault_mcp.exceptions import StoreNotConfiguredError, StoreUnavailableError

if TYPE_CHECKING:
    from collections.abc import Iterable

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
_TIMEOUT_STATES = frozenset({"57014", "55P03"})
_QUOTED = re.compile(r"""(["'])(.*?)\1""")
_REDACTED = "<redacted>"
_SAFE_KEYS = ("host", "hostaddr", "port", "dbname", "user")
# (parameter, default, libpq environment variable that also sets it)
_DEFAULTS = (
    ("connect_timeout", "10", "PGCONNECT_TIMEOUT"),
    ("keepalives", "1", None),
    ("keepalives_idle", "30", None),
    ("keepalives_interval", "10", None),
    ("keepalives_count", "3", None),
)


class Conninfo(str):
    """A prepared connection string; its ``repr`` hides it.

    pytest and other traceback renderers show each frame's arguments by
    ``repr``, so the password a connection string may carry never prints.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        """Hide the connection string."""
        return "Conninfo(<hidden>)"


def _option_names() -> frozenset[str]:
    """The connection option names libpq knows; safe to show in a message."""
    return frozenset(opt.keyword.decode() for opt in psycopg.pq.Conninfo.get_defaults())


def redact(text: str, *, safe: Iterable[str]) -> str:
    """Hide every quoted token in *text* that is not a known-safe value.

    Args:
        text: A message from libpq.
        safe: Values that may appear (the parsed host, port, database, user);
            connection option names are always safe.

    Returns:
        *text* with each other quoted token replaced by ``<redacted>``, and
        trailing whitespace removed.
    """
    allowed = frozenset(safe) | _option_names()

    def hide(match: re.Match[str]) -> str:
        if match.group(2) in allowed:
            return match.group(0)
        return f"{match.group(1)}{_REDACTED}{match.group(1)}"

    return _QUOTED.sub(hide, text).rstrip()


def _safe_values(conninfo: str) -> frozenset[str]:
    """The parsed host, port, database and user of *conninfo*."""
    params = conninfo_to_dict(conninfo)
    values: set[str] = set()
    for key in _SAFE_KEYS:
        raw = params.get(key)
        if raw:
            values.update(part for part in str(raw).split(",") if part)
    return frozenset(values)


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
        "postgres_vector_store_unavailable schema=%s reason=%s detail=%s",
        schema,
        reason,
        message,
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
    exc: psycopg.OperationalError, *, schema: str, safe: Iterable[str] = ()
) -> StoreUnavailableError:
    """Return the outcome of a failed connection attempt.

    The order matters: libpq sets ``used_password`` also when a password was
    demanded and none was available, so ``needs_password`` is tested first.

    Args:
        exc: What ``psycopg.connect`` raised.
        schema: The vault schema.
        safe: Values libpq's message may show unredacted (see :func:`redact`).

    Returns:
        *Unavailable*, with ``timeout``, ``no_password``, ``refused`` or
        ``connect``.
    """
    detail = redact(str(exc), safe=safe)
    message = f"Cannot connect to PostgreSQL for schema {schema}: {detail}"
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
        *Unavailable* by SQLSTATE class; ``timeout`` for a statement or lock
        timeout; ``error`` for a class this does not name, so no PostgreSQL
        error ever reads as corrupt.
    """
    state = exc.sqlstate
    message = f"Vector store in schema {schema} cannot be {action}: {str(exc).rstrip()}"
    if state in _TIMEOUT_STATES:
        return unavailable(message, reason="timeout", schema=schema)
    if state is None or state[:2] in _CONNECT_CLASSES:
        return unavailable(message, reason="connect", schema=schema)
    if state.startswith("28"):
        return unavailable(message, reason="refused", schema=schema)
    if state == "42501":
        return unavailable(message, reason="privilege", schema=schema)
    return unavailable(f"{message} [SQLSTATE {state}]", reason="error", schema=schema)


def prepare_conninfo(dsn: str, *, schema: str) -> Conninfo:
    """Return *dsn* with the store's connection defaults where it sets none.

    Adds ``connect_timeout`` (libpq otherwise waits indefinitely), TCP
    keepalives and ``application_name=markdown-vault-mcp:<schema>`` where
    neither the DSN nor libpq's environment (``PGCONNECT_TIMEOUT``,
    ``PGAPPNAME``) sets them; an empty value counts as unset.

    Args:
        dsn: The operator's libpq URI or key/value connection string.
        schema: The vault schema.

    Returns:
        The connection string to connect with.

    Raises:
        StoreNotConfiguredError: If *dsn* is not a valid connection string,
            or names a host holding ``@`` or whitespace (an unencoded
            character of a password, usually); the message never quotes it.
    """
    try:
        params = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        params = None
    if params is None:
        raise not_configured(
            f"The PostgreSQL DSN for schema {schema} is not a valid connection "
            "string; check its quoting and percent-encoding.",
            schema=schema,
        )
    _refuse_suspicious_host(params, schema=schema)
    for key, value, env in (
        *_DEFAULTS,
        ("application_name", f"markdown-vault-mcp:{schema}", "PGAPPNAME"),
    ):
        if not params.get(key) and not (env and os.environ.get(env)):
            params[key] = value
    return Conninfo(make_conninfo("", **params))


def _refuse_suspicious_host(params: dict[str, Any], *, schema: str) -> None:
    """Refuse a host value that holds ``@`` or whitespace, without echoing it."""
    host = str(params.get("host") or "")
    if "@" in host or any(c.isspace() for c in host):
        raise not_configured(
            f"The PostgreSQL DSN for schema {schema} names a host containing '@' "
            "or whitespace, which usually means a password character that is not "
            "percent-encoded; write '@' as %40 (and ':' as %3A, '/' as %2F) in "
            "the password.",
            schema=schema,
        )


def connect(conninfo: Conninfo, *, schema: str) -> psycopg.Connection[tuple[Any, ...]]:
    """Open one autocommit connection, failing with the classified outcome.

    Args:
        conninfo: A connection string from :func:`prepare_conninfo`.
        schema: The vault schema.

    Returns:
        The open connection.

    Raises:
        StoreUnavailableError: If it cannot connect.
        StoreNotConfiguredError: If libpq refuses a setting (a malformed
            ``connect_timeout``, say).

    Neither error chains psycopg's frames, which hold *conninfo*, and both
    carry libpq's message redacted.
    """
    failure: StoreUnavailableError | StoreNotConfiguredError
    try:
        return psycopg.connect(conninfo, autocommit=True)
    except psycopg.OperationalError as exc:
        failure = classify_connect_failure(
            exc, schema=schema, safe=_safe_values(conninfo)
        )
    except psycopg.Error as exc:
        detail = redact(str(exc), safe=_safe_values(conninfo))
        failure = not_configured(
            f"The PostgreSQL connection settings for schema {schema} are "
            f"refused: {detail}",
            schema=schema,
        )
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


class PoolSettings(NamedTuple):
    """How the store's pool is sized and timed (spec §6.3).

    Attributes:
        pool_size: The most connections the pool holds; at least 1.
        statement_timeout_s: The ``statement_timeout`` each connection sets;
            0 sets none, and a positive value is at least one millisecond.
        wait_s: How long opening waits for the first connection; closing a
            pool that never connected may add up to ``connect_timeout``.
        get_timeout_s: How long a caller waits for a free connection.
    """

    pool_size: int = 4
    statement_timeout_s: float = 30.0
    wait_s: float = 30.0
    get_timeout_s: float = 30.0


def statement_timeout_ms(settings: PoolSettings) -> int:
    """Validate *settings*; return the statement timeout in milliseconds.

    Raises:
        ValueError: If a value is out of range or not finite.
    """
    if settings.pool_size < 1:
        raise ValueError(f"pool_size must be at least 1; got {settings.pool_size}.")
    timeout = settings.statement_timeout_s
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError(
            f"statement_timeout_s must be finite and at least 0; got {timeout}."
        )
    for name in ("wait_s", "get_timeout_s"):
        value = getattr(settings, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and above 0; got {value}.")
    return 0 if timeout == 0 else max(1, round(timeout * 1000))


def open_pool(
    conninfo: Conninfo, *, schema: str, extension: Extension, settings: PoolSettings
) -> ConnectionPool[psycopg.Connection[tuple[Any, ...]]]:
    """Open the store's pool, every connection set up for the vault schema.

    Each connection is autocommit (the store opens its own transactions),
    searches the vault schema then the extension's, carries the statement
    timeout, and knows the ``vector`` type. A connection is checked before it
    is handed out, so a PostgreSQL restart does not fail the next caller.

    Args:
        conninfo: A connection string from :func:`prepare_conninfo`.
        schema: The vault schema, put first on ``search_path``.
        extension: Where the ``vector`` extension lives.
        settings: The pool's size and timeouts.

    Returns:
        The open pool.

    Raises:
        ValueError: If a setting is out of range (see :class:`PoolSettings`).
        StoreUnavailableError: If the first connection is not ready within
            ``settings.wait_s`` (``reason=pool``); the pool is closed first.
    """
    timeout_ms = statement_timeout_ms(settings)
    path = sql.SQL("SET search_path TO {}, {}").format(
        sql.Identifier(schema), sql.Identifier(extension.schema)
    )
    timeout = sql.SQL("SET statement_timeout = {}").format(sql.Literal(timeout_ms))

    def configure(conn: psycopg.Connection[Any]) -> None:
        conn.autocommit = True
        conn.execute(path)
        # Before the timeout, so a short one cannot cancel the type lookup.
        register_vector(conn)
        conn.execute(timeout)

    pool: ConnectionPool[psycopg.Connection[tuple[Any, ...]]] = ConnectionPool(
        conninfo,
        min_size=1,
        max_size=settings.pool_size,
        timeout=settings.get_timeout_s,
        open=False,
        configure=configure,
        check=ConnectionPool.check_connection,
        name=f"mvm-vectors-{schema}",
    )
    try:
        pool.open(wait=True, timeout=settings.wait_s)
    except PoolTimeout:
        pool.close()
        failure = unavailable(
            f"No PostgreSQL connection for schema {schema} became ready within "
            f"{settings.wait_s:g}s.",
            reason="pool",
            schema=schema,
        )
    else:
        return pool
    raise failure from None
