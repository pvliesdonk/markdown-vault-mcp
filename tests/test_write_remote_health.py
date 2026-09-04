"""Write tools report a clone that is not reaching its remote (#1287).

The incident these tests pin: with pushes failing, ``write`` returned
``created: true`` and ``read`` served the content back, so an agent whose
only route to the repository is this server believed it had saved work that
never left the host.  The write path now says so on its own result.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from fastmcp import Client

from markdown_vault_mcp._server_tools._common import attach_remote_health
from markdown_vault_mcp._write_tools import WRITE_TOOL_NAMES
from markdown_vault_mcp.git.types import (
    PUSH_REASON_NON_FAST_FORWARD,
    REMOTE_STATE_UNSYNCED,
)
from tests.fixtures.git import _run_git
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.vault import Vault
    from tests.fixtures.git import GitRepoPair


#: Write tools whose result carries the sync-health key, driven end to end
#: by the tests below.
_DRIVEN = (
    "write",
    "edit",
    "append",
    "delete",
    "rename",
    "move_folder",
)

#: Write tools that carry the key but are not driven here: ``fetch`` needs a
#: network round-trip, and the OKF tools need OKF mode plus (for
#: ``okf_verify``) an authenticated principal.
_ATTACHING_NOT_DRIVEN = (
    "fetch",
    "okf_convert_links",
    "okf_generate_index",
    "okf_seed_log",
    "okf_verify",
)

#: Write tools that deliberately do not carry it. ``git_sync`` reports the
#: sync outcome directly — it is the tool an operator calls to find out — and
#: ``create_upload_link`` hands back a URL, with the writes it enables landing
#: through the upload route rather than in its own response.
_EXEMPT = ("git_sync", "create_upload_link")


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


class TestUnsyncedCloneIsReported:
    """A write onto a clone that cannot reach its remote says so."""

    async def test_write_reports_the_unsynced_remote(
        self, git_repo_pair: GitRepoPair, managed_vault: Path
    ) -> None:
        """The key the caller reacts to, on the result of its own write."""
        _seed_remote_commit(git_repo_pair, clone_name="clone_write")
        (managed_vault / "local.md").write_text("# local\n")
        _run_git(managed_vault, "add", "local.md")
        _run_git(managed_vault, "commit", "-m", "local")

        server = make_server()
        async with Client(server) as client:
            await _strand_the_clone(client)
            result = await client.call_tool(
                "write", {"path": "note.md", "content": "body"}
            )

        payload = _parse_tool_data(result)
        assert payload["created"] is True
        assert payload["remote"]["state"] == REMOTE_STATE_UNSYNCED
        assert payload["remote"]["reason"] == PUSH_REASON_NON_FAST_FORWARD
        assert "committed locally" in payload["remote"]["detail"]

    @pytest.mark.usefixtures("managed_vault")
    async def test_a_healthy_clone_says_nothing(self) -> None:
        """Absent means "nothing to report" — no key on every ordinary write."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write", {"path": "note.md", "content": "body"}
            )

        assert "remote" not in _parse_tool_data(result)

    async def test_a_vault_with_no_remote_says_nothing(
        self, client: Client[Any]
    ) -> None:
        """A commit-only or non-git vault has no remote to be unsynced from."""
        result = await client.call_tool("write", {"path": "note.md", "content": "body"})

        assert "remote" not in _parse_tool_data(result)

    @pytest.mark.parametrize("tool_name", _DRIVEN)
    async def test_every_write_tool_reports_it(
        self, tool_name: str, git_repo_pair: GitRepoPair, managed_vault: Path
    ) -> None:
        """One stranded write is as invisible as another, whichever tool made it."""
        _seed_remote_commit(git_repo_pair, clone_name=f"clone_{tool_name}")
        (managed_vault / "local.md").write_text("# local\n")
        _run_git(managed_vault, "add", "local.md")
        _run_git(managed_vault, "commit", "-m", "local")
        (managed_vault / "folder").mkdir()
        (managed_vault / "folder" / "subject.md").write_text("# subject\nbody\n")

        arguments: dict[str, dict[str, Any]] = {
            "write": {"path": "folder/subject.md", "content": "replaced"},
            "edit": {
                "path": "folder/subject.md",
                "old_text": "body",
                "new_text": "edited",
            },
            "append": {"path": "folder/subject.md", "content": "more"},
            "delete": {"path": "folder/subject.md"},
            "rename": {
                "old_path": "folder/subject.md",
                "new_path": "folder/renamed.md",
            },
            "move_folder": {"old_dir": "folder", "new_dir": "moved"},
        }

        server = make_server()
        async with Client(server) as client:
            await _strand_the_clone(client)
            result = await client.call_tool(tool_name, arguments[tool_name])

        payload = _parse_tool_data(result)
        assert payload["remote"]["state"] == REMOTE_STATE_UNSYNCED


class TestAttachIsSafeOnAnyStore:
    """The helper answers for stores that cannot report health at all."""

    def test_a_store_that_cannot_report_health_adds_nothing(self) -> None:
        """``Vault`` accepts no git strategy at all, and library callers use that."""
        vault = cast("Vault", SimpleNamespace(_git_strategy=None))

        assert attach_remote_health(vault, {"path": "note.md"}) == {"path": "note.md"}


class TestEveryWriteToolIsClassified:
    """A new write tool cannot quietly skip the signal (#1287)."""

    def test_the_registry_is_partitioned(self) -> None:
        """Every write-tagged tool either carries the key or is exempt on purpose.

        The three groups are the test's own bookkeeping; what makes this
        binding is that they must cover
        :data:`~markdown_vault_mcp._write_tools.WRITE_TOOL_NAMES` exactly, so
        adding a write tool forces a decision about it here.
        """
        classified = (*_DRIVEN, *_ATTACHING_NOT_DRIVEN, *_EXEMPT)

        assert len(classified) == len(set(classified))
        assert set(classified) == set(WRITE_TOOL_NAMES)
