"""Shared fixtures and helpers for Git health tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.git.types import PUSH_REASON_NON_FAST_FORWARD
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import Client

    from tests.fixtures.git import GitRepoPair


def _parse_tool_data(result: Any) -> Any:
    """Extract the tool's structured payload from a ``CallToolResult``."""
    data = result.data
    if isinstance(data, dict):
        return data
    raw = result.content[0].text if result.content else "{}"
    return json.loads(raw)


def _seed_remote_commit(pair: GitRepoPair, *, clone_name: str) -> None:
    """Push one commit to the bare remote from a sibling clone.

    Puts the server's clone behind its remote, so its next push is rejected
    as non-fast-forward — the divergence the reporter hit.
    """
    sibling = pair.remote_path.parent / clone_name
    sibling.mkdir()
    _run_git(sibling, "init", "--initial-branch=main")
    _run_git(sibling, "config", "user.email", "other@example.com")
    _run_git(sibling, "config", "user.name", "Other")
    _run_git(sibling, "remote", "add", "origin", str(pair.remote_path))
    _run_git(sibling, "pull", "origin", "main")
    (sibling / "remote.md").write_text("# remote\n")
    _run_git(sibling, "add", "remote.md")
    _run_git(sibling, "commit", "-m", "remote commit")
    _run_git(sibling, "push", "origin", "main")


@pytest.fixture
def managed_vault(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch, _clear_env: None
) -> Path:
    """A writable managed-git vault with the periodic loops out of the way."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(git_repo_pair.local_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_GIT_REPO_URL", str(git_repo_pair.remote_path)
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "0")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "0")
    return git_repo_pair.local_path


async def _strand_the_clone(client: Client[Any]) -> None:
    """Drive the clone into the unsynced state through a real rejected push."""
    result = await client.call_tool("git_sync", {"direction": "push"})
    payload = _parse_tool_data(result)
    assert payload["push"]["reason"] == PUSH_REASON_NON_FAST_FORWARD
