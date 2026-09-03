"""Revision reads and the overwrite breadcrumb (#1137).

The point of these tests is what the feature *refuses*. A revision read exists
so a caller can put the content back, so handing back bytes belonging to some
other note is worse than any error: every shape below where git cannot prove
the note the caller named existed at that revision must raise, and each one is
a shape a real vault produces.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.types import RevisionQuery
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_CAP = 262144


def _git(repo: Path, *args: str) -> str:
    """Run git in *repo* and return stdout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _init(repo: Path, *, object_format: str | None = None) -> None:
    """Initialise a repository with a committer identity configured."""
    repo.mkdir(parents=True, exist_ok=True)
    args = ["git", "-C", str(repo), "init"]
    if object_format is not None:
        args.append(f"--object-format={object_format}")
    init = subprocess.run(args, capture_output=True, text=True)
    if init.returncode != 0:
        pytest.skip(f"git init failed: {(init.stderr or '').strip()}")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")


def _commit(repo: Path, message: str) -> str:
    """Stage everything and commit, returning the new commit's SHA."""
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _write(repo: Path, rel: str, text: str) -> None:
    """Write *text* to *rel* inside *repo*, creating parent directories."""
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def _read_at(
    repo: Path, note: str, ref: str, *, vault: Path | None = None, cap: int = _CAP
):
    """Read *note* at *ref* through a fresh strategy (git roots are memoised)."""
    root = vault if vault is not None else repo
    return GitWriteStrategy().get_file_at_ref(
        RevisionQuery(repo_path=root, path=root / note, ref=ref, max_bytes=cap)
    )


_BODY = "# Alpha\n" + "".join(f"line {i}\n" for i in range(40))
_LONG_BODY = "# Alpha\n" + "".join(f"line {i}\n" for i in range(60))


