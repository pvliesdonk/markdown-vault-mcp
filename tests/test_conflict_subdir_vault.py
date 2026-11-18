"""Real Git conflict recovery for a vault below its repository root."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import frontmatter

from markdown_vault_mcp.git import GitWriteStrategy
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from tests.fixtures.git import GitRepoPair


def test_force_pull_recovers_a_conflict_in_a_subdirectory_vault(
    git_repo_pair: GitRepoPair,
) -> None:
    """Both versions are committed at repository-relative paths."""
    root = git_repo_pair.local_path
    vault = root / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("base\n")
    _run_git(root, "add", "vault/note.md")
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
    (other / "vault/note.md").write_text("remote version\n")
    _run_git(other, "add", "vault/note.md")
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    note.write_text("local version\n")
    _run_git(root, "add", "vault/note.md")
    _run_git(root, "commit", "-m", "local")

    result = GitWriteStrategy(
        enable_pull=True, enable_push=False, repo_path=vault
    ).force_pull()

    assert result.reason == "conflicts_resolved_with_siblings"
    sibling_path = result.conflict_files[0]
    assert sibling_path.startswith("vault/note.conflict-mcp-")
    sibling = root / sibling_path
    original = frontmatter.loads(note.read_text())
    saved = frontmatter.loads(sibling.read_text())
    assert original.content == "remote version"
    assert saved.content == "local version"
    assert saved.metadata["conflict_with"] == "vault/note.md"
    assert not (vault / "vault").exists()
    assert _run_git(root, "status", "--porcelain") == ""
