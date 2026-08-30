"""Unit tests for GitQueryManager (#610).

Covers the git-strategy-None fallbacks, the get_diff argument validation
branches (exactly-one-of since_sha/since_timestamp, SHA format) — previously
only reachable via the MCP tool layer — and the forwarding contract, using a
recording fake strategy so no real git repo is needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

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
        """collect_paths=True normalises path lines to vault-relative form."""
        from markdown_vault_mcp.git.query import _parse_history_block

        block = (
            "sha\x00short\x00ts\x00An Author <a@b>\x00msg\nvault/note.md\n\nother.md"
        )
        entry = _parse_history_block(block, "vault/", collect_paths=True)
        assert entry is not None
        assert entry.sha == "sha"
        assert entry.author == "An Author <a@b>"
        assert entry.paths_changed == ["note.md", "other.md"]

        no_paths = _parse_history_block(block, "vault/", collect_paths=False)
        assert no_paths is not None
        assert no_paths.paths_changed == []

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