class TestResolvesTheNote:
    """Shapes where git's records do connect the note to the revision."""

    def test_unchanged_note(self, tmp_path: Path) -> None:
        """The base case: same path, same lineage, content comes back."""
        _init(tmp_path)
        _write(tmp_path, "note.md", "# v1\n")
        first = _commit(tmp_path, "add")
        _write(tmp_path, "note.md", "# v2\n")
        _commit(tmp_path, "edit")

        result = _read_at(tmp_path, "note.md", first)
        assert result.content == "# v1\n"
        assert result.historical_path == "note.md"
        assert result.revision == first

    def test_plain_rename_reports_the_historical_path(self, tmp_path: Path) -> None:
        """A renamed note is read by the name it has today, not the old one."""
        _init(tmp_path)
        _write(tmp_path, "old.md", _BODY)
        first = _commit(tmp_path, "add")
        _git(tmp_path, "mv", "old.md", "new.md")
        _commit(tmp_path, "rename")

        result = _read_at(tmp_path, "new.md", first)
        assert result.content == _BODY
        assert result.path == "new.md"
        assert result.historical_path == "old.md"

    def test_rename_then_later_rewrite(self, tmp_path: Path) -> None:
        """Renamed at one commit and rewritten at a later one still resolves.

        The endpoint diff git would compute between the two revisions reports
        an unrelated add and delete here; walking per commit keeps the rename.
        """
        _init(tmp_path)
        _write(tmp_path, "old.md", _BODY)
        first = _commit(tmp_path, "add")
        _git(tmp_path, "mv", "old.md", "new.md")
        _commit(tmp_path, "rename")
        _write(tmp_path, "new.md", "# Totally different\n" + "x\n" * 40)
        _commit(tmp_path, "rewrite")

        assert _read_at(tmp_path, "new.md", first).historical_path == "old.md"

    def test_rename_that_also_rewrote_half_the_note(self, tmp_path: Path) -> None:
        """A 30%-similar rename resolves; git's own 50% default would not.

        Pins the threshold as this feature's, not the operator's: at git's
        default this shape reports a bare add and the read would refuse.
        """
        _init(tmp_path)
        _write(tmp_path, "old.md", _LONG_BODY)
        first = _commit(tmp_path, "add")
        _git(tmp_path, "mv", "old.md", "new.md")
        _write(
            tmp_path,
            "new.md",
            "# Alpha\n"
            + "".join(
                f"rewritten {i}\n" if i < 35 else f"line {i}\n" for i in range(60)
            ),
        )
        rewrote = _commit(tmp_path, "rename and rewrite")

        # The premise: git's own default would not pair these as a rename.
        at_default = _git(tmp_path, "show", "--name-status", "--format=", rewrote)
        assert "R" not in at_default.split("\t")[0]

        assert _read_at(tmp_path, "new.md", first).historical_path == "old.md"

    def test_rename_carried_in_by_a_merge(self, tmp_path: Path) -> None:
        """A rename made on a branch and merged is still followed."""
        _init(tmp_path)
        _write(tmp_path, "old.md", _BODY)
        first = _commit(tmp_path, "add")
        _git(tmp_path, "checkout", "-q", "-b", "side")
        _git(tmp_path, "mv", "old.md", "new.md")
        _commit(tmp_path, "rename on side")
        _git(tmp_path, "checkout", "-q", "-")
        _write(tmp_path, "other.md", "unrelated\n")
        _commit(tmp_path, "unrelated on trunk")
        _git(tmp_path, "merge", "--no-edit", "side")

        assert _read_at(tmp_path, "new.md", first).historical_path == "old.md"

    def test_rename_performed_while_resolving_a_merge(self, tmp_path: Path) -> None:
        """A rename that exists only *in* the merge commit is still followed.

        This shape belongs to no parent's diff, so a walk without
        ``-m --first-parent`` sees no records at all and would fall through to
        trusting today's path at an old revision — the exact way a revision
        read can return a different note's content.
        """
        _init(tmp_path)
        _write(tmp_path, "old.md", _BODY)
        first = _commit(tmp_path, "add")
        _git(tmp_path, "checkout", "-q", "-b", "side")
        _write(tmp_path, "old.md", _BODY + "side change\n")
        _commit(tmp_path, "side edit")
        _git(tmp_path, "checkout", "-q", "-")
        _write(tmp_path, "other.md", "unrelated\n")
        _commit(tmp_path, "trunk edit")
        subprocess.run(
            ["git", "-C", str(tmp_path), "merge", "--no-commit", "--no-ff", "side"],
            capture_output=True,
        )
        _git(tmp_path, "mv", "old.md", "renamed.md")
        _commit(tmp_path, "merge, renaming during resolution")

        result = _read_at(tmp_path, "renamed.md", first)
        assert result.historical_path == "old.md"
        assert result.content == _BODY

    def test_note_deleted_since_is_still_readable(self, tmp_path: Path) -> None:
        """Recovering a deleted note is the other half of recovery."""
        _init(tmp_path)
        _write(tmp_path, "gone.md", "# Wanted\n")
        first = _commit(tmp_path, "add")
        _git(tmp_path, "rm", "-q", "gone.md")
        _commit(tmp_path, "delete")

        assert _read_at(tmp_path, "gone.md", first).content == "# Wanted\n"

    def test_non_ascii_path(self, tmp_path: Path) -> None:
        """Paths git would octal-escape survive: the walk is NUL-framed (#1282)."""
        _init(tmp_path)
        _write(tmp_path, "café.md", "# Accented\n")
        first = _commit(tmp_path, "add")
        _git(tmp_path, "mv", "café.md", "résumé.md")
        _commit(tmp_path, "rename")

        result = _read_at(tmp_path, "résumé.md", first)
        assert result.historical_path == "café.md"
        assert result.content == "# Accented\n"

    def test_vault_nested_below_the_git_root(self, tmp_path: Path) -> None:
        """The walk speaks git's paths, and reports the vault's.

        Git emits repository-root-relative paths while the vault's own paths
        start at the vault root. Comparing the two forms directly would make
        every rename and creation test silently never match, disabling the
        refusals this feature is built on.
        """
        _init(tmp_path)
        vault = tmp_path / "vault"
        _write(tmp_path, "vault/old.md", _BODY)
        first = _commit(tmp_path, "add")
        _git(tmp_path, "mv", "vault/old.md", "vault/new.md")
        _commit(tmp_path, "rename")

        result = _read_at(tmp_path, "new.md", first, vault=vault)
        assert result.path == "new.md"
        assert result.historical_path == "old.md"
        assert result.content == _BODY

    def test_sha256_repository(self, tmp_path: Path) -> None:
        """A 64-hex revision reads like any other (#1284)."""
        _init(tmp_path, object_format="sha256")
        _write(tmp_path, "note.md", "# v1\n")
        first = _commit(tmp_path, "add")
        if len(first) != 64:
            pytest.skip("local git does not mint sha256 object IDs")
        _write(tmp_path, "note.md", "# v2\n")
        _commit(tmp_path, "edit")

        assert _read_at(tmp_path, "note.md", first).content == "# v1\n"


