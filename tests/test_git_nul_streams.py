"""Git NUL streams preserve path bytes and sanitize commit metadata."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git._run import run_git_capturing
from markdown_vault_mcp.git.query import _commit_rows_for, _history_entries_for
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from pathlib import Path


def _git_bytes(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=data, capture_output=True, check=True
    ).stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX filesystem byte names")
def test_invalid_path_bytes_round_trip(git_repo: Path) -> None:
    name = b"dir/bad\xff[\r\n].md"
    blob = _git_bytes(git_repo, "hash-object", "-w", "--stdin", data=b"note\n").strip()
    record = b"100644 " + blob + b"\t" + name + b"\0"
    _git_bytes(git_repo, "update-index", "-z", "--index-info", data=record)
    _run_git(git_repo, "commit", "-qm", "birth")
    decoded = os.fsdecode(name)
    listed = run_git_capturing(git_repo, "ls-files", "-z")
    assert decoded in listed.stdout.split("\0")
    selected = run_git_capturing(git_repo, "ls-files", "-z", pathspecs=[decoded])
    assert selected.stdout == decoded + "\0"
    (git_repo / ".gitignore").write_text("dir/\n")
    ignored = run_git_capturing(
        git_repo, "check-ignore", "--no-index", "--stdin", "-z", input=decoded + "\0"
    )
    assert ignored.stdout == decoded + "\0"
    (git_repo / "dir").mkdir()
    strategy = GitWriteStrategy()
    history = strategy.get_file_history(git_repo, None, None, 10)
    assert decoded in history[0].paths_changed
    directory = strategy.get_file_history(
        git_repo, git_repo / "dir", None, 10, is_dir=True
    )
    assert decoded in directory[0].paths_changed


def test_metadata_preserves_carriage_return_and_replaces_invalid_utf8(
    git_repo: Path,
) -> None:
    tree = _git_bytes(git_repo, "mktree", data=b"").strip()
    raw = (
        b"tree " + tree + b"\nauthor Test <test@example.com> 1 +0000\n"
        b"committer Test <test@example.com> 1 +0000\n\nsubject\rbroken\xff\n"
    )
    commit = (
        _git_bytes(git_repo, "hash-object", "-t", "commit", "-w", "--stdin", data=raw)
        .strip()
        .decode()
    )
    _run_git(git_repo, "update-ref", "HEAD", commit)
    entries = _history_entries_for(git_repo, {commit}, "", None)
    rows = _commit_rows_for(git_repo, {commit}, "note.md", None)
    assert entries[0].message == "subject\rbroken\ufffd"
    assert rows[0][3] == "subject\rbroken\ufffd"
