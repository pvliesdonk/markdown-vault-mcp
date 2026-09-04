"""Unit tests for GitQueryManager (#610).

Covers the git-strategy-None fallbacks, the get_diff argument validation
branches (exactly-one-of since_sha/since_timestamp, SHA format) — previously
only reachable via the MCP tool layer — and the forwarding contract, using a
recording fake strategy so no real git repo is needed.

One class is the exception: :class:`TestSha256Repository` drives a real
``--object-format=sha256`` repository, because the claim it pins — that a SHA
``get_history`` returned is one ``get_diff`` accepts — is only observable when
git itself mints the object IDs (#1284).
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.managers.git_query import GitQueryManager

if TYPE_CHECKING:
    from pathlib import Path


class _RecordingStrategy:
    """Fake GitWriteStrategy that records calls and returns sentinels."""

    def __init__(self) -> None:
        self.history_calls: list[tuple[Any, ...]] = []
        self.history_is_dir: list[bool] = []
        self.diff_calls: list[tuple[Any, ...]] = []
        self.diff_kwargs: list[dict[str, Any]] = []

    def get_file_history(
        self,
        source_dir: Path,
        abs_path: Path | None,
        since: str | None,
        limit: int,
        until: str | None = None,
        *,
        is_dir: bool = False,
    ) -> list[str]:
        self.history_calls.append((source_dir, abs_path, since, limit, until))
        self.history_is_dir.append(is_dir)
        return ["HIST"]

    def get_file_diff(
        self,
        source_dir: Path,
        abs_path: Path,
        ref: str | None,
        per_commit: bool,
        since_timestamp: str | None = None,
        limit: int | None = None,
        *,
        summarize_binary: bool = False,
    ) -> str | list[str]:
        self.diff_calls.append(
            (source_dir, abs_path, ref, per_commit, since_timestamp, limit)
        )
        self.diff_kwargs.append({"summarize_binary": summarize_binary})
        return ["CD"] if per_commit else "DIFF"


class TestNoGitStrategy:
    def test_get_history_returns_empty(self, tmp_path: Path) -> None:
        mgr = GitQueryManager(None, tmp_path)
        assert mgr.get_history() == []
        assert mgr.get_history("note.md") == []

    def test_get_diff_returns_empty(self, tmp_path: Path) -> None:
        mgr = GitQueryManager(None, tmp_path)
        assert mgr.get_diff("note.md") == ""
        assert mgr.get_diff("note.md", per_commit=True) == []


class TestGetDiffValidation:
    def test_neither_reference_raises(self, tmp_path: Path) -> None:
        mgr = GitQueryManager(_RecordingStrategy(), tmp_path)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Exactly one"):
            mgr.get_diff("note.md")

    def test_both_references_raise(self, tmp_path: Path) -> None:
        mgr = GitQueryManager(_RecordingStrategy(), tmp_path)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Exactly one"):
            mgr.get_diff("note.md", since_sha="abcd", since_timestamp="2026-01-01")

    def test_malformed_sha_raises(self, tmp_path: Path) -> None:
        mgr = GitQueryManager(_RecordingStrategy(), tmp_path)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Invalid SHA"):
            mgr.get_diff("note.md", since_sha="XYZ!")

    def test_sha256_length_sha_is_accepted(self, tmp_path: Path) -> None:
        """A 64-hex object ID reaches the strategy instead of being rejected (#1284)."""
        strat = _RecordingStrategy()
        mgr = GitQueryManager(strat, tmp_path)  # type: ignore[arg-type]
        sha = "a" * 64
        assert mgr.get_diff("note.md", since_sha=sha) == "DIFF"
        assert strat.diff_calls[0][2] == sha

    def test_sha_longer_than_any_object_id_raises(self, tmp_path: Path) -> None:
        """65 hex digits is not an object ID in either hash algorithm."""
        mgr = GitQueryManager(_RecordingStrategy(), tmp_path)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Invalid SHA"):
            mgr.get_diff("note.md", since_sha="a" * 65)

    @pytest.mark.parametrize(
        "since_sha",
        ["HEAD~1", "@{upstream}", "--all", "-x", "ABCDEF", "abcd def", "main"],
    )
    def test_non_object_id_input_never_reaches_git(
        self, tmp_path: Path, since_sha: str
    ) -> None:
        """Widening the bound to 64 keeps the shape check, not just the length.

        The validator's job is to keep caller-supplied text out of the git
        argv: a revision expression, an option-looking string and an uppercase
        digit are all rejected before the strategy is called.
        """
        strat = _RecordingStrategy()
        mgr = GitQueryManager(strat, tmp_path)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Invalid SHA"):
            mgr.get_diff("note.md", since_sha=since_sha)
        assert strat.diff_calls == []


class TestForwarding:
    def test_get_history_forwards_args(self, tmp_path: Path) -> None:
        strat = _RecordingStrategy()
        mgr = GitQueryManager(strat, tmp_path)  # type: ignore[arg-type]
        result = mgr.get_history("note.md", since="1 week ago", until="now", limit=5)
        assert result == ["HIST"]
        source_dir, abs_path, since, limit, until = strat.history_calls[0]
        assert source_dir == tmp_path
        assert abs_path == tmp_path / "note.md"
        assert (since, until, limit) == ("1 week ago", "now", 5)

    def test_get_history_vault_wide_passes_none_path(self, tmp_path: Path) -> None:
        strat = _RecordingStrategy()
        mgr = GitQueryManager(strat, tmp_path)  # type: ignore[arg-type]
        mgr.get_history()
        assert strat.history_calls[0][1] is None  # abs_path
        assert strat.history_is_dir[0] is False

    def test_get_history_file_passes_is_dir_false(self, tmp_path: Path) -> None:
        strat = _RecordingStrategy()
        mgr = GitQueryManager(strat, tmp_path)  # type: ignore[arg-type]
        mgr.get_history("note.md")
        assert strat.history_calls[0][1] == tmp_path / "note.md"
        assert strat.history_is_dir[0] is False

    def test_get_history_existing_dir_detected_as_dir(self, tmp_path: Path) -> None:
        # A path resolving to a real directory is scoped to its subtree.
        (tmp_path / "guides").mkdir()
        strat = _RecordingStrategy()
        mgr = GitQueryManager(strat, tmp_path)  # type: ignore[arg-type]
        mgr.get_history("guides")
        assert strat.history_calls[0][1] == tmp_path / "guides"
        assert strat.history_is_dir[0] is True

    def test_get_history_nonexistent_bare_name_rejected_as_file(
        self, tmp_path: Path
    ) -> None:
        # A non-directory path with no note/attachment extension still fails
        # file validation (the directory branch requires an on-disk folder).
        strat = _RecordingStrategy()
        mgr = GitQueryManager(strat, tmp_path)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=r"must be a \.md note or a configured"):
            mgr.get_history("guides")

    def test_get_diff_limit_gated_on_per_commit(self, tmp_path: Path) -> None:
        strat = _RecordingStrategy()
        mgr = GitQueryManager(strat, tmp_path)  # type: ignore[arg-type]
        # per_commit=False -> caller's limit is suppressed to None (no clamp here;
        # the [1, 100] clamp lives downstream in GitWriteStrategy.get_file_diff)
        mgr.get_diff("note.md", since_sha="abcd", limit=5)
        assert strat.diff_calls[0] == (
            tmp_path,
            tmp_path / "note.md",
            "abcd",
            False,
            None,
            None,
        )
        # per_commit=True -> caller limit forwarded
        mgr.get_diff("note.md", since_timestamp="2026-01-01", per_commit=True, limit=5)
        assert strat.diff_calls[1] == (
            tmp_path,
            tmp_path / "note.md",
            None,
            True,
            "2026-01-01",
            5,
        )


