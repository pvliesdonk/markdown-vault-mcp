"""Tests for non-blocking startup background indexing (#513)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.collection import Collection

if TYPE_CHECKING:
    from pathlib import Path


def test_index_status_idle_before_anything_runs(tmp_path: Path) -> None:
    """A fresh Collection that hasn't started background work reports idle status."""
    collection = Collection(source_dir=tmp_path)
    try:
        status = collection.index_status()
        assert status == {
            "background_running": False,
            "background_phase": None,
            "last_run_started_at": None,
            "last_run_completed_at": None,
            "last_error": None,
        }
    finally:
        collection.close()
