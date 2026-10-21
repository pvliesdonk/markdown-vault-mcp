"""Contract tests: ``serve`` configures logging once, in the root callback.

Template-owned. ``cli._root`` configures pvl-core's logging before any
subcommand and honours ``-v``; ``make_server()`` configures it too, for
callers with no setup of their own.  Under ``serve`` that second call
reset ``-v`` to the environment's level and logged the legacy
``FASTMCP_LOG_LEVEL`` warning twice (template #726), so ``serve`` passes
``configure_logging=False``.

Each test runs the real ``serve`` command and the real ``make_server``,
replacing only the built server's ``run`` so nothing starts listening; the
root logger is read at the moment the server would have started.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp import server as server_module
from markdown_vault_mcp.cli import app

if TYPE_CHECKING:
    from collections.abc import Iterator

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"


def _preset_contract_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set every variable the project's ``config_contract_env`` returns.

    The fixture in the project's ``tests/conftest.py`` supplies values for
    the variables its ``from_env`` reads with ``env(..., required=True)``.
    Resolved via ``getfixturevalue`` so a ``conftest.py`` that predates the
    fixture keeps passing with nothing preset.  Repeated verbatim in every
    template-owned test that builds from the environment rather than
    imported: a sibling import only resolves when ``tests/`` is not a package.
    """
    try:
        env = request.getfixturevalue("config_contract_env")
    except pytest.FixtureLookupError:
        return
    for key, value in dict(env).items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _isolated_root_logger(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Preset the contract env and restore the root logger afterwards.

    pvl-core installs its handler chain at root on the stream ``CliRunner``
    substitutes for stderr; left in place, it would outlive the test.
    """
    _preset_contract_env(request, monkeypatch)
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    monkeypatch.setenv(f"{_ENV_PREFIX}_LOG_FORMAT", "json")
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _serve(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    """Run ``serve`` on stdio; return the root level when it would start."""
    seen: list[int] = []
    real_make_server = server_module.make_server

    def make_server(**kwargs: Any) -> Any:
        server = real_make_server(**kwargs)
        monkeypatch.setattr(
            server,
            "run",
            lambda **_: seen.append(logging.getLogger().level),
        )
        return server

    monkeypatch.setattr(server_module, "make_server", make_server)
    result = CliRunner().invoke(app, [*args, "serve"])
    assert result.exit_code == 0, result.output
    (level,) = seen
    return level


def test_verbose_flag_survives_server_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``serve -v`` starts the server at DEBUG, not the environment's INFO."""
    assert _serve(monkeypatch, "-v") == logging.DEBUG


def test_legacy_level_variable_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``FASTMCP_LOG_LEVEL`` logs ``log_level_env_deprecated`` exactly once."""
    monkeypatch.setenv("FASTMCP_LOG_LEVEL", "DEBUG")
    assert _serve(monkeypatch) == logging.DEBUG
    warnings = [
        r
        for r in caplog.records
        if r.getMessage().startswith("log_level_env_deprecated")
    ]
    assert len(warnings) == 1
