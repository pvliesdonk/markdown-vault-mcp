"""Single-note history and diff across a merge (#1306).

``git log --follow`` resolves a note's identity from the diff of a single
parent, so it cannot cross a rename that belongs to no parent's diff — the
shape a ``git mv`` performed while resolving a merge produces.  The revision
reader crosses it by walking ``-m --first-parent``; the history and per-commit
diff walks did not, and reported a note renamed that way as having no past at
all, or — once a later commit landed on the new name — only the commits after
the merge.

Every shape below is one a vault synced from two machines produces, since
``git_sync`` merges whenever both ends moved.  The merge topologies that
already worked are here as the regression guard, because the small fix would
have cost them: a rename made *on* a branch, whose history reaches the note's
birth, and a note edited on a branch and merged, whose history names the
commits that changed it rather than collapsing them into the merge — which is
what restricting the walk to the first parent would have done.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git.strategy import GitWriteStrategy

if TYPE_CHECKING:
    from pathlib import Path


# A body long enough for git to score a rename that also gains a line: the
# similarity index decides whether these topologies are a rename at all.
_BODY = "# Alpha\n" + "".join(f"line {i}\n" for i in range(40))
_SIDE = _BODY + "side change\n"
_LATER = _SIDE + "later\n"

# One day apart, so a date window can be placed between any two commits.
_DAY1 = "2020-01-01T00:00:00+00:00"
_DAY2 = "2020-01-02T00:00:00+00:00"
_DAY3 = "2020-01-03T00:00:00+00:00"
_DAY4 = "2020-01-04T00:00:00+00:00"
_DAY5 = "2020-01-05T00:00:00+00:00"


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
    """Stage everything and commit at *when*, returning the new commit's SHA."""
    _git(repo, "add", "-A")
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
    )
    return _git(repo, "rev-parse", "HEAD").strip()


def _renamed_while_resolving_a_merge(repo: Path) -> tuple[str, str, str, str]:
    """Build a repository whose rename exists only *in* the merge commit.

    ``old.md`` is created on the trunk and edited on a branch; the trunk moves
    on; the merge is resolved by hand and the note renamed to ``renamed.md``
    before the merge is committed.  The rename therefore appears in neither
    parent's diff.

    Returns:
        The four SHAs oldest-first: the note's creation, the branch edit, the
        unrelated trunk commit, and the merge that performed the rename.
    """
    _init(repo)
    (repo / "old.md").write_text(_BODY)
    birth = _commit(repo, "c1 add old.md", _DAY1)
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "old.md").write_text(_SIDE)
    side = _commit(repo, "c2 edit old.md on the branch", _DAY2)
    _git(repo, "checkout", "-q", "-")
    (repo / "other.md").write_text("unrelated\n")
    trunk = _commit(repo, "c3 unrelated trunk commit", _DAY3)
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-commit", "--no-ff", "side"],
        capture_output=True,
        check=False,
    )
    _git(repo, "mv", "old.md", "renamed.md")
    merge = _commit(repo, "c4 merge, renaming during resolution", _DAY4)
    return birth, side, trunk, merge


def _renamed_on_a_branch_then_merged(repo: Path) -> tuple[str, str, str, str]:
    """``old.md`` renamed to ``renamed.md`` on a branch, edited, then merged.

    Returns:
        The four SHAs oldest-first: the note's creation, the rename, the edit
        that followed it on the branch, and the merge.
    """
    _init(repo)
    (repo / "old.md").write_text(_BODY)
    birth = _commit(repo, "c1 add old.md", _DAY1)
    _git(repo, "checkout", "-q", "-b", "side")
    _git(repo, "mv", "old.md", "renamed.md")
    rename = _commit(repo, "c2 rename on the branch", _DAY2)
    (repo / "renamed.md").write_text(_SIDE)
    edit = _commit(repo, "c3 edit renamed.md on the branch", _DAY3)
    _git(repo, "checkout", "-q", "-")
    (repo / "other.md").write_text("unrelated\n")
    _commit(repo, "c4 unrelated trunk commit", _DAY4)
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "-m", "c5 merge side", "side"],
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_AUTHOR_DATE": _DAY5, "GIT_COMMITTER_DATE": _DAY5},
    )
    merge = _git(repo, "rev-parse", "HEAD").strip()
    return birth, rename, edit, merge


