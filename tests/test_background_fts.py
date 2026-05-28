"""Tests for issue #513 PR1 — cold-start background FTS build."""

from __future__ import annotations

from markdown_vault_mcp.exceptions import IndexBuildFailedError, MarkdownMCPError


def test_index_build_failed_error_subclasses_base() -> None:
    err = IndexBuildFailedError("scan failed")
    assert isinstance(err, MarkdownMCPError)
    assert str(err) == "scan failed"
