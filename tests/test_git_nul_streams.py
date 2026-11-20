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
    (git_repo / ".gitignore").write_text("dir/\n")
    ignored = run_git_capturing(
        git_repo, "check-ignore", "--no-index", "--stdin", "-z", input=decoded + "\0"
    )
    assert ignored.stdout == decoded + "\0"
    strategy = GitWriteStrategy()
    history = strategy.get_file_history(git_repo, None, None, 10)
    assert decoded in history[0].paths_changed


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX filesystem byte names")
@pytest.mark.parametrize("marker", [b"\x1e", b"\xff\x1e"])
def test_record_separator_in_history_paths(git_repo: Path, marker: bytes) -> None:
    """History paths containing RS remain usable across a rename and empty subject."""
    from markdown_vault_mcp.git.query import _name_segments, _per_commit_rows
    from markdown_vault_mcp.git.types import RevisionQuery

    old = os.fsdecode(marker + b"old[\r\n].md")
    new = os.fsdecode(marker + b"new[\r\n].md")
    body = "".join(f"line {i}\n" for i in range(30))
    blob = _git_bytes(
        git_repo, "hash-object", "-w", "--stdin", data=body.encode()
    ).strip()
    _git_bytes(
        git_repo,
        "update-index",
        "-z",
        "--index-info",
        data=b"100644 " + blob + b"\t" + os.fsencode(old) + b"\0",
    )
    (git_repo / "aaa.md").write_text("peer\n")
    _run_git(git_repo, "add", "--", "aaa.md")
    _run_git(git_repo, "commit", "-q", "--allow-empty-message", "-m", "")
    first = _run_git(git_repo, "rev-parse", "HEAD").strip()
    _git_bytes(
        git_repo,
        "update-index",
        "-z",
        "--index-info",
        data=b"0 "
        + b"0" * 40
        + b"\t"
        + os.fsencode(old)
        + b"\0"
        + b"100644 "
        + blob
        + b"\t"
        + os.fsencode(new)
        + b"\0",
    )
    _run_git(git_repo, "commit", "-qm", "rename")
    renamed = _run_git(git_repo, "rev-parse", "HEAD").strip()
    edited = _git_bytes(
        git_repo, "hash-object", "-w", "--stdin", data=(body + "edit\n").encode()
    ).strip()
    _git_bytes(
        git_repo,
        "update-index",
        "-z",
        "--index-info",
        data=b"100644 " + edited + b"\t" + os.fsencode(new) + b"\0",
    )
    _run_git(git_repo, "commit", "-qm", "edit")
    last = _run_git(git_repo, "rev-parse", "HEAD").strip()
    strategy = GitWriteStrategy()
    history = strategy.get_file_history(git_repo, None, None, 10)
    assert history[0].paths_changed == [new]
    birth = next(entry for entry in history if entry.sha == first)
    assert set(birth.paths_changed) == {"aaa.md", old}
    assert birth.message == ""
    for entry in history:
        for name in entry.paths_changed:
            result = strategy.get_file_at_ref(
                RevisionQuery(
                    repo_path=git_repo, path=git_repo / name, ref=entry.sha, max_bytes=0
                )
            )
            assert result.historical_path == name
    single = strategy.get_file_history(git_repo, git_repo / new, None, 10)
    assert [entry.sha for entry in single] == [last, renamed, first]
    result = strategy.get_file_at_ref(
        RevisionQuery(repo_path=git_repo, path=git_repo / new, ref=first, max_bytes=0)
    )
    assert (result.historical_path, result.content) == (old, body)
    assert [
        row[0] for row in _per_commit_rows(git_repo, git_repo / new, first, None, None)
    ] == [last, renamed]
    assert {
        entry.sha
        for entry in _history_entries_for(git_repo, {first, renamed, last}, "", None)
    } == {first, renamed, last}
    assert {
        row[0] for row in _commit_rows_for(git_repo, {first, renamed, last}, new, None)
    } == {first, renamed, last}
    assert {seg.name for seg in _name_segments(git_repo, new, None)} == {old, new}