def _renamed_on_a_branch_and_again_in_the_merge(
    repo: Path,
) -> tuple[str, str, str, str]:
    """``old.md`` renamed on a branch, then renamed again while merging.

    The name in the middle lives only on the merged branch: the merge's
    first-parent diff records a single rename, ``old.md`` straight to
    ``new.md``, and never mentions ``middle.md`` at all.  Resolving the note's
    names along the first parent alone therefore never learns the name under
    which the branch's own commits were made.

    Returns:
        The four SHAs oldest-first: the note's creation, the rename made on
        the branch, the edit that followed it there, and the merge that
        renamed the note again.
    """
    _init(repo)
    (repo / "old.md").write_text(_BODY)
    birth = _commit(repo, "c1 add old.md", _DAY1)
    _git(repo, "checkout", "-q", "-b", "side")
    _git(repo, "mv", "old.md", "middle.md")
    rename = _commit(repo, "c2 rename to middle.md on the branch", _DAY2)
    (repo / "middle.md").write_text(_SIDE)
    edit = _commit(repo, "c3 edit middle.md on the branch", _DAY3)
    _git(repo, "checkout", "-q", "-")
    (repo / "other.md").write_text("unrelated\n")
    _commit(repo, "c4 unrelated trunk commit", _DAY4)
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-commit", "--no-ff", "side"],
        capture_output=True,
        check=False,
    )
    _git(repo, "mv", "middle.md", "new.md")
    merge = _commit(repo, "c5 merge, renaming again during resolution", _DAY5)
    return birth, rename, edit, merge


def _edited_on_a_branch_then_merged(repo: Path) -> tuple[str, str, str, str]:
    """``note.md`` edited twice on a branch and merged, with no rename at all.

    Returns:
        The four SHAs oldest-first: the note's creation, both branch edits,
        and the merge.
    """
    _init(repo)
    (repo / "note.md").write_text(_BODY)
    birth = _commit(repo, "c1 add note.md", _DAY1)
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "note.md").write_text(_SIDE)
    first = _commit(repo, "c2 first branch edit", _DAY2)
    (repo / "note.md").write_text(_LATER)
    second = _commit(repo, "c3 second branch edit", _DAY3)
    _git(repo, "checkout", "-q", "-")
    (repo / "other.md").write_text("unrelated\n")
    _commit(repo, "c4 unrelated trunk commit", _DAY4)
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "-m", "c5 merge side", "side"],
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_AUTHOR_DATE": _DAY5, "GIT_COMMITTER_DATE": _DAY5},
    )
    merge = _git(repo, "rev-parse", "HEAD").strip()
    return birth, first, second, merge


def _history(repo: Path, name: str) -> list[str]:
    """Return the SHAs ``get_history`` reports for the note at *name*."""
    entries = GitWriteStrategy().get_file_history(repo, repo / name, None, 10)
    return [e.sha for e in entries]


