"""Tests for the BackgroundIndexer orchestrator (issue #513)."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.background_indexer import BackgroundIndexer
from markdown_vault_mcp.collection import Collection

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def small_collection(tmp_path: Path) -> Iterator[Collection]:
    (tmp_path / "note.md").write_text("# Hello\n\nWorld.\n", encoding="utf-8")
    col = Collection(
        source_dir=tmp_path,
        state_path=tmp_path / ".state" / "state.json",
    )
    yield col
    col.close()


def _wait_for_state(
    indexer: BackgroundIndexer,
    state: str,
    *,
    timeout: float = 5.0,
) -> dict:
    """Poll until *state* is reached, or short-circuit on a different terminal.

    If the indexer reaches ``failed`` while we're waiting for any other state,
    the test fails with the error rather than running out the clock on a
    misleading timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = indexer.status
        if snap["state"] == state:
            return snap
        if snap["state"] == "failed" and state != "failed":
            pytest.fail(
                f"indexer transitioned to failed while waiting for "
                f"state={state!r}: error_type={snap['error_type']!r} "
                f"error={snap['error']!r}"
            )
        time.sleep(0.02)
    pytest.fail(f"never reached state={state!r}, last={indexer.status!r}")


def test_status_before_start(small_collection: Collection) -> None:
    indexer = BackgroundIndexer(small_collection, has_provider=False)
    s = indexer.status
    assert s["state"] == "idle"
    assert s["error"] is None
    assert s["error_type"] is None
    assert s["documents_indexed"] == 0
    assert s["chunks_indexed"] == 0
    assert indexer.is_started is False


def test_run_to_ready_without_provider(small_collection: Collection) -> None:
    indexer = BackgroundIndexer(small_collection, has_provider=False)
    indexer.start()
    final = _wait_for_state(indexer, "ready")
    assert final["error"] is None
    assert final["error_type"] is None
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
    assert final["error_type"] == "RuntimeError"
    assert indexer.stop(timeout=5.0) is True


def test_double_start_is_idempotent(small_collection: Collection) -> None:
    indexer = BackgroundIndexer(small_collection, has_provider=False)
    assert indexer.is_started is False
    indexer.start()
    assert indexer.is_started is True
    # Second start must not spawn a second worker thread executing _run.
    matching = [
        t
        for t in threading.enumerate()
        if t.name == "markdown-vault-background-indexer"
    ]
    assert len(matching) == 1
    indexer.start()
    matching_after = [
        t
        for t in threading.enumerate()
        if t.name == "markdown-vault-background-indexer"
    ]
    assert len(matching_after) == 1
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


def test_stop_signal_observed_between_phases(
    small_collection: Collection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop_event is checked after build_index completes and before
    build_embeddings begins. Setting stop during a slow build_index keeps the
    worker out of the embedding phase.
    """
    original_build_index = small_collection.build_index
    slow_started = threading.Event()

    def slow_build_index(*args: object, **kwargs: object) -> object:
        slow_started.set()
        time.sleep(0.3)
        return original_build_index(*args, **kwargs)

    def must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "build_embeddings called despite stop_event set after build_index"
        )

    monkeypatch.setattr(small_collection, "build_index", slow_build_index)
    monkeypatch.setattr(small_collection, "build_embeddings", must_not_run)

    indexer = BackgroundIndexer(small_collection, has_provider=True)
    indexer.start()
    assert slow_started.wait(timeout=2.0), "slow build_index never started"
    # Set the stop signal while build_index is mid-sleep. When build_index
    # returns, _run sees stop_event set and returns before build_embeddings.
    assert indexer.stop(timeout=5.0) is True
    # State remains "indexing" — _set(state="embedding") never ran.
    final = indexer.status
    assert final["state"] == "indexing"
    assert final["error"] is None
