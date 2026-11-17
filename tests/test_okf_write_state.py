"""OKF write-state reporting reflects the configured and live gates."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("mode", "declared", "write", "read_only", "active", "effective"),
    [
        ("auto", True, True, False, True, True),
        ("auto", True, False, False, True, False),
        ("auto", True, True, True, True, False),
        ("auto", False, True, False, False, False),
        ("on", False, True, False, True, True),
        ("off", True, False, False, False, False),
    ],
)
async def test_write_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    declared: bool,
    write: bool,
    read_only: bool,
    active: bool,
    effective: bool,
) -> None:
    if declared:
        (tmp_path / "index.md").write_text(
            '---\nokf_version: "0.2"\n---\n', encoding="utf-8"
        )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", mode)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", str(write).lower())
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", str(read_only).lower())
    async with Client(make_server()) as client:
        config = json.loads((await client.read_resource("config://vault"))[0].text)
        stats: dict[str, Any] = (await client.call_tool("stats", {})).structured_content
        assert config["okf_active"] is active
        assert config["okf_write_configured"] is write
        assert config["okf_write_effective"] is effective
        if active:
            assert stats["okf"]["write_configured"] is write
            assert stats["okf"]["write_effective"] is effective
        else:
            assert "okf" not in stats


async def test_write_state_tracks_live_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    async with Client(make_server()) as client:
        for declared in (False, True, False):
            index = tmp_path / "index.md"
            if declared:
                index.write_text('---\nokf_version: "0.2"\n---\n', encoding="utf-8")
            elif index.exists():
                index.unlink()
            config = json.loads((await client.read_resource("config://vault"))[0].text)
            stats = (await client.call_tool("stats", {})).structured_content
            assert config["okf_write_configured"] is True
            assert config["okf_write_effective"] is declared
            assert ("okf" in stats) is declared
            if declared:
                assert stats["okf"]["write_effective"] is True


def test_write_state_uses_vault_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "false")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=True, okf_write=True)
    )
    try:
        assert vault.okf_write_state(active=True) == (True, False)
    finally:
        vault.close()