class TestRenameMadeWhileResolvingAMerge:
    """The rename belongs to no parent's diff, but the note still has a past."""

    def test_history_reaches_the_notes_birth(self, tmp_path: Path) -> None:
        """Every commit that touched the note is reported, under either name."""
        repo = tmp_path / "vault"
        birth, side, _trunk, merge = _renamed_while_resolving_a_merge(repo)
        assert _history(repo, "renamed.md") == [merge, side, birth]

    def test_history_is_whole_when_the_new_name_has_commits(
        self, tmp_path: Path
    ) -> None:
        """A later commit on the new name must not truncate the walk.

        This is the shape a fallback that fires only on an empty result would
        miss: ``git log --follow`` returns the post-merge commit and stops at
        the merge, so the answer is short rather than absent.
        """
        repo = tmp_path / "vault"
        birth, side, _trunk, merge = _renamed_while_resolving_a_merge(repo)
        (repo / "renamed.md").write_text(_LATER)
        later = _commit(repo, "c5 later edit", _DAY5)
        assert _history(repo, "renamed.md") == [later, merge, side, birth]

    def test_per_commit_diff_reaches_the_notes_birth(self, tmp_path: Path) -> None:
        """``get_diff(per_commit=True)`` crosses the same rename."""
        repo = tmp_path / "vault"
        birth, side, _trunk, merge = _renamed_while_resolving_a_merge(repo)
        diffs = GitWriteStrategy().get_file_diff(repo, repo / "renamed.md", birth, True)
        assert isinstance(diffs, list)
        assert [d.sha for d in diffs] == [merge, side]

    def test_the_renaming_merge_reads_as_a_rename(self, tmp_path: Path) -> None:
        """The commit that renamed the note is diffed under the name it gave it.

        Which name a recovered commit is paired with decides what its diff
        says.  Targeting the *old* name at the commit that removed it produces
        a diff of the note being deleted; only the new name lets git pair the
        two blobs and render the rename.  The commit list is identical either
        way, so it takes the diff body to tell them apart.
        """
        repo = tmp_path / "vault"
        birth, _side, _trunk, merge = _renamed_while_resolving_a_merge(repo)
        diffs = GitWriteStrategy().get_file_diff(repo, repo / "renamed.md", birth, True)
        assert isinstance(diffs, list)
        renaming = next(d for d in diffs if d.sha == merge)
        assert "a/old.md b/renamed.md" in renaming.diff
        assert "+side change" in renaming.diff

    def test_a_window_still_bounds_the_history(self, tmp_path: Path) -> None:
        """``--since`` trims the commits reported, in either name's segment."""
        repo = tmp_path / "vault"
        _birth, side, _trunk, merge = _renamed_while_resolving_a_merge(repo)
        entries = GitWriteStrategy().get_file_history(
            repo, repo / "renamed.md", "2020-01-01T12:00:00+00:00", 10
        )
        assert [e.sha for e in entries] == [merge, side]

    def test_the_far_end_of_the_window_is_bounded_too(self, tmp_path: Path) -> None:
        """``--until`` reaches the recovered names as well as the current one."""
        repo = tmp_path / "vault"
        birth, side, _trunk, _merge = _renamed_while_resolving_a_merge(repo)
        entries = GitWriteStrategy().get_file_history(
            repo, repo / "renamed.md", None, 10, "2020-01-03T12:00:00+00:00"
        )
        assert [e.sha for e in entries] == [side, birth]

    def test_a_shallow_clone_reports_only_what_it_holds(self, tmp_path: Path) -> None:
        """A truncated history is answered truthfully, not extrapolated.

        The clone holds the merge and nothing before it, so there is no
        creation record to tell the walk where the earlier name stops being
        this note's — and a name recovered without that boundary could belong
        to another note entirely.  The reader reports what the repository can
        support instead.
        """
        source = tmp_path / "vault"
        _birth, _side, _trunk, merge = _renamed_while_resolving_a_merge(source)
        shallow = tmp_path / "shallow"
        clone = subprocess.run(
            ["git", "clone", "--depth", "1", f"file://{source}", str(shallow)],
            capture_output=True,
            text=True,
        )
        if clone.returncode != 0:
            pytest.skip(f"shallow clone failed: {(clone.stderr or '').strip()}")
        assert _history(shallow, "renamed.md") == [merge]


