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


def test_close_joins_background_thread_within_timeout(tmp_path: Path) -> None:
    """close() joins the background thread with a bounded timeout.

    The thread for a small vault completes in milliseconds; close()
    must return quickly and the thread must be observably finished.
    """
    vault = _vault(tmp_path)
    for i in range(3):
        _seed(vault, f"n_{i}.md", f"# N{i}\n\nbody {i}\n")
    col = Collection(source_dir=vault)

    col.start_background_build_index()
    col.close()

    thread = col._background_build_thread
    assert thread is not None
    assert not thread.is_alive(), (
        "background thread should have finished within close() join"
    )


async def _call_status(server) -> dict:
    from fastmcp import Client

    async with Client(server) as client:
        result = await client.call_tool("get_index_status", {})
        return result.structured_content or {}


def test_mcp_tool_get_index_status_reports_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The MCP tool surfaces the correct status dict.

    On a cold start the lifespan routes the build to the background thread,
    so status may initially be 'building'.  Poll until ready.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    from markdown_vault_mcp.server import make_server

    server = make_server()

    async def _run2() -> dict:
        from fastmcp import Client

        async with Client(server) as client:
            for _ in range(50):
                result = await client.call_tool("get_index_status", {})
                status = result.structured_content or {}
                if status.get("status") == "ready":
                    return status
                await asyncio.sleep(0.05)
            return status

    status = asyncio.run(_run2())
    assert status["status"] == "ready"
    assert status["error"] is None


def test_lifespan_cold_start_returns_quickly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On cold start (no persisted FTS), lifespan routes to the background
    thread — get_index_status initially reports building, then ready once
    the background build completes."""
    import time as time_mod

    from markdown_vault_mcp.server import make_server

    vault = tmp_path / "vault"
    vault.mkdir()
    for i in range(20):
        (vault / f"n_{i}.md").write_text(f"# N{i}\n\n" + ("body " * 200) + "\n")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    server = make_server()

    async def _run() -> tuple[float, dict]:
        from fastmcp import Client

        start = time_mod.perf_counter()
        async with Client(server) as client:
            handshake_elapsed = time_mod.perf_counter() - start
            # Wait for the background build to complete via the status tool.
            res: object = None
            for _ in range(50):
                res = await client.call_tool("get_index_status", {})
                if (res.structured_content or {}).get("status") == "ready":
                    break
                await asyncio.sleep(0.1)
            final = res.structured_content or {}
        return handshake_elapsed, final

    handshake_elapsed, final = asyncio.run(_run())
    assert handshake_elapsed < 1.0, (
        f"cold-start handshake took {handshake_elapsed:.3f}s, expected < 1.0s"
    )
    assert final["status"] == "ready"
    assert final["documents_indexed"] == 20


def test_lifespan_cold_start_spawns_background_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On cold start, the lifespan must route FTS build to the background
    thread — the Collection's background thread is alive (or completed)
    after handshake; a synchronous-only lifespan would leave it None."""
    from markdown_vault_mcp._server_deps import get_collection_singleton
    from markdown_vault_mcp.server import make_server

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody\n")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    server = make_server()

    background_thread_seen: list[object] = []

    async def _run() -> None:
        from fastmcp import Client

        async with Client(server) as client:
            col = get_collection_singleton()
            background_thread_seen.append(col._background_build_thread)
            # Drain to ready so close() doesn't race.
            for _ in range(50):
                res = await client.call_tool("get_index_status", {})
                if (res.structured_content or {}).get("status") == "ready":
                    break
                await asyncio.sleep(0.05)

    asyncio.run(_run())
    # The background thread must have been spawned (not None).
    assert background_thread_seen[0] is not None, (
        "Cold-start lifespan must spawn a background build thread; "
        "got None (synchronous path taken instead)"
    )


def test_lifespan_warm_start_skips_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On warm start (sentinel present), lifespan uses the synchronous
    short-circuit; no background thread is spawned."""
    from markdown_vault_mcp._server_deps import get_collection_singleton
    from markdown_vault_mcp.server import make_server

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody\n")
    index_path = tmp_path / "fts.db"

    # Phase 1: pre-build via direct Collection so the sentinel is set.
    pre = Collection(source_dir=vault, index_path=index_path)
    pre.build_index()
    pre.close()

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    server = make_server()

    background_thread_seen: list[object] = []

    async def _run() -> dict:
        from fastmcp import Client

        async with Client(server) as client:
            col = get_collection_singleton()
            background_thread_seen.append(col._background_build_thread)
            res = await client.call_tool("get_index_status", {})
            return res.structured_content or {}

    status = asyncio.run(_run())
    assert status["status"] == "ready"
    assert status["documents_indexed"] == 1
    # Warm path must NOT spawn a background thread.
    assert background_thread_seen[0] is None, (
        "Warm-start lifespan must NOT spawn a background thread; "
        f"got thread={background_thread_seen[0]!r}"
    )


def test_foreground_write_during_background_scan(tmp_path: Path) -> None:
    """A foreground write() racing with the background scan results in a
    consistent FTS row for that path after both finish.

    Smoke test for the PR #523 per-thread-connection contract: both
    writers use the FTS API concurrently; last-write-wins per path.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    # Seed enough files that the scan takes a noticeable amount of time.
    for i in range(50):
        (vault / f"seed_{i}.md").write_text(f"# Seed {i}\n\n" + ("x " * 500) + "\n")

    col = Collection(source_dir=vault, read_only=False)
    col.start_background_build_index()

    # Race in a foreground write.
    col.write("racy.md", "# Racy\n\nforeground content\n")

    col.wait_for_index_ready(timeout=10.0)

    rows = {r["path"]: r for r in col._fts.list_notes()}
    assert "racy.md" in rows, "foreground write must end up in the FTS"
    # The disk content (which the scan also reads) is what foreground wrote.
    assert "Racy" in rows["racy.md"]["title"]
    col.close()
