"""Read-only stats expose observed Git sync health."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from markdown_vault_mcp import domain
from tests.fixtures.git import _run_git
from tests.server_factory import make_server
from tests.test_write_remote_health import (
    _parse_tool_data,
    _seed_remote_commit,
    _strand_the_clone,
)
from tests.test_write_remote_health import managed_vault as managed_vault

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


async def test_stats_reports_rejected_push_until_successful_push(
    git_repo_pair: GitRepoPair, managed_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a rejected push until a later successful push clears it."""
    _seed_remote_commit(git_repo_pair, clone_name="stats_sibling")
    (managed_vault / "local.md").write_text("# local\n")
    _run_git(managed_vault, "add", "local.md")
    _run_git(managed_vault, "commit", "-m", "local")

    async with Client(make_server()) as client:
        await _strand_the_clone(client)
        with monkeypatch.context() as patch:

            def no_subprocess(*_args: Any, **_kwargs: Any) -> Any:
                pytest.fail("polling stats must not run a subprocess")

            patch.setattr(subprocess, "run", no_subprocess)
            first_result = await client.call_tool("stats", {})
            first = _parse_tool_data(first_result)
            second = _parse_tool_data(await client.call_tool("stats", {}))
        assert first["remote"]["state"] == "unsynced"
        assert first["remote"]["reason"] == "non_fast_forward"
        assert first["remote"] == second["remote"]
        assert first["remote"]["since"]
        assert isinstance(first_result.meta["index_stale"], bool)

        pull = _parse_tool_data(
            await client.call_tool("git_sync", {"direction": "pull"})
        )
        assert pull["pull"]["applied"] is True
        assert (
            _parse_tool_data(await client.call_tool("stats", {}))["remote"]
            == first["remote"]
        )
        push = _parse_tool_data(
            await client.call_tool("git_sync", {"direction": "push"})
        )
        assert push["push"]["applied"] is True
        assert "remote" not in _parse_tool_data(await client.call_tool("stats", {}))


@pytest.mark.usefixtures("managed_vault")
async def test_stats_omits_remote_for_healthy_and_unsupported_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omit the warning for healthy and unsupported Git strategies."""
    async with Client(make_server()) as client:
        assert "remote" not in _parse_tool_data(await client.call_tool("stats", {}))
        vault = domain.get_vault_singleton()
        with monkeypatch.context() as patch:
            patch.setattr(vault, "_git_strategy", object())
            assert "remote" not in _parse_tool_data(await client.call_tool("stats", {}))


async def test_stats_omits_remote_without_git(client: Client[Any]) -> None:
    """Omit the warning when the vault has no Git remote."""
    assert "remote" not in _parse_tool_data(await client.call_tool("stats", {}))


async def test_stats_reports_startup_dirt(managed_vault: Path) -> None:
    """Report uncommitted changes observed at startup."""
    (managed_vault / "leftover.md").write_text("uncommitted\n")
    async with Client(make_server()) as client:
        payload = _parse_tool_data(await client.call_tool("stats", {}))
        assert payload["remote"]["reason"] == "uncommitted_changes"
