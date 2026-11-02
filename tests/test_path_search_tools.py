"""Path discovery through library and real MCP transports."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from markdown_vault_mcp._path_search import search_paths as discover_paths
from markdown_vault_mcp.domain import get_vault_singleton
from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.query_types import PathQuery
from markdown_vault_mcp.vault import Vault
from tests.conftest import wait_for_mcp_writer_drain
from tests.fixtures.query_http import live_mcp
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP


@pytest.fixture
def path_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """Provide a server holding synthetic path matches and an active OKF bundle."""
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Index\n')
    for name in ["target.md", "target_extra.md", "targat.md", "targit.md"]:
        (tmp_path / name).write_text(
            "---\ntags: [keep]\nstatus: stable\n---\n# Example\n\nBody\n"
        )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "tags,status")
    return make_server(transport="http")


async def test_path_tool_over_http_and_reader(path_server: FastMCP) -> None:
    """Wire items retain metadata, OKF and the shared freshness observation."""
    async with live_mcp(path_server) as url, Client(url) as client:
        await wait_for_mcp_writer_drain(client)
        ticks = iter([0.0, 0.2])
        with patch(
            "markdown_vault_mcp.managers.search.search_paths",
            side_effect=lambda index, request: discover_paths(
                index, request, clock=lambda: next(ticks)
            ),
        ):
            reply = await client.call_tool(
                "search_paths", {"query": "target", "fuzzy": True, "limit": 1}
            )
        data = reply.structured_content
        assert data["items"][0]["path"] == "target.md" and data["truncated"]
        assert data["coverage"] == "limited" and data["reasons"] == ["time_limit"]
        assert data["items"][0]["frontmatter"]["tags"] == ["keep"]
        assert data["items"][0]["okf"]["status"] == "stable"
        assert "sections" not in data["items"][0]
        assert data["index_stale"] == reply.meta["index_stale"] is False
        assert json.loads(reply.content[0].text) == data
        vault = get_vault_singleton()
        assert (
            vault.reader.search_paths(PathQuery(query="target")).items[0]["path"]
            == "target.md"
        )
        empty = await client.call_tool("search_paths", {"query": "absent"})
        assert empty.structured_content["items"] == []


async def test_schema_and_request_refusals(path_server: FastMCP) -> None:
    """Path schemas retain explicit bounds and deliberate field refusals."""
    async with Client(path_server) as client:
        await wait_for_mcp_writer_drain(client)
        tools = {x.name: x for x in await client.list_tools()}
        tool = tools["search_paths"]
        assert tool.input_schema["additionalProperties"] is False
        assert not {"order", "pattern_mode"} & tool.input_schema["properties"].keys()
        assert set(tool.output_schema["required"]) == {
            "items",
            "coverage",
            "reasons",
            "truncated",
            "index_stale",
        }
        assert (
            tool.annotations.read_only_hint
            and tool.annotations.idempotent_hint
            and tool.icons
        )
        for args in [
            {"query": " "},
            {"query": "x" * 257},
            {"query": "abc", "limit": True},
            {"query": "abc", "filters": {"status": "stable"}},
            {"query": "abc", "filters": {"unknown": "keep"}},
        ]:
            with pytest.raises(ToolError):
                await client.call_tool("search_paths", args)


async def test_path_fault_is_server_failure(path_server: FastMCP) -> None:
    """Unexpected decoding ValueError is not presented as a caller refusal."""
    async with Client(path_server) as client:
        await wait_for_mcp_writer_drain(client)
        with (
            patch.object(
                type(get_vault_singleton().reader),
                "search_paths",
                side_effect=ValueError("private decoding fault"),
            ),
            pytest.raises(ToolError) as exc,
        ):
            await client.call_tool("search_paths", {"query": "abc"})
        assert "private decoding fault" not in str(exc.value)


def test_reader_requires_index(tmp_path: Path) -> None:
    """Unbuilt readers never return false empty path matches."""
    vault = Vault(source_dir=tmp_path)
    try:
        with pytest.raises(IndexUnavailableError):
            vault.reader.search_paths(PathQuery(query="abc"))
    finally:
        vault.close()


async def test_stale_receipt_keeps_path_coverage(path_server: FastMCP) -> None:
    """Pending-writer freshness is distinct from approximate scan coverage."""
    async with Client(path_server) as client:
        await wait_for_mcp_writer_drain(client)
        with patch(
            "markdown_vault_mcp._tools.query._maybe_wait_for_drain", return_value=False
        ):
            reply = await client.call_tool(
                "search_paths", {"query": "target", "wait_for_pending_writes": True}
            )
        data = reply.structured_content
        assert data["index_stale"] == reply.meta["index_stale"] is True
        assert json.loads(reply.content[0].text) == data
        assert data["coverage"] == "not_run" and data["reasons"] == ["fuzzy_disabled"]