class TestRefusesRatherThanGuess:
    """Shapes where git's records do not reach — every one must raise."""

    def test_reused_filename(self, tmp_path: Path) -> None:
        """A name reused by a different note refuses, naming the creation.

        The reported shape: ``a.md`` renamed to ``b.md``, then a new and
        unrelated ``a.md`` created. Reading today's ``a.md`` at the first
        revision must not return what now lives in ``b.md`` — the documented
        recovery route would write it straight into ``a.md``.
        """
        _init(tmp_path)
        _write(tmp_path, "a.md", "ORIGINAL - this note is now called b.md\n")
        first = _commit(tmp_path, "add")
        _git(tmp_path, "mv", "a.md", "b.md")
        _commit(tmp_path, "rename")
        _write(tmp_path, "a.md", "NEW - a different note that reuses the name\n")
        _commit(tmp_path, "reuse the name")

        with pytest.raises(ValueError, match="was created after revision") as exc:
            _read_at(tmp_path, "a.md", first)
        assert "ORIGINAL" not in str(exc.value)

    def test_reused_filename_the_other_note_still_resolves(
        self, tmp_path: Path
    ) -> None:
        """The mirror: the note that was renamed away reads back correctly."""
        _init(tmp_path)
        _write(tmp_path, "a.md", "ORIGINAL - this note is now called b.md\n")
        first = _commit(tmp_path, "add")
        _git(tmp_path, "mv", "a.md", "b.md")
        _commit(tmp_path, "rename")
        _write(tmp_path, "a.md", "NEW - a different note that reuses the name\n")
        _commit(tmp_path, "reuse the name")

        result = _read_at(tmp_path, "b.md", first)
        assert result.content.startswith("ORIGINAL")
        assert result.historical_path == "a.md"

    def test_name_reused_by_a_note_similar_enough_to_look_copied(
        self, tmp_path: Path
    ) -> None:
        """git may call the reusing note a *copy* of the renamed one; still no.

        A copy is a birth, not a continuation: the note now at that path did
        not exist at the revision asked for, whichever way git classifies the
        resemblance.
        """
        _init(tmp_path)
        _write(tmp_path, "alpha.md", "# Alpha\n\nVersion 1.\n")
        first = _commit(tmp_path, "add")
        _write(tmp_path, "alpha.md", "# Alpha\n\nVersion 2.\n")
        _commit(tmp_path, "edit")
        _git(tmp_path, "mv", "alpha.md", "beta.md")
        _commit(tmp_path, "rename")
        _write(tmp_path, "alpha.md", "# Alpha\n\nA different note.\n")
        _commit(tmp_path, "reuse the name")

        with pytest.raises(ValueError, match="did not exist at that revision") as exc:
            _read_at(tmp_path, "alpha.md", first)
        assert "Version 1" not in str(exc.value)

    def test_deleted_and_recreated_at_the_same_path(self, tmp_path: Path) -> None:
        """Git cannot tell a recreation from a reused name, so neither do we."""
        _init(tmp_path)
        _write(tmp_path, "a.md", "FIRST\n")
        first = _commit(tmp_path, "add")
        _git(tmp_path, "rm", "-q", "a.md")
        _commit(tmp_path, "delete")
        _write(tmp_path, "a.md", "SECOND\n")
        _commit(tmp_path, "recreate")

        with pytest.raises(ValueError, match="was created after revision"):
            _read_at(tmp_path, "a.md", first)

    def test_untracked_note_at_a_reused_path(self, tmp_path: Path) -> None:
        """An uncommitted note leaves no creation record to stop the walk.

        Committed, deleted, then recreated *untracked*, git reports a delete
        and nothing else — so the walk alone would hand back the deleted
        note's bytes under the new note's name.
        """
        _init(tmp_path)
        _write(tmp_path, "a.md", "OLD note\n")
        first = _commit(tmp_path, "add")
        _git(tmp_path, "rm", "-q", "a.md")
        _commit(tmp_path, "delete")
        _write(tmp_path, "a.md", "NEW untracked note\n")

        with pytest.raises(ValueError, match="not tracked by git") as exc:
            _read_at(tmp_path, "a.md", first)
        assert "OLD note" not in str(exc.value)

    def test_rename_that_rewrote_the_note_entirely(self, tmp_path: Path) -> None:
        """Below any similarity threshold git reports an add; that is a refusal."""
        _init(tmp_path)
        _write(tmp_path, "old.md", _BODY)
        first = _commit(tmp_path, "add")
        _git(tmp_path, "mv", "old.md", "new.md")
        _write(tmp_path, "new.md", "# Unrelated\n" + "different\n" * 40)
        _commit(tmp_path, "rename and rewrite entirely")

        with pytest.raises(ValueError, match="was created after revision"):
            _read_at(tmp_path, "new.md", first)

    def test_note_absent_at_that_revision(self, tmp_path: Path) -> None:
        """A note created later has nothing to return at an earlier revision."""
        _init(tmp_path)
        _write(tmp_path, "first.md", "# One\n")
        first = _commit(tmp_path, "add")
        _write(tmp_path, "second.md", "# Two\n")
        _commit(tmp_path, "add another")

        with pytest.raises(ValueError, match="was created after revision"):
            _read_at(tmp_path, "second.md", first)

    def test_revision_that_is_not_an_ancestor(self, tmp_path: Path) -> None:
        """A SHA from history that was rebased away cannot anchor the walk."""
        _init(tmp_path)
        _write(tmp_path, "note.md", "# v1\n")
        _commit(tmp_path, "add")
        _git(tmp_path, "checkout", "-q", "-b", "side")
        _write(tmp_path, "note.md", "# side\n")
        stranded = _commit(tmp_path, "side edit")
        _git(tmp_path, "checkout", "-q", "-")

        with pytest.raises(ValueError, match="not an ancestor"):
            _read_at(tmp_path, "note.md", stranded)

    def test_unknown_revision(self, tmp_path: Path) -> None:
        """A SHA no object matches is rejected before any content is read."""
        _init(tmp_path)
        _write(tmp_path, "note.md", "# v1\n")
        _commit(tmp_path, "add")

        with pytest.raises(ValueError, match="not an ancestor"):
            _read_at(tmp_path, "note.md", "0" * 40)

    def test_note_that_was_a_symlink_at_that_revision(self, tmp_path: Path) -> None:
        """Git stores a symlink's target string, which is not note content.

        The note is an ordinary file today, so nothing on disk warns the
        caller that the revision they asked for holds a link.
        """
        _init(tmp_path)
        _write(tmp_path, "real.md", "# Real\n")
        (tmp_path / "note.md").symlink_to("real.md")
        first = _commit(tmp_path, "add")
        (tmp_path / "note.md").unlink()
        _write(tmp_path, "note.md", "# A real note now\n")
        _commit(tmp_path, "replace the link with a file")

        with pytest.raises(ValueError, match="was a symlink"):
            _read_at(tmp_path, "note.md", first)

    def test_symlinked_note_today_follows_the_link(self, tmp_path: Path) -> None:
        """A link on disk reads as its target, matching a plain read of it."""
        _init(tmp_path)
        _write(tmp_path, "real.md", "# Real v1\n")
        (tmp_path / "link.md").symlink_to("real.md")
        first = _commit(tmp_path, "add")
        _write(tmp_path, "real.md", "# Real v2\n")
        _commit(tmp_path, "edit the target")

        result = _read_at(tmp_path, "link.md", first)
        assert result.content == "# Real v1\n"
        assert result.historical_path == "real.md"

    def test_content_that_is_not_utf8(self, tmp_path: Path) -> None:
        """A revision whose bytes are not text is an error, not a decode crash."""
        _init(tmp_path)
        (tmp_path / "note.md").write_bytes(b"\xff\xfe not text \x00\x01")
        first = _commit(tmp_path, "add")

        with pytest.raises(ValueError, match="not valid UTF-8"):
            _read_at(tmp_path, "note.md", first)

    def test_content_over_the_read_cap(self, tmp_path: Path) -> None:
        """The cap is checked from git's object size, before the blob is read."""
        _init(tmp_path)
        _write(tmp_path, "big.md", "x" * 5000)
        first = _commit(tmp_path, "add")

        with pytest.raises(ValueError, match="MAX_NOTE_READ_BYTES"):
            _read_at(tmp_path, "big.md", first, cap=1000)

    def test_uncapped_read_returns_everything(self, tmp_path: Path) -> None:
        """``max_bytes=0`` disables the cap, matching the on-disk read."""
        _init(tmp_path)
        _write(tmp_path, "big.md", "x" * 5000)
        first = _commit(tmp_path, "add")

        assert len(_read_at(tmp_path, "big.md", first, cap=0).content) == 5000

    def test_vault_without_git(self, tmp_path: Path) -> None:
        """No git means raise: an empty string would read as an empty note."""
        with pytest.raises(ValueError, match="requires a git-backed vault"):
            GitWriteStrategy().get_file_at_ref(
                RevisionQuery(
                    repo_path=tmp_path,
                    path=tmp_path / "note.md",
                    ref="abcdef",
                    max_bytes=_CAP,
                )
            )


