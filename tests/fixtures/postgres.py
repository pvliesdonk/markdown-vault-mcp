"""PostgreSQL for tests: a database from the environment, or a throwaway container (#1504).

``MVM_TEST_POSTGRES_DSN`` names an existing database with the ``vector``
extension installed; the harness then creates nothing but its own schemas.
Without it, a ``pgvector/pgvector`` container is started through the Docker
CLI for the session (``MVM_TEST_POSTGRES_IMAGE`` overrides the image) and
removed at its end. With neither, the PostgreSQL tests skip with the reason,
except under CI (``CI=true``), where they fail so coverage cannot silently
drop. Each test gets a schema of its own (``mvm_test_`` and a random suffix),
dropped afterwards. psycopg is imported lazily, so a checkout without the
``postgres`` extra still collects every other test.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import secrets
import shutil
import subprocess
import time
import uuid
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    import psycopg

DSN_ENV = "MVM_TEST_POSTGRES_DSN"
IMAGE_ENV = "MVM_TEST_POSTGRES_IMAGE"
DEFAULT_IMAGE = "pgvector/pgvector:0.8.1-pg17"
SCHEMA_PREFIX = "mvm_test_"
_READY_TIMEOUT_S = 90.0
_DOCKER_TIMEOUT_S = 600.0
_DROP_LOCK_TIMEOUT_MS = 10_000
_HIDDEN = "<hidden>"


class SecretDsn(str):
    """A connection string whose ``repr`` hides it.

    pytest shows each traceback frame's arguments by ``repr``; a DSN passed as
    this type never prints, whichever harness function it reaches.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        """Hide the connection string."""
        return f"'{_HIDDEN}'"


class Source(NamedTuple):
    """Where the session's PostgreSQL comes from.

    Attributes:
        kind: ``"dsn"``, ``"docker"`` or ``"unavailable"``.
        detail: The DSN, the image, or why neither is possible.
    """

    kind: Literal["dsn", "docker", "unavailable"]
    detail: str


class PostgresServer(NamedTuple):
    """The session's database.

    Its ``repr`` hides the DSN, which may carry a real password, so a failing
    test that shows its arguments does not print it.

    Attributes:
        dsn: A libpq connection string for it.
        superuser: Whether the DSN's role is a superuser.
        throwaway: Whether the harness started it; tests that create
            databases run only against a throwaway server.
    """

    dsn: str
    superuser: bool
    throwaway: bool

    def __repr__(self) -> str:
        """Describe the server without its DSN."""
        return (
            f"PostgresServer(dsn={_HIDDEN}, superuser={self.superuser}, "
            f"throwaway={self.throwaway})"
        )


class PostgresSchema(NamedTuple):
    """A schema created for one test.

    Attributes:
        dsn: The session database's connection string.
        name: The schema's name.
    """

    dsn: str
    name: str

    def __repr__(self) -> str:
        """Describe the schema without the DSN."""
        return f"PostgresSchema(dsn={_HIDDEN}, name={self.name!r})"


def _driver_installed() -> bool:
    """Whether psycopg can be imported."""
    return importlib.util.find_spec("psycopg") is not None


def resolve_source(
    environ: Mapping[str, str],
    *,
    docker_available: Callable[[], bool],
    driver_available: bool | None = None,
) -> Source:
    """Decide where the session's PostgreSQL comes from.

    Args:
        environ: The environment to read ``DSN_ENV`` and ``IMAGE_ENV`` from.
        docker_available: Asked only when no DSN is set.
        driver_available: Whether psycopg is installed; ``None`` checks.

    Returns:
        The DSN when one is set, else Docker with the image, else why not.
    """
    if driver_available is None:
        driver_available = _driver_installed()
    if not driver_available:
        return Source(
            "unavailable",
            "psycopg is not installed: install the 'postgres' extra "
            "(uv sync --extra postgres).",
        )
    dsn = environ.get(DSN_ENV, "").strip()
    if dsn:
        return Source("dsn", SecretDsn(dsn))
    if docker_available():
        return Source("docker", environ.get(IMAGE_ENV, "").strip() or DEFAULT_IMAGE)
    return Source(
        "unavailable",
        f"no PostgreSQL for the tests: set {DSN_ENV} to a database with the "
        "vector extension, or make the Docker CLI and daemon available.",
    )


def report_unavailable(reason: str, environ: Mapping[str, str]) -> None:
    """Fail under CI, skip elsewhere, with *reason*.

    Raises:
        pytest.fail.Exception: When ``CI`` is ``true`` (any case).
        pytest.skip.Exception: Otherwise.
    """
    if environ.get("CI", "").strip().lower() == "true":
        pytest.fail(reason)
    pytest.skip(reason)


