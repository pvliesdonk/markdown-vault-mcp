"""A git vault with no commits yet has an empty history, not a fault (#1608)."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git.strategy import GitWriteStrategy

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def unborn(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "note.md").write_text("# Note\n")
    return repo


@pytest.mark.parametrize("scope", ["vault", "file", "folder"])
def test_history_is_empty(unborn: Path, scope: str) -> None:
    path = {"vault": None, "file": unborn / "note.md", "folder": unborn}[scope]
    entries = GitWriteStrategy().get_file_history(
        unborn, path, None, 10, is_dir=scope == "folder"
    )
    assert entries == []


@pytest.mark.parametrize(("per_commit", "empty"), [(False, ""), (True, [])])
def test_diff_since_a_timestamp_is_empty(
    unborn: Path, per_commit: bool, empty: object
) -> None:
    diff = GitWriteStrategy().get_file_diff(
        unborn,
        unborn / "note.md",
        None,
        per_commit=per_commit,
        since_timestamp="2026-01-01T00:00:00Z",
    )
    assert diff == empty


def test_head_probe_outside_a_repository_is_a_fault(tmp_path: Path) -> None:
    from markdown_vault_mcp.git.query import _has_commits

    with pytest.raises(ValueError, match="could not read HEAD"):
        _has_commits(tmp_path, None)


def test_corrupt_branch_ref_is_a_fault_not_an_empty_history(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    branch = subprocess.run(
        [*git, "symbolic-ref", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / ".git" / branch).write_text("garbage\n")
    with pytest.raises(ValueError):
        GitWriteStrategy().get_file_history(repo, None, None, 10)
