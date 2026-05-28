"""Tests for issue #513 PR1 — cold-start background FTS build."""

from __future__ import annotations

import asyncio
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


def test_start_background_build_index_eventually_ready(tmp_path: Path) -> None:
    """After start_background_build_index(), wait_for_index_ready()
    eventually returns and bucket-3 calls succeed."""
    vault = _vault(tmp_path)
    for i in range(5):
        _seed(vault, f"n_{i}.md", f"# N{i}\n\nbody {i}\n")
    col = Collection(source_dir=vault)

    col.start_background_build_index()
    col.wait_for_index_ready(timeout=5.0)

    assert col.is_index_ready()
    # Bucket-3 call must succeed now.
    col.get_backlinks("n_0.md")  # may return [] but must not raise
    col.close()


def test_start_background_build_index_captures_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception raised by _index_mgr.build_index is captured into
    _background_build_error; the event is still set so callers unblock;
    subsequent wait_for_index_ready raises IndexBuildFailedError."""
    col = Collection(source_dir=_vault(tmp_path))

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("simulated scan failure")

    monkeypatch.setattr(col._index_mgr, "build_index", boom)
    col.start_background_build_index()

    with pytest.raises(IndexBuildFailedError):
        col.wait_for_index_ready(timeout=5.0)
    assert col.is_index_ready() is False
    col.close()


def test_start_background_build_index_idempotent(tmp_path: Path) -> None:
    """Second call while a build is in flight (or finished) is a no-op
    — at most one thread is ever spawned."""
    col = Collection(source_dir=_vault(tmp_path))

    col.start_background_build_index()
    first_thread = col._background_build_thread
    assert first_thread is not None

    col.start_background_build_index()
    assert col._background_build_thread is first_thread

    col.wait_for_index_ready(timeout=5.0)

    # After completion, another call still must not spawn a new thread.
    col.start_background_build_index()
    assert col._background_build_thread is first_thread
    col.close()


def test_get_index_status_ready(tmp_path: Path) -> None:
    """A built Collection reports status=ready."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    status = col.get_index_status()
    assert status["status"] == "ready"
    assert status["documents_indexed"] == 1
    assert status["error"] is None
    col.close()


def test_get_index_status_building(tmp_path: Path) -> None:
    """While a background build is in flight, status=building."""
    col = Collection(source_dir=_vault(tmp_path))
    # Manually put the Collection into the "building" state without
    # actually spawning a thread (so the test is deterministic).
    col._background_build_done.clear()
    col._background_started = True
    status = col.get_index_status()
    assert status["status"] == "building"
    assert status["error"] is None
    col._background_build_done.set()  # cleanup
    col.close()


def test_get_index_status_failed(tmp_path: Path) -> None:
    """After a background build raised, status=failed and error is set."""
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_error = RuntimeError("scan failed for X")
    status = col.get_index_status()
    assert status["status"] == "failed"
    assert "scan failed for X" in status["error"]
    col.close()


async def _call_status(server) -> dict:
    from fastmcp import Client

    async with Client(server) as client:
        result = await client.call_tool("get_index_status", {})
        return result.structured_content or {}


def test_mcp_tool_get_index_status_reports_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new MCP tool surfaces the same dict as Collection.get_index_status."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    from markdown_vault_mcp.server import make_server

    server = make_server()
    status = asyncio.run(_call_status(server))
    assert status["status"] == "ready"
    assert status["error"] is None
