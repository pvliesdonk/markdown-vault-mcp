"""Reserved files retain their authored bytes during Git conflict recovery."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git import GitWriteStrategy
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


@pytest.mark.parametrize("name", ["index.md", "log.md"])
@pytest.mark.parametrize(
    "authority", ["disabled", "outside", "inactive", "readonly", "raising"]
)
def test_reserved_conflict_preserves_both_versions(
    git_repo_pair: GitRepoPair, name: str, authority: str
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = git_repo_pair.local_path
    vault = root / "vault"
    vault.mkdir()
    note = vault / name
    note.write_bytes(b"base\r\n")
    _run_git(root, "add", f"vault/{name}")
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
    (other / "vault" / name).write_bytes(b"---\r\ntitle: remote\r\n---\r\nremote\r\n")
    _run_git(other, "add", f"vault/{name}")
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    local = b"---\r\ntitle: local\r\n---\r\nlocal\r\n"
    note.write_bytes(local)
    _run_git(root, "add", f"vault/{name}")
    _run_git(root, "commit", "-m", "local")
    strategy = GitWriteStrategy(enable_pull=True, enable_push=False, repo_path=vault)
    mode = "auto" if authority == "inactive" else "on"
    caps = ContentConfig(
        okf_mode=mode,
        okf_index=authority != "disabled",
        okf_folders=("other",) if authority == "outside" else (".",),
        okf_log="off",
    ).okf_capabilities
    instance = Vault(
        source_dir=vault,
        settings=VaultSettings(
            read_only=authority == "readonly", okf_mode=mode, okf_capabilities=caps
        ),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        if authority == "raising":

            def fail_policy(_path: object) -> bool:
                raise RuntimeError("synthetic policy failure")

            strategy.set_generated_index_policy(fail_policy)
        result = strategy.force_pull()
        assert result.reason == "conflicts_resolved_with_siblings"
        assert result.conflict_files[0].startswith("vault/")
        assert (root / result.conflict_files[0]).read_bytes() == local
        assert note.read_bytes() == b"---\r\ntitle: remote\r\n---\r\nremote\r\n"
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        instance.close()
        strategy.close()


def _fail_generation(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("synthetic generation failure")


def _setup_generated_conflict(
    git_repo_pair: GitRepoPair, subdir: bool
) -> tuple[Path, Path, Path]:
    root = git_repo_pair.local_path
    vault_root = root / "vault" if subdir else root
    vault_root.mkdir(exist_ok=True)
    prefix = "vault/" if subdir else ""
    index = vault_root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Base\n')
    _run_git(root, "add", f"{prefix}index.md")
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
    (other / prefix / "remote.md").write_text("# Remote\n")
    (other / prefix / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Remote listing\n'
    )
    _run_git(other, "add", f"{prefix}index.md", f"{prefix}remote.md")
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    (vault_root / "local.md").write_text("# Local\n")
    index.write_text('---\nokf_version: "0.2"\n---\n# Local listing\n')
    _run_git(root, "add", f"{prefix}index.md", f"{prefix}local.md")
    _run_git(root, "commit", "-m", "local")
    return root, vault_root, index


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("subdir", [False, True])
@pytest.mark.parametrize("tick", [False, True])
def test_generated_index_is_recovered_after_pull(
    git_repo_pair: GitRepoPair,
    restart: bool,
    subdir: bool,
    tick: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root, vault_root, index = _setup_generated_conflict(git_repo_pair, subdir)
    strategy = GitWriteStrategy(
        enable_pull=True, enable_push=False, repo_path=vault_root
    )
    caps = ContentConfig(okf_mode="on", okf_index=True, okf_log="off").okf_capabilities
    vault = Vault(
        source_dir=vault_root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        if tick:
            assert strategy.sync_once(vault_root)
        else:
            result = strategy.force_pull()
            assert result.reason == "rebased"
            assert not result.conflict_files
            assert result.to_sha != result.from_sha
        marker = root / ".git" / "markdown-vault-pending-indexes.json"
        assert marker.exists()
        if restart:
            vault.close()
            strategy.close()
            strategy = GitWriteStrategy(
                enable_pull=True, enable_push=False, repo_path=vault_root
            )
            vault = Vault(
                source_dir=vault_root,
                settings=VaultSettings(
                    read_only=False, okf_mode="on", okf_capabilities=caps
                ),
                git_strategy=strategy,
                on_write=strategy,
            )
            vault.index.build_index()
        with monkeypatch.context() as patch:
            patch.setattr(vault._okf_migrate, "generate_index", _fail_generation)
            assert vault.reconcile_index_with_head(source="git_sync") in (
                "current",
                "reindexed",
            )
            assert marker.exists()
        if tick:
            vault._reconcile_on_tick()
        else:
            assert vault.reconcile_index_with_head(source="git_sync") in (
                "current",
                "reindexed",
            )
        assert not marker.exists()
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


def _setup_multiple_conflicts(git_repo_pair: GitRepoPair) -> tuple[Path, Path, Path]:
    root = git_repo_pair.local_path
    index = root / "index.md"
    concept = root / "concept.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Initial\n')
    concept.write_text("# Initial\n")
    _run_git(root, "add", "index.md", "concept.md")
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
    (other / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Remote\n')
    (other / "concept.md").write_text("# Remote concept\n")
    _run_git(other, "add", "index.md", "concept.md")
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    for number in (1, 2):
        index.write_text(f'---\nokf_version: "0.2"\n---\n# Local {number}\n')
        concept.write_text(f"# Local concept {number}\n")
        _run_git(root, "add", "index.md", "concept.md")
        _run_git(root, "commit", "-m", f"local {number}")
    index.write_text('---\nokf_version: "0.2"\n---\n# Index-only change\n')
    _run_git(root, "add", "index.md")
    _run_git(root, "commit", "-m", "index only")
    (root / "clean.md").write_text("# Clean\n")
    _run_git(root, "add", "clean.md")
    _run_git(root, "commit", "-m", "clean")
    return root, index, concept


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
            local_concept = concept.read_text()
            monkeypatch.setattr(
                conflict,
                "resolve_rebase_conflicts",
                lambda _root, _env: [
                    ("index.md", local_index),
                    ("concept.md", local_concept),
                ],
            )
        result = strategy.force_pull()
        assert result.reason == "conflicts_resolved_with_siblings"
        assert len(result.conflict_files) == 1
        assert result.conflict_files[0].startswith("concept.conflict-mcp-")
        assert "conflict_with" in concept.read_text()
        assert "conflict_with" in (root / result.conflict_files[0]).read_text()
        if abort:
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
        assert "index only" not in _run_git(root, "log", "--format=%s")
        assert not list(root.glob("index.conflict-mcp-*.md"))
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        vault.close()
        instances.close()
