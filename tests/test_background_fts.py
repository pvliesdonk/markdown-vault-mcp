"""Tests for issue #513 PR1 — cold-start background FTS build."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.exceptions import (
    IndexBuildFailedError,
    IndexNotReadyError,
    MarkdownMCPError,
)


def test_index_build_failed_error_subclasses_base() -> None:
    err = IndexBuildFailedError("scan failed")
    assert isinstance(err, MarkdownMCPError)
    assert str(err) == "scan failed"


def test_index_build_failed_error_carries_cause() -> None:
    """Verify __cause__ is set via 'raise X from Y'."""
    original = RuntimeError("scan exploded")
    try:
        raise IndexBuildFailedError("background build failed") from original
    except IndexBuildFailedError as err:
        assert err.__cause__ is original


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _seed(vault: Path, name: str = "n.md", body: str = "# N\n\nbody\n") -> None:
    (vault / name).write_text(body)


def test_is_index_ready_false_after_construction(tmp_path: Path) -> None:
    """A freshly-constructed Collection is not ready until build_index runs."""
    col = Collection(source_dir=_vault(tmp_path))
    assert col.is_index_ready() is False
    col.close()


def test_is_index_ready_true_after_synchronous_build(tmp_path: Path) -> None:
    """After build_index() returns successfully, is_index_ready() is True."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    assert col.is_index_ready() is True
    col.close()


def test_wait_for_index_ready_returns_when_event_set(tmp_path: Path) -> None:
    """A built Collection's wait_for_index_ready returns immediately."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    col.wait_for_index_ready(timeout=0.1)  # must not raise
    col.close()


def test_wait_for_index_ready_blocks_on_cleared_event(tmp_path: Path) -> None:
    """With the build-done event cleared, wait_for_index_ready blocks until the event is set from another thread."""
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_done.clear()  # simulate "build in flight"
    col._index_built = False

    def setter() -> None:
        time.sleep(0.05)
        col._index_built = True
        col._background_build_done.set()

    threading.Thread(target=setter).start()
    col.wait_for_index_ready(timeout=1.0)  # must return within 1s
    col.close()


def test_wait_for_index_ready_raises_on_timeout_during_build(tmp_path: Path) -> None:
    """A cleared event with no setter — wait_for_index_ready raises IndexNotReadyError after the timeout."""
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_done.clear()
    col._index_built = False
    with pytest.raises(IndexNotReadyError, match=r"timed out|not built"):
        col.wait_for_index_ready(timeout=0.05)
    col.close()


def test_wait_for_index_ready_raises_build_failed_when_error_set(
    tmp_path: Path,
) -> None:
    """If the background build captured an exception, wait_for_index_ready raises IndexBuildFailedError with the original as __cause__."""
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_error = RuntimeError("scan exploded")
    # event stays set (thread exited); _index_built also False
    with pytest.raises(IndexBuildFailedError) as excinfo:
        col.wait_for_index_ready(timeout=0.1)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    col.close()
