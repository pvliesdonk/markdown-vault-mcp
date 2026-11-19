"""Shared Git conflict recovery test setup."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.git import GitWriteStrategy
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


def recovery_vault(
    root: Path, *, authority: str = "enabled", build: bool = False, log: str = "off"
) -> tuple[GitWriteStrategy, Vault]:
    mode = "auto" if authority == "inactive" else "on"
    caps = ContentConfig(
        okf_mode=mode,
        okf_index=authority != "disabled",
        okf_folders=("other",) if authority == "outside" else (".",),
        okf_log=log,
    ).okf_capabilities
    strategy = GitWriteStrategy(
        enable_pull=True, enable_push=False, repo_path=root, git_lfs=False
    )
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=authority == "readonly", okf_mode=mode, okf_capabilities=caps
        ),
        git_strategy=strategy,
        on_write=strategy,
    )
    if build:
        vault.index.build_index()
    return strategy, vault


def _setup_generated_conflict(
    git_repo_pair: GitRepoPair, subdir: bool, *, with_log: bool = False
) -> tuple[Path, Path, Path]:
    root = git_repo_pair.local_path
    vault_root = root / "vault" if subdir else root
    vault_root.mkdir(exist_ok=True)
    prefix = "vault/" if subdir else ""
    index = vault_root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Base\n')
    if with_log:
        (vault_root / "log.md").write_text("base log\n")
    _run_git(
        root, "add", f"{prefix}index.md", *([f"{prefix}log.md"] if with_log else [])
    )
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
    if with_log:
        (other / prefix / "log.md").write_text("remote log\n")
    _run_git(
        other,
        "add",
        f"{prefix}index.md",
        f"{prefix}remote.md",
        *([f"{prefix}log.md"] if with_log else []),
    )
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    (vault_root / "local.md").write_text("# Local\n")
    index.write_text('---\nokf_version: "0.2"\n---\n# Local listing\n')
    if with_log:
        (vault_root / "log.md").write_text("local log\n")
    _run_git(
        root,
        "add",
        f"{prefix}index.md",
        f"{prefix}local.md",
        *([f"{prefix}log.md"] if with_log else []),
    )
    _run_git(root, "commit", "-m", "local")
    return root, vault_root, index


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