class TestCommittedRevision:
    """The breadcrumb probe: it names a commit only when it can prove one."""

    def test_clean_note_names_its_newest_commit(self, tmp_path: Path) -> None:
        _init(tmp_path)
        _write(tmp_path, "note.md", "# v1\n")
        _commit(tmp_path, "add")
        _write(tmp_path, "other.md", "unrelated\n")
        latest_unrelated = _commit(tmp_path, "unrelated")

        found = GitWriteStrategy().committed_revision(tmp_path, tmp_path / "note.md")
        assert (
            found == _git(tmp_path, "rev-list", "-1", "HEAD", "--", "note.md").strip()
        )
        assert found != latest_unrelated

    def test_modified_note_has_no_breadcrumb(self, tmp_path: Path) -> None:
        """Uncommitted edits mean no commit holds what is about to be replaced."""
        _init(tmp_path)
        _write(tmp_path, "note.md", "# v1\n")
        _commit(tmp_path, "add")
        _write(tmp_path, "note.md", "# edited on disk\n")

        assert (
            GitWriteStrategy().committed_revision(tmp_path, tmp_path / "note.md")
            is None
        )

    def test_untracked_note_has_no_breadcrumb(self, tmp_path: Path) -> None:
        _init(tmp_path)
        _write(tmp_path, "seed.md", "# seed\n")
        _commit(tmp_path, "add")
        _write(tmp_path, "fresh.md", "# never committed\n")

        assert (
            GitWriteStrategy().committed_revision(tmp_path, tmp_path / "fresh.md")
            is None
        )

    def test_recreated_after_a_committed_delete_has_no_breadcrumb(
        self, tmp_path: Path
    ) -> None:
        """The note's newest commit is its *deletion*; it holds no content.

        A working-tree comparison against HEAD would call this clean, because
        an untracked file is invisible to it — the probe compares against the
        named commit instead.
        """
        _init(tmp_path)
        _write(tmp_path, "note.md", "# committed once\n")
        _commit(tmp_path, "add")
        _git(tmp_path, "rm", "-q", "note.md")
        _commit(tmp_path, "delete")
        _write(tmp_path, "note.md", "# back, untracked\n")

        assert (
            GitWriteStrategy().committed_revision(tmp_path, tmp_path / "note.md")
            is None
        )

    def test_no_git_yields_no_breadcrumb(self, tmp_path: Path) -> None:
        _write(tmp_path, "note.md", "# v1\n")
        assert (
            GitWriteStrategy().committed_revision(tmp_path, tmp_path / "note.md")
            is None
        )


