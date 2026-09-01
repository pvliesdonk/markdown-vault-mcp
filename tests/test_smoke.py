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

import asyncio
import json
from typing import TYPE_CHECKING, Any

from fastmcp import Client

from markdown_vault_mcp.server import make_server
from tests.conftest import wait_for_mcp_writer_drain

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_make_server_constructs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """make_server() returns a FastMCP instance without raising."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    assert make_server() is not None


async def test_config_resource_round_trips(
    client: Client[Any], vault_path: Path
) -> None:
    """``config://vault`` exposes the server config as JSON with the expected fields.

    Adapts the template's ``status://`` example to MVM's surface (MVM has no
    ``status://``): the config resource is reachable via the client and carries
    the wired ``source_dir`` plus a typed ``read_only`` flag.
    """
    result = await client.read_resource("config://vault")
    data = json.loads(result[0].text)
    assert data["source_dir"] == str(vault_path)
    assert isinstance(data["read_only"], bool)


async def test_get_server_info_tool_registered(client: Client[Any]) -> None:
    """``get_server_info`` is wired by default and returns wrapper info."""
    tools = {t.name for t in await client.list_tools()}
    assert "get_server_info" in tools
    result = await client.call_tool("get_server_info", {})
    assert result.data.get("server_name") == "markdown-vault-mcp"
    assert result.data.get("server_version")  # non-empty version string
    assert result.data.get("core_version")  # non-empty


async def test_summarize_prompt_round_trips_path(client: Client[Any]) -> None:
    """MVM's built-in ``summarize`` prompt requires a ``path`` arg and its
    rendered output includes the supplied path value.

    Adapts the template's ``summarize``-``context`` example: MVM's summarize is
    a built-in markdown prompt taking ``path`` (required).
    """
    result = await client.get_prompt("summarize", {"path": "notes/intro.md"})
    rendered = result.messages[0].content.text
    assert "notes/intro.md" in rendered


def test_server_name_env_override(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    """``MARKDOWN_VAULT_MCP_SERVER_NAME`` overrides the FastMCP server name."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "renamed-instance")
    server = make_server()
    assert server.name == "renamed-instance"
    assert (server.instructions or "").startswith(
        "renamed-instance: Generic markdown vault MCP with hybrid search"
    )


def test_server_name_override_reaches_server_info(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    """The overridden name also flows through to ``get_server_info``.

    Uses an inline ``Client`` rather than the shared ``client`` fixture because
    it needs a custom ``SERVER_NAME`` set before ``make_server()``; the autouse
    ``_clear_env`` still isolates the env (it runs before this test body).
    """
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "renamed-instance")
    server = make_server()

    async def _call_server_info() -> Any:
        async with Client(server) as c:
            await wait_for_mcp_writer_drain(c)
            return await c.call_tool("get_server_info", {})

    result = asyncio.run(_call_server_info())
    assert result.data.get("server_name") == "renamed-instance"


def test_instructions_env_override(
    monkeypatch: pytest.MonkeyPatch,
    vault_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy instructions replace generated text and warn about both additions."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", "Custom operator text.")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTANCE_DESCRIPTION", "Demo material.")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA", "House rule: be brief.")
    # Scope to core's logger: make_server() re-applies FASTMCP_LOG_LEVEL to the
    # root logger, which would otherwise drop the record under a stricter env.
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    with caplog.at_level("WARNING", logger="fastmcp_pvl_core"):
        server = make_server()
    assert server.instructions == "Custom operator text."
    warning = next(
        rec.getMessage()
        for rec in caplog.records
        if "MARKDOWN_VAULT_MCP_INSTRUCTIONS" in rec.getMessage()
    )
    assert "MARKDOWN_VAULT_MCP_INSTANCE_DESCRIPTION" in warning
    assert "MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA" in warning


def test_instructions_compose_semantic_operator_roles(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    """Operator routing and policy retain their semantic positions."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTANCE_DESCRIPTION", "Demo material.")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA", "House rule: be brief.")
    text = make_server().instructions or ""
    sections = text.split("\n\n")
    assert sections[0] == (
        "markdown-vault-mcp: Generic markdown vault MCP with hybrid search"
    )
    assert sections[1] == "Demo material."
    mode = "This instance is READ-WRITE; write tools are available."
    policy = "House rule: be brief."
    assert sections[2] == mode
    assert sections.index(mode) < sections.index(policy)
    assert sections.index(policy) < next(
        index for index, section in enumerate(sections) if "'search' finds" in section
    )
    assert sections[-1] == (
        "Full documentation for this server: "
        "https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt"
    )


def test_blank_overrides_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    """Blank SERVER_NAME / INSTRUCTIONS are treated as unset (env strips).

    A blank INSTRUCTIONS must leave the composed text in place rather than
    replacing it with whitespace, which is what a regression to a raw
    ``os.environ.get`` would do.
    """
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "   ")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", "   ")
    server = make_server()
    assert server.name == "markdown-vault-mcp"
    assert (server.instructions or "").startswith(
        "markdown-vault-mcp: Generic markdown vault MCP with hybrid search"
    )


def test_no_file_exchange_scaffolding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """make_server() on stdio registers no file-exchange or transfer tools.

    Two guards: no tool name carries the removed file-exchange scaffolding
    substrings (``file_exchange`` / ``upload_file``), and the real transfer
    tools (``create_upload_link`` / ``create_download_link``) are absent. The
    transfer tools register only when ``make_server()`` runs with a non-stdio
    transport; here it uses its default ``transport="stdio"`` argument (the
    in-memory ``Client`` channel is unrelated to that gating).
    """
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    server = make_server()

    async def _list_tools() -> set[str]:
        async with Client(server) as c:
            await wait_for_mcp_writer_drain(c)
            return {t.name for t in await c.list_tools()}

    tools = asyncio.run(_list_tools())
    assert not any("file_exchange" in t or "upload_file" in t for t in tools)
    assert "create_upload_link" not in tools
    assert "create_download_link" not in tools


def test_register_apps_logs_configured_domain(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    vault_path: Path,
) -> None:
    """register_apps logs the configured app domain.

    Adapted from the template: MVM's ``register_apps`` is real (not a no-op), so
    we construct a server with APP_DOMAIN set and assert the log record (from
    the _server_apps logger, args carrying the domain) rather than re-calling
    register_apps on an already-registered server.
    """
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_APP_DOMAIN", "example.com")
    with caplog.at_level("INFO", logger="markdown_vault_mcp._server_apps"):
        make_server()
    assert any(
        r.name == "markdown_vault_mcp._server_apps" and r.args == ("example.com",)
        for r in caplog.records
    )
