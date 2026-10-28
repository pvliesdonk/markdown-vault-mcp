"""The measurement harness's PostgreSQL configuration (#1895).

PostgreSQL comes from ``MVM_TEST_POSTGRES_DSN`` or a throwaway container,
never anything else, and its connection string reaches no output.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest
from scripts.envelope import __main__ as cli
from scripts.envelope import postgres, report, worker

from tests.fixtures.postgres import DSN_ENV, docker_available, report_unavailable

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.postgres import PostgresServer

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads /proc"
)


class TestTheParsers:
    def test_docker_memory_usage_is_read_in_bytes(self) -> None:
        assert postgres.parse_mem_usage("123.5MiB / 125.4GiB") == int(123.5 * 2**20)
        assert postgres.parse_mem_usage("2GiB / 8GiB") == 2 << 30
        assert postgres.parse_mem_usage("512kB / 1GiB") == 512_000
        assert postgres.parse_mem_usage("--") is None

    def test_a_restarted_container_gets_its_new_port(self) -> None:
        dsn = "host=127.0.0.1 port=49153 user=postgres password=pw dbname=postgres"
        assert postgres.with_port(dsn, "50001") == (
            "host=127.0.0.1 port=50001 user=postgres password=pw dbname=postgres"
        )

    def test_no_database_is_a_clear_refusal(self) -> None:
        pytest.importorskip("psycopg")  # without it the refusal names the extra
        with (
            pytest.raises(SystemExit, match="no PostgreSQL"),
            postgres.database({}, docker_available=lambda: False),
        ):
            pass  # pragma: no cover


@linux_only
def test_a_run_measures_postgres_without_showing_its_dsn(
    tmp_path: Path,
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(DSN_ENV, postgres_server.dsn)
    out = tmp_path / "out"
    code = cli.main(
        [
            "--notes", "20", "--dim", "16", "--queries", "2", "--discard", "1",
            "--edits", "3", "--kill-after", "1", "--backends", "postgres",
            "--out", str(out),
        ]
    )  # fmt: skip
    assert code == 0
    (path,) = out.glob("*.json")
    text = path.read_text()
    printed = capsys.readouterr()
    password = re.search(r"password=(\S+)", postgres_server.dsn)
    secrets = [postgres_server.dsn, "password="]
    secrets += [password.group(1)] if password else []
    for secret in secrets:
        assert secret not in text
        assert secret not in printed.out + printed.err

    result: dict[str, Any] = json.loads(text)
    (config,) = result["configs"]
    assert (config["backend"], config["search"]) == ("postgres", "streamed")
    assert config["source"] == "MVM_TEST_POSTGRES_DSN (hidden DSN)"
    assert all("not restarted" in v for v in config["latency"]["eviction"].values())
    assert config["restart"]["texts_embedded"] == 0
    assert config["memory"]["store_closed_at_baseline"] is True
    status = config["status"]
    assert status["search_mode"] == "streamed"
    assert status["resident_vector_bytes"] == 0
    assert status["stored_bytes"] > 0
    assert status["measured_store_bytes"] > 0  # the schema's size
    assert config["edits"]["texts_embedded"]["max"] == 1
    assert config["edits"]["store_delta"] is None  # no local store files
    assert config["edits"]["wal_bytes"]["median"] > 0
    service = config["service"]
    assert service["memory"] is None  # a database it did not start
    assert service["schema_bytes"] > 0
    assert service["database_bytes"] >= service["schema_bytes"]
    assert service["server_version"]
    assert service["pgvector_version"]
    assert config["recovery"]["texts_beyond_allowed"] == 0
    assert "PostgreSQL service" in printed.out
    assert _envelope_schemas(postgres_server.dsn) == []


def _envelope_schemas(dsn: str) -> list[str]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s",
            (postgres.SCHEMA_PREFIX + "%",),
        ).fetchall()
    return [row[0] for row in rows]


def test_a_database_never_shows_its_dsn_in_its_repr() -> None:
    db = postgres.Database("host=h password=hunter2", "MVM_TEST_POSTGRES_DSN")
    assert "hunter2" not in repr(db)


def _fake_run(seen: dict[str, Any], stderr: str = "") -> Any:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs["env"])
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr=stderr)

    return run


def _config(tmp_path: Path, backend: str) -> dict[str, Any]:
    return {
        "work": str(tmp_path), "backend": backend, "search": "streamed",
        "schema": "absent", "dim": 8, "batch_size": 8, "phase_timeout_s": 120,
    }  # fmt: skip


def test_a_failing_phase_never_shows_the_dsn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dsn = "host=127.0.0.1 port=1 user=u password=hunter2 dbname=x"
    db = postgres.Database(dsn, "MVM_TEST_POSTGRES_DSN (hidden DSN)")
    stderr = f"boom {dsn}\nuri postgresql://u:hunter2@h/x\nretry password=hunter2"
    monkeypatch.setattr(cli.subprocess, "run", _fake_run({}, stderr))
    config = _config(tmp_path, "postgres")
    with pytest.raises(SystemExit) as failure:
        cli._spawn("build", config, db=db)
    assert "hunter2" not in str(failure.value)
    assert "<hidden>" in str(failure.value)
    assert "phase build failed" in str(failure.value)


@pytest.mark.parametrize("backend", ["numpy", "sqlite", "postgres"])
def test_only_a_postgres_worker_gets_a_connection_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setenv(DSN_ENV, "host=h password=given")
    db = postgres.Database("host=h password=given", "MVM_TEST_POSTGRES_DSN")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli.subprocess, "run", _fake_run(seen))
    config = _config(tmp_path, backend)
    with pytest.raises(SystemExit):
        cli._spawn("build", config, db=db)
    assert DSN_ENV not in seen
    assert (worker.DSN_ENV in seen) == (backend == "postgres")


def test_a_bare_run_leaves_postgres_out() -> None:
    assert cli._parser().parse_args(["--out", "x"]).backends == ["numpy", "sqlite"]


def test_the_tables_name_where_the_database_came_from() -> None:
    result = {
        "size": "custom",
        "environment": {"git_sha": "abc"},
        "configs": [
            {
                "backend": "postgres",
                "search": "streamed",
                "source": "throwaway container pgvector/pgvector:0.8.1-pg17",
                "service": {"memory": 1 << 20},
            }
        ],
    }
    text = report.render_markdown(result)
    assert "| throwaway container pgvector/pgvector:0.8.1-pg17 |" in text


def test_a_failed_container_restart_is_not_reported_as_a_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def docker(*args: str, timeout: float = 0) -> subprocess.CompletedProcess[str]:
        del timeout
        return subprocess.CompletedProcess(list(args), 1, stdout="", stderr="no")

    monkeypatch.setattr(postgres, "_docker", docker)
    db = postgres.Database("host=h port=5 password=p", "throwaway", "abc123")
    with pytest.raises(SystemExit, match="could not restart"):
        postgres.restart(db)


@linux_only
def test_a_run_on_a_throwaway_container_restarts_it_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not docker_available():
        report_unavailable("needs the Docker CLI and daemon", os.environ)
    monkeypatch.delenv(DSN_ENV, raising=False)  # keeps MVM_TEST_POSTGRES_IMAGE
    before = _containers()
    out = tmp_path / "out"
    code = cli.main(
        [
            "--notes", "12", "--dim", "16", "--queries", "2", "--discard", "1",
            "--edits", "2", "--kill-after", "1", "--backends", "postgres",
            "--out", str(out),
        ]
    )  # fmt: skip
    assert code == 0
    (path,) = out.glob("*.json")
    (config,) = json.loads(path.read_text())["configs"]
    assert config["source"].startswith("throwaway container ")
    assert config["service"]["memory"] > 0
    assert all(
        note.endswith("service restarted, host page cache kept")
        for note in config["latency"]["eviction"].values()
    )
    assert _containers() == before  # removed, schema and all


def _containers() -> set[str]:
    listed = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", "name=mvm-test-pg-"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return set(listed.stdout.split())
