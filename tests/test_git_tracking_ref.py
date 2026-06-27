"""Tests for tracking-independent remote-ref resolution.

Managed-git sync resolves the remote-tracking ref as ``origin/<branch>``
(:func:`markdown_vault_mcp.git._run.resolve_tracking_ref`) rather than the
branch's configured upstream (``@{upstream}``).  A managed clone may be a
detached or ``git clone --branch`` checkout that never had upstream tracking
configured; these tests pin the behaviour for the normal, non-tracking,
detached, and no-remote cases, plus an end-to-end ``force_pull`` on a clone
whose upstream tracking has been unset.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from markdown_vault_mcp.git._run import resolve_tracking_ref
from tests.fixtures.git import _run_git
from tests.test_git_force_methods import _seed_remote_commit

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


class TestResolveTrackingRef:
    """:func:`resolve_tracking_ref` derives ``origin/<branch>`` locally."""

    def test_normal_clone_returns_origin_branch(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """A clone on ``main`` resolves to ``origin/main``."""
        assert resolve_tracking_ref(git_repo_pair.local_path) == "origin/main"

    def test_non_tracking_checkout_still_resolves(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """Resolution works even with no ``@{upstream}`` tracking configured.

        This is the core of the fix: unsetting the upstream makes
        ``@{upstream}`` fail, but ``origin/main`` still exists and is
        derived from the current branch name.
        """
        _run_git(git_repo_pair.local_path, "branch", "--unset-upstream")
        # Sanity: @{upstream} no longer resolves.
        upstream = subprocess.run(
            ["git", "-C", str(git_repo_pair.local_path), "rev-parse", "@{upstream}"],
            capture_output=True,
            text=True,
        )
        assert upstream.returncode != 0

        assert resolve_tracking_ref(git_repo_pair.local_path) == "origin/main"

    def test_detached_head_falls_back_to_origin_head(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """A detached HEAD with no current branch falls back to ``origin/HEAD``."""
        # origin/HEAD is not set by the manual-clone fixture; establish it.
        _run_git(git_repo_pair.local_path, "remote", "set-head", "origin", "main")
        head_sha = _run_git(git_repo_pair.local_path, "rev-parse", "HEAD").strip()
        _run_git(git_repo_pair.local_path, "checkout", head_sha)

        assert resolve_tracking_ref(git_repo_pair.local_path) == "origin/HEAD"

    def test_no_remote_returns_none(self, tmp_path: Path) -> None:
        """A repo with a commit but no ``origin`` resolves to ``None``."""
        assert resolve_tracking_ref(_no_remote_repo(tmp_path)) is None


def _no_remote_repo(tmp_path: Path) -> Path:
    """Create a repo with one commit on ``main`` and no ``origin`` remote."""
    repo = tmp_path / "no_remote"
    repo.mkdir()
    _run_git(repo, "init", "--initial-branch=main")
    _run_git(repo, "config", "user.email", "t@example.com")
    _run_git(repo, "config", "user.name", "T")
    (repo / "f.md").write_text("x\n")
    _run_git(repo, "add", "f.md")
    _run_git(repo, "commit", "-m", "c")
    return repo


class TestNoRemoteRefGuards:
    """Sync entry points bail out when no remote-tracking ref resolves."""

    def test_sync_once_skips_without_remote_ref(self, tmp_path: Path) -> None:
        """:meth:`sync_once` returns ``False`` when ``origin/<branch>`` is absent."""
        from markdown_vault_mcp.git import GitWriteStrategy

        repo = _no_remote_repo(tmp_path)
        strategy = GitWriteStrategy(enable_pull=True, enable_push=False, repo_path=repo)
        assert strategy.sync_once(repo) is False

    def test_start_disables_loop_without_remote_ref(self, tmp_path: Path) -> None:
        """:meth:`start` does not spin up the pull thread without a remote ref."""
        from markdown_vault_mcp.git import GitWriteStrategy

        repo = _no_remote_repo(tmp_path)
        strategy = GitWriteStrategy(enable_pull=True, enable_push=False, repo_path=repo)
        strategy.start(repo_path=repo, pull_interval_s=600)
        assert strategy._pull_thread is None


class TestForcePullWithoutTracking:
    """End-to-end: ``force_pull`` works without ``@{upstream}`` configured."""

    def test_force_pull_pulls_on_non_tracking_checkout(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """A clean ff pull succeeds even after the upstream tracking is unset."""
        from markdown_vault_mcp.git import GitWriteStrategy

        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_no_track",
            file_name="new.md",
            body="from remote\n",
        )
        _run_git(git_repo_pair.local_path, "branch", "--unset-upstream")

        strategy = GitWriteStrategy(
            enable_pull=True,
            enable_push=False,
            repo_path=git_repo_pair.local_path,
        )
        result = strategy.force_pull()

        assert result.applied is True
        assert result.fast_forward is True
        assert result.commits_pulled == 1
        assert (git_repo_pair.local_path / "new.md").exists()

    def test_force_pull_rebases_on_non_tracking_checkout(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """Divergent histories rebase via the derived ref without ``@{upstream}``.

        The rebase fallback targets the resolved ``origin/<branch>`` ref, not
        ``@{upstream}``.  This drives ``git rebase origin/main`` on a clone
        whose upstream tracking has been unset — the exact path the fix exists
        for, which the fast-forward case above never reaches.
        """
        from markdown_vault_mcp.git import (
            PULL_REASON_REBASED,
            GitWriteStrategy,
        )

        # Remote advances on file A; local commits a different file B.
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_nt_rebase",
            file_name="remote_only.md",
            body="remote\n",
        )
        (git_repo_pair.local_path / "local_only.md").write_text("local\n")
        _run_git(git_repo_pair.local_path, "add", "local_only.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "local divergent")
        _run_git(git_repo_pair.local_path, "branch", "--unset-upstream")

        strategy = GitWriteStrategy(
            enable_pull=True,
            enable_push=False,
            repo_path=git_repo_pair.local_path,
        )
        result = strategy.force_pull()

        assert result.applied is True
        assert result.fast_forward is False
        assert result.reason == PULL_REASON_REBASED
        assert (git_repo_pair.local_path / "remote_only.md").exists()
        assert (git_repo_pair.local_path / "local_only.md").exists()

    def test_force_pull_resolves_conflict_with_siblings_on_non_tracking_checkout(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """Same-file divergence resolves to siblings via the derived ref.

        Drives the rebase fallback's conflict-resolution branch (rebase onto
        ``origin/main`` then Syncthing-style sibling write) on a clone with
        upstream tracking unset, so the ``ref``-threaded rebase target is
        exercised end-to-end on the non-tracking conflict path.
        """
        from markdown_vault_mcp.git import (
            PULL_REASON_CONFLICTS_RESOLVED_WITH_SIBLINGS,
            GitWriteStrategy,
        )

        # Remote and local edit the SAME file differently → rebase conflict.
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_nt_conflict",
            file_name="README.md",
            body="# remote-edited\n",
        )
        (git_repo_pair.local_path / "README.md").write_text("# local-edited\n")
        _run_git(git_repo_pair.local_path, "add", "README.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "local edit")
        _run_git(git_repo_pair.local_path, "branch", "--unset-upstream")

        strategy = GitWriteStrategy(
            enable_pull=True,
            enable_push=False,
            repo_path=git_repo_pair.local_path,
        )
        result = strategy.force_pull()

        assert result.applied is True
        assert result.fast_forward is False
        assert result.reason == PULL_REASON_CONFLICTS_RESOLVED_WITH_SIBLINGS
        assert result.conflict_files
        for rel in result.conflict_files:
            assert (git_repo_pair.local_path / rel).exists(), rel
