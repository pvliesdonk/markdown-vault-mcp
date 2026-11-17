"""Real-repository tests for libgit2 history and revision reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.git.libgit2 import Libgit2Store
from markdown_vault_mcp.git.types import RevisionQuery
from tests.fixtures.libgit2 import _git
from tests.fixtures.libgit2 import isolated_repo as isolated_repo

if TYPE_CHECKING:
    from pathlib import Path


def test_rename_history_and_revision(isolated_repo: Path) -> None:
    """History and revision reads follow a rename back to the note's birth."""
    store = Libgit2Store(isolated_repo)
    old = isolated_repo / "old*.md"
    new = isolated_repo / "new.md"
    text = "".join(f"line {n}\n" for n in range(30))
    old.write_text(text)
    store(old, text, "write")
    first = _git(isolated_repo, "rev-parse", "HEAD").strip()
    old.rename(new)
    store(new, text, "rename", old_path=old)
    history = store.get_file_history(isolated_repo, new, None, 20)
    assert [entry.sha for entry in history] == [
        _git(isolated_repo, "rev-parse", "HEAD").strip(),
        first,
    ]
    result = store.get_file_at_ref(RevisionQuery(isolated_repo, new, first, 0))
    assert (result.historical_path, result.content) == (old.name, text)
    assert store.committed_revision(isolated_repo, new) == history[0].sha
    new.write_text("different")
    assert store.committed_revision(isolated_repo, new) is None


def test_reused_name_does_not_cross_birth(isolated_repo: Path) -> None:
    """A newly created note never inherits a deleted note's earlier content."""
    store = Libgit2Store(isolated_repo)
    note = isolated_repo / "note.md"
    note.write_text("first\n")
    store(note, "first\n", "write")
    first = _git(isolated_repo, "rev-parse", "HEAD").strip()
    note.unlink()
    store(note, "", "delete")
    note.write_text("second\n")
    store(note, "second\n", "write")
    history = store.get_file_history(isolated_repo, note, None, 20)
    assert len(history) == 1
    with pytest.raises(InvalidRequestError):
        store.get_file_at_ref(RevisionQuery(isolated_repo, note, first, 0))


def test_diff_includes_changed_text(isolated_repo: Path) -> None:
    """Aggregate and per-commit diffs describe the selected note."""
    store = Libgit2Store(isolated_repo)
    note = isolated_repo / "note.md"
    note.write_text("first\n")
    store(note, "first\n", "write")
    first = _git(isolated_repo, "rev-parse", "HEAD").strip()
    note.write_text("second\n")
    store(note, "second\n", "edit")
    diff = store.get_file_diff(isolated_repo, note, first, False)
    assert isinstance(diff, str) and "-first" in diff and "+second" in diff
    commits = store.get_file_diff(isolated_repo, note, first, True)
    assert isinstance(commits, list) and len(commits) == 1
    assert "+second" in commits[0].diff


def test_untracked_recreation_cannot_read_old_note(isolated_repo: Path) -> None:
    """An untracked replacement cannot borrow a deleted note's history."""
    store = Libgit2Store(isolated_repo)
    note = isolated_repo / "note.md"
    note.write_text("old\n")
    store(note, "old\n", "write")
    first = _git(isolated_repo, "rev-parse", "HEAD").strip()
    note.unlink()
    store(note, "", "delete")
    note.write_text("new\n")
    with pytest.raises(InvalidRequestError):
        store.get_file_at_ref(RevisionQuery(isolated_repo, note, first, 0))


def test_date_bounds_and_invalid_dates_match_cli(isolated_repo: Path) -> None:
    """Advertised ISO and relative date filters match the CLI on this history."""
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    native = Libgit2Store(isolated_repo)
    cli = GitWriteStrategy(repo_path=isolated_repo, enable_push=False, git_lfs=False)
    note = isolated_repo / "note.md"
    note.write_text("first\n")
    native(note, "first\n", "write")
    for since, until in [
        ("1 week ago", None),
        ("2000-01-01T00:00:00+00:00", None),
        (None, "2000-01-01T00:00:00+00:00"),
    ]:
        actual = native.get_file_history(isolated_repo, note, since, 20, until)
        expected = cli.get_file_history(isolated_repo, note, since, 20, until)
        assert actual == expected
    with pytest.raises(InvalidRequestError):
        native.get_file_history(isolated_repo, note, "not a date", 20)


def test_revision_read_refuses_oversize_and_non_text(isolated_repo: Path) -> None:
    """Limits and unreadable content are enforced before returning a revision."""
    from markdown_vault_mcp.exceptions import DocumentUnreadableError

    store = Libgit2Store(isolated_repo)
    note = isolated_repo / "note.md"
    note.write_bytes(b"\xff\xfe")
    store(note, "", "write")
    ref = _git(isolated_repo, "rev-parse", "HEAD").strip()
    with pytest.raises(InvalidRequestError):
        store.get_file_at_ref(RevisionQuery(isolated_repo, note, ref, 1))
    with pytest.raises(DocumentUnreadableError):
        store.get_file_at_ref(RevisionQuery(isolated_repo, note, ref, 0))
    with pytest.raises(InvalidRequestError):
        store.get_file_at_ref(RevisionQuery(isolated_repo, note, "not-a-ref", 0))


