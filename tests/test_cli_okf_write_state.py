"""The local CLI exposes the vault's OKF write state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from tests.fixtures.cli_discovery import reader_backend

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