class TestAttachmentExtensions:
    """Verify that GitQueryManager validates paths with attachment_extensions."""

    def test_get_diff_rejects_unknown_extension(self, tmp_path: Path) -> None:
        """Manager built with png (not exe) must reject .exe paths."""
        mgr = GitQueryManager(
            _RecordingStrategy(),  # type: ignore[arg-type]
            tmp_path,
            attachment_extensions=["png"],
        )
        with pytest.raises(ValueError, match=r"\.md note or a configured attachment"):
            mgr.get_diff("evil.exe", since_sha="abcd1234")

    def test_get_diff_attachment_passes_summarize_binary_true(
        self, tmp_path: Path
    ) -> None:
        """get_diff on an attachment path must forward summarize_binary=True."""
        strat = _RecordingStrategy()
        mgr = GitQueryManager(
            strat,  # type: ignore[arg-type]
            tmp_path,
            attachment_extensions=["png"],
        )
        mgr.get_diff("assets/diagram.png", since_sha="abcd1234")
        assert strat.diff_kwargs[0]["summarize_binary"] is True

    def test_get_diff_md_passes_summarize_binary_false(self, tmp_path: Path) -> None:
        """get_diff on a .md path must forward summarize_binary=False."""
        strat = _RecordingStrategy()
        mgr = GitQueryManager(
            strat,  # type: ignore[arg-type]
            tmp_path,
            attachment_extensions=["png"],
        )
        mgr.get_diff("note.md", since_sha="abcd1234")
        assert strat.diff_kwargs[0]["summarize_binary"] is False

    def test_get_history_attachment_does_not_raise(self, tmp_path: Path) -> None:
        """get_history on a known attachment extension must succeed."""
        strat = _RecordingStrategy()
        mgr = GitQueryManager(
            strat,  # type: ignore[arg-type]
            tmp_path,
            attachment_extensions=["png"],
        )
        result = mgr.get_history("assets/photo.png")
        assert result == ["HIST"]

    def test_get_diff_no_extensions_rejects_attachment(self, tmp_path: Path) -> None:
        """Manager with empty attachment_extensions must reject any non-.md path."""
        mgr = GitQueryManager(
            _RecordingStrategy(),  # type: ignore[arg-type]
            tmp_path,
            attachment_extensions=[],
        )
        with pytest.raises(ValueError, match=r"\.md note or a configured attachment"):
            mgr.get_diff("image.png", since_sha="abcd1234")

    def test_get_history_no_extensions_rejects_attachment(self, tmp_path: Path) -> None:
        """get_history with empty attachment_extensions must reject non-.md path."""
        mgr = GitQueryManager(
            _RecordingStrategy(),  # type: ignore[arg-type]
            tmp_path,
            attachment_extensions=[],
        )
        with pytest.raises(ValueError, match=r"\.md note or a configured attachment"):
            mgr.get_history("image.png")