def test_vault_and_directory_history_paths(isolated_repo: Path) -> None:
    """Directory and whole-vault history only return paths inside their scope."""
    store = Libgit2Store(isolated_repo)
    directory = isolated_repo / "folder"
    directory.mkdir()
    note = directory / "note.md"
    note.write_text("hello")
    store(note, "hello", "write")
    history = store.get_file_history(isolated_repo, directory, None, 10, is_dir=True)
    assert history[0].paths_changed == ["folder/note.md"]
    whole = store.get_file_history(isolated_repo, None, None, 10)
    assert "folder/note.md" in whole[0].paths_changed


def test_merge_treesame_history_matches_cli(isolated_repo: Path) -> None:
    """A merge identical to one parent does not invent a note change."""
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    native = Libgit2Store(isolated_repo)
    cli = GitWriteStrategy(repo_path=isolated_repo, enable_push=False, git_lfs=False)
    note = isolated_repo / "note.md"
    note.write_text("base\n")
    native(note, "base\n", "write")
    branch = _git(isolated_repo, "branch", "--show-current").strip()
    _git(isolated_repo, "checkout", "-qb", "side")
    note.write_text("side\n")
    _git(isolated_repo, "add", "note.md")
    _git(isolated_repo, "commit", "-qm", "side edit")
    _git(isolated_repo, "checkout", "-q", branch)
    (isolated_repo / "other.txt").write_text("other\n")
    _git(isolated_repo, "add", "other.txt")
    _git(isolated_repo, "commit", "-qm", "other edit")
    _git(isolated_repo, "merge", "--no-ff", "-qm", "merge", "side")
    actual = native.get_file_history(isolated_repo, note, None, 10)
    expected = cli.get_file_history(isolated_repo, note, None, 10)
    assert [entry.sha for entry in actual] == [entry.sha for entry in expected]


def test_diff_does_not_include_reused_old_name(isolated_repo: Path) -> None:
    """A replacement at a renamed note's old path never leaks into its diff."""
    store = Libgit2Store(isolated_repo)
    old = isolated_repo / "old.md"
    new = isolated_repo / "new.md"
    text = "".join(f"line {i}\n" for i in range(40))
    old.write_text(text)
    store(old, text, "write")
    first = _git(isolated_repo, "rev-parse", "HEAD").strip()
    old.rename(new)
    store(new, text, "rename", old_path=old)
    old.write_text("unrelated replacement\n")
    store(old, "unrelated replacement\n", "write")
    new.write_text(text + "changed\n")
    store(new, "changed", "edit")
    diff = store.get_file_diff(isolated_repo, new, first, False)
    assert isinstance(diff, str)
    assert "+changed" in diff
    assert "unrelated replacement" not in diff


@pytest.mark.parametrize(
    "unit", ["second", "minute", "hour", "day", "week", "month", "year"]
)
def test_relative_dates_match_git_parser(isolated_repo: Path, unit: str) -> None:
    """Advertised relative-date boundaries match Git's local calendar rules."""
    from markdown_vault_mcp.git.libgit2_history import _date

    text = f"1 {unit} ago"
    expected = int(_git(isolated_repo, "rev-parse", f"--since={text}").split("=")[1])
    actual = _date(text)
    assert actual is not None and abs(actual - expected) <= 2


def test_rename_and_rewrite_threshold_agrees_with_cli(isolated_repo: Path) -> None:
    """A 36%-similar rename keeps the earlier revisions in both stores."""
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    native = Libgit2Store(isolated_repo)
    cli = GitWriteStrategy(repo_path=isolated_repo, enable_push=False, git_lfs=False)
    old = isolated_repo / "a.md"
    new = isolated_repo / "b.md"
    old.write_text("# Title\nDraft line\n")
    native(old, "", "write")
    old.write_text("# Title\nOriginal line\n")
    native(old, "", "edit")
    old.rename(new)
    new.write_text("# Title\nModified line\n")
    native(new, "", "rename", old_path=old)
    expected = cli.get_file_history(isolated_repo, new, None, 10)
    assert len(expected) == 3
    from markdown_vault_mcp.exceptions import InvalidRequestError

    with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
        native.get_file_history(isolated_repo, new, None, 10)


@pytest.mark.parametrize("per_commit", [False, True])
def test_diff_missing_or_early_bound_matches_cli(
    isolated_repo: Path, per_commit: bool
) -> None:
    """Missing bounds fail and dates before the first commit produce no diff."""
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    native = Libgit2Store(isolated_repo)
    cli = GitWriteStrategy(repo_path=isolated_repo, enable_push=False, git_lfs=False)
    note = isolated_repo / "note.md"
    note.write_text("content\n")
    native(note, "content\n", "write")
    with pytest.raises(ValueError):
        cli.get_file_diff(isolated_repo, note, None, per_commit)
    with pytest.raises(ValueError):
        native.get_file_diff(isolated_repo, note, None, per_commit)
    since = "2000-01-01T00:00:00+00:00"
    assert native.get_file_diff(
        isolated_repo, note, None, per_commit, since
    ) == cli.get_file_diff(isolated_repo, note, None, per_commit, since)
