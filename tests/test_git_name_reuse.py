"""Single-note history and diff when a note's name was used before (#1285).

``git log --follow`` does not stop at the commit that created the file it is
following: where a name was freed and later reused, it walks straight past the
delete and continues into the history of the *previous* occupant.  Both
single-note readers took that output at face value, so ``get_history`` listed
another note's commits and ``get_diff`` paired one note's content against
another's as though it were one file being edited.

Every shape below is one a real vault produces: a note renamed away and the
name reused, a note deleted and the name reused, and — as the regression that
keeps the fix honest — an ordinary rename, whose pre-rename commits *do* belong
to the note and must still be reported.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git.strategy import GitWriteStrategy

if TYPE_CHECKING:
    from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    """Run git in *repo* and return stdout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _init(repo: Path) -> None:
    """Initialise a repository with a committer identity configured."""
    repo.mkdir(parents=True, exist_ok=True)
    init = subprocess.run(
        ["git", "-C", str(repo), "init"], capture_output=True, text=True
    )
    if init.returncode != 0:
        pytest.skip(f"git init failed: {(init.stderr or '').strip()}")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")


def _commit(repo: Path, message: str, when: str) -> str:
    """Stage everything and commit at *when*, returning the new commit's SHA.

    The date is pinned on both the author and the committer because the
    windowed-history test needs a window that falls strictly between two
    commits, and commits made in one test run otherwise share a timestamp.
    """
    _git(repo, "add", "-A")
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
    )
    return _git(repo, "rev-parse", "HEAD").strip()


# One day apart, so a date window can be placed between any two of them.
_DAY1 = "2020-01-01T00:00:00+00:00"
_DAY2 = "2020-01-02T00:00:00+00:00"
_DAY3 = "2020-01-03T00:00:00+00:00"


_STRANGER = "ORIGINAL note, the one that owned the name first\n"
_REUSER = "NEW note, created under the freed name\n"


def _renamed_away_then_reused(repo: Path) -> tuple[str, str, str]:
    """Build the issue's repository: ``a.md`` renamed to ``b.md``, then reused.

    Returns the three commit SHAs oldest-first: the stranger's creation, the
    rename that freed the name, and the creation of the note now at ``a.md``.
    """
    _init(repo)
    (repo / "a.md").write_text(_STRANGER)
    c1 = _commit(repo, "c1 stranger created as a.md", _DAY1)
    _git(repo, "mv", "a.md", "b.md")
    c2 = _commit(repo, "c2 stranger renamed to b.md", _DAY2)
    (repo / "a.md").write_text(_REUSER)
    c3 = _commit(repo, "c3 new note created as a.md", _DAY3)
    return c1, c2, c3


def _deleted_then_reused(repo: Path) -> tuple[str, str, str]:
    """Same shape, but the name is freed by a delete rather than a rename."""
    _init(repo)
    (repo / "a.md").write_text(_STRANGER)
    c1 = _commit(repo, "c1 stranger created as a.md", _DAY1)
    _git(repo, "rm", "-q", "a.md")
    c2 = _commit(repo, "c2 stranger deleted", _DAY2)
    (repo / "a.md").write_text(_REUSER)
    c3 = _commit(repo, "c3 new note created as a.md", _DAY3)
    return c1, c2, c3


