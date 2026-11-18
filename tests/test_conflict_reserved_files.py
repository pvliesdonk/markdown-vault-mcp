"""Reserved files retain their authored bytes during Git conflict recovery."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.fixtures.conflict_recovery import (
    _setup_generated_conflict,
    _setup_multiple_conflicts,
    recovery_vault,
)
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


@pytest.mark.parametrize(
    ("name", "authority"),
    [("index.md", a) for a in ("disabled", "outside", "inactive", "readonly")]
    + [("log.md", "enabled")],
)
def test_reserved_conflict_preserves_both_versions(
    git_repo_pair: GitRepoPair, name: str, authority: str
) -> None:
    root = git_repo_pair.local_path
    vault = root / "vault"
    vault.mkdir()
    names = ("index.md", "log.md") if authority == "disabled" else (name,)
    for item in names:
        (vault / item).write_bytes(b"base\r\n")
    _run_git(root, "add", *(f"vault/{item}" for item in names))
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
    for item in names:
        (other / "vault" / item).write_bytes(
            b"---\r\ntitle: remote\r\n---\r\nremote\r\n"
        )
    _run_git(other, "add", *(f"vault/{item}" for item in names))
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    local = b"---\r\ntitle: local\r\n---\r\nlocal\r\n"
    for item in names:
        (vault / item).write_bytes(local)
    _run_git(root, "add", *(f"vault/{item}" for item in names))
    _run_git(root, "commit", "-m", "local")
    strategy, instance = recovery_vault(vault, authority=authority)
    try:
        result = strategy.force_pull()
        assert result.reason == "conflicts_resolved_with_siblings"
        assert len(result.conflict_files) == len(names)
        for item in names:
            sibling = next(
                path
                for path in result.conflict_files
                if path.startswith(f"vault/{item[:-3]}.conflict-mcp-")
            )
            assert (root / sibling).read_bytes() == local
            assert (
                vault / item
            ).read_bytes() == b"---\r\ntitle: remote\r\n---\r\nremote\r\n"
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        instance.close()
        strategy.close()


@pytest.mark.parametrize(
    ("subdir", "tick"),
    [
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ],
)
def test_generated_index_is_recovered_after_pull(
    git_repo_pair: GitRepoPair,
    subdir: bool,
    tick: bool,
) -> None:
    from tests.conftest import wait_for_writer_drain

    root, vault_root, index = _setup_generated_conflict(git_repo_pair, subdir)
    strategy, vault = recovery_vault(vault_root)
    try:
        vault.index.build_index()
        if tick:
            assert strategy.sync_once(vault_root)
        else:
            result = strategy.force_pull()
            assert result.reason == "rebased"
            assert not result.conflict_files
            assert result.to_sha != result.from_sha
        assert "local.md" not in index.read_text()
        if tick:
            vault._reconcile_on_tick()
        else:
            assert vault.reconcile_index_with_head(source="git_sync") in (
                "current",
                "reindexed",
            )
        wait_for_writer_drain(vault)
        text = index.read_text()
        assert "local.md" in text and "remote.md" in text
        assert "okf_version" in text
        assert not list(vault_root.glob("index.conflict-mcp-*.md"))
        assert "conflict_with" not in text and "conflict_date" not in text
        assert not (vault_root / "vault").exists()
        assert vault._write_callback.drain(timeout=5.0)
        assert _run_git(root, "status", "--porcelain") == ""
        vault.writer.okf_generate_index()
        wait_for_writer_drain(vault)
        assert index.read_text() == text
    finally:
        vault.close()
        strategy.close()


def test_generated_index_and_log_conflicts_with_write_logging(
    git_repo_pair: GitRepoPair,
) -> None:
    from tests.conftest import wait_for_writer_drain

    root, vault_root, index = _setup_generated_conflict(
        git_repo_pair, False, with_log=True
    )
    strategy, vault = recovery_vault(vault_root, log="writes", build=True)
    try:
        result = strategy.force_pull()
        assert result.reason == "conflicts_resolved_with_siblings"
        assert len(result.conflict_files) == 1
        assert (root / result.conflict_files[0]).read_text() == "local log\n"
        assert (root / "log.md").read_text() == "remote log\n"
        vault.reconcile_index_with_head(source="git_sync")
        wait_for_writer_drain(vault)
        assert vault._write_callback.drain(timeout=5)
        assert "local.md" in index.read_text() and "remote.md" in index.read_text()
        assert (root / "log.md").read_text() == "remote log\n"
        assert not list(root.glob("index.conflict-mcp-*.md"))
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        vault.close()
        strategy.close()


@pytest.mark.parametrize("abort", [False, True])
def test_multiple_replayed_commits_keep_concept_sibling_and_regenerate_index(
    git_repo_pair: GitRepoPair,
    abort: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.config_sections._assembly import (
        to_vault_instances,
        to_vault_settings,
    )
    from markdown_vault_mcp.vault import Vault
    from tests.conftest import wait_for_writer_drain

    root, index, concept = _setup_multiple_conflicts(git_repo_pair)
    config = ProjectConfig(
        source_dir=root,
        read_only=False,
        okf_mode="on",
        okf_index=True,
        okf_log="off",
        git_repo_url=str(git_repo_pair.remote_path),
        git_lfs=False,
        git_push_delay_s=3600,
    )
    instances = to_vault_instances(config)
    strategy = instances.git_strategy
    vault = Vault(
        source_dir=root,
        settings=to_vault_settings(config, instances=instances),
        git_strategy=strategy,
        on_write=instances.on_write,
    )
    try:
        vault.index.build_index()
        if abort:
            from markdown_vault_mcp.git import conflict

            local_index = index.read_text()

            def saved_at_rebase_head(
                _root: Path, _env: object, *, conflicted: set[str] | None = None
            ) -> list[tuple[str, str]]:
                if conflicted is not None:
                    conflicted.update(("index.md", "concept.md"))
                return [
                    (name, _run_git(root, "show", f"REBASE_HEAD:{name}") + "\n")
                    for name in ("index.md", "concept.md")
                ]

            monkeypatch.setattr(
                conflict,
                "resolve_rebase_conflicts",
                saved_at_rebase_head,
            )
        result = strategy.force_pull()
        assert result.reason == "conflicts_resolved_with_siblings"
        assert len(result.conflict_files) == 1
        assert result.conflict_files[0].startswith("concept.conflict-mcp-")
        assert "conflict_with" in concept.read_text()
        assert "conflict_with" in (root / result.conflict_files[0]).read_text()
        if abort:
            assert "# Local concept 1" in (root / result.conflict_files[0]).read_text()
            assert index.read_text() == local_index
            assert not (root / ".git" / "markdown-vault-pending-indexes.json").exists()
            assert not (root / ".git" / "rebase-merge").exists()
            return
        assert vault.reconcile_index_with_head(source="git_sync") in (
            "current",
            "reindexed",
        )
        wait_for_writer_drain(vault)
        assert vault._write_callback.drain(timeout=5)
        assert "clean.md" in index.read_text()
        assert not list(root.glob("index.conflict-mcp-*.md"))
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        vault.close()
        instances.close()


def test_sync_publishes_recovered_index_inside_commit_scope(
    git_repo_pair: GitRepoPair,
) -> None:
    import asyncio
    from typing import Any

    from markdown_vault_mcp._commit_scope import bound_commit_scope
    from markdown_vault_mcp._tools.git import _run_pull_leg, _run_push_leg

    _, root, _index = _setup_generated_conflict(git_repo_pair, False)
    strategy, vault = recovery_vault(root, build=True)
    try:

        async def both() -> tuple[dict[str, Any], dict[str, Any]]:
            pull = await _run_pull_leg(strategy, vault, dry_run=False)
            push = await _run_push_leg(strategy, dry_run=False)
            return pull, push

        with bound_commit_scope("git_sync") as scope:
            pull, push = asyncio.run(both())
            assert pull["applied"] and push["applied"]
            remote_index = _run_git(git_repo_pair.remote_path, "show", "main:index.md")
            vault.end_commit_scope(scope)
            assert "local.md" in remote_index and "remote.md" in remote_index
        assert vault._write_callback.drain(timeout=5)
    finally:
        vault.close()
        strategy.close()
