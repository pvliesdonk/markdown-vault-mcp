"""Transactions group explicit MCP writes while ordinary writes commit alone."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.usefixtures("_mcp_env")
async def test_transaction_groups_writes_and_rejects_closed_handle(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(git_repo))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")

    def subject() -> str:
        return subprocess.check_output(
            ["git", "-C", str(git_repo), "log", "-1", "--format=%s"], text=True
        ).strip()

    async with Client(make_server()) as client:
        started = await client.call_tool(
            "begin_transaction", {"description": "Three notes"}
        )
        handle = started.data["handle"]
        before = subject()
        for name in ("a", "b", "c"):
            await client.call_tool(
                "write", {"path": f"{name}.md", "content": name, "transaction": handle}
            )
        assert subject() == before
        ended = await client.call_tool("end_transaction", {"handle": handle})
        assert ended.data["status"] == "committed"
        assert ended.data["files"] == 3
        assert subject() == "Three notes"
        with pytest.raises(ToolError):
            await client.call_tool(
                "write", {"path": "d.md", "content": "d", "transaction": handle}
            )
        await client.call_tool("write", {"path": "alone.md", "content": "alone"})
        assert subject() == "write: alone.md"


@pytest.mark.usefixtures("_mcp_env")
async def test_transaction_tools_hidden_in_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert not {"begin_transaction", "end_transaction"} & names


@pytest.mark.usefixtures("_mcp_env")
async def test_all_write_operations_accept_transaction(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(git_repo))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool(
                "begin_transaction", {"description": "Mixed operations"}
            )
        ).data["handle"]
        await client.call_tool(
            "write", {"path": "folder/a.md", "content": "first", "transaction": handle}
        )
        await client.call_tool(
            "edit",
            {
                "path": "folder/a.md",
                "old_text": "first",
                "new_text": "second",
                "transaction": handle,
            },
        )
        await client.call_tool(
            "append", {"path": "folder/a.md", "content": "third", "transaction": handle}
        )
        await client.call_tool(
            "rename",
            {
                "old_path": "folder/a.md",
                "new_path": "folder/b.md",
                "transaction": handle,
            },
        )
        await client.call_tool(
            "move_folder",
            {"old_dir": "folder", "new_dir": "moved", "transaction": handle},
        )
        await client.call_tool("delete", {"path": "moved/b.md", "transaction": handle})
        result = await client.call_tool("end_transaction", {"handle": handle})
        assert result.data["status"] == "no_changes"
        with pytest.raises(ToolError, match="begin_transaction"):
            await client.call_tool(
                "append",
                {
                    "path": "missing.md",
                    "content": "x",
                    "create_if_missing": True,
                    "transaction": "unknown",
                },
            )
        assert not (git_repo / "missing.md").is_file()