@pytest.fixture
def git_vault(tmp_path: Path) -> Iterator[Vault]:
    """A writable, git-backed vault holding one committed note."""
    repo = tmp_path / "vault"
    _init(repo)
    _write(repo, "note.md", "---\ntitle: Original\n---\n\n# Head\n\nbody text\n")
    _commit(repo, "seed")
    strategy = GitWriteStrategy(push_delay_s=0)
    vault = Vault(
        source_dir=repo, git_strategy=strategy, on_write=strategy, read_only=False
    )
    vault.index.build_index()
    try:
        yield vault
    finally:
        vault.close()


class TestRecoveryThroughTheVault:
    """The route the tools document, exercised end to end."""

    def test_overwrite_then_restore_round_trips_byte_for_byte(
        self, git_vault: Vault
    ) -> None:
        """The documented two-step returns the file to exactly what it was.

        Including frontmatter: ``read_revision`` hands back the raw file and
        ``write`` with no ``frontmatter`` argument stores content verbatim, so
        the two compose without the header being re-serialised or doubled.
        """
        original = (git_vault.source_dir / "note.md").read_text()

        overwrite = git_vault.writer.write("note.md", "# Clobbered\n")
        assert overwrite.created is False
        assert overwrite.previous_revision is not None

        replaced = git_vault.reader.read_revision(
            "note.md", overwrite.previous_revision
        )
        assert replaced.content == original
        assert replaced.historical_path == "note.md"

        current = git_vault.reader.read("note.md")
        assert current is not None
        git_vault.writer.write("note.md", replaced.content, if_match=current.etag)
        assert (git_vault.source_dir / "note.md").read_text() == original

    def test_creating_a_note_has_no_breadcrumb(self, git_vault: Vault) -> None:
        """A create replaces nothing, so there is nothing to point back to."""
        result = git_vault.writer.write("fresh.md", "# New\n")
        assert result.created is True
        assert result.previous_revision is None

    def test_uncommitted_content_gets_no_breadcrumb(self, git_vault: Vault) -> None:
        """Two writes in a row: the first one's content never reached a commit.

        The commit is asynchronous, so at the second write the note's newest
        commit no longer holds what is being replaced.  Naming it anyway would
        hand the caller a revision that looks like the replaced content
        without being it.
        """
        git_vault.writer.write("note.md", "# First overwrite\n")
        second = git_vault.writer.write("note.md", "# Second overwrite\n")
        assert second.previous_revision is None

    def test_section_of_a_historical_revision(self, git_vault: Vault) -> None:
        overwrite = git_vault.writer.write("note.md", "# Clobbered\n")
        assert overwrite.previous_revision is not None

        section = git_vault.reader.read_revision(
            "note.md", overwrite.previous_revision, section="Head"
        )
        assert section.content.strip() == "body text"

    def test_missing_section_lists_what_was_there(self, git_vault: Vault) -> None:
        overwrite = git_vault.writer.write("note.md", "# Clobbered\n")
        assert overwrite.previous_revision is not None

        with pytest.raises(ValueError, match="Headings there: 'Head'"):
            git_vault.reader.read_revision(
                "note.md", overwrite.previous_revision, section="Absent"
            )

    def test_attachment_paths_are_refused(self, git_vault: Vault) -> None:
        """Attachment content at a revision is binary; the tool returns text."""
        with pytest.raises(ValueError, match="markdown notes"):
            git_vault.reader.read_revision("assets/x.png", "a" * 40)

    @pytest.mark.parametrize("revision", ["HEAD~1", "--all", "ABCDEF", "abc", "main"])
    def test_revision_must_be_an_object_id(
        self, git_vault: Vault, revision: str
    ) -> None:
        """Caller text never reaches the git argv as a revision expression."""
        with pytest.raises(ValueError, match="Invalid revision"):
            git_vault.reader.read_revision("note.md", revision)

    def test_maintenance_writes_do_not_probe_git(self, git_vault: Vault) -> None:
        """The breadcrumb costs nothing on the server's own bulk write path.

        ``DocumentManager.write`` is what OKF link conversion drives across
        every note in a vault; a git probe wired in there would spend two
        subprocesses per file on a result no caller ever sees.  The probe
        lives one layer up, in the facet the tools call.
        """
        calls: list[str] = []
        original = git_vault._git_query_mgr.committed_revision

        def counting(path: str) -> str | None:
            calls.append(path)
            return original(path)

        git_vault._writer_facet._previous_revision = counting
        git_vault._doc_mgr.write("note.md", "# maintenance rewrite\n")
        assert calls == []

        git_vault.writer.write("note.md", "# through the facet\n")
        assert calls == ["note.md"]


