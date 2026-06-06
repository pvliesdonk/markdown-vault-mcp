"""Smoke tests for Markdown Vault MCP.

This file diverges from the template's scaffold because MV's
``make_server`` calls ``VaultConfig.from_env`` which requires a non-empty
``MARKDOWN_VAULT_MCP_SOURCE_DIR``.  The template's bare
``server = make_server(); assert server`` would raise before the
assertion ever runs.  Providing a throwaway ``tmp_path`` via
``monkeypatch`` keeps the check meaningful without tying the smoke
test to any real vault.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_make_server_constructs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """make_server() returns a FastMCP instance without raising."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    server = make_server()
    assert server is not None
