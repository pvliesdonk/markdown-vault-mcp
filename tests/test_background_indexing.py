"""Tests for non-blocking startup background indexing (#513)."""

from __future__ import annotations

import time
from pathlib import Path  # noqa: TC003

from markdown_vault_mcp.collection import Collection


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


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.01) -> None:
    """Poll a predicate until it returns truthy or timeout expires.

    Raises AssertionError if predicate never becomes true.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"predicate {predicate!r} not satisfied within {timeout}s")


def test_start_background_reindex_runs_and_completes(tmp_path: Path) -> None:
    """Background reindex transitions through phases and reaches idle."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")
    collection = Collection(source_dir=tmp_path)
    try:
        collection.start_background_reindex()

        # Eventually the background thread completes; final state is idle, no error.
        _wait_until(lambda: not collection.index_status()["background_running"])

        status = collection.index_status()
        assert status["background_running"] is False
        assert status["background_phase"] is None
        assert status["last_error"] is None
        assert status["last_run_started_at"] is not None
        assert status["last_run_completed_at"] is not None
    finally:
        collection.close()


def test_start_background_reindex_is_idempotent(tmp_path: Path) -> None:
    """Calling start twice while a thread is alive does not spawn a second thread."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")
    collection = Collection(source_dir=tmp_path)
    try:
        collection.start_background_reindex()
        first_thread = collection._background_thread

        # Second call must be a no-op while the first thread is alive.
        # If the first thread completes between calls, this still must not
        # raise — we just accept whichever invariant holds.
        collection.start_background_reindex()
        second_thread = collection._background_thread

        assert second_thread is first_thread or not first_thread.is_alive()

        _wait_until(lambda: not collection.index_status()["background_running"])
    finally:
        collection.close()
