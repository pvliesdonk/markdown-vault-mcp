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

On native Linux, the next start also reclaims labeled containers whose owner
is gone, but only in the initial PID/user namespaces and the same boot/UID.
Other platforms, nested namespaces and legacy containers are left alone.
See ``docs/design/reference/test-postgres-container-ownership.md`` (#1952).
"""

from __future__ import annotations

import contextlib
import errno
import importlib.util
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    import psycopg

DSN_ENV = "MVM_TEST_POSTGRES_DSN"
IMAGE_ENV = "MVM_TEST_POSTGRES_IMAGE"
# Set to ``true`` only where the image's pgvector is below the store's minimum
# (the range workflow's refusal job, #1897); the refusal test skips without it.
OLD_PGVECTOR_ENV = "MVM_TEST_POSTGRES_OLD_PGVECTOR"
DEFAULT_IMAGE = "pgvector/pgvector:0.8.1-pg17"
SCHEMA_PREFIX = "mvm_test_"
_READY_TIMEOUT_S = 90.0
_DOCKER_TIMEOUT_S = 600.0
_DROP_LOCK_TIMEOUT_MS = 10_000
_HIDDEN = "<hidden>"
_CLEANUP_TIMEOUT_S = 10.0
_OWNER_LABEL = "io.github.pvliesdonk.markdown-vault-mcp.test-postgres"
_FULL_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
logger = logging.getLogger(__name__)


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


def _initial_namespace(name: str, inode: int, kind: int) -> str:
    """Identify a genuine initial namespace handle, or reject the scope."""
    import fcntl

    # NS_GET_NSTYPE, PID_NS_INIT_INO and USER_NS_INIT_INO: Linux nsfs UAPI.
    # /proc/self remains correct even with an ancestor namespace's proc mount.
    with Path(f"/proc/self/ns/{name}").open("rb") as handle:
        if fcntl.ioctl(handle.fileno(), 0xB703) != kind:
            raise ValueError("unexpected namespace type")
        identity = os.fstat(handle.fileno())
    if identity.st_ino != inode:
        raise ValueError("not the initial namespace")
    return f"{identity.st_dev}:{identity.st_ino}"


def _owner_scope() -> dict[str, str]:
    """Return collectable ownership only when Linux identity is unambiguous."""
    if sys.platform != "linux":
        return {}
    try:
        uid = os.getuid()
        if uid != os.geteuid():
            return {}
        pidns = _initial_namespace("pid", 0xEFFFFFFC, 0x20000000)
        userns = _initial_namespace("user", 0xEFFFFFFD, 0x10000000)
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        hostname = os.uname().nodename
        if str(uuid.UUID(boot)) != boot or not hostname:
            return {}
    except (OSError, ValueError):
        return {}
    return {
        f"{_OWNER_LABEL}.{key}": value
        for key, value in {
            "version": "1",
            "boot": boot,
            "host": hostname,
            "pidns": pidns,
            "userns": userns,
            "uid": str(uid),
        }.items()
    }


def _owner_is_gone(labels: object, scope: Mapping[str, str]) -> bool:
    """Require matching ownership and ESRCH; reused or inaccessible PIDs stay."""
    if (
        not scope
        or not isinstance(labels, dict)
        or any(labels.get(key) != value for key, value in scope.items())
    ):
        return False
    pid = labels.get(f"{_OWNER_LABEL}.pid")
    if not isinstance(pid, str) or re.fullmatch(r"[1-9][0-9]{0,9}", pid) is None:
        return False
    if int(pid) > 2**31 - 1:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError as exc:
        return exc.errno == errno.ESRCH
    return False


def _cleanup_docker(deadline: float, *args: str) -> subprocess.CompletedProcess[str]:
    """Keep the entire best-effort sweep within one startup budget."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("docker cleanup", _CLEANUP_TIMEOUT_S)
    return _docker(*args, timeout=remaining)


def _collect_orphan(container: str, scope: Mapping[str, str], deadline: float) -> None:
    """Recheck immutable metadata and remove only the inspected full ID."""
    try:
        inspected = _cleanup_docker(deadline, "inspect", container)
        if inspected.returncode:
            return
        records = json.loads(inspected.stdout)
        if not isinstance(records, list) or len(records) != 1:
            return
        record = records[0]
        if not isinstance(record, dict) or record.get("Id") != container:
            return
        config = record.get("Config")
        if not isinstance(config, dict) or not _owner_is_gone(
            config.get("Labels"), scope
        ):
            return
        removed = _cleanup_docker(deadline, "rm", "-f", container)
        if removed.returncode == 0:
            logger.info("postgres_orphan_removed container=%s", container)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        # Cleanup is optional: incomplete evidence must never broaden its scope.
        return


def _collect_orphans(scope: Mapping[str, str]) -> None:
    """Find this harness's containers without sweeping names or other owners."""
    if not scope:
        return
    deadline = time.monotonic() + _CLEANUP_TIMEOUT_S
    filters = [
        arg
        for key, value in scope.items()
        for arg in ("--filter", f"label={key}={value}")
    ]
    try:
        listed = _cleanup_docker(
            deadline,
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            *filters,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if listed.returncode:
        return
    for container in set(listed.stdout.splitlines()):
        if time.monotonic() >= deadline:
            break
        if _FULL_CONTAINER_ID.fullmatch(container):
            _collect_orphan(container, scope, deadline)


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
    scope = _owner_scope()
    _collect_orphans(scope)
    # Override inherited image labels, including images committed from old runs.
    labels = (
        {**scope, f"{_OWNER_LABEL}.pid": str(os.getpid())}
        if scope
        else {f"{_OWNER_LABEL}.version": "unsupported"}
    )
    label_args = [
        arg for key, value in labels.items() for arg in ("--label", f"{key}={value}")
    ]
    password = secrets.token_hex(16)
    name = f"mvm-test-pg-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    # No --rm: a container that exits must keep its logs until they are read.
    run = _docker(
        "run",
        "-d",
        *label_args,
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


def lease_is_free(dsn: str, schema: str) -> bool:
    """Whether another session could take *schema*'s writer lease right now.

    Takes the key and gives it straight back, so it changes nothing.
    """
    import psycopg

    from markdown_vault_mcp.stores._postgres_lease import LEASE_CLASS

    key = (
        "(%s::bigint << 32) | (SELECT n.oid::bigint FROM pg_catalog.pg_namespace n"
        " WHERE n.nspname = %s)"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT pg_try_advisory_lock(" + key + ")", (LEASE_CLASS, schema)
        ).fetchone()
        taken = bool(row and row[0])
        if taken:
            conn.execute(
                "SELECT pg_advisory_unlock(" + key + ")", (LEASE_CLASS, schema)
            )
    return taken
