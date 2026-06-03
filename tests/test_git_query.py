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
        self.diff_calls: list[tuple[Any, ...]] = []

    def get_file_history(
        self,
        source_dir: Path,
        abs_path: Path | None,
        since: str | None,
        limit: int,
        until: str | None = None,
    ) -> list[str]:
        self.history_calls.append((source_dir, abs_path, since, limit, until))
        return ["HIST"]

    def get_file_diff(
        self,
        source_dir: Path,
        abs_path: Path,
        ref: str | None,
        per_commit: bool,
        since_timestamp: str | None = None,
        limit: int | None = None,
    ) -> str | list[str]:
        self.diff_calls.append(
            (source_dir, abs_path, ref, per_commit, since_timestamp, limit)
        )
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
