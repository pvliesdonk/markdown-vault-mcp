"""Recovery failures preserve local data and remain visible to pull callers."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._tools.git import _run_pull_leg
from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.git import GitWriteStrategy, conflict
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.test_conflict_reserved_files import (
    _setup_generated_conflict,
    _setup_multiple_conflicts,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


def _vault(root: Path) -> tuple[GitWriteStrategy, Vault]:
    strategy = GitWriteStrategy(
        enable_pull=True, enable_push=False, repo_path=root, git_lfs=False
    )
    caps = ContentConfig(okf_mode="on", okf_index=True, okf_log="off").okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
        git_strategy=strategy,
        on_write=strategy,
    )
    vault.index.build_index()
    return strategy, vault


def test_marker_write_failure_preserves_all_local_conflicts(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _index, _concept = _setup_multiple_conflicts(git_repo_pair)
    strategy, vault = _vault(root)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    try:
        monkeypatch.setattr(conflict, "_write_generated_indexes", fail)
        result = strategy.force_pull()
        assert result.reason == "conflicts_resolved_with_siblings"
        assert list(root.glob("concept.conflict-mcp-*.md"))
        assert list(root.glob("index.conflict-mcp-*.md"))
    finally:
        vault.close()
        strategy.close()


def test_generation_failure_reaches_pull_result(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, _ = _setup_generated_conflict(git_repo_pair, False)
    strategy, vault = _vault(root)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("generation unavailable")

    try:
        monkeypatch.setattr(vault._okf_migrate, "generate_index", fail)
        result = asyncio.run(_run_pull_leg(strategy, vault, dry_run=False))
        assert result["reindex_failed"] is True
        assert (root / ".git/markdown-vault-pending-indexes.json").exists()
    finally:
        vault.close()
        strategy.close()


def test_failed_commit_keeps_marker_and_retries(git_repo_pair: GitRepoPair) -> None:
    root, _, index = _setup_generated_conflict(git_repo_pair, False)
    strategy, vault = _vault(root)
    callback = vault._write_callback._on_write

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("commit unavailable")

    try:
        vault._write_callback._on_write = fail
        result = asyncio.run(_run_pull_leg(strategy, vault, dry_run=False))
        assert result["reindex_failed"] is True
        marker = root / ".git/markdown-vault-pending-indexes.json"
        assert marker.exists()
        vault._write_callback._on_write = callback
        assert vault.reconcile_index_with_head(source="git_sync") in (
            "current",
            "reindexed",
        )
        assert not marker.exists()
        assert "local.md" in index.read_text()
    finally:
        vault.close()
        strategy.close()


def test_unknown_marker_version_is_not_overwritten(git_repo_pair: GitRepoPair) -> None:
    root = git_repo_pair.local_path
    marker = root / ".git/markdown-vault-pending-indexes.json"
    original = json.dumps({"version": 99, "paths": ["some/index.md"]})
    marker.write_text(original)
    with pytest.raises(ValueError):
        conflict.record_generated_indexes(root, ["index.md"])
    assert marker.read_text() == original


def test_outside_pending_index_does_not_starve_current_vault(
    git_repo_pair: GitRepoPair,
) -> None:
    root, vault_root, index = _setup_generated_conflict(git_repo_pair, True)
    strategy, vault = _vault(vault_root)
    try:
        strategy.force_pull()
        conflict.record_generated_indexes(root, ["elsewhere/index.md"])
        vault.reconcile_index_with_head(source="git_sync")
        assert "local.md" in index.read_text() and "remote.md" in index.read_text()
        assert "vault/index.md" not in conflict.read_generated_indexes(root)
    finally:
        vault.close()
        strategy.close()


@pytest.mark.parametrize("name", ["log.md", "index.md", "concept.md"])
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


def test_pending_marker_git_directory_with_trailing_space(git_repo: Path) -> None:
    gitdir = git_repo.parent / "git metadata "
    (git_repo / ".git").rename(gitdir)
    (git_repo / ".git").write_text(f"gitdir: {gitdir}\n")
    conflict.record_generated_indexes(git_repo, ["index.md"])
    assert (gitdir / "markdown-vault-pending-indexes.json").exists()


@pytest.mark.parametrize("damaged", ['["index.md",', '["../outside/index.md"]'])
def test_invalid_marker_is_never_replaced(git_repo: Path, damaged: str) -> None:
    marker = git_repo / ".git/markdown-vault-pending-indexes.json"
    marker.write_text(damaged)
    with pytest.raises(ValueError):
        conflict.record_generated_indexes(git_repo, ["index.md"])
    assert marker.read_text() == damaged


def test_disabled_policy_settles_without_rewriting_authored_index(
    git_repo_pair: GitRepoPair,
) -> None:
    root = git_repo_pair.local_path
    index = root / "index.md"
    index.write_text("authored\n")
    conflict.record_generated_indexes(root, ["index.md"])
    strategy = GitWriteStrategy(
        enable_pull=True, enable_push=False, repo_path=root, git_lfs=False
    )
    caps = ContentConfig(okf_mode="on", okf_index=False, okf_log="off").okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
        git_strategy=strategy,
        on_write=strategy,
    )
    vault.index.build_index()
    try:
        assert vault.reconcile_index_with_head(source="git_sync") in (
            "current",
            "reindexed",
        )
        assert index.read_text() == "authored\n"
        assert not conflict.read_generated_indexes(root)
    finally:
        vault.close()
        strategy.close()


def test_new_conflict_for_same_path_cannot_be_settled_by_old_snapshot(
    git_repo_pair: GitRepoPair,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    root = git_repo_pair.local_path
    strategy, vault = _vault(root)
    read = Event()
    recorded = Event()

    def recover() -> bool:
        paths, snapshot = strategy.pending_generated_indexes(root)
        read.set()
        assert recorded.wait(5)
        return strategy.settle_generated_indexes(root, paths, snapshot)

    try:
        conflict.record_generated_indexes(root, ["README.md"])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(recover)
            assert read.wait(5)
            conflict.record_generated_indexes(root, ["README.md"])
            recorded.set()
            assert not future.result(timeout=5)
        assert conflict.read_generated_indexes(root) == ["README.md"]
    finally:
        vault.close()
        strategy.close()