class TestHistoryQueryHelpers:
    """Unit tests for the module-level helpers behind get_file_history (#1159)."""

    def test_parse_history_block_rejects_malformed_blocks(self) -> None:
        """Empty, whitespace-only, and short-header blocks parse to None."""
        from markdown_vault_mcp.git.query import _parse_history_block

        assert _parse_history_block("", "", collect_paths=False) is None
        assert _parse_history_block("   \n  ", "", collect_paths=False) is None
        # Header with fewer than five NUL-separated fields is malformed.
        assert (
            _parse_history_block(
                "sha\x00short\x00ts\x00author", "", collect_paths=False
            )
            is None
        )

    def test_parse_history_block_strips_vault_prefix(self) -> None:
        """collect_paths=True normalises path tokens to vault-relative form."""
        from markdown_vault_mcp.git.query import _parse_history_block

        # `git log -z` framing: five NUL-terminated header fields, then a
        # single newline, then the NUL-terminated paths (#1282).
        block = (
            "sha\x00short\x00ts\x00An Author <a@b>\x00msg\x00"
            "\nvault/note.md\x00other.md\x00"
        )
        entry = _parse_history_block(block, "vault/", collect_paths=True)
        assert entry is not None
        assert entry.sha == "sha"
        assert entry.author == "An Author <a@b>"
        assert entry.paths_changed == ["note.md", "other.md"]

        no_paths = _parse_history_block(block, "vault/", collect_paths=False)
        assert no_paths is not None
        assert no_paths.paths_changed == []

    def test_vault_relative_paths_drops_an_empty_token(self) -> None:
        """An empty token never becomes a bare-prefix path entry (#1282).

        ``_split_z_block`` removes the one empty token git's final NUL leaves,
        so this guards a stream that is malformed rather than merely framed
        differently — an empty entry in ``paths_changed`` would read as a note
        at the vault root with no name.
        """
        from markdown_vault_mcp.git.query import _vault_relative_paths

        assert _vault_relative_paths(["vault/a.md", "", "vault/b.md"], "vault/") == [
            "a.md",
            "b.md",
        ]

    def test_vault_prefix_outside_git_root_is_empty(self, tmp_path: Path) -> None:
        """A repo_path not under git_root yields no prefix to strip."""
        from markdown_vault_mcp.git.query import _vault_prefix

        outside = tmp_path / "elsewhere"
        outside.mkdir()
        assert _vault_prefix(tmp_path / "root", outside) == ""

    def test_vault_prefix_for_nested_vault(self, tmp_path: Path) -> None:
        """A vault nested under the git root yields its posix prefix."""
        from markdown_vault_mcp.git.query import _vault_prefix

        vault = tmp_path / "vault"
        vault.mkdir()
        assert _vault_prefix(tmp_path, vault) == "vault/"
        assert _vault_prefix(tmp_path, tmp_path) == ""


