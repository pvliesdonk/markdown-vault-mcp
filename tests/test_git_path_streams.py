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
from markdown_vault_mcp.git.strategy import (
    GitWriteStrategy,
    _ignored_paths,
    _stage_rename,
)
from markdown_vault_mcp.git.types import RevisionQuery
from tests.fixtures.git import GitRepoPair, _run_git

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
@pytest.mark.parametrize("directory", [False, True])
def test_history_preserves_distinct_paths(
    git_repo_pair: GitRepoPair, marker: str, directory: bool
) -> None:
    """A CR/CRLF path must not become its existing LF sibling in history."""
    repo = git_repo_pair.local_path
    folder = repo / "notes"
    folder.mkdir()
    names = {f"notes/{marker}note.md", "notes/\nnote.md"}
    for name in names:
        (repo / name).write_text("note\n")
    _commit(repo, "names", *sorted(names))

    entries = GitWriteStrategy().get_file_history(
        repo, folder if directory else None, None, 10, is_dir=directory
    )

    assert set(entries[0].paths_changed) == names


@pytest.mark.parametrize("marker", _MARKERS)
def test_rename_history_and_diffs_keep_the_notes_identity(
    git_repo_pair: GitRepoPair, marker: str
) -> None:
    """Historical paths returned by Git remain usable as subsequent pathspecs."""
    repo = git_repo_pair.local_path
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


@pytest.mark.parametrize("marker", _MARKERS)
def test_revision_read_keeps_the_historical_name(
    git_repo_pair: GitRepoPair, marker: str
) -> None:
    """A historical CR/CRLF filename still names the note being restored."""
    repo = git_repo_pair.local_path
    old, new, first = _renamed_note(repo, marker)
    result = GitWriteStrategy().get_file_at_ref(
        RevisionQuery(repo_path=repo, path=repo / new, ref=first, max_bytes=0)
    )
    assert result.path == new
    assert result.historical_path == old
    assert result.content == _BODY


@pytest.mark.parametrize("marker", _MARKERS)
def test_conflict_resolution_preserves_the_unmerged_name(
    git_repo_pair: GitRepoPair, marker: str
) -> None:
    """The actual rebase resolves the named file and saves its local content."""
    repo = git_repo_pair.local_path
    name = f"{marker}note.md"
    (repo / name).write_text("base\n")
    _commit(repo, "birth", name)
    _run_git(repo, "checkout", "-qb", "local")
    (repo / name).write_text("local\n")
    _commit(repo, "local edit", name)
    _run_git(repo, "checkout", "-q", "main")
    (repo / name).write_text("upstream\n")
    _commit(repo, "upstream edit", name)
    _run_git(repo, "checkout", "-q", "local")
    rebase = subprocess.run(
        ["git", "-C", str(repo), "rebase", "main"], capture_output=True
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


@pytest.mark.parametrize("marker", _MARKERS)
def test_ignored_destination_does_not_break_scoped_staging(
    git_repo_pair: GitRepoPair, marker: str
) -> None:
    """Ignore echoes retain identity without staging a bystander's work."""
    repo = git_repo_pair.local_path
    source, destination = repo / "source.md", repo / f"{marker}note.ignored"
    source.write_text("source\n")
    _commit(repo, "birth", source.name)
    (repo / ".gitignore").write_text("*.ignored\n")
    source.rename(destination)
    bystander = repo / "bystander.md"
    bystander.write_text("staged\n")
    _run_git(repo, "add", "--", bystander.name)
    bystander.write_text("unstaged\n")

    assert _ignored_paths(str(repo), (source, destination)) == {str(destination)}
    assert _stage_rename(str(repo), destination, source) == [f":(literal){source}"]
    assert _run_git(repo, "diff", "--cached", "--name-status") == (
        "A\tbystander.md\nD\tsource.md\n"
    )
    assert _run_git(repo, "show", ":bystander.md") == "staged\n"
    assert bystander.read_text() == "unstaged\n"


def test_capture_defaults_and_error_contract(git_repo_pair: GitRepoPair) -> None:
    """Opting into path capture leaves ordinary text and error results intact."""
    repo = git_repo_pair.local_path
    args = ("-c", "fixture.value=a\rb\r\nc\n", "config", "--get", "fixture.value")
    assert run_git_capturing(repo, *args).stdout == "a\nb\nc\n\n"
    assert run_git_capturing(repo, *args, preserve_newlines=True).stdout == (
        "a\rb\r\nc\n\n"
    )
    failed = run_git_capturing(repo, "not\r\na\rcommand", preserve_newlines=True)
    assert failed.returncode != 0
    assert failed.stdout == ""
    assert isinstance(failed.stderr, str)
    assert "\r" not in failed.stderr
    assert failed.stderr == run_git_capturing(repo, "not\r\na\rcommand").stderr
    with pytest.raises(subprocess.CalledProcessError) as exc:
        failed.check_returncode()
    assert exc.value.stderr == failed.stderr
    assert exc.value.stdout == ""
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
