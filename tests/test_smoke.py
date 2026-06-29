"""Smoke tests for Markdown Vault MCP.

This file diverges from the template's scaffold because MVM's
``make_server`` calls ``ProjectConfig.from_env`` which requires a non-empty
``MARKDOWN_VAULT_MCP_SOURCE_DIR``.  The template's bare
``server = make_server(); assert server`` would raise before the
assertion ever runs.  Providing a throwaway ``tmp_path`` via
``monkeypatch`` keeps the check meaningful without tying the smoke
test to any real vault.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastmcp import Client

from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_make_server_constructs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """make_server() returns a FastMCP instance without raising."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    assert make_server() is not None


async def test_config_resource_round_trips(client: Client[Any]) -> None:
    """A real MVM resource is reachable via the client and returns valid JSON.

    Adapts the template's ``status://`` example to MVM's surface (MVM has no
    ``status://``): ``config://vault`` round-trips the server's config.
    """
    result = await client.read_resource("config://vault")
    data = json.loads(result[0].text)
    assert "source_dir" in data
    assert isinstance(data["read_only"], bool)


async def test_get_server_info_tool_registered(client: Client[Any]) -> None:
    """``get_server_info`` is wired by default and returns wrapper info."""
    tools = {t.name for t in await client.list_tools()}
    assert "get_server_info" in tools
    result = await client.call_tool("get_server_info", {})
    assert result.data  # non-empty server-info payload


async def test_summarize_prompt_round_trips_path(client: Client[Any]) -> None:
    """MVM's built-in ``summarize`` prompt round-trips its ``path`` argument.

    Adapts the template's ``summarize``-``context`` example: MVM's summarize is
    a built-in markdown prompt taking ``path`` (required); its body substitutes
    ``$path``.
    """
    result = await client.get_prompt("summarize", {"path": "notes/intro.md"})
    rendered = " ".join(m.content.text for m in result.messages)
    assert "notes/intro.md" in rendered


def test_server_name_env_override(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    """``MARKDOWN_VAULT_MCP_SERVER_NAME`` overrides the FastMCP server name."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "renamed-instance")
    assert make_server().name == "renamed-instance"


async def test_server_name_override_reaches_server_info(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    """The overridden name also flows through to ``get_server_info``."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "renamed-instance")
    async with Client(make_server()) as c:
        result = await c.call_tool("get_server_info", {})
    assert "renamed-instance" in json.dumps(result.data)


def test_instructions_env_override(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    """``MARKDOWN_VAULT_MCP_INSTRUCTIONS`` replaces the built-in instructions."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", "Custom operator text.")
    assert make_server().instructions == "Custom operator text."


def test_instructions_default_is_built(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    """With no override, instructions fall back to the built default."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", raising=False)
    assert make_server().instructions  # non-empty default


def test_blank_overrides_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    """Blank SERVER_NAME / INSTRUCTIONS are treated as unset (env strips)."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "   ")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", "   ")
    server = make_server()
    assert server.name == "markdown-vault-mcp"
    assert server.instructions  # non-empty default, not "   "


async def test_no_file_exchange_scaffolding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """make_server() registers no file-exchange tools (removed scaffolding)."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    async with Client(make_server()) as c:
        tools = {t.name for t in await c.list_tools()}
    assert not any("file_exchange" in t or "upload_file" in t for t in tools)


def test_register_apps_logs_configured_domain(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    vault_path: Path,
) -> None:
    """register_apps logs the configured app domain.

    Adapted from the template: MVM's ``register_apps`` is real (not a no-op), so
    we construct a server with APP_DOMAIN set and assert the structured log,
    rather than re-calling register_apps on an already-registered server.
    """
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_APP_DOMAIN", "example.com")
    with caplog.at_level("INFO", logger="markdown_vault_mcp._server_apps"):
        make_server()
    assert any(r.args == ("example.com",) for r in caplog.records)
