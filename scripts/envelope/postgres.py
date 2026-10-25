"""PostgreSQL for the harness: the test database rules, plus service figures (#1895).

The database comes from where the test suite's does (``tests/fixtures/
postgres.py``): ``MVM_TEST_POSTGRES_DSN``, or else a throwaway
``pgvector/pgvector`` container the harness starts and removes. Nothing else
is ever reached. The connection string is held as a ``SecretDsn``, handed to
worker processes in the environment (``worker.DSN_ENV``), never in argv or JSON,
and every output labels the database by its source.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.postgres import (
    SecretDsn,
    _connect,
    _docker,
    _drop_schema,
    _published_port,
    _remove,
    _wait_until_ready,
    check_vector_extension,
    docker_available,
    resolve_source,
    start_container,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

SCHEMA_PREFIX = "mvm_envelope_"
_READY_TIMEOUT_S = 90.0
_UNITS = {
    "B": 1,
    "kB": 1000,
    "KiB": 1 << 10,
    "MB": 1000**2,
    "MiB": 1 << 20,
    "GB": 1000**3,
    "GiB": 1 << 30,
}


@dataclasses.dataclass
class Database:
    """The run's PostgreSQL.

    Attributes:
        dsn: Its connection string, left out of the ``repr``.
        source: Where it came from, safe to print.
        container: The throwaway container's id; ``None`` for a given DSN.
    """

    dsn: str = dataclasses.field(repr=False)
    source: str
    container: str | None = None


@contextlib.contextmanager
def database(
    environ: Mapping[str, str],
    *,
    docker_available: Callable[[], bool] = docker_available,
) -> Iterator[Database]:
    """Yield the run's PostgreSQL; remove a container it started on exit.

    Args:
        environ: Where ``MVM_TEST_POSTGRES_DSN`` is read from.
        docker_available: Asked only when no DSN is set.

    Yields:
        The database, with the ``vector`` extension present.

    Raises:
        SystemExit: When neither a DSN nor Docker is available, or the
            database does not start or lacks the extension.
    """
    source = resolve_source(environ, docker_available=docker_available)
    if source.kind == "unavailable":
        raise SystemExit(source.detail)
    if source.kind == "dsn":
        given = SecretDsn(source.detail)
        _fixture_call(check_vector_extension, given, create=False)
        yield Database(given, "MVM_TEST_POSTGRES_DSN (hidden DSN)")
        return
    container, dsn = _fixture_call(start_container, source.detail)
    try:
        _fixture_call(check_vector_extension, dsn, create=True)
        yield Database(dsn, f"throwaway container {source.detail}", container)
    finally:
        _remove(container)


@contextlib.contextmanager
def schema(db: Database) -> Iterator[str]:
    """Create a schema of the harness's own; drop it on exit.

    The drop reads ``db.dsn`` then, so it follows a restarted container.

    Args:
        db: The run's PostgreSQL.

    Yields:
        The schema's name: ``mvm_envelope_`` and twelve hex digits.
    """
    from psycopg import sql

    name = f"{SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}"
    with _connect(db.dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        _drop_schema(db.dsn, name)


def _fixture_call(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a test-fixture helper, turning its ``pytest.fail`` into an exit.

    ``pytest.fail`` raises an exception outside ``Exception``, which nothing
    in a script would otherwise catch.
    """
    try:
        return call(*args, **kwargs)
    except pytest.fail.Exception as failure:
        raise SystemExit(str(failure)) from None


def with_port(dsn: str, port: str) -> str:
    """Return *dsn* with its ``port=`` set to *port*.

    Args:
        dsn: A key-value libpq connection string.
        port: The new port.

    Returns:
        The connection string, otherwise unchanged.
    """
    return re.sub(r"\bport=\d+", f"port={port}", dsn)


def restart(db: Database) -> None:
    """Restart the throwaway container, dropping the service's buffers.

    Docker may publish it on another host port; ``db.dsn`` follows.

    Args:
        db: A database the harness started.

    Raises:
        SystemExit: For a database the harness did not start, or one that
            does not come back in time.
    """
    if db.container is None:
        raise SystemExit("only a throwaway PostgreSQL is restarted")
    _docker("restart", db.container, timeout=120)
    port = _published_port(db.container)
    if port is not None:
        db.dsn = SecretDsn(with_port(db.dsn, port))
        deadline = time.monotonic() + _READY_TIMEOUT_S
        if _wait_until_ready(db.dsn, deadline, db.container) == "ready":
            return
    raise SystemExit("the throwaway PostgreSQL did not come back after a restart")


def parse_mem_usage(text: str) -> int | None:
    """Read the used half of ``docker stats``'s ``MemUsage``.

    Args:
        text: Such as ``"123.5MiB / 125.4GiB"``.

    Returns:
        The bytes used, or ``None`` when Docker reports none.
    """
    match = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)\s*/", text)
    if match is None or match.group(2) not in _UNITS:
        return None
    return int(float(match.group(1)) * _UNITS[match.group(2)])


def service_memory(container: str) -> int | None:
    """Return the container's memory as ``docker stats`` counts it.

    On cgroup v2 that is ``memory.current`` less ``inactive_file``, so the
    inactive page cache is left out and the active part is not.

    Args:
        container: The container's id.

    Returns:
        The bytes used, or ``None`` when ``docker stats`` cannot say.
    """
    stats = _docker("stats", "--no-stream", "--format", "{{json .}}", container)
    if stats.returncode or not stats.stdout.strip():
        return None
    return parse_mem_usage(json.loads(stats.stdout)["MemUsage"])


def sizes(dsn: str, schema: str) -> dict[str, int]:
    """Return the database's size and the vectors' schema's.

    Args:
        dsn: The database.
        schema: The schema the store writes in.

    Returns:
        ``database_bytes`` and ``schema_bytes`` (tables, indexes and TOAST).
    """
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT pg_database_size(current_database()), "
            "COALESCE(SUM(pg_total_relation_size(c.oid)), 0) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind IN ('r', 'm')",
            (schema,),
        ).fetchone()
    if row is None:  # an aggregate always returns a row
        raise RuntimeError("the size query returned no row")
    return {"database_bytes": int(row[0]), "schema_bytes": int(row[1])}


def versions(dsn: str) -> dict[str, str]:
    """Return the server's and the ``vector`` extension's versions.

    Args:
        dsn: The database.

    Returns:
        ``server_version`` and ``pgvector_version``.
    """
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT current_setting('server_version'), "
            "(SELECT extversion FROM pg_extension WHERE extname = 'vector')"
        ).fetchone()
    if row is None:
        raise RuntimeError("the version query returned no row")
    return {"server_version": str(row[0]), "pgvector_version": str(row[1])}


def wal_position(dsn: str) -> int:
    """Return the cluster's current WAL position, in bytes from its origin.

    The difference of two positions is the WAL every session wrote in
    between, so it is cluster-wide.

    Args:
        dsn: The database.

    Returns:
        The position.
    """
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0')::bigint"
        ).fetchone()
    if row is None:
        raise RuntimeError("the WAL position query returned no row")
    return int(row[0])
