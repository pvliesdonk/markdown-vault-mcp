"""End-to-end tests for the ``git_sync`` MCP tool (added in #444).

The tool composes :meth:`GitWriteStrategy.force_pull` and
:meth:`GitWriteStrategy.force_push` behind a single MCP call.  These
tests drive the tool through the in-memory :class:`fastmcp.Client` so we
verify both the registered shape and the wire-level response.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from markdown_vault_mcp import _server_deps
from markdown_vault_mcp.server import make_server
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


_CLEAR_VARS = (
    "MARKDOWN_VAULT_MCP_INDEX_PATH",
    "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
    "MARKDOWN_VAULT_MCP_STATE_PATH",
    "MARKDOWN_VAULT_MCP_INDEXED_FIELDS",
    "MARKDOWN_VAULT_MCP_REQUIRED_FIELDS",
    "MARKDOWN_VAULT_MCP_EXCLUDE",
    "MARKDOWN_VAULT_MCP_GIT_TOKEN",
    "MARKDOWN_VAULT_MCP_GIT_REPO_URL",
    "MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER",
    "MARKDOWN_VAULT_MCP_SERVER_NAME",
    "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
    "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
    "MARKDOWN_VAULT_MCP_AUTH_MODE",
    "MARKDOWN_VAULT_MCP_BASE_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
)


@pytest.fixture
def _git_managed_env(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Wire ``make_server`` to the bare-remote + local-clone fixture in managed mode.

    Sets ``READ_ONLY=false`` (write tools must be visible) and disables
    the periodic pull loop (the test drives sync explicitly via the
    tool).  Returns the local clone path so tests can stage commits
    against the working tree.
    """
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(git_repo_pair.local_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_GIT_REPO_URL", str(git_repo_pair.remote_path)
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "0")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "0")
    return git_repo_pair.local_path


def _seed_remote_commit(
    pair: GitRepoPair, *, clone_name: str, file_name: str, body: str
) -> None:
    """Push one new commit to the bare remote from a sibling clone.

    Mirrors the helper in ``test_git_force_methods.py`` so this module is
    self-contained — both modules use the same bare-remote fixture but
    test different layers (strategy unit tests vs. MCP tool integration).
    """
    sibling = pair.remote_path.parent / clone_name
    sibling.mkdir()
    _run_git(sibling, "init", "--initial-branch=main")
    _run_git(sibling, "config", "user.email", "other@example.com")
    _run_git(sibling, "config", "user.name", "Other")
    _run_git(sibling, "remote", "add", "origin", str(pair.remote_path))
    _run_git(sibling, "pull", "origin", "main")
    (sibling / file_name).write_text(body)
    _run_git(sibling, "add", file_name)
    _run_git(sibling, "commit", "-m", f"remote commit: {file_name}")
    _run_git(sibling, "push", "origin", "main")


def _parse_tool_data(result: Any) -> Any:
    """Extract the tool's structured payload from a ``CallToolResult``.

    FastMCP v2 returns dicts directly via ``result.data``; this helper
    falls back to parsing the raw text content when the typed accessor
    cannot resolve the shape.
    """
    data = result.data
    if isinstance(data, dict):
        return data
    raw = result.content[0].text if result.content else "{}"
    return json.loads(raw)


