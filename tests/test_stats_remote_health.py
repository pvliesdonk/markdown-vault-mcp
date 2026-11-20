"""Read-only stats expose observed Git sync health."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING
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
    from pathlib import Path

    import pytest

    from tests.fixtures.git import GitRepoPair


async def test_stats_reports_rejected_push_until_successful_push(
    git_repo_pair: GitRepoPair, managed_vault: Path
) -> None:
    """Report a rejected push until a later successful push clears it."""
    _seed_remote_commit(git_repo_pair, clone_name="stats_sibling")
    (managed_vault / "local.md").write_text("# local\n")
    _run_git(managed_vault, "add", "local.md")
    _run_git(managed_vault, "commit", "-m", "local")

    async with Client(make_server()) as client:
        await _strand_the_clone(client)
        first = _parse_tool_data(await client.call_tool("stats", {}))
        assert first["remote"]["state"] == "unsynced"
        assert first["remote"]["reason"] == "non_fast_forward"
        resource = await client.read_resource_mcp("stats://vault")
        assert json.loads(resource.contents[0].text)["remote"] == first["remote"]

        pull = _parse_tool_data(
            await client.call_tool("git_sync", {"direction": "pull"})
        )
        assert pull["pull"]["applied"] is True
        push = _parse_tool_data(
            await client.call_tool("git_sync", {"direction": "push"})
        )
        assert push["push"]["applied"] is True
        assert "remote" not in _parse_tool_data(await client.call_tool("stats", {}))
        resource = await client.read_resource_mcp("stats://vault")
        assert "remote" not in json.loads(resource.contents[0].text)


async def test_stats_reports_startup_dirt(
    managed_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read cached startup dirt on both surfaces without invoking Git."""
    (managed_vault / "leftover.md").write_text("uncommitted\n")
    async with Client(make_server()) as client:
        trap = Mock(side_effect=AssertionError("stats must not run a subprocess"))
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "run", trap)
            payload = _parse_tool_data(await client.call_tool("stats", {}))
            resource = await client.read_resource_mcp("stats://vault")
        trap.assert_not_called()
        assert payload["remote"]["reason"] == "uncommitted_changes"
        assert json.loads(resource.contents[0].text)["remote"] == payload["remote"]
