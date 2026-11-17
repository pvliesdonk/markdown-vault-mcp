"""The local and HTTP CLI expose the same OKF write state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from tests.fixtures.cli_discovery import reader_backend

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("read_only", [False, True])
def test_stats_okf_write_state(
    discovery_source: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote: bool,
    read_only: bool,
) -> None:
    (discovery_source / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Index\n', encoding="utf-8"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", str(read_only).lower())
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(app, ["stats", *connection, "--json"])
    assert result.exit_code == 0, result.output
    section = json.loads(result.stdout)["okf"]
    assert section["write_configured"] is True
    assert section["write_effective"] is (not read_only)
