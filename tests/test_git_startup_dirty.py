"""Startup visibility for uncommitted work, without claiming its ownership."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.git.strategy import GitWriteStrategy
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


@pytest.mark.parametrize("change", ["modified", "staged", "deleted", "untracked"])
@pytest.mark.parametrize("managed", [False, True])
def test_startup_reports_but_preserves_uncommitted_work(
    git_repo_pair: GitRepoPair,
    caplog: pytest.LogCaptureFixture,
    change: str,
    managed: bool,
) -> None:
    """A restarted strategy reports the on-disk crash shape before any write."""
    repo = git_repo_pair.local_path
    note = repo / "README.md"
    if change == "deleted":
        note.unlink()
    elif change == "untracked":
        (repo / "new.md").write_text("not committed\n")
    else:
        note.write_text("not committed\n")
        if change == "staged":
            _run_git(repo, "add", "README.md")
    before = _run_git(repo, "status", "--porcelain=v1", "-z")
    head = _run_git(repo, "rev-parse", "HEAD")
    index = _run_git(repo, "diff", "--cached")

    with caplog.at_level(logging.WARNING):
        strategy = GitWriteStrategy(
            repo_path=repo,
            repo_url=str(git_repo_pair.remote_path) if managed else None,
            managed=managed,
            enable_pull=managed,
            enable_push=False,
            git_lfs=False,
        )
    try:
        health = strategy.sync_health()
        assert health is not None
        assert health.reason == "uncommitted_changes"
        assert "not automatically committed" in health.as_payload()["detail"]
        assert "git_worktree_dirty" in caplog.text
        assert _run_git(repo, "status", "--porcelain=v1", "-z") == before
        assert _run_git(repo, "rev-parse", "HEAD") == head
        assert _run_git(repo, "diff", "--cached") == index
    finally:
        strategy.close()


@pytest.mark.parametrize("entry", ["constructor", "start", "sync", "closed"])
def test_startup_entry_points_check_only_an_open_strategy(
    git_repo_pair: GitRepoPair, entry: str
) -> None:
    """Open lifecycle entry points inspect even without a usable remote."""
    repo = git_repo_pair.local_path
    _run_git(repo, "remote", "remove", "origin")
    (repo / "README.md").write_text("pending\n")
    strategy = GitWriteStrategy(
        repo_path=repo if entry == "constructor" else None,
        enable_push=False,
        git_lfs=False,
    )
    try:
        if entry == "closed":
            strategy.close()
            with patch("subprocess.run", side_effect=AssertionError("closed git")):
                strategy.start(repo_path=repo, pull_interval_s=600)
            assert strategy.sync_health() is None
            return
        if entry == "start":
            strategy.start(repo_path=repo, pull_interval_s=0)
        elif entry == "sync":
            strategy.sync_once(repo)
        assert strategy.sync_health() is not None
    finally:
        strategy.close()


@pytest.mark.parametrize("with_git", [False, True])
def test_clean_or_non_git_startup_stays_quiet(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    with_git: bool,
) -> None:
    """Ignored files do not create a warning, nor does a plain-directory vault."""
    repo = git_repo_pair.local_path if with_git else tmp_path
    if with_git:
        (repo / ".git" / "info" / "exclude").write_text("ignored.txt\n")
        (repo / "ignored.txt").write_text("ignored\n")
    strategy = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    try:
        assert strategy.sync_health() is None
        assert "git_worktree_dirty" not in caplog.text
        if with_git:
            # Normal queued writes after a clean startup must not open a
            # startup condition while another path is still pending.
            (repo / "pending.md").write_text("pending\n")
            (repo / "written.md").write_text("written\n")
            strategy(repo / "written.md", "written\n", "write")
            assert strategy.sync_health() is None
    finally:
        strategy.close()


@pytest.mark.parametrize(
    "repair", ["commit", "batch", "external_pull", "external_push"]
)
def test_startup_dirt_clears_only_after_observing_a_clean_tree(
    git_repo_pair: GitRepoPair, caplog: pytest.LogCaptureFixture, repair: str
) -> None:
    """Success at the remote or on one path does not hide another dirty path."""
    repo = git_repo_pair.local_path
    (repo / "README.md").write_text("pending\n")
    (repo / "other.md").write_text("other pending\n")
    strategy = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    try:
        first = strategy.sync_health()
        assert first is not None
        assert strategy.force_pull().applied
        assert strategy.sync_health() == first
        assert strategy.force_push().applied
        assert strategy.sync_health() == first
        # One committed path is not the whole dirty tree.
        strategy(repo / "README.md", "pending\n", "write")
        assert strategy.sync_health() == first
        with caplog.at_level(logging.INFO):
            if repair == "commit":
                strategy(repo / "other.md", "other pending\n", "write")
            elif repair == "batch":
                strategy.on_write_batch(
                    [(repo / "other.md", "write", None, None)], "write"
                )
            else:
                _run_git(repo, "add", "other.md")
                _run_git(repo, "commit", "-m", "operator recovery")
                assert strategy.sync_health() == first  # cached, not a subprocess
                if repair == "external_push":
                    strategy.force_push()
                else:
                    strategy.sync_once(repo)
        assert strategy.sync_health() is None
        assert caplog.text.count("git_worktree_dirty") == 1
        assert caplog.text.count("git_worktree_clean") == 1
    finally:
        strategy.close()


@pytest.mark.parametrize("dirt_clears_first", [False, True])
@pytest.mark.parametrize("last_remote", ["push", "pull"])
def test_worktree_and_remote_recovery_are_reported_independently(
    caplog: pytest.LogCaptureFixture, dirt_clears_first: bool, last_remote: str
) -> None:
    """Each condition logs recovery once, whichever one remains outstanding."""
    from markdown_vault_mcp.git.health import SyncHealthTracker

    tracker = SyncHealthTracker()
    with caplog.at_level(logging.INFO):
        tracker.worktree_observed(dirty=True)
        dirty = tracker.snapshot()
        assert dirty is not None
        tracker.push_failed("push_failed")
        tracker.pull_failed("conflict_resolution_failed")
        assert tracker.snapshot() == dirty
        if dirt_clears_first:
            tracker.worktree_observed(dirty=False)
            remote = tracker.snapshot()
            assert remote is not None
            assert remote.reason == "push_failed"
            assert remote.since == dirty.since

        first_success = (
            tracker.pull_succeeded if last_remote == "push" else tracker.push_succeeded
        )
        last_success = (
            tracker.push_succeeded if last_remote == "push" else tracker.pull_succeeded
        )
        first_success()
        assert "git_remote_resynced" not in caplog.text
        last_success()
        assert caplog.text.count("git_remote_resynced") == 1
        if not dirt_clears_first:
            assert tracker.snapshot() == dirty
            tracker.worktree_observed(dirty=False)
        assert tracker.snapshot() is None

        # Repeated clean observations are not new recovery transitions.
        tracker.push_succeeded()
        tracker.pull_succeeded()
        tracker.worktree_observed(dirty=False)
        assert caplog.text.count("git_remote_unsynced") == 1
        assert caplog.text.count("git_remote_resynced") == 1
        assert caplog.text.count("git_worktree_clean") == 1


def test_failed_status_does_not_clear_dirty_health(
    git_repo_pair: GitRepoPair,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed or timed-out observation is not evidence that work was saved."""
    import subprocess

    repo = git_repo_pair.local_path
    (repo / "README.md").write_text("pending\n")
    strategy = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    dirty = strategy.sync_health()
    try:
        for failure in (
            OSError("private detail"),
            subprocess.TimeoutExpired("git", 10),
            subprocess.CalledProcessError(1, "git", stderr="private detail"),
        ):
            with patch("subprocess.run", side_effect=failure):
                with strategy._lock:
                    strategy._observe_worktree(repo)
                assert strategy.sync_health() == dirty
        assert "git_worktree_check_failed" in caplog.text
        assert "private detail" not in caplog.text
    finally:
        strategy.close()


def test_subdirectory_vault_reports_changes_in_its_repository(
    git_repo_pair: GitRepoPair,
) -> None:
    """Sync affects the full repository, not just the configured vault folder."""
    repo = git_repo_pair.local_path
    source = repo / "notes"
    source.mkdir()
    (repo / "README.md").write_text("operator edit outside vault\n")
    strategy = GitWriteStrategy(repo_path=source, enable_push=False, git_lfs=False)
    try:
        assert strategy.sync_health() is not None
    finally:
        strategy.close()
