"""Reader logging stays live across pytest and command capture lifetimes."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from markdown_vault_mcp import cli
from tests.fixtures.logging import reader_logging

_LIFETIME_CASE = """\
import logging
import threading

import pytest
from fastmcp_pvl_core import configure_logging_from_env
from typer.testing import CliRunner

from markdown_vault_mcp import cli
from tests.fixtures.cli_discovery import reader_backend

pytest_plugins = ["tests.conftest"]
ready, release, emitted = (threading.Event() for _ in range(3))


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    source = tmp_path_factory.mktemp("vault")
    (source / "alpha.md").write_text("---\\npayload: !!binary /w==\\n---\\n# Alpha\\n")
    (source / "beta.md").write_text("# Beta\\n")
    original_warning = logging.Logger.warning
    original_configure = cli.configure_logging_from_env
    calls = 0
    live = False

    def warning(logger, message, *args, **kwargs):
        gated = message.startswith("reindex_index_failed")
        if gated:
            ready.set()
            assert release.wait(10), "writer release timed out"
        original_warning(logger, message, *args, **kwargs)
        if gated:
            emitted.set()

    def configure(*args, **kwargs):
        nonlocal calls
        if live:
            calls += 1
        if calls == 2:
            release.set()
            assert emitted.wait(5), "writer warning timed out"
        original_configure(*args, **kwargs)

    before = set(threading.enumerate())
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
        patch.setenv("MARKDOWN_VAULT_MCP_LOG_FORMAT", "json")
        patch.setattr(logging.Logger, "warning", warning)
        patch.setattr(cli, "configure_logging_from_env", configure)
        configure_logging_from_env("MARKDOWN_VAULT_MCP")
        try:
            with reader_backend(True) as connection:
                live = True
                assert ready.wait(5), "real reindex warning never arrived"
                owned = set(threading.enumerate()) - before
                sink = next(
                    h.stream for h in logging.getLogger().handlers
                    if type(h) is logging.StreamHandler
                )
                yield connection, sink
        finally:
            release.set()
    assert all(not thread.is_alive() for thread in owned)


def test_prime(backend, capsys):
    connection, _ = backend
    result = CliRunner().invoke(cli.app, ["read", "beta.md", *connection, "--json"])
    assert result.exit_code == 0, result.output
    # Real pytest teardown closes this capture after the test returns.
    configure_logging_from_env("MARKDOWN_VAULT_MCP")


def test_later_capture(backend):
    connection, sink = backend
    result = CliRunner().invoke(cli.app, ["read", "alpha.md", *connection, "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ERROR:" in result.stderr
    assert "Traceback" not in result.stderr
    logs = sink.getvalue()
    assert "reindex_index_failed" in logs
    assert "TypeError" in logs
    assert "Logging error" not in logs
"""


def test_reader_logging_survives_capture_teardown(tmp_path: Path) -> None:
    """A predecessor's closed capture cannot contaminate a later real CLI read."""
    case = tmp_path / "test_lifetime_case.py"
    case.write_text(_LIFETIME_CASE)
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(case), "-q"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("fmt", ["json", "rich"])
@pytest.mark.parametrize("failed_body", [False, True])
def test_reader_logs_render_outside_command_capture(
    monkeypatch: pytest.MonkeyPatch, fmt: str, failed_body: bool
) -> None:
    """Warnings and exceptions render without entering command output or leaking state."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_LOG_FORMAT", fmt)
    root = logging.getLogger()
    custom = logging.NullHandler()
    root.addHandler(custom)
    incoming, level, stderr = root.handlers[:], root.level, sys.stderr
    outcome = (
        pytest.raises(ValueError, match="body failed") if failed_body else nullcontext()
    )
    with outcome, reader_logging() as destination:
        with CliRunner().isolation() as (stdout, captured, _):
            cli.configure_logging_from_env("MARKDOWN_VAULT_MCP")
            root.warning("synthetic_warning value=retained")
            try:
                raise TypeError("synthetic exception retained")
            except TypeError:
                root.error("synthetic_error value=retained", exc_info=True)
            typer.echo("ERROR: intended diagnostic", err=True)
            assert stdout.getvalue() == b""
            assert captured.getvalue() == b"ERROR: intended diagnostic\n"
        assert "synthetic_warning" in destination.getvalue()
        assert "TypeError: synthetic exception retained" in destination.getvalue()
        assert custom in root.handlers
        if failed_body:
            raise ValueError("body failed")
    assert root.handlers == incoming
    assert root.level == level
    assert sys.stderr is stderr
    assert destination.closed


def test_reader_logging_restores_after_bootstrap_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bootstrap failure detaches its chain before the destination closes."""
    original = cli.configure_logging_from_env
    root = logging.getLogger()
    incoming, level, stderr = root.handlers[:], root.level, sys.stderr

    def failed(env_prefix: str, *, verbose: bool = False) -> None:
        original(env_prefix, verbose=verbose)
        raise ValueError("bootstrap failed")

    monkeypatch.setattr(cli, "configure_logging_from_env", failed)
    with pytest.raises(ValueError, match="bootstrap failed"), reader_logging():
        pytest.fail("A failed bootstrap cannot enter the body")
    assert root.handlers == incoming
    assert root.level == level
    assert sys.stderr is stderr
    assert cli.configure_logging_from_env is failed