class TestGitSync:
    """:tool:`git_sync` integration tests through the in-memory MCP client."""

    async def test_clean_both_direction_pulls_and_pushes(
        self, git_repo_pair: GitRepoPair, _git_managed_env: Path
    ) -> None:
        """direction='both' on a clean clone runs both legs and reports applied=True."""
        # Stage one local commit so the push leg has something to send.
        (git_repo_pair.local_path / "local.md").write_text("local\n")
        _run_git(git_repo_pair.local_path, "add", "local.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "local commit")

        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("git_sync", {"direction": "both"})

        payload = _parse_tool_data(result)

        assert payload["direction"] == "both"
        assert "head_sha" in payload and len(payload["head_sha"]) == 40
        assert "branch" in payload
        assert payload["pull"] is not None
        assert payload["pull"]["applied"] is True
        assert payload["push"] is not None
        assert payload["push"]["applied"] is True
        assert payload["push"]["commits_pushed"] == 1
        # dry_run key is only present when the caller passed dry_run=True.
        assert "dry_run" not in payload

    async def test_pull_only_direction_skips_push(
        self, git_repo_pair: GitRepoPair, _git_managed_env: Path
    ) -> None:
        """direction='pull' fast-forwards the local clone and leaves push untouched."""
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_pull_only",
            file_name="seeded.md",
            body="seeded\n",
        )

        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("git_sync", {"direction": "pull"})

        payload = _parse_tool_data(result)

        assert payload["direction"] == "pull"
        assert payload["pull"] is not None
        assert payload["pull"]["applied"] is True
        assert payload["pull"]["commits_pulled"] == 1
        assert payload["push"] is None  # push leg not invoked

    async def test_dry_run_pull_does_not_modify_head(
        self, git_repo_pair: GitRepoPair, _git_managed_env: Path
    ) -> None:
        """dry_run=True predicts the would-be pull without moving HEAD."""
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_dry",
            file_name="dryseed.md",
            body="dry\n",
        )

        head_before = _run_git(git_repo_pair.local_path, "rev-parse", "HEAD").strip()

        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "git_sync", {"direction": "pull", "dry_run": True}
            )

        head_after = _run_git(git_repo_pair.local_path, "rev-parse", "HEAD").strip()
        payload = _parse_tool_data(result)

        assert head_before == head_after  # HEAD did not move
        assert payload["dry_run"] is True
        assert payload["pull"] is not None
        # In dry-run mode the response carries the projection, not actual.
        assert payload["pull"]["applied"] is False
        assert payload["pull"]["would_apply"] is True
        assert payload["pull"]["commits_pulled"] == 1
        assert not (git_repo_pair.local_path / "dryseed.md").exists()

    async def test_dry_run_pull_already_up_to_date_would_apply_false(
        self, _git_managed_env: Path
    ) -> None:
        """Refs #467: dry-run pull on an up-to-date clone reports would_apply=False."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "git_sync", {"direction": "pull", "dry_run": True}
            )

        payload = _parse_tool_data(result)

        assert payload["dry_run"] is True
        assert payload["pull"] is not None
        # force_pull early-returns applied=True for an already-up-to-date
        # clone regardless of dry_run; would_apply must be False because
        # from_sha == to_sha (no commits to apply).
        assert payload["pull"]["applied"] is True
        assert payload["pull"]["would_apply"] is False
        assert payload["pull"]["commits_pulled"] == 0
        assert payload["pull"]["from_sha"] == payload["pull"]["to_sha"]

    async def test_dry_run_both_reports_push_dry_run_unsupported(
        self, git_repo_pair: GitRepoPair, _git_managed_env: Path
    ) -> None:
        """direction='both' + dry_run=True reports push.reason='dry_run_unsupported'.

        Regression for the local-circus finding: in dry-run mode the pull
        leg returns ``applied=False`` (HEAD didn't move) which used to fire
        the "skip push when pull failed" short-circuit and return
        ``push: null``.  That made a healthy dry-run preview indistinguishable
        from a real-pull-failed-push-skipped result.  The fix excludes
        dry-run from the short-circuit so the push leg's
        ``dry_run_unsupported`` reason is preserved in the response.
        """
        # Seed one remote commit so the dry-run pull projection produces
        # ``applied=False`` for a non-trivial reason (HEAD would move on a
        # real pull, but doesn't move in dry-run mode).  Without a seeded
        # commit ``force_pull`` short-circuits with "already up to date"
        # and ``applied=True``, which doesn't exercise the regression path.
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_dry_both",
            file_name="dry_both_seed.md",
            body="dry-both\n",
        )

        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "git_sync", {"direction": "both", "dry_run": True}
            )

        payload = _parse_tool_data(result)

        assert payload["dry_run"] is True
        assert payload["pull"] is not None
        # Pull leg ran; ``applied`` is False because dry-run never moves HEAD
        # (even though ``would_apply=True``).
        assert payload["pull"]["applied"] is False
        assert payload["pull"]["would_apply"] is True
        # Push leg MUST surface even though the pull leg "failed" by the
        # applied=False signal — dry-run is special-cased.
        assert payload["push"] is not None, payload
        assert payload["push"]["applied"] is False
        assert payload["push"]["reason"] == "dry_run_unsupported", payload["push"]

    async def test_pull_conflict_returns_conflict_files(
        self, git_repo_pair: GitRepoPair, _git_managed_env: Path
    ) -> None:
        """Real two-clone conflict surfaces Syncthing-style sibling paths.

        Edits ``README.md`` differently on the local clone and on a sibling
        clone (which pushes first), then drives ``git_sync`` and asserts
        the tool reports the #232 conflict-resolution outcome:

        * ``applied=True``  — HEAD did move (rebase resolved on top of remote)
        * ``fast_forward=False`` — divergent history required rebase
        * ``reason="conflicts_resolved_with_siblings"`` — local MCP version
          was preserved as a ``.conflict-mcp-<timestamp>.md`` sibling
          rather than discarded.
        * ``conflict_files`` — non-empty, contains the README-derived sibling
          path written by ``_write_conflict_files``.
        """
        # --- Remote side: seed one commit via a sibling clone. ---
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_conflict_remote",
            file_name="README.md",
            body="# remote-edited\n",
        )

        # --- Local side: edit the same file differently and commit. ---
        (git_repo_pair.local_path / "README.md").write_text("# local-edited\n")
        _run_git(git_repo_pair.local_path, "add", "README.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "local edit")

        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("git_sync", {"direction": "pull"})

        payload = _parse_tool_data(result)

        assert payload["pull"] is not None, payload
        # Per #232 Syncthing-style resolution: rebase succeeds on top of the
        # remote, and the local version is saved as a sibling — so the pull
        # IS applied, just not as a fast-forward.
        assert payload["pull"]["applied"] is True, payload["pull"]
        assert payload["pull"]["fast_forward"] is False, payload["pull"]
        assert payload["pull"]["reason"] == "conflicts_resolved_with_siblings", payload[
            "pull"
        ]

        conflict_files = payload["pull"].get("conflict_files", [])
        # Sibling format is ``<stem>.conflict-mcp-<timestamp><ext>`` per
        # ``_write_conflict_files`` — match either the original name or the
        # Syncthing-style ``.conflict-mcp-`` marker.
        assert any("README" in f or "conflict-mcp-" in f for f in conflict_files), (
            conflict_files
        )
        # Sibling file actually exists on disk (rebase landed, files written).
        for rel in conflict_files:
            assert (git_repo_pair.local_path / rel).exists(), rel

    async def test_pull_reindex_failure_surfaces_on_payload(
        self,
        git_repo_pair: GitRepoPair,
        _git_managed_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Failed reindex after a successful pull surfaces ``reindex_failed=True``.

        Regression for ``fb524e8``: when ``IndexFacet.reindex()`` raises
        after the pull leg has already moved HEAD, the tool must not fail —
        the side-effect on disk is real and the agent needs to see it.
        Instead, the bookkeeping failure is surfaced on the pull payload as
        ``reindex_failed=True`` + ``reindex_hint`` so the caller knows the
        FTS index is stale and can recover via the standalone ``reindex``
        tool.

        The reindex callable is monkey-patched on the singleton Collection
        AFTER the lifespan has constructed it (the singleton accessor is
        the only seam that exposes the live instance to tests; FastMCP's
        ``Depends(get_collection)`` resolves through the lifespan context).
        """
        # Seed a remote commit so ``force_pull`` actually moves HEAD —
        # without it the pull short-circuits as "already up to date" and
        # the post-pull reindex branch never fires.
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_reindex_fail",
            file_name="reindex_seed.md",
            body="seed\n",
        )

        server = make_server()
        async with Client(server) as client:
            # Lifespan has set the singleton; monkey-patch reindex BEFORE
            # the tool call so the post-pull bookkeeping path raises.
            collection = _server_deps.get_collection_singleton()

            def _failing_reindex() -> None:
                raise RuntimeError("simulated reindex failure")

            monkeypatch.setattr(collection.index, "reindex", _failing_reindex)

            result = await client.call_tool("git_sync", {"direction": "pull"})

        payload = _parse_tool_data(result)

        # Pull side-effect is real — HEAD moved, files on disk.
        assert payload["pull"] is not None, payload
        assert payload["pull"]["applied"] is True, payload["pull"]
        assert payload["pull"]["commits_pulled"] == 1, payload["pull"]
        # And the bookkeeping failure surfaces on the same payload.
        assert payload["pull"]["reindex_failed"] is True, payload["pull"]
        assert "reindex_hint" in payload["pull"], payload["pull"]
        assert "stale" in payload["pull"]["reindex_hint"].lower()
        # The seeded file landed on disk despite the reindex blow-up.
        assert (git_repo_pair.local_path / "reindex_seed.md").exists()

    async def test_both_direction_skips_push_when_pull_fails(
        self,
        git_repo_pair: GitRepoPair,
        _git_managed_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """direction='both' with a real (non-dry-run) pull failure short-circuits push.

        Monkey-patches ``force_pull`` after lifespan startup to return a
        ``PullResult`` with ``applied=False`` (the strategy's managed-mode
        startup probe rejects mismatched remote URLs, so we cannot break
        ``origin`` ahead of time).  The tool must then:

        * skip the push leg entirely (``payload["push"] is None``) — pushing
          on top of an unreconciled clone would risk overwriting upstream;
        * still refresh ``head_sha`` on the response so the caller sees the
          post-sync state.

        This exercises the ``direction == 'both' and not pull_result.applied
        and not dry_run`` short-circuit (lines 1471-1475 of ``_server_tools``).
        """
        from markdown_vault_mcp.git import PullResult

        # Stage a local commit so push has *something* it could send if the
        # short-circuit didn't fire — makes the assertion meaningful.
        (git_repo_pair.local_path / "would_push.md").write_text("x\n")
        _run_git(git_repo_pair.local_path, "add", "would_push.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "would push")

        server = make_server()
        async with Client(server) as client:
            collection = _server_deps.get_collection_singleton()
            strategy = collection._git_strategy

            push_called = {"count": 0}

            def _failing_pull(*_args: object, **_kwargs: object) -> PullResult:
                head = strategy._head_sha(strategy._resolve_force_repo())
                return PullResult(
                    applied=False,
                    fast_forward=False,
                    commits_pulled=0,
                    from_sha=head,
                    to_sha=head,
                    reason="fetch_failed",
                )

            def _spy_push(*_args: object, **_kwargs: object) -> object:
                push_called["count"] += 1
                raise AssertionError("push leg must be short-circuited")

            monkeypatch.setattr(strategy, "force_pull", _failing_pull)
            monkeypatch.setattr(strategy, "force_push", _spy_push)

            result = await client.call_tool("git_sync", {"direction": "both"})

        payload = _parse_tool_data(result)

        # Pull leg ran and failed.
        assert payload["pull"] is not None, payload
        assert payload["pull"]["applied"] is False, payload["pull"]
        assert payload["pull"]["reason"] == "fetch_failed", payload["pull"]
        # Push leg short-circuited — never reached the strategy.force_push call.
        assert payload["push"] is None, payload
        assert push_called["count"] == 0
        # head_sha refreshed at the short-circuit, not at the function tail.
        assert "head_sha" in payload and len(payload["head_sha"]) == 40


class TestGitSyncVisibility:
    """Verify the ``git_sync`` tool is hidden when the deployment isn't managed.

    Composition contract: the tool is tagged ``{"write", "git-managed"}``.
    ``make_server`` runs two independent ``mcp.disable`` passes — one for
    ``"write"`` (hides on read-only) and one for ``"git-managed"`` (hides
    when the strategy is unmanaged).  The tool surfaces only when *both*
    conditions are absent (managed git mode + read-write).
    """

    async def test_visible_in_managed_mode(self, _git_managed_env: Path) -> None:
        """git_sync IS listed in managed git + read-write mode."""
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "git_sync" in names

    async def test_hidden_when_no_git_repo_url(
        self, vault_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """git_sync is NOT listed when no GIT_REPO_URL is wired (unmanaged mode)."""
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        # No GIT_REPO_URL → to_collection_kwargs() falls into the unmanaged
        # commit-only branch, which builds a strategy with managed=False.

        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "git_sync" not in names

    async def test_hidden_in_read_only_mode(
        self, git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """git_sync is NOT listed in managed mode when READ_ONLY=true.

        The ``"write"`` tag alone already hides the tool; this confirms
        the two disable passes compose without one masking the other.
        """
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_SOURCE_DIR", str(git_repo_pair.local_path)
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_GIT_REPO_URL", str(git_repo_pair.remote_path)
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "0")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "0")

        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "git_sync" not in names