class TestRenameThresholdAgreement:
    """The readers agree about a rename git scores between 30% and 50% (#1297).

    Real git again, not the fake strategy: the defect was that one walk asked
    git to detect renames at 30% and another let it default to 50%, so a
    revision ``read`` served was one ``get_history`` never listed.

    The threshold is what these pin.  A rename made while *resolving a merge*
    belongs to no parent's diff and the two readers still differ there — a
    separate mechanism, tracked in #1306.
    """

    def _renamed_note_repo(self, tmp_path: Path) -> tuple[Path, str, str, str]:
        """Build a repo whose third commit renames and rewrites a note.

        ``a.md`` is created, edited, then renamed to ``b.md`` and rewritten in
        one commit.  The blob pair either side of the rename is 36% similar —
        over the 30% this project pins, under git's 50% default — so the two
        thresholds disagree about whether ``b.md`` has any history at all.

        Returns:
            The repo path and the three commit SHAs, oldest first.
        """
        repo = tmp_path / "renamed"
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init", "--initial-branch=main"],
            check=True,
            capture_output=True,
        )
        for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
            subprocess.run(
                ["git", "-C", str(repo), "config", key, value],
                check=True,
                capture_output=True,
            )

        def _commit(message: str) -> str:
            subprocess.run(
                ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", message],
                check=True,
                capture_output=True,
            )
            return subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        (repo / "a.md").write_text("# Title\nDraft line\n")
        created = _commit("create a.md")
        (repo / "a.md").write_text("# Title\nOriginal line\n")
        edited = _commit("edit a.md")
        subprocess.run(
            ["git", "-C", str(repo), "mv", "a.md", "b.md"],
            check=True,
            capture_output=True,
        )
        (repo / "b.md").write_text("# Title\nModified line\n")
        renamed = _commit("rename a.md to b.md and rewrite it")

        # The premise: git's own default does not pair these two blobs — it
        # reports an unrelated delete and add, so a walk that leaves the
        # threshold to git sees b.md born at this commit with no past.
        at_default = subprocess.run(
            ["git", "-C", str(repo), "show", "--name-status", "--format=", renamed],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert at_default == "D\ta.md\nA\tb.md\n", at_default

        return repo, created, edited, renamed

    def test_history_lists_the_revisions_a_revision_read_serves(
        self, tmp_path: Path
    ) -> None:
        """Every revision ``read`` accepts for the note is one history reports."""
        repo, created, edited, renamed = self._renamed_note_repo(tmp_path)
        mgr = GitQueryManager(GitWriteStrategy(), repo)

        listed = [entry.sha for entry in mgr.get_history("b.md", limit=10)]

        assert listed == [renamed, edited, created]
        # Both pre-rename revisions read back as the same note, under the name
        # it had then — which is what made listing only the rename a lie.
        for revision in (edited, created):
            assert mgr.read_at_revision("b.md", revision).historical_path == "a.md"

    def test_per_commit_diff_includes_the_commit_on_the_old_name(
        self, tmp_path: Path
    ) -> None:
        """A pre-rename commit inside the range is diffed, not skipped."""
        repo, created, edited, renamed = self._renamed_note_repo(tmp_path)
        mgr = GitQueryManager(GitWriteStrategy(), repo)

        diffs = mgr.get_diff("b.md", since_sha=created, per_commit=True)

        assert isinstance(diffs, list)
        assert [diff.sha for diff in diffs] == [renamed, edited]
        # The older one is the edit made while the note was still a.md; it is
        # rendered from that name, not skipped for not matching b.md.
        assert "-Draft line" in diffs[1].diff
        assert "+Original line" in diffs[1].diff


class TestSha256Repository:
    """A SHA-256 repository's object IDs survive the round trip (#1284).

    The rest of this module fakes the strategy; here git mints the IDs, because
    the defect was a width assumption about what git returns.
    """

    def _sha256_repo(self, tmp_path: Path) -> Path:
        """Build a two-commit repo with 64-hex object IDs, or skip."""
        repo = tmp_path / "sha256"
        repo.mkdir()
        init = subprocess.run(
            ["git", "-C", str(repo), "init", "--object-format=sha256"],
            capture_output=True,
            text=True,
        )
        if init.returncode != 0:
            pytest.skip(
                "local git does not support --object-format=sha256: "
                f"{(init.stderr or '').strip()}"
            )
        for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
            subprocess.run(
                ["git", "-C", str(repo), "config", key, value],
                check=True,
                capture_output=True,
            )
        note = repo / "note.md"
        for body, message in (("# v1\n", "add"), ("# v2\n", "edit")):
            note.write_text(body)
            subprocess.run(
                ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", message],
                check=True,
                capture_output=True,
            )
        return repo

    def test_history_sha_is_accepted_by_get_diff(self, tmp_path: Path) -> None:
        """The SHA get_history returns is one get_diff accepts, 64 hex digits and all."""
        repo = self._sha256_repo(tmp_path)
        mgr = GitQueryManager(GitWriteStrategy(), repo)

        history = mgr.get_history("note.md", limit=2)
        assert len(history) == 2
        oldest = history[-1].sha
        # The premise of the test: this repo really does mint 64-hex IDs, so a
        # 40-digit ceiling would reject every SHA the vault can offer a caller.
        assert len(oldest) == 64

        diff = mgr.get_diff("note.md", since_sha=oldest)
        assert isinstance(diff, str)
        assert "-# v1" in diff
        assert "+# v2" in diff
