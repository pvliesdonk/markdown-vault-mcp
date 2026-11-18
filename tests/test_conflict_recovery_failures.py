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


def test_local_delete_upstream_modify_concept_keeps_failure_outcome(
    git_repo_pair: GitRepoPair,
) -> None:
    import subprocess

    from tests.fixtures.git import _run_git

    root = git_repo_pair.local_path
    (root / "c.md").write_text("base\n")
    _run_git(root, "add", "c.md")
    _run_git(root, "commit", "-m", "seed")
    _run_git(root, "push", "origin", "main")
    other = root.parent / "other"
    subprocess.run(
        ["git", "clone", str(git_repo_pair.remote_path), str(other)],
        check=True,
        capture_output=True,
    )
    _run_git(other, "config", "user.email", "other@example.com")
    _run_git(other, "config", "user.name", "Other")
    (other / "c.md").write_text("remote edit\n")
    _run_git(other, "add", "c.md")
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    _run_git(root, "rm", "c.md")
    _run_git(root, "commit", "-m", "local delete")
    strategy, vault = recovery_vault(root)
    try:
        result = strategy.force_pull()
        assert result.reason == "conflict_resolution_failed"
        assert not result.applied
        assert (root / "c.md").read_text() == "remote edit\n"
        assert not list(root.glob("c.conflict-mcp-*.md"))
    finally:
        vault.close()
        strategy.close()


def test_unexpected_refresh_failure_retries_then_empty_reconcile_noops(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.conflict_recovery import _setup_generated_conflict

    _root, vault_root, index = _setup_generated_conflict(git_repo_pair, False)
    strategy, vault = recovery_vault(vault_root, build=True)
    assert vault._okf_convention is not None

    calls: list[list[Path]] = []

    def fail(paths: list[Path]) -> list[Path]:
        calls.append(paths)
        raise RuntimeError("unexpected refresh error")

    try:
        assert strategy.force_pull().reason == "rebased"
        with monkeypatch.context() as patch:
            patch.setattr(vault._okf_convention, "refresh_generated", fail)
            vault.reconcile_index_with_head(source="git_sync")
        assert "local.md" not in index.read_text()
        vault.reconcile_index_with_head(source="git_sync")
        assert "local.md" in index.read_text()
        assert vault._write_callback.drain(timeout=5)
        with monkeypatch.context() as patch:
            patch.setattr(vault._okf_convention, "refresh_generated", fail)
            vault.reconcile_index_with_head(source="git_sync")
        assert len(calls) == 1
        assert strategy.take_generated_indexes() == []
    finally:
        vault.close()
        strategy.close()


def test_generated_index_refuses_path_outside_vault(git_repo_pair: GitRepoPair) -> None:
    root = git_repo_pair.local_path
    outside = root.parent / "outside" / "index.md"
    outside.parent.mkdir()
    outside.write_text("authored\n")
    strategy, vault = recovery_vault(root, build=True)
    try:
        assert vault._okf_convention is not None
        assert not vault._okf_convention.generated_index(outside)
        assert vault._okf_convention.refresh_generated([outside]) == []
        assert outside.read_text() == "authored\n"
    finally:
        vault.close()
        strategy.close()
