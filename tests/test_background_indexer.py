"""Tests for the BackgroundIndexer orchestrator (issue #513)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.background_indexer import BackgroundIndexer
from markdown_vault_mcp.collection import Collection

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def small_collection(tmp_path: Path) -> Collection:
    (tmp_path / "note.md").write_text("# Hello\n\nWorld.\n", encoding="utf-8")
    col = Collection(
        source_dir=tmp_path,
        state_path=tmp_path / ".state" / "state.json",
    )
    yield col
    col.close()


def _wait_for_state(
    indexer: BackgroundIndexer, state: str, *, timeout: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = indexer.status
        if snap["state"] == state:
            return snap
        time.sleep(0.02)
    pytest.fail(f"never reached state={state!r}, last={indexer.status!r}")


def test_status_before_start(small_collection: Collection) -> None:
    indexer = BackgroundIndexer(small_collection, has_provider=False)
    s = indexer.status
    assert s["state"] == "idle"
    assert s["error"] is None
    assert s["documents_indexed"] == 0
    assert s["chunks_indexed"] == 0


def test_run_to_ready_without_provider(small_collection: Collection) -> None:
    indexer = BackgroundIndexer(small_collection, has_provider=False)
    indexer.start()
    final = _wait_for_state(indexer, "ready")
    assert final["error"] is None
    assert final["documents_indexed"] >= 1
    assert indexer.stop(timeout=5.0) is True


def test_failure_sets_failed_state(
    small_collection: Collection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(small_collection, "build_index", boom)
    indexer = BackgroundIndexer(small_collection, has_provider=False)
    indexer.start()
    final = _wait_for_state(indexer, "failed")
    assert "synthetic build failure" in (final["error"] or "")
    assert indexer.stop(timeout=5.0) is True


def test_double_start_is_idempotent(small_collection: Collection) -> None:
    indexer = BackgroundIndexer(small_collection, has_provider=False)
    indexer.start()
    first = indexer._thread
    indexer.start()
    assert indexer._thread is first
    _wait_for_state(indexer, "ready")
    assert indexer.stop(timeout=5.0) is True


def test_stop_without_start_is_noop(small_collection: Collection) -> None:
    indexer = BackgroundIndexer(small_collection, has_provider=False)
    assert indexer.stop(timeout=1.0) is True


def test_stop_after_ready_is_noop(small_collection: Collection) -> None:
    indexer = BackgroundIndexer(small_collection, has_provider=False)
    indexer.start()
    _wait_for_state(indexer, "ready")
    assert indexer.stop(timeout=5.0) is True
    assert indexer.stop(timeout=1.0) is True
