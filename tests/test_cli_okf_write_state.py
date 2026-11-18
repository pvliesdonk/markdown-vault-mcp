"""The local CLI exposes the vault's OKF write state."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from tests.fixtures.cli_discovery import reader_backend
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path


def test_stats_okf_write_state(
    discovery_source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (discovery_source / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Index\n', encoding="utf-8"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    with reader_backend(False) as connection:
        result = CliRunner().invoke(app, ["stats", *connection, "--json"])
    assert result.exit_code == 0, result.output
    section = json.loads(result.stdout)["okf"]
    assert section["write_configured"] is True
    assert section["write_effective"] is False


@pytest.mark.usefixtures("discovery_source")
def test_stats_capabilities_when_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    with reader_backend(False) as connection:
        result = CliRunner().invoke(app, ["stats", *connection, "--json"])
    assert result.exit_code == 0, result.output
    section = json.loads(result.stdout)["okf"]
    assert set(section) == {"capabilities"}
    assert section["capabilities"]["verify"] == {
        "configured": "elicit",
        "effective": True,
        "reason": None,
    }
    assert section["capabilities"]["index"] == {
        "configured": True,
        "effective": False,
        "reason": "okf_inactive",
    }


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("remote", [False, True])
def test_cli_matches_config_capabilities(
    discovery_source: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote: bool,
    declared: bool,
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    if declared:
        (discovery_source / "index.md").write_text('---\nokf_version: "0.2"\n---\n')

    async def resource_report() -> dict:
        async with Client(make_server()) as client:
            return json.loads((await client.read_resource("config://vault"))[0].text)[
                "okf_capabilities"
            ]

    expected = asyncio.run(resource_report())
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(app, ["stats", *connection, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["okf"]["capabilities"] == expected
