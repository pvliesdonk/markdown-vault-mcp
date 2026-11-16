"""History queries name the repository boundary instead of pretending it is empty."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.managers.git_query import GitQueryManager
from tests.fixtures.git import make_nested_repository, make_superproject

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP


@pytest.mark.parametrize("operation", ["history", "diff", "commits", "revision"])
@pytest.mark.parametrize("layout", ["submodule", "deinitialized", "nested", "subvault"])
def test_nested_history_names_boundary(
    tmp_path: Path, operation: str, layout: str
) -> None:
    """The same honest diagnostic reaches every supported history query."""
    if layout == "nested":
        source = make_nested_repository(tmp_path)
    else:
        source = make_superproject(
            tmp_path, vault="notes" if layout == "subvault" else ""
        )
    if layout == "deinitialized":
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "submodule",
                "deinit",
                "--force",
                "--quiet",
                "sub",
            ],
            check=True,
            capture_output=True,
        )
    manager = GitQueryManager(GitWriteStrategy(), source)
    with pytest.raises(
        InvalidRequestError, match=r"path 'sub/note\.md'.*repository 'sub'"
    ):
        if operation == "history":
            manager.get_history("sub/note.md")
        elif operation == "revision":
            manager.read_at_revision("sub/note.md", "abcd")
        else:
            manager.get_diff(
                "sub/note.md", since_sha="abcd", per_commit=operation == "commits"
            )


def test_scope_outside_submodule_keeps_superproject_history(tmp_path: Path) -> None:
    """Whole-vault and containing-folder queries still show gitlink changes."""
    source = make_superproject(tmp_path, sub="deep/sub")
    manager = GitQueryManager(GitWriteStrategy(), source)
    assert manager.get_history()
    assert manager.get_history("deep")
    assert manager.get_history("top.md")
    assert manager.get_history("subway.md") == []
    assert manager.get_history("deep/sub")


def test_vault_rooted_in_submodule_reads_its_own_history(tmp_path: Path) -> None:
    """A submodule used as the vault root is a normal repository."""
    source = make_superproject(tmp_path) / "sub"
    manager = GitQueryManager(GitWriteStrategy(), source)
    history = manager.get_history("note.md")
    assert history
    assert manager.get_diff("note.md", since_sha=history[0].sha) == ""
    assert manager.read_at_revision("note.md", history[0].sha).content == "# Upstream\n"


@pytest.fixture
def submodule_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """Build the read-only test server before entering an async client loop."""
    from markdown_vault_mcp.server import make_server

    source = make_superproject(tmp_path)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    return make_server()


@pytest.mark.parametrize("tool", ["get_history", "get_diff", "read"])
async def test_tool_reports_separate_history_without_server_fault(
    submodule_server: FastMCP,
    caplog: pytest.LogCaptureFixture,
    tool: str,
) -> None:
    """MCP exposes the actionable library diagnostic at INFO, not a server error."""
    import logging

    from fastmcp import Client

    arguments = {"path": "sub/note.md"}
    if tool == "get_diff":
        arguments["since_sha"] = "abcd"
    elif tool == "read":
        arguments["revision"] = "abcd"
    async with Client(submodule_server) as client:
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            result = await client.call_tool_mcp(tool, arguments)
        assert result.is_error
        assert "repository 'sub'" in result.content[0].text  # type: ignore[union-attr]
        assert not [
            record for record in caplog.records if record.levelno >= logging.ERROR
        ]
        current = await client.call_tool("read", {"path": "sub/note.md"})
        assert current.structured_content is not None
