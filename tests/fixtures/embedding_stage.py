"""Shared synthetic embedding-stage cleanup failure."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.indexing._embedding_stage import EmbeddingStage

if TYPE_CHECKING:
    import pytest


def install_cleanup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make stage cleanup remove its files and then report a synthetic error."""
    enter = EmbeddingStage.__enter__

    def failing_cleanup(stage: EmbeddingStage) -> EmbeddingStage:
        enter(stage)
        cleanup = stage._temp.cleanup

        def fail() -> None:
            cleanup()
            raise OSError("scratch cleanup failed")

        monkeypatch.setattr(stage._temp, "cleanup", fail)
        return stage

    monkeypatch.setattr(EmbeddingStage, "__enter__", failing_cleanup)
