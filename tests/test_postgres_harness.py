"""The PostgreSQL test harness and the ``postgres`` extra (#1504)."""

from __future__ import annotations

import os
import subprocess
import tomllib
import uuid
from pathlib import Path

import pytest

from tests.fixtures.postgres import (
    DEFAULT_IMAGE,
    DSN_ENV,
    IMAGE_ENV,
    SCHEMA_PREFIX,
    PostgresSchema,
    PostgresServer,
    Source,
    check_vector_extension,
    docker_available,
    report_unavailable,
    resolve_source,
    scratch_schema,
    start_container,
)

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
_POSTGRES_PACKAGES = ("psycopg[binary]", "psycopg-pool", "pgvector", "numpy")


def _extras() -> dict[str, list[str]]:
    with _PYPROJECT.open("rb") as fh:
        extras: dict[str, list[str]] = tomllib.load(fh)["project"][
            "optional-dependencies"
        ]
    return extras


def _names(requirements: list[str]) -> set[str]:
    return {r.split(">")[0].split("<")[0].split("=")[0].strip() for r in requirements}


def test_postgres_extra_declares_the_client_libraries() -> None:
    assert _names(_extras()["postgres"]) == set(_POSTGRES_PACKAGES)


def test_all_extra_includes_the_postgres_client_libraries() -> None:
    assert set(_POSTGRES_PACKAGES) <= _names(_extras()["all"])


def _never(*_args: object) -> bool:
    raise AssertionError("docker must not be probed when a DSN is set")


def test_resolve_source_prefers_the_dsn() -> None:
    source = resolve_source({DSN_ENV: " host=db dbname=x "}, docker_available=_never)
    assert source == Source("dsn", "host=db dbname=x")


def test_resolve_source_uses_docker_with_the_default_image() -> None:
    source = resolve_source({}, docker_available=lambda: True)
    assert source == Source("docker", DEFAULT_IMAGE)


def test_resolve_source_honours_the_image_override() -> None:
    source = resolve_source(
        {IMAGE_ENV: "pgvector/pgvector:pg16"}, docker_available=lambda: True
    )
    assert source == Source("docker", "pgvector/pgvector:pg16")


def test_resolve_source_without_docker_or_dsn_is_unavailable() -> None:
    source = resolve_source({}, docker_available=lambda: False)
    assert source.kind == "unavailable"
    assert DSN_ENV in source.detail
    assert "Docker" in source.detail


def test_resolve_source_without_driver_names_the_extra() -> None:
    source = resolve_source(
        {DSN_ENV: "host=db"}, docker_available=_never, driver_available=False
    )
    assert source.kind == "unavailable"
    assert "'postgres' extra" in source.detail


@pytest.mark.parametrize(
    ("environ", "outcome"),
    [
        ({"CI": "true"}, pytest.fail.Exception),
        ({"CI": "TRUE"}, pytest.fail.Exception),
        ({}, pytest.skip.Exception),
        ({"CI": "false"}, pytest.skip.Exception),
    ],
)
def test_unavailable_fails_under_ci_and_skips_outside(
    environ: dict[str, str], outcome: type[BaseException]
) -> None:
    with pytest.raises(outcome, match="no PostgreSQL"):
        report_unavailable("no PostgreSQL", environ)


def test_postgres_server_has_the_vector_extension(
    postgres_server: PostgresServer,
) -> None:
    import psycopg

    with psycopg.connect(postgres_server.dsn) as conn:
        row = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    assert row is not None
    assert row[0]


def test_inner_product_with_a_zero_vector_is_minus_zero_not_nan(
    postgres_server: PostgresServer,
) -> None:
    import psycopg

    with psycopg.connect(postgres_server.dsn) as conn:
        row = conn.execute(
            "SELECT '[0,0,0]'::vector <#> '[1,0,0]'::vector,"
            " '[0,0,0]'::vector <=> '[1,0,0]'::vector"
        ).fetchone()
    assert row is not None
    inner, cosine = row
    assert inner == 0.0
    assert cosine != cosine  # NaN


def _schema_exists(dsn: str, name: str) -> bool:
    import psycopg

    with psycopg.connect(dsn) as conn:
        found = conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)
        ).fetchone()
    return found is not None


def test_each_test_gets_its_own_schema(postgres_schema: PostgresSchema) -> None:
    assert postgres_schema.name.startswith(SCHEMA_PREFIX)
    assert _schema_exists(postgres_schema.dsn, postgres_schema.name)


def test_scratch_schema_is_dropped_even_when_the_body_raises(
    postgres_server: PostgresServer,
) -> None:
    import psycopg
    from psycopg import sql

    seen: list[str] = []
    with (
        pytest.raises(RuntimeError, match="body failed"),
        scratch_schema(postgres_server.dsn) as name,
    ):
        seen.append(name)
        with psycopg.connect(postgres_server.dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE TABLE {}.t (i int)").format(sql.Identifier(name))
            )
        raise RuntimeError("body failed")
    assert seen
    assert not _schema_exists(postgres_server.dsn, seen[0])


