"""OKF write-state reporting reflects the configured and live gates."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client

from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("declared", "write", "read_only"),
    [
        (True, True, False),
        (True, False, False),
        (True, True, True),
        (False, True, False),
    ],
)
def test_write_state_uses_vault_settings(
    tmp_path: Path, declared: bool, write: bool, read_only: bool
) -> None:
    if declared:
        (tmp_path / "index.md").write_text(
            '---\nokf_version: "0.2"\n---\n', encoding="utf-8"
        )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=read_only, okf_mode="auto", okf_write=write),
    )
    try:
        state = vault.okf.state()
        assert state.active is declared
        if declared:
            section = vault.reader.okf_stats()
            assert section is not None
            assert "write_configured" not in section
            assert "write_effective" not in section
    finally:
        vault.close()


async def test_mcp_reports_write_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n', encoding="utf-8"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        config = json.loads((await client.read_resource("config://vault"))[0].text)
        stats = (await client.call_tool("stats", {})).structured_content
    assert "okf_write_configured" not in config
    assert "okf_write_effective" not in config
    assert "write_configured" not in stats["okf"]
    assert "write_effective" not in stats["okf"]
