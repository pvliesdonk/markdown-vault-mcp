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
from typing import Literal

import psycopg

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