class TestHistoryStopsAtTheNotesBirth:
    """``get_history`` lists the named note's commits and no one else's."""

    def test_rename_freed_the_name(self, tmp_path: Path) -> None:
        """Commits of the note now at b.md are not attributed to a.md."""
        repo = tmp_path / "vault"
        c1, c2, c3 = _renamed_away_then_reused(repo)
        entries = GitWriteStrategy().get_file_history(repo, repo / "a.md", None, 10)
        assert [e.sha for e in entries] == [c3]
        assert c2 not in {e.sha for e in entries}
        assert c1 not in {e.sha for e in entries}

    def test_delete_freed_the_name(self, tmp_path: Path) -> None:
        """A deleted-then-recreated name is the same boundary, not a lineage."""
        repo = tmp_path / "vault"
        _c1, _c2, c3 = _deleted_then_reused(repo)
        entries = GitWriteStrategy().get_file_history(repo, repo / "a.md", None, 10)
        assert [e.sha for e in entries] == [c3]

    def test_date_window_does_not_reopen_the_boundary(self, tmp_path: Path) -> None:
        """A window hiding the birth commit must not resurrect the stranger.

        ``--since`` / ``--until`` trim the stream git returns, so a window that
        excludes the note's creation leaves no birth record in that stream.  The
        boundary has to be established independently of the window, or every
        windowed query walks back into the previous occupant's history.
        """
        repo = tmp_path / "vault"
        _renamed_away_then_reused(repo)
        # A window closing before the note was created: it spans only the
        # stranger's two commits, and neither of them is this note's.
        entries = GitWriteStrategy().get_file_history(
            repo, repo / "a.md", None, 10, "2020-01-02T12:00:00+00:00"
        )
        assert entries == []


class TestRangeDiffDoesNotPairTwoNotes:
    """A single-range ``get_diff`` never pairs one note against another."""

    def test_rename_freed_the_name(self, tmp_path: Path) -> None:
        """Diffing from before the note existed reads as a creation."""
        repo = tmp_path / "vault"
        c1, _c2, _c3 = _renamed_away_then_reused(repo)
        diff = GitWriteStrategy().get_file_diff(repo, repo / "a.md", c1, False)
        assert isinstance(diff, str)
        assert _REUSER.strip() in diff
        assert _STRANGER.strip() not in diff
        assert f"-{_STRANGER.strip()}" not in diff

    def test_delete_freed_the_name(self, tmp_path: Path) -> None:
        """The delete-freed shape reads as a creation too."""
        repo = tmp_path / "vault"
        c1, _c2, _c3 = _deleted_then_reused(repo)
        diff = GitWriteStrategy().get_file_diff(repo, repo / "a.md", c1, False)
        assert isinstance(diff, str)
        assert _REUSER.strip() in diff
        assert _STRANGER.strip() not in diff


class TestPerCommitDiffStopsAtTheNotesBirth:
    """``get_diff(per_commit=True)`` yields only the named note's commits."""

    def test_rename_freed_the_name(self, tmp_path: Path) -> None:
        """The stranger's commits are absent from the per-commit list."""
        repo = tmp_path / "vault"
        c1, c2, c3 = _renamed_away_then_reused(repo)
        diffs = GitWriteStrategy().get_file_diff(repo, repo / "a.md", c1, True)
        assert isinstance(diffs, list)
        assert [d.sha for d in diffs] == [c3]
        assert c2 not in {d.sha for d in diffs}

    def test_delete_freed_the_name(self, tmp_path: Path) -> None:
        """Same for a name freed by a delete."""
        repo = tmp_path / "vault"
        c1, _c2, c3 = _deleted_then_reused(repo)
        diffs = GitWriteStrategy().get_file_diff(repo, repo / "a.md", c1, True)
        assert isinstance(diffs, list)
        assert [d.sha for d in diffs] == [c3]


