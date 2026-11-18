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
    for name in ("edit.md", "append.md", "delete.md", "rename.md", "folder/move.md"):
        path = git_repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original\n")

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(git_repo), *args], text=True
        ).strip()

    git("add", ".")
    git("commit", "-m", "seed")
    before = int(git("rev-list", "--count", "HEAD"))
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool(
                "begin_transaction", {"description": "Mixed operations"}
            )
        ).data["handle"]
        calls = [
            ("write", {"path": "new.md", "content": "new"}),
            (
                "edit",
                {"path": "edit.md", "old_text": "original", "new_text": "changed"},
            ),
            ("append", {"path": "append.md", "content": "more"}),
            ("delete", {"path": "delete.md"}),
            ("rename", {"old_path": "rename.md", "new_path": "renamed.md"}),
            ("move_folder", {"old_dir": "folder", "new_dir": "moved"}),
        ]
        for name, arguments in calls:
            await client.call_tool(name, {**arguments, "transaction": handle})
            assert int(git("rev-list", "--count", "HEAD")) == before
        result = await client.call_tool("end_transaction", {"handle": handle})
        assert result.data["status"] == "committed"
        assert int(git("rev-list", "--count", "HEAD")) == before + 1
        assert set(
            git(
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "--no-renames",
                "-r",
                "HEAD",
            ).splitlines()
        ) == {
            "new.md",
            "edit.md",
            "append.md",
            "delete.md",
            "rename.md",
            "renamed.md",
            "folder/move.md",
            "moved/move.md",
        }
        with pytest.raises(ToolError, match="begin_transaction") as error:
            await client.call_tool(
                "append",
                {
                    "path": "missing.md",
                    "content": "x",
                    "create_if_missing": True,
                    "transaction": "unknown",
                },
            )
        assert "active writes" not in str(error.value)