class TestWithoutGit:
    """A vault with no git backing: reads raise, writes carry no breadcrumb."""

    def test_read_revision_raises(self, tmp_path: Path) -> None:
        _write(tmp_path, "note.md", "# Plain\n")
        vault = Vault(source_dir=tmp_path, read_only=False)
        vault.index.build_index()
        try:
            with pytest.raises(ValueError, match="requires a git-backed vault"):
                vault.reader.read_revision("note.md", "a" * 40)
        finally:
            vault.close()

    def test_write_reports_no_previous_revision(self, tmp_path: Path) -> None:
        _write(tmp_path, "note.md", "# Plain\n")
        vault = Vault(source_dir=tmp_path, read_only=False)
        vault.index.build_index()
        try:
            assert vault.writer.write("note.md", "# New\n").previous_revision is None
        finally:
            vault.close()


class TestWalkRules:
    """The record-walk rules, exercised directly on git's record stream.

    A malformed or unfamiliar record stream is exactly where a walk is
    tempted to carry on and answer anyway, so the rules get their own tests
    rather than only the shapes that happen to produce them.
    """

    def test_unrecognised_record_refuses(self) -> None:
        """An unfamiliar status is not evidence of continuity."""
        from markdown_vault_mcp.git.query import _path_at_ref

        stream = "\x1eSHA\0\nU\0note.md\0"
        with pytest.raises(ValueError, match="does not establish"):
            _path_at_ref(stream, "note.md", "abc123")

    def test_record_for_another_path_refuses(self) -> None:
        """A record about a file the walk is not tracking proves nothing."""
        from markdown_vault_mcp.git.query import _path_at_ref

        stream = "\x1eSHA\0\nM\0other.md\0"
        with pytest.raises(ValueError, match="does not establish"):
            _path_at_ref(stream, "note.md", "abc123")

    def test_truncated_record_ends_the_walk(self) -> None:
        """A rename record missing its second path is dropped, not guessed at."""
        from markdown_vault_mcp.git.query import _iter_name_status

        assert list(_iter_name_status("\x1eSHA\0\nR100\0only-one-path\0")) == []

    def test_edits_and_deletes_carry_the_identity_forward(self) -> None:
        """The walk survives the record classes that keep a note's identity."""
        from markdown_vault_mcp.git.query import _path_at_ref

        stream = "\x1eA\0\nM\0new.md\0\x1eB\0\nR100\0old.md\0new.md\0"
        assert _path_at_ref(stream, "new.md", "abc123") == "old.md"