class TestANameThatLivedOnlyOnTheBranch:
    """A merge's first-parent diff can hide a whole name (#1314 review).

    Where a note is renamed on a branch and renamed *again* while the branch
    is merged, the merge records one rename from the trunk's name to the name
    it settled on.  The name in between belongs to no first-parent diff, so
    resolving identity along the first parent alone never learns it — and the
    branch's commits, made under that name, go unreported.
    """

    def test_history_reports_the_commits_made_under_it(self, tmp_path: Path) -> None:
        """The branch's own commits are the note's, under whatever name."""
        repo = tmp_path / "vault"
        birth, rename, edit, merge = _renamed_on_a_branch_and_again_in_the_merge(repo)
        assert _history(repo, "new.md") == [merge, edit, rename, birth]

    def test_the_branch_rename_reads_as_a_rename(self, tmp_path: Path) -> None:
        """Its diff is taken under the name the branch gave the note.

        A ``git mv`` with no edit has no content diff, so what a reader gets
        is the rename itself (#683).  Recovered under the trunk's name
        instead, the same commit renders as that file being deleted.
        """
        repo = tmp_path / "vault"
        birth, rename, _edit, _merge = _renamed_on_a_branch_and_again_in_the_merge(repo)
        diffs = GitWriteStrategy().get_file_diff(repo, repo / "new.md", birth, True)
        assert isinstance(diffs, list)
        renaming = next(d for d in diffs if d.sha == rename)
        assert "old.md => middle.md" in renaming.diff
        assert "deleted file" not in renaming.diff


class TestRenameMadeOnABranch:
    """A rename in a parent's diff was reachable already, and stays reachable."""

    def test_history_reaches_the_notes_birth(self, tmp_path: Path) -> None:
        """The commit that created the note under its old name is its own."""
        repo = tmp_path / "vault"
        birth, rename, edit, _merge = _renamed_on_a_branch_then_merged(repo)
        assert _history(repo, "renamed.md") == [edit, rename, birth]


class TestTheBoundaryHoldsAcrossTheMerge:
    """Recovering the earlier name must not recover another note with it."""

    def test_a_predecessor_of_the_old_name_stays_out(self, tmp_path: Path) -> None:
        """The name the note was created under may have had an owner before.

        The commits from before this note existed are its predecessor's, and
        the walk that recovers the old name has to stop at the same boundary
        the ``--follow`` walk does (#1285).
        """
        repo = tmp_path / "vault"
        _init(repo)
        (repo / "old.md").write_text("A STRANGER, the first owner of the name\n")
        stranger = _commit(repo, "c0 stranger created as old.md", _DAY1)
        _git(repo, "rm", "-q", "old.md")
        freed = _commit(repo, "c1 stranger deleted", _DAY2)
        (repo / "old.md").write_text(_BODY)
        birth = _commit(repo, "c2 add old.md", _DAY3)
        _git(repo, "checkout", "-q", "-b", "side")
        (repo / "old.md").write_text(_SIDE)
        side = _commit(repo, "c3 edit old.md on the branch", _DAY4)
        _git(repo, "checkout", "-q", "-")
        (repo / "other.md").write_text("unrelated\n")
        _commit(repo, "c4 unrelated trunk commit", _DAY5)
        subprocess.run(
            ["git", "-C", str(repo), "merge", "--no-commit", "--no-ff", "side"],
            capture_output=True,
            check=False,
        )
        _git(repo, "mv", "old.md", "renamed.md")
        merge = _commit(repo, "c5 merge, renaming during resolution", _DAY5)

        reported = _history(repo, "renamed.md")
        assert reported == [merge, side, birth]
        assert stranger not in reported
        assert freed not in reported

    def test_a_successor_to_the_old_name_stays_out(self, tmp_path: Path) -> None:
        """The rename frees the old name, and a new note may take it.

        The recovered name is bounded above by the commit that renamed the
        note away, so the note created under the freed name afterwards is not
        reachable from it — its commits are its own.
        """
        repo = tmp_path / "vault"
        birth, side, _trunk, merge = _renamed_while_resolving_a_merge(repo)
        (repo / "old.md").write_text("A SUCCESSOR, created under the freed name\n")
        successor = _commit(repo, "c5 new note created as old.md", _DAY5)
        (repo / "old.md").write_text("A SUCCESSOR, edited\n")
        successor_edit = _commit(repo, "c6 successor edited", _DAY5)

        reported = _history(repo, "renamed.md")
        assert reported == [merge, side, birth]
        assert successor not in reported
        assert successor_edit not in reported