def test_dsn_without_vector_extension_fails_with_the_fix(
    postgres_server: PostgresServer,
) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    if not postgres_server.throwaway:
        pytest.skip("creates a database: runs only against the harness's own container")
    database = f"mvm_test_noext_{uuid.uuid4().hex[:8]}"
    other = make_conninfo(postgres_server.dsn, dbname=database)
    try:
        with psycopg.connect(postgres_server.dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                    sql.Identifier(database)
                )
            )
        with pytest.raises(pytest.fail.Exception, match="CREATE EXTENSION vector"):
            check_vector_extension(other, create=False)
    finally:
        with psycopg.connect(postgres_server.dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database))
            )


def _containers_named(prefix: str) -> set[str]:
    listing = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {line for line in listing.stdout.split() if line}


def test_container_that_never_answers_is_removed(
    postgres_server: PostgresServer,
) -> None:
    """A container that runs but never listens fails with its logs and is removed."""
    del postgres_server  # orders it after the session container exists
    if not docker_available():
        pytest.skip("needs the Docker CLI")
    prefix = f"mvm-test-pg-{os.getpid()}-"
    before = _containers_named(prefix)
    with pytest.raises(
        pytest.fail.Exception, match="did not accept connections within 3s"
    ):
        start_container(DEFAULT_IMAGE, command=("sleep", "60"), ready_timeout_s=3)
    assert _containers_named(prefix) == before


def test_container_that_cannot_start_is_reported(
    postgres_server: PostgresServer,
) -> None:
    """An image that cannot be pulled fails with docker's reason, leaving nothing."""
    del postgres_server
    if not docker_available():
        pytest.skip("needs the Docker CLI")
    prefix = f"mvm-test-pg-{os.getpid()}-"
    before = _containers_named(prefix)
    with pytest.raises(pytest.fail.Exception, match=r"docker run .* failed"):
        start_container("pgvector/pgvector:no-such-tag-1504", ready_timeout_s=3)
    assert _containers_named(prefix) == before


def test_descriptions_never_show_the_dsn() -> None:
    secret = "host=db user=u password=TOPSECRET dbname=d"
    server = PostgresServer(secret, superuser=False, throwaway=False)
    schema = PostgresSchema(secret, "mvm_test_abc")
    assert "TOPSECRET" not in repr(server)
    assert "TOPSECRET" not in repr(schema)
    assert "mvm_test_abc" in repr(schema)


def test_unreachable_dsn_fails_without_the_password_or_a_traceback() -> None:
    dsn = "host=127.0.0.1 port=1 user=u password=TOPSECRET dbname=d connect_timeout=2"
    with pytest.raises(pytest.fail.Exception) as failure:
        check_vector_extension(dsn, create=False)
    assert "TOPSECRET" not in str(failure.value)
    assert "cannot connect" in str(failure.value)
    assert failure.value.pytrace is False


def test_container_that_exits_on_start_reports_its_logs(
    postgres_server: PostgresServer,
) -> None:
    """A container that crashes at once fails with its own log lines, not a timeout."""
    del postgres_server
    if not docker_available():
        pytest.skip("needs the Docker CLI")
    prefix = f"mvm-test-pg-{os.getpid()}-"
    before = _containers_named(prefix)
    with pytest.raises(pytest.fail.Exception, match="exited") as failure:
        start_container(
            DEFAULT_IMAGE,
            command=("sh", "-c", "echo BOOM-LOG-LINE >&2; exit 3"),
            ready_timeout_s=20,
        )
    assert "BOOM-LOG-LINE" in str(failure.value)
    assert _containers_named(prefix) == before


def test_interrupted_start_removes_the_container(
    postgres_server: PostgresServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C while waiting for the container leaves no container behind."""
    del postgres_server
    if not docker_available():
        pytest.skip("needs the Docker CLI")
    import tests.fixtures.postgres as harness

    def interrupted(*_args: object) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(harness, "_wait_until_ready", interrupted)
    prefix = f"mvm-test-pg-{os.getpid()}-"
    before = _containers_named(prefix)
    with pytest.raises(KeyboardInterrupt):
        start_container(DEFAULT_IMAGE, command=("sleep", "60"))
    assert _containers_named(prefix) == before


def test_schema_drop_blocked_by_a_lock_fails_instead_of_hanging(
    postgres_server: PostgresServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg
    from psycopg import sql

    import tests.fixtures.postgres as harness

    monkeypatch.setattr(harness, "_DROP_LOCK_TIMEOUT_MS", 300)
    holder = psycopg.connect(postgres_server.dsn)
    try:
        with (
            pytest.raises(pytest.fail.Exception, match="could not drop"),
            scratch_schema(postgres_server.dsn) as name,
        ):
            with psycopg.connect(postgres_server.dsn, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("CREATE TABLE {}.t (i int)").format(sql.Identifier(name))
                )
            holder.execute(
                sql.SQL("LOCK TABLE {}.t IN ACCESS SHARE MODE").format(
                    sql.Identifier(name)
                )
            )
    finally:
        holder.close()
    with psycopg.connect(postgres_server.dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name))
        )
