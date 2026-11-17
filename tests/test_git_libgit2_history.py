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


def test_committed_revision_after_rename(isolated_repo: Path) -> None:
    """A renamed note retains a breadcrumb only while its content matches."""
    store = Libgit2Store(isolated_repo)
    old = isolated_repo / "old*.md"
    new = isolated_repo / "new.md"
    text = "".join(f"line {n}\n" for n in range(30))
    old.write_text(text)
    store(old, text, "write")
    old.rename(new)
    store(new, text, "rename", old_path=old)
    history = store.get_file_history(isolated_repo, new, None, 20)
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
    with pytest.raises(InvalidRequestError, match="did not exist at revision"):
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
    with pytest.raises(InvalidRequestError, match="is not tracked by Git"):
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


def test_revision_read_refuses_oversize_and_bad_ref(isolated_repo: Path) -> None:
    """Size limits and invalid refs are rejected before returning a revision."""
    store = Libgit2Store(isolated_repo)
    note = isolated_repo / "note.md"
    note.write_bytes(b"\xff\xfe")
    store(note, "", "write")
    ref = _git(isolated_repo, "rev-parse", "HEAD").strip()
    with pytest.raises(InvalidRequestError, match="exceeding the 1-byte limit"):
        store.get_file_at_ref(RevisionQuery(isolated_repo, note, ref, 1))
    with pytest.raises(InvalidRequestError, match="not a valid ancestral commit"):
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
    side = _git(isolated_repo, "rev-parse", "HEAD").strip()
    _git(isolated_repo, "checkout", "-q", branch)
    (isolated_repo / "other.txt").write_text("other\n")
    _git(isolated_repo, "add", "other.txt")
    _git(isolated_repo, "commit", "-qm", "other edit")
    _git(isolated_repo, "merge", "--no-ff", "-qm", "merge", "side")
    actual = native.get_file_history(isolated_repo, note, None, 10)
    expected = cli.get_file_history(isolated_repo, note, None, 10)
    assert [entry.sha for entry in actual] == [entry.sha for entry in expected]
    actual_diff = native.get_file_diff(isolated_repo, note, side, True)
    expected_diff = cli.get_file_diff(isolated_repo, note, side, True)
    assert [row.sha for row in actual_diff] == [row.sha for row in expected_diff]


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


def test_history_author_honors_mailmap(isolated_repo: Path) -> None:
    """Native history reports the same effective author as Git's mailmap."""
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    repo = isolated_repo
    note = repo / "note.md"
    note.write_text("note\n")
    _git(repo, "add", "note.md")
    _git(repo, "commit", "-qm", "note")
    (repo / ".mailmap").write_text(
        "Canonical Person <canonical@example.com> Fixture <fixture@example.com>\n"
    )
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    expected = cli.get_file_history(repo, note, None, 20)[0].author
    assert expected == "Canonical Person <canonical@example.com>"
    assert (
        Libgit2Store(repo).get_file_history(repo, note, None, 20)[0].author == expected
    )


def test_history_short_sha_honors_core_abbrev(isolated_repo: Path) -> None:
    """Native short IDs match Git's configured unambiguous abbreviation."""
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    repo = isolated_repo
    note = repo / "note.md"
    note.write_text("note\n")
    _git(repo, "add", "note.md")
    _git(repo, "commit", "-qm", "note")
    _git(repo, "config", "core.abbrev", "12")
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    expected = cli.get_file_history(repo, note, None, 20)[0].short_sha
    assert len(expected) == 12
    assert (
        Libgit2Store(repo).get_file_history(repo, note, None, 20)[0].short_sha
        == expected
    )


def test_committed_revision_rejects_mode_change(isolated_repo: Path) -> None:
    """A mode-only worktree change invalidates a breadcrumb when Git tracks modes."""
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    repo = isolated_repo
    _git(repo, "config", "core.filemode", "true")
    note = repo / "note.md"
    note.write_text("note\n")
    note.chmod(0o644)
    _git(repo, "add", "note.md")
    _git(repo, "commit", "-qm", "note")
    note.chmod(0o755)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    assert cli.committed_revision(repo, note) is None
    assert Libgit2Store(repo).committed_revision(repo, note) is None


def test_committed_revision_after_filter_change_is_best_effort(
    isolated_repo: Path,
) -> None:
    """Newly unsupported filters cannot make breadcrumb lookup fail a write."""
    note = isolated_repo / "note.md"
    note.write_text("note\n")
    _git(isolated_repo, "add", "note.md")
    _git(isolated_repo, "commit", "-qm", "note")
    store = Libgit2Store(isolated_repo)
    (isolated_repo / ".gitattributes").write_text("note.md filter=external\n")
    _git(isolated_repo, "config", "filter.external.clean", "cat")
    assert store.committed_revision(isolated_repo, note) is None


@pytest.mark.parametrize("setting", [None, "auto", "false", "invalid"])
@pytest.mark.parametrize("per_commit", [False, True])
def test_auto_abbreviation_queries_refuse_actionably(
    isolated_repo: Path, setting: str | None, per_commit: bool
) -> None:
    """Git's adaptive abbreviation requires an explicit supported configuration."""
    repo = isolated_repo
    note = repo / "note.md"
    note.write_text("first\n")
    _git(repo, "add", "note.md")
    _git(repo, "commit", "-qm", "first")
    before = _git(repo, "rev-parse", "HEAD").strip()
    note.write_text("second\n")
    _git(repo, "commit", "-qam", "second")
    if setting is None:
        _git(repo, "config", "--unset", "core.abbrev")
    else:
        _git(repo, "config", "core.abbrev", setting)
    store = Libgit2Store(repo)
    if setting != "invalid":
        assert (
            store.get_file_at_ref(RevisionQuery(repo, note, before, 0)).content
            == "first\n"
        )
        assert "+second" in store.get_file_diff(repo, note, before, False)
        note.write_text("third\n")
        store(note, "", "write")
        assert _git(repo, "show", "HEAD:note.md") == "third\n"
    with pytest.raises(InvalidRequestError, match=r"core\.abbrev.*GitWriteStrategy"):
        if per_commit:
            store.get_file_diff(repo, note, before, True)
        else:
            store.get_file_history(repo, note, None, 20)