class TestMergesThatChangedNothingStayOut:
    """The fix must not cost a note the commits that name their author."""

    def test_branch_edits_are_reported_not_the_merge(self, tmp_path: Path) -> None:
        """Restricting the walk to the first parent would collapse these.

        Under ``--first-parent`` the two branch edits disappear and the merge
        stands in for both, so a caller asking who changed the note is told
        "the merge".  The merge changed nothing here relative to what it
        merged, and stays out.
        """
        repo = tmp_path / "vault"
        birth, first, second, merge = _edited_on_a_branch_then_merged(repo)
        reported = _history(repo, "note.md")
        assert reported == [second, first, birth]
        assert merge not in reported


class TestSegmentWalkEdges:
    """Where git cannot answer, the supplement adds nothing rather than lying.

    Each of these points the helper at a directory that is not a repository,
    which is how a caller reaches a failed git invocation without staging one.
    """

    def test_names_are_unknown_when_the_walk_cannot_run(self, tmp_path: Path) -> None:
        """No segments, so both readers keep the answer they already had."""
        from markdown_vault_mcp.git.query import _name_segments

        assert _name_segments(tmp_path / "no-such-repo", "a.md", None) == []

    def test_a_failed_segment_walk_contributes_nothing(self, tmp_path: Path) -> None:
        """One unreadable segment is skipped, not raised through."""
        from markdown_vault_mcp.git.query import (
            _NameSegment,
            _segment_shas,
            _WalkBounds,
        )

        segments = [_NameSegment("b.md", "deadbeef", "cafef00d", None)]
        found = _segment_shas(tmp_path / "no-such-repo", segments, _WalkBounds(), None)
        assert found == {}

    def test_entries_are_empty_when_they_cannot_be_formatted(
        self, tmp_path: Path
    ) -> None:
        """The union cannot be described, so the caller is given nothing new."""
        from markdown_vault_mcp.git.query import _history_entries_for

        assert (
            _history_entries_for(tmp_path / "no-such-repo", {"deadbeef"}, "", None)
            == []
        )

    def test_rows_are_empty_when_they_cannot_be_formatted(self, tmp_path: Path) -> None:
        """Same for the per-commit reader's half of the union."""
        from markdown_vault_mcp.git.query import _commit_rows_for

        rows = _commit_rows_for(tmp_path / "no-such-repo", {"deadbeef"}, "a.md", None)
        assert rows == []

    def test_ordering_is_empty_when_git_cannot_supply_it(self, tmp_path: Path) -> None:
        """An empty order leaves the caller's own order in place."""
        from markdown_vault_mcp.git.query import _canonical_order

        assert _canonical_order(tmp_path / "no-such-repo", {"deadbeef"}, None) == []

    def test_a_merges_branches_are_unknown_when_git_cannot_answer(
        self, tmp_path: Path
    ) -> None:
        """No parents means no branch to walk, and no name recovered from one."""
        from markdown_vault_mcp.git.query import _parents

        assert _parents(tmp_path / "no-such-repo", "deadbeef", None) == []

    def test_a_segment_reaching_back_to_a_first_commit_has_no_lower_bound(
        self, tmp_path: Path
    ) -> None:
        """A parent-less commit leaves nothing older to exclude."""
        from markdown_vault_mcp.git.query import _first_parent

        repo = tmp_path / "vault"
        _init(repo)
        (repo / "a.md").write_text(_BODY)
        root = _commit(repo, "c1 add a.md", _DAY1)
        (repo / "a.md").write_text(_SIDE)
        second = _commit(repo, "c2 edit a.md", _DAY2)

        assert _first_parent(repo, root, None) is None
        assert _first_parent(repo, second, None) == root