class TestBreadcrumbNeverFailsAWrite:
    """A write must not fail over a breadcrumb it could not compute."""

    def test_git_failure_is_swallowed(self, git_vault: Vault) -> None:
        """A probe that raises yields no breadcrumb, and the write still lands."""

        def exploding(*_args: object, **_kwargs: object) -> str | None:
            raise subprocess.SubprocessError("git exploded")

        git_vault._git_query_mgr._git_strategy.committed_revision = exploding  # type: ignore[union-attr]
        result = git_vault.writer.write("note.md", "# Written anyway\n")

        assert result.previous_revision is None
        assert (git_vault.source_dir / "note.md").read_text() == "# Written anyway\n"


class TestHistoricalSections:
    """Section selection over content that is no longer on disk."""

    def test_empty_section_is_rejected(self, git_vault: Vault) -> None:
        overwrite = git_vault.writer.write("note.md", "# Clobbered\n")
        assert overwrite.previous_revision is not None

        with pytest.raises(ValueError, match="non-empty heading"):
            git_vault.reader.read_revision(
                "note.md", overwrite.previous_revision, section="   "
            )

    def test_malformed_historical_frontmatter(self, tmp_path: Path) -> None:
        """Frontmatter that no longer parses is an error the caller can act on.

        The note is fine today; only the revision being asked for is broken,
        so nothing on disk warns the caller.
        """
        repo = tmp_path / "vault"
        _init(repo)
        _write(repo, "note.md", "---\ntitle: [unclosed\n---\n\n# Head\n\nbody\n")
        broken = _commit(repo, "seed with broken frontmatter")
        _write(repo, "note.md", "---\ntitle: fine\n---\n\n# Head\n\nbody\n")
        _commit(repo, "fix the frontmatter")

        strategy = GitWriteStrategy(push_delay_s=0)
        vault = Vault(
            source_dir=repo, git_strategy=strategy, on_write=strategy, read_only=False
        )
        vault.index.build_index()
        try:
            with pytest.raises(ValueError, match="malformed frontmatter"):
                vault.reader.read_revision("note.md", broken, section="Head")
            # The whole note at that revision is still readable.
            assert "unclosed" in vault.reader.read_revision("note.md", broken).content
        finally:
            vault.close()
