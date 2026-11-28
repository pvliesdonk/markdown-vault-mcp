"""Read-only stats expose observed sync health."""

from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

from fastmcp import Client

from tests.fixtures.git import _run_git
from tests.fixtures.git_health import (
    _parse_tool_data,
    _seed_remote_commit,
    _strand_the_clone,
)
from tests.fixtures.git_health import managed_vault as managed_vault
from tests.server_factory import make_server

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest
    from fastmcp import FastMCP

    from tests.fixtures.git import GitRepoPair


@contextmanager
def _no_subprocess(monkeypatch: pytest.MonkeyPatch) -> Iterator[Mock]:
    """Fail any subprocess spawned inside the block: stats must run no git."""
    trap = Mock(side_effect=AssertionError("stats must not run a subprocess"))
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", trap)
        patch.setattr(subprocess, "Popen", trap)
        yield trap
    trap.assert_not_called()


async def _both_surfaces(client: Client[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read the ``stats`` tool and the ``stats://vault`` resource."""
    tool = _parse_tool_data(await client.call_tool("stats", {}))
    resource = await client.read_resource_mcp("stats://vault")
    return tool, json.loads(resource.contents[0].text)


async def test_stats_reports_rejected_push_until_successful_push(
    git_repo_pair: GitRepoPair,
    managed_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report a rejected push until a later successful push clears it."""
    _seed_remote_commit(git_repo_pair, clone_name="stats_sibling")
    (managed_vault / "local.md").write_text("# local\n")
    _run_git(managed_vault, "add", "local.md")
    _run_git(managed_vault, "commit", "-m", "local")

    async with Client(make_server()) as client:
        await _strand_the_clone(client)
        with _no_subprocess(monkeypatch):
            tool, resource = await _both_surfaces(client)
        assert tool["remote"]["state"] == "unsynced"
        assert tool["remote"]["reason"] == "non_fast_forward"
        assert resource["remote"] == tool["remote"]

        pull = _parse_tool_data(
            await client.call_tool("git_sync", {"direction": "pull"})
        )
        assert pull["pull"]["applied"] is True
        push = _parse_tool_data(
            await client.call_tool("git_sync", {"direction": "push"})
        )
        assert push["push"]["applied"] is True
        tool, resource = await _both_surfaces(client)
        assert "remote" not in tool
        assert "remote" not in resource


async def test_stats_reports_startup_dirt(
    managed_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read cached startup dirt on both surfaces without invoking Git."""
    (managed_vault / "leftover.md").write_text("uncommitted\n")
    async with Client(make_server()) as client:
        with _no_subprocess(monkeypatch):
            tool, resource = await _both_surfaces(client)
        assert tool["remote"]["reason"] == "uncommitted_changes"
        assert resource["remote"] == tool["remote"]


async def test_stats_without_version_control_has_no_remote(
    server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vault with no version control carries no ``remote`` key on either surface."""
    async with Client(server) as client:
        with _no_subprocess(monkeypatch):
            tool, resource = await _both_surfaces(client)
    assert "remote" not in tool
    assert "remote" not in resource
    assert "document_count" in tool
