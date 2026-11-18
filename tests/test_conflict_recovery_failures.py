"""Recovery failures preserve local data and remain visible to pull callers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git import GitWriteStrategy
from tests.fixtures.conflict_recovery import recovery_vault

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


@pytest.mark.parametrize("name", ["index.md", "concept.md"])
def test_non_utf8_conflict_retains_local_bytes(
    git_repo_pair: GitRepoPair, name: str
) -> None:
    import subprocess

    from tests.fixtures.git import _run_git

    root = git_repo_pair.local_path
    note = root / name
    note.write_bytes(b"base\n")
    _run_git(root, "add", name)
    _run_git(root, "commit", "-m", "seed")
    _run_git(root, "push", "origin", "main")
    other = root.parent / "other"
    subprocess.run(
        ["git", "clone", str(git_repo_pair.remote_path), str(other)],
        check=True,
        capture_output=True,
    )
    _run_git(other, "config", "user.name", "Other")
    _run_git(other, "config", "user.email", "other@example.com")
    (other / name).write_bytes(b"remote\n")
    _run_git(other, "add", name)
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    local = b"local\xff\n"
    note.write_bytes(local)
    _run_git(root, "add", name)
    _run_git(root, "commit", "-m", "local")
    strategy = GitWriteStrategy(
        enable_pull=True, enable_push=False, repo_path=root, git_lfs=False
    )
    try:
        result = strategy.force_pull()
        if name == "concept.md":
            assert result.reason == "conflict_resolution_failed"
            assert note.read_bytes() == local
        else:
            assert result.reason == "conflicts_resolved_with_siblings"
            assert (root / result.conflict_files[0]).read_bytes() == local
    finally:
        strategy.close()


def test_deleted_pending_index_is_not_recreated_or_retried(
    git_repo_pair: GitRepoPair,
) -> None:
    root = git_repo_pair.local_path
    index = root / "index.md"
    index.write_text("---\nokf_version: 1\n---\n")
    strategy, vault = recovery_vault(root, build=True)
    try:
        strategy.retry_generated_indexes([index])
        index.unlink()
        vault.reconcile_index_with_head(source="git_sync")
        assert not index.exists()
        assert strategy.take_generated_indexes() == []
    finally:
        vault.close()
        strategy.close()


def test_no_longer_designated_index_is_not_refreshed(
    git_repo_pair: GitRepoPair,
) -> None:
    root = git_repo_pair.local_path
    index = root / "index.md"
    index.write_text("authored\n")
    strategy, vault = recovery_vault(root, authority="outside", build=True)
    try:
        assert vault._okf_convention is not None
        assert vault._okf_convention.refresh_generated([index]) == []
        assert index.read_text() == "authored\n"
    finally:
        vault.close()
        strategy.close()


def test_new_conflict_for_same_path_survives_old_recovery(git_repo: Path) -> None:
    strategy = GitWriteStrategy(
        enable_pull=True, enable_push=False, repo_path=git_repo, git_lfs=False
    )
    index = git_repo / "index.md"
    try:
        strategy.retry_generated_indexes([index])
        old_work = strategy.take_generated_indexes()
        assert old_work == [index]
        strategy.retry_generated_indexes([index])
        # Successful old work has no failed paths to retry.
        strategy.retry_generated_indexes([])
        assert strategy.take_generated_indexes() == [index]
    finally:
        strategy.close()