class TestGenuineLineageIsStillFollowed:
    """The fix must not cost a renamed note the commits that are its own."""

    @staticmethod
    def _renamed_note(repo: Path) -> tuple[str, str, str]:
        """``a.md`` created, edited, then renamed to ``b.md``."""
        _init(repo)
        (repo / "a.md").write_text("one\n")
        c1 = _commit(repo, "c1 created as a.md", _DAY1)
        (repo / "a.md").write_text("one\ntwo\n")
        c2 = _commit(repo, "c2 edited as a.md", _DAY2)
        _git(repo, "mv", "a.md", "b.md")
        c3 = _commit(repo, "c3 renamed to b.md", _DAY3)
        return c1, c2, c3

    def test_history_spans_the_rename(self, tmp_path: Path) -> None:
        """Pre-rename commits belong to the note and are still reported."""
        repo = tmp_path / "vault"
        c1, c2, c3 = self._renamed_note(repo)
        entries = GitWriteStrategy().get_file_history(repo, repo / "b.md", None, 10)
        assert [e.sha for e in entries] == [c3, c2, c1]

    def test_per_commit_diff_spans_the_rename(self, tmp_path: Path) -> None:
        """The per-commit walk still reaches the pre-rename commit."""
        repo = tmp_path / "vault"
        c1, c2, c3 = self._renamed_note(repo)
        diffs = GitWriteStrategy().get_file_diff(repo, repo / "b.md", c1, True)
        assert isinstance(diffs, list)
        assert [d.sha for d in diffs] == [c3, c2]

    def test_range_diff_pairs_the_rename(self, tmp_path: Path) -> None:
        """The range diff still pairs the note's own pre-rename content."""
        repo = tmp_path / "vault"
        c1, _c2, _c3 = self._renamed_note(repo)
        diff = GitWriteStrategy().get_file_diff(repo, repo / "b.md", c1, False)
        assert isinstance(diff, str)
        assert "+two" in diff

    def test_rename_that_also_rewrote_the_note(self, tmp_path: Path) -> None:
        """A rename with a heavy edit is a rename, not a birth (#338).

        At git's default 50% similarity a commit that renames and substantially
        rewrites a note reports as a plain add — indistinguishable, to a walk
        reading records at that threshold, from the name reuse this fix exists
        to catch.  So the boundary walk pins the same 30% the rest of the
        module resolves renames at; without that, the range diff below reads
        as a creation and the caller loses the content delta.
        """
        repo = tmp_path / "vault"
        _init(repo)
        (repo / "a.md").write_text("# Title\nOriginal line\n")
        c1 = _commit(repo, "c1 created as a.md", _DAY1)
        _git(repo, "mv", "a.md", "b.md")
        (repo / "b.md").write_text("# Title\nModified line\n")
        _commit(repo, "c2 renamed and rewritten", _DAY2)
        diff = GitWriteStrategy().get_file_diff(repo, repo / "b.md", c1, False)
        assert isinstance(diff, str)
        assert "-Original line" in diff
        assert "+Modified line" in diff

    def test_diff_from_before_an_unused_name(self, tmp_path: Path) -> None:
        """A name never used before still diffs as a plain creation."""
        repo = tmp_path / "vault"
        _init(repo)
        (repo / "other.md").write_text("unrelated\n")
        c1 = _commit(repo, "c1", _DAY1)
        (repo / "a.md").write_text(_REUSER)
        _commit(repo, "c2 a.md created", _DAY2)
        diff = GitWriteStrategy().get_file_diff(repo, repo / "a.md", c1, False)
        assert isinstance(diff, str)
        assert f"+{_REUSER.strip()}" in diff


class TestBoundaryWalkHelpers:
    """The record walk's own edges, which no repository shape reaches."""

    def test_a_record_that_names_another_path_is_ignored(self) -> None:
        """Only records naming the tracked path move the walk."""
        from markdown_vault_mcp.git.query import _track_records

        assert _track_records(["A", "other.md"], "a.md") == ("a.md", False)

    def test_empty_and_short_records_end_the_walk_quietly(self) -> None:
        """A padding token is skipped; a truncated record stops the scan."""
        from markdown_vault_mcp.git.query import _track_records

        assert _track_records(["", "A", "a.md"], "a.md") == ("a.md", True)
        # An `R` record missing its destination half is not half a rename.
        assert _track_records(["R100", "a.md"], "b.md") == ("b.md", False)

    def test_empty_tree_falls_back_when_git_cannot_resolve_it(
        self, tmp_path: Path
    ) -> None:
        """A repository git cannot read yields the well-known SHA-1 constant."""
        from markdown_vault_mcp.git.query import _EMPTY_TREE_SHA, _empty_tree

        assert _empty_tree(tmp_path / "no-such-repo", None) == _EMPTY_TREE_SHA

    def test_path_outside_the_repository_has_no_relative_form(
        self, tmp_path: Path
    ) -> None:
        """A path outside the git root resolves to None, not an error."""
        from markdown_vault_mcp.git.query import _repo_rel

        assert _repo_rel(tmp_path / "repo", tmp_path / "elsewhere" / "a.md") is None
