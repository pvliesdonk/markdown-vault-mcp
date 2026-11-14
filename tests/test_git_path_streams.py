"""Real Git path identity through NUL streams, including CR and CRLF (#1290)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from markdown_vault_mcp.git import conflict
from markdown_vault_mcp.git._run import run_git_capturing
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.types import RevisionQuery
from tests.fixtures.git import _run_git

_MARKERS = ["\r", "\r\n", "\n", "\t", "é", "plain"]
_BODY = "".join(f"line {i}\n" for i in range(30))


def _commit(repo: Path, message: str, *paths: str) -> str:
    _run_git(repo, "add", "--", *paths)
    _run_git(repo, "commit", "-qm", message)
    return _run_git(repo, "rev-parse", "HEAD").strip()


def _renamed_note(repo: Path, marker: str) -> tuple[str, str, str]:
    old, new = f"{marker}old.md", f"{marker}new.md"
    (repo / old).write_text(_BODY)
    first = _commit(repo, "birth", old)
    _run_git(repo, "mv", "--", old, new)
    _run_git(repo, "commit", "-qm", "rename")
    (repo / new).write_text(_BODY + "update\n")
    _commit(repo, "edit", new)
    return old, new, first


@pytest.mark.parametrize("marker", _MARKERS)
def test_rename_history_and_diffs_keep_the_notes_identity(
    git_repo: Path, marker: str
) -> None:
    """Historical paths returned by Git remain usable as subsequent pathspecs."""
    repo = git_repo
    old, new, first = _renamed_note(repo, marker)
    strategy = GitWriteStrategy()
    history = strategy.get_file_history(repo, repo / new, None, 10)
    assert [entry.message for entry in history] == ["edit", "rename", "birth"]
    diffs = strategy.get_file_diff(repo, repo / new, first, per_commit=True)
    assert isinstance(diffs, list)
    assert [entry.message for entry in diffs] == ["edit", "rename"]
    assert "+update" in diffs[0].diff
    assert diffs[1].diff == f"{old} => {new} (renamed, no content change)\n"
    combined = strategy.get_file_diff(repo, repo / new, first, per_commit=False)
    assert isinstance(combined, str)
    assert "+update" in combined
    assert "-line 0" not in combined
    result = strategy.get_file_at_ref(
        RevisionQuery(repo_path=repo, path=repo / new, ref=first, max_bytes=0)
    )
    assert result.path == new
    assert result.historical_path == old
    assert result.content == _BODY


@pytest.mark.parametrize("marker", _MARKERS)
def test_conflict_resolution_preserves_the_unmerged_name(
    git_repo: Path, marker: str
) -> None:
    """The actual rebase resolves the named file and saves its local content."""
    repo = git_repo
    name = f"{marker}note.md"
    (repo / name).write_text("base\n")
    _commit(repo, "birth", name)
    upstream_branch = _run_git(repo, "branch", "--show-current").strip()
    _run_git(repo, "checkout", "-qb", "local")
    (repo / name).write_text("local\n")
    _commit(repo, "local edit", name)
    _run_git(repo, "checkout", "-q", upstream_branch)
    (repo / name).write_text("upstream\n")
    _commit(repo, "upstream edit", name)
    _run_git(repo, "checkout", "-q", "local")
    rebase = subprocess.run(
        ["git", "-C", str(repo), "rebase", upstream_branch], capture_output=True
    )
    assert rebase.returncode == 1
    try:
        assert conflict.resolve_rebase_conflicts(repo, None) == [(name, "local\n")]
        assert (repo / name).read_text() == "upstream\n"
        assert not (repo / ".git/rebase-merge").exists()
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "rebase", "--abort"], capture_output=True
        )


def test_capture_defaults_and_error_contract(git_repo: Path) -> None:
    """Opting into path capture leaves ordinary text and error results intact."""
    repo = git_repo
    args = ("-c", "fixture.value=a\rb\r\nc\n", "config", "--get", "fixture.value")
    assert run_git_capturing(repo, *args).stdout == "a\nb\nc\n\n"
    assert run_git_capturing(repo, *args, preserve_newlines=True).stdout == (
        "a\rb\r\nc\n\n"
    )
    failed = run_git_capturing(repo, "not\r\na\rcommand", preserve_newlines=True)
    assert failed.returncode != 0
    assert failed.stderr == run_git_capturing(repo, "not\r\na\rcommand").stderr
    assert run_git_capturing(repo, "diff", "-z", preserve_newlines=True).stdout == ""
    configured = run_git_capturing(
        repo,
        "config",
        "--get",
        "fixture.value",
        env={
            **os.environ,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "fixture.value",
            "GIT_CONFIG_VALUE_0": "from\renv",
        },
        preserve_newlines=True,
    )
    assert configured.stdout == "from\renv\n"


@settings(max_examples=25, deadline=None)
@example(name="\rnote.md")
@example(name="\r\nnote.md")
@given(name=st.text(alphabet="abcXYZé\r\n\t", min_size=1, max_size=24))
def test_capture_round_trips_git_path_stdin(name: str) -> None:
    """Git's independently supplied ignored pathname returns character-for-character."""
    with tempfile.TemporaryDirectory(prefix="git-path-property-") as temp:
        repo = Path(temp)
        _run_git(repo, "init", "-q")
        (repo / ".gitignore").write_text("*.ignored\n")
        name += ".ignored"
        result = run_git_capturing(
            repo,
            "check-ignore",
            "--no-index",
            "-z",
            "--stdin",
            preserve_newlines=True,
            input=name + "\0",
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull},
        )
        assert result.returncode == 0
        assert result.stdout == name + "\0"
        assert result.stderr == ""
