"""The server lifespan builds ``Service`` from the bound config (template-owned).

``make_server(config=...)`` binds its ``ProjectConfig`` for the registrars;
the lifespan must hand the same object to ``Service`` so the running service
never re-reads the environment (pvliesdonk/fastmcp-server-template#727).  A
recording stand-in replaces ``Service`` so the check holds whatever this
project's ``Service`` does.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from markdown_vault_mcp import _server_deps
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.server import make_server


@pytest.fixture(autouse=True)
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


class _RecordingService:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


def test_lifespan_passes_the_bound_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_server_deps, "Service", _RecordingService)
    config = ProjectConfig.from_env()
    server = make_server(config=config)

    async def _run() -> Any:
        async with _server_deps.server_lifespan(server) as state:
            return state["service"]

    service = asyncio.run(_run())
    assert service.config is config