def docker_available() -> bool:
    """Whether the Docker CLI is installed and its daemon answers."""
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        probe = subprocess.run(
            [docker, "info"], capture_output=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def _docker(
    *args: str, timeout: float = _DOCKER_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    """Run ``docker *args``, capturing text output, without raising on failure."""
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def _remove(container: str) -> None:
    """Remove *container*, ignoring one that is already gone."""
    _docker("rm", "-f", container, timeout=60)


def _published_port(container: str) -> str | None:
    """Return the host port mapped to the container's 5432, or ``None``."""
    mapping = _docker("port", container, "5432/tcp", timeout=30)
    if mapping.returncode or not mapping.stdout.strip():
        return None
    return mapping.stdout.split()[0].rsplit(":", 1)[1]


def _connect(dsn: str, *, autocommit: bool = False) -> psycopg.Connection[Any]:
    """Connect to *dsn* without letting psycopg's frames, which hold it, reach a report.

    Raises:
        ConnectionError: If the connection fails, with libpq's message and
            no chained psycopg traceback.
    """
    import psycopg

    try:
        return psycopg.connect(dsn, autocommit=autocommit)
    except psycopg.OperationalError as exc:
        message = f"cannot connect to the test database: {exc}"
    raise ConnectionError(message) from None


def _running(container: str) -> bool:
    """Whether *container* is still running."""
    state = _docker("inspect", "-f", "{{.State.Running}}", container, timeout=30)
    return state.returncode == 0 and state.stdout.strip() == "true"


def _wait_until_ready(dsn: str, deadline: float, container: str) -> str:
    """Connect to *dsn* until it answers, *container* exits or *deadline* passes.

    Returns:
        ``"ready"``, ``"exited"`` or ``"timeout"``.
    """
    import psycopg

    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
        except psycopg.OperationalError:
            if not _running(container):
                return "exited"
            time.sleep(0.5)
        else:
            return "ready"
    return "timeout"


def start_container(
    image: str,
    *,
    command: tuple[str, ...] = (),
    ready_timeout_s: float = _READY_TIMEOUT_S,
) -> tuple[str, str]:
    """Start *image* on a random local port and wait until it accepts connections.

    Args:
        image: The image to run.
        command: Overrides the image's command; empty runs its own.
        ready_timeout_s: How long to wait for a connection.

    Returns:
        The container id and a DSN for its ``postgres`` database.

    Raises:
        pytest.fail.Exception: If it does not start or answer in time; the
            container is removed first and its last log lines are reported.
    """
    password = secrets.token_hex(16)
    name = f"mvm-test-pg-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    # No --rm: a container that exits must keep its logs until they are read.
    run = _docker(
        "run",
        "-d",
        "--name",
        name,
        "-p",
        "127.0.0.1::5432",
        "-e",
        f"POSTGRES_PASSWORD={password}",
        image,
        *command,
    )
    if run.returncode:
        _remove(name)
        pytest.fail(f"docker run {image} failed: {run.stderr.strip()}")
    container = run.stdout.strip()
    try:
        return container, _await_container(container, image, password, ready_timeout_s)
    except BaseException:
        _remove(container)
        raise


def _await_container(
    container: str, image: str, password: str, ready_timeout_s: float
) -> str:
    """Wait for *container* to accept connections; return its DSN.

    Raises:
        pytest.fail.Exception: If it exits or does not answer in time, with
            its last log lines.
    """
    port = _published_port(container)
    dsn = SecretDsn(
        f"host=127.0.0.1 port={port} user=postgres password={password} dbname=postgres"
    )
    outcome = (
        "exited"
        if port is None
        else _wait_until_ready(dsn, time.monotonic() + ready_timeout_s, container)
    )
    if outcome == "ready":
        return dsn
    logs = _docker("logs", "--tail", "30", container, timeout=30)
    what = (
        "exited before accepting connections"
        if outcome == "exited"
        else f"did not accept connections within {ready_timeout_s:.0f}s"
    )
    pytest.fail(f"PostgreSQL in {image} {what}:\n{logs.stdout}{logs.stderr}")


def check_vector_extension(dsn: str, *, create: bool) -> None:
    """Make sure the ``vector`` extension exists in *dsn*'s database.

    Args:
        dsn: The database.
        create: Create it when missing (a throwaway container); otherwise a
            missing extension fails the session with the SQL to run.

    Raises:
        pytest.fail.Exception: If it is missing and *create* is false.
    """
    with _connect(dsn, autocommit=True) as conn:
        if create:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            return
        found = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    if found is None:
        pytest.fail(
            f"The database {DSN_ENV} names has no vector extension; run "
            "CREATE EXTENSION vector; in it as a superuser."
        )


def _is_superuser(dsn: str) -> bool:
    """Whether *dsn*'s role is a superuser."""
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    return bool(row and row[0])


@contextlib.contextmanager
def scratch_schema(dsn: str) -> Iterator[str]:
    """Create a schema of its own for one test; drop it on exit, however it ends.

    Yields:
        The schema's name: ``SCHEMA_PREFIX`` and twelve hex digits.
    """
    from psycopg import sql

    name = f"{SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}"
    with _connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        _drop_schema(dsn, name)


def _drop_schema(dsn: str, name: str) -> None:
    """Drop *name*, giving up rather than waiting forever on another session's lock.

    Raises:
        psycopg.errors.LockNotAvailable: If a lock held elsewhere (a
            connection left open in a transaction) outlasts
            ``_DROP_LOCK_TIMEOUT_MS``; the schema is then left in place.
    """
    from psycopg import sql

    with _connect(dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("SET lock_timeout = {}").format(sql.Literal(_DROP_LOCK_TIMEOUT_MS))
        )
        conn.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name))
        )


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[PostgresServer]:
    """The session's PostgreSQL with the ``vector`` extension (see the module docstring)."""
    source = resolve_source(os.environ, docker_available=docker_available)
    if source.kind == "unavailable":
        report_unavailable(source.detail, os.environ)
    if source.kind == "dsn":
        given = SecretDsn(source.detail)
        check_vector_extension(given, create=False)
        yield PostgresServer(given, _is_superuser(given), throwaway=False)
        return
    container, dsn = start_container(source.detail)
    try:
        check_vector_extension(dsn, create=True)
        yield PostgresServer(dsn, superuser=True, throwaway=True)
    finally:
        _remove(container)


@pytest.fixture
def postgres_schema(postgres_server: PostgresServer) -> Iterator[PostgresSchema]:
    """A schema of its own in the session database, dropped after the test."""
    with scratch_schema(postgres_server.dsn) as name:
        yield PostgresSchema(postgres_server.dsn, name)
