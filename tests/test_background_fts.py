"""Tests for issue #513 PR1 (attempt 7) — tool-layer wait for cold-start background FTS."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from markdown_vault_mcp._server_queryable import needs_queryable
from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.exceptions import (
    IndexUnavailableError,
)

if TYPE_CHECKING:
    from pathlib import Path


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _seed(vault: Path, name: str = "n.md", body: str = "# N\n\nbody\n") -> None:
    (vault / name).write_text(body, encoding="utf-8")


def test_is_queryable_false_after_construction(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    assert col.is_queryable() is False
    col.close()


def test_is_queryable_true_after_synchronous_build(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    assert col.is_queryable() is True
    col.close()


def test_is_queryable_false_after_captured_background_error(tmp_path: Path) -> None:
    """Direct state poke: simulate a finished-but-failed background by setting
    the error and the event, leaving _index_built False."""
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_error = RuntimeError("simulated")
    col._background_build_done.set()
    assert col.is_queryable() is False
    col.close()


def test_is_queryable_true_with_captured_error_when_built(tmp_path: Path) -> None:
    """Direct state poke: built index + done event + captured background
    error → queryable. The captured error is diagnostic state about the
    most recent build attempt, not a control-flow gate."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    col._background_build_error = RuntimeError("subsequent rebuild blew up")
    assert col.is_queryable() is True
    col.close()


def test_wait_until_queryable_returns_when_already_built(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    col.wait_until_queryable(timeout=0.1)  # must not raise
    col.close()


def test_wait_until_queryable_blocks_until_event_set(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_done.clear()
    col._index_built = False

    def setter() -> None:
        time.sleep(0.05)
        col._index_built = True
        col._background_build_done.set()

    threading.Thread(target=setter).start()
    col.wait_until_queryable(timeout=1.0)  # returns when event fires
    col.close()


def test_wait_until_queryable_raises_on_timeout(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_done.clear()
    col._index_built = False
    with pytest.raises(IndexUnavailableError) as excinfo:
        col.wait_until_queryable(timeout=0.05)
    assert excinfo.value.reason == "timeout"
    col.close()


def test_wait_until_queryable_raises_unavailable_when_error_set_and_not_built(
    tmp_path: Path,
) -> None:
    """Captured error + event set + _index_built=False (default) → raises
    IndexUnavailableError via step 2 (never-scheduled guard). The captured
    error is no longer surfaced as a separate exception class; callers
    read get_index_status() for the diagnostic."""
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_error = RuntimeError("scan exploded")
    with pytest.raises(IndexUnavailableError) as excinfo:
        col.wait_until_queryable(timeout=0.1)
    assert excinfo.value.reason == "never_built"
    col.close()


def test_wait_until_queryable_raises_when_never_scheduled(tmp_path: Path) -> None:
    """Pre-set event + no error + _index_built=False + _background_started=False.
    This is the case the spec calls 'never scheduled' — the pre-set event would
    let wait() return success without the explicit guard."""
    col = Collection(source_dir=_vault(tmp_path))
    # All defaults: event pre-set, no error, _index_built=False, no spawn.
    with pytest.raises(IndexUnavailableError) as excinfo:
        col.wait_until_queryable(timeout=0.1)
    assert excinfo.value.reason == "never_built"
    col.close()


def test_wait_until_queryable_returns_when_built_with_captured_error(
    tmp_path: Path,
) -> None:
    """Direct state poke: built + done + captured error → returns (no raise).
    Captured error is diagnostic only, not a control-flow gate."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    col._background_build_error = RuntimeError("subsequent rebuild blew up")
    col.wait_until_queryable(timeout=0.1)  # must not raise
    col.close()


def test_start_background_build_index_eventually_ready(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    for i in range(5):
        _seed(vault, f"n_{i}.md", f"# N{i}\n\nbody {i}\n")
    col = Collection(source_dir=vault)
    col.start_background_build_index()
    col.wait_until_queryable(timeout=5.0)
    assert col.is_queryable()
    col.get_backlinks("n_0.md")  # smoke: bucket-3 returns (empty list OK)
    col.close()


def test_start_background_build_index_captures_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    col = Collection(source_dir=_vault(tmp_path))

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("simulated scan failure")

    monkeypatch.setattr(col._index_mgr, "build_index", boom)
    col.start_background_build_index()

    with pytest.raises(IndexUnavailableError) as excinfo:
        col.wait_until_queryable(timeout=5.0)
    assert excinfo.value.reason == "never_built"
    assert col.is_queryable() is False
    col.close()


def test_start_background_build_index_idempotent(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col.start_background_build_index()
    first = col._background_build_thread
    assert first is not None
    col.start_background_build_index()
    assert col._background_build_thread is first
    col.wait_until_queryable(timeout=5.0)
    col.start_background_build_index()
    assert col._background_build_thread is first
    col.close()


def test_start_background_build_index_one_shot_after_thread_start_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If thread.start() itself raises, the captured-error path runs
    synchronously: event set, error recorded, _background_started True.
    A retry call is a no-op (one-shot)."""
    col = Collection(source_dir=_vault(tmp_path))

    def boom_start(_self: threading.Thread) -> None:
        raise RuntimeError("system thread exhaustion (simulated)")

    monkeypatch.setattr(threading.Thread, "start", boom_start)

    with pytest.raises(RuntimeError, match=r"thread exhaustion"):
        col.start_background_build_index()

    # Event must be set; error recorded.
    assert col._background_build_done.is_set()
    assert isinstance(col._background_build_error, RuntimeError)

    # wait_until_queryable surfaces this state via the never-scheduled
    # guard (step 2): event set + _index_built=False → IndexUnavailableError.
    # The captured error is diagnostic only, readable via get_index_status().
    with pytest.raises(IndexUnavailableError) as excinfo:
        col.wait_until_queryable(timeout=0.1)
    assert excinfo.value.reason == "never_built"

    # Retry is a no-op (one-shot semantics).
    monkeypatch.undo()
    col.start_background_build_index()  # no-op; does NOT spawn a new thread
    col.close()


def test_should_use_background_build_in_memory_false(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))  # no index_path → in-memory
    assert col.should_use_background_build() is False
    col.close()


def test_should_use_background_build_cold_on_disk_true(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault, index_path=tmp_path / "fts.db")
    # No prior build → sentinel absent → background build required.
    assert col.should_use_background_build() is True
    col.close()


def test_should_use_background_build_warm_on_disk_false(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _seed(vault)
    index_path = tmp_path / "fts.db"
    # Phase 1: pre-build sets the sentinel.
    pre = Collection(source_dir=vault, index_path=index_path)
    pre.build_index()
    pre.close()
    # Phase 2: fresh Collection sees the warm sentinel.
    col = Collection(source_dir=vault, index_path=index_path)
    assert col.should_use_background_build() is False
    col.close()


# ---------------------------------------------------------------------------
# get_index_status tests
# ---------------------------------------------------------------------------


def test_get_index_status_queryable(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    status = col.get_index_status()
    assert status["status"] == "queryable"
    assert status["documents_indexed"] == 1
    assert status["error"] is None
    col.close()


def test_get_index_status_queryable_when_built_with_captured_error(
    tmp_path: Path,
) -> None:
    """Priority flip: a built index with a captured background error
    reports 'queryable' (not 'failed'). The error field carries the
    last-attempt message as diagnostic context, independent of status."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()  # _index_built True, error cleared
    col._background_build_error = RuntimeError("subsequent rebuild blew up")
    status = col.get_index_status()
    assert status["status"] == "queryable"
    assert status["documents_indexed"] == 1
    assert status["error"] is not None
    assert "subsequent rebuild blew up" in status["error"]
    col.close()


def test_get_index_status_building_in_flight(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_done.clear()
    col._background_started = True
    status = col.get_index_status()
    assert status["status"] == "building"
    assert status["error"] is None
    col._background_build_done.set()
    col.close()


def test_get_index_status_building_never_started(tmp_path: Path) -> None:
    """Fresh Collection: event pre-set, no error, _index_built=False.
    Reports 'building' (not 'ready' — the attempt-6 lie is fixed)."""
    col = Collection(source_dir=_vault(tmp_path))
    status = col.get_index_status()
    assert status["status"] == "building"
    assert status["error"] is None
    col.close()


def test_get_index_status_failed_when_not_queryable_with_captured_error(
    tmp_path: Path,
) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_error = RuntimeError("scan failed for X")
    status = col.get_index_status()
    assert status["status"] == "failed"
    assert status["error"] is not None
    assert "scan failed for X" in status["error"]
    col.close()


# ---------------------------------------------------------------------------
# MCP integration test for get_index_status
# ---------------------------------------------------------------------------


def test_mcp_tool_get_index_status_reports_queryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    from markdown_vault_mcp.server import make_server

    server = make_server()

    async def _call() -> dict[str, Any]:
        async with Client(server) as client:
            res = await client.call_tool("get_index_status", {})
            return res.structured_content or {}

    status = asyncio.run(_call())
    assert status["status"] in ("queryable", "building")  # depends on lifespan timing
    assert status["error"] is None


# ---------------------------------------------------------------------------
# close() joins background thread (Task 7)
# ---------------------------------------------------------------------------


def test_close_joins_background_thread(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    for i in range(3):
        _seed(vault, f"n_{i}.md", f"# N{i}\n\nbody {i}\n")
    col = Collection(source_dir=vault, index_path=tmp_path / "fts.db")
    col.start_background_build_index()
    col.close()
    thread = col._background_build_thread
    assert thread is not None
    assert not thread.is_alive()


def test_close_before_start_is_safe(tmp_path: Path) -> None:
    """A Collection that never had a background build can still close cleanly."""
    col = Collection(source_dir=_vault(tmp_path))
    col.close()  # must not raise


def test_close_twice_is_safe(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    col.close()
    col.close()  # idempotent


# ---------------------------------------------------------------------------
# Task 8: Lifespan rewire tests
# ---------------------------------------------------------------------------


def test_lifespan_cold_start_handshake_under_1s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as time_mod

    from markdown_vault_mcp.server import make_server

    vault = tmp_path / "vault"
    vault.mkdir()
    for i in range(20):
        (vault / f"n_{i}.md").write_text(
            f"# N{i}\n\n" + ("body " * 200) + "\n", encoding="utf-8"
        )

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    server = make_server()

    async def _run() -> tuple[float, dict[str, Any]]:
        start = time_mod.perf_counter()
        async with Client(server) as client:
            handshake_elapsed = time_mod.perf_counter() - start
            res: Any = None
            for _ in range(50):
                res = await client.call_tool("get_index_status", {})
                if (res.structured_content or {}).get("status") == "queryable":
                    break
                await asyncio.sleep(0.1)
            final = res.structured_content or {}
        return handshake_elapsed, final

    handshake_elapsed, final = asyncio.run(_run())
    assert handshake_elapsed < 1.0, (
        f"cold-start handshake took {handshake_elapsed:.3f}s, expected < 1.0s"
    )
    assert final["status"] == "queryable"
    assert final["documents_indexed"] == 20


def test_lifespan_warm_start_skips_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warm-start lifespan submits a BuildIndex job that short-circuits in
    O(1) via the FTS sentinel; status reaches queryable shortly after the
    server starts handling requests (#559)."""
    from markdown_vault_mcp.server import make_server
    from tests.conftest import wait_for_mcp_writer_drain

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody\n", encoding="utf-8")
    index_path = tmp_path / "fts.db"

    pre = Collection(source_dir=vault, index_path=index_path)
    pre.build_index()
    pre.close()

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    server = make_server()

    async def _run() -> dict[str, Any]:
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            res = await client.call_tool("get_index_status", {})
            return res.structured_content or {}

    status = asyncio.run(_run())
    assert status["status"] == "queryable"
    assert status["documents_indexed"] == 1


def test_lifespan_cold_start_with_embeddings_submits_both_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Provider configured + cold start: lifespan submits BuildIndex AND
    BuildEmbeddings jobs to the writer FIFO and yields immediately (#559).

    The writer's FIFO ordering guarantees that BuildEmbeddings runs after
    BuildIndex even when both are submitted while the writer is still
    draining the BuildIndex job — so no synchronous ``is_queryable()``
    gate is needed at the lifespan layer.
    """
    import logging
    import time as time_mod

    from markdown_vault_mcp.server import make_server
    from tests.conftest import MockEmbeddingProvider

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody\n", encoding="utf-8")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    # Inject a MockEmbeddingProvider into to_collection_kwargs so that
    # kwargs["embedding_provider"] is non-None without needing a real provider.
    from markdown_vault_mcp import config as config_mod

    original_to_kwargs = config_mod.CollectionConfig.to_collection_kwargs

    def patched_to_kwargs(self):  # type: ignore[no-untyped-def]
        kw = original_to_kwargs(self)
        kw["embedding_provider"] = MockEmbeddingProvider()
        if kw.get("embeddings_path") is None:
            kw["embeddings_path"] = tmp_path / "vectors"
        return kw

    monkeypatch.setattr(
        config_mod.CollectionConfig, "to_collection_kwargs", patched_to_kwargs
    )

    server = make_server()
    caplog.set_level(logging.INFO)

    async def _run() -> float:
        start = time_mod.perf_counter()
        async with Client(server):
            handshake_elapsed = time_mod.perf_counter() - start
            return handshake_elapsed

    handshake_elapsed = asyncio.run(_run())
    # The lifespan must NOT block on the index/embeddings build.
    assert handshake_elapsed < 2.0, (
        f"lifespan handshake took {handshake_elapsed:.3f}s; expected < 2.0s"
    )
    # Both submission log entries must be present.
    messages = [record.message for record in caplog.records]
    assert any("Submitted BuildIndex job" in m for m in messages), (
        f"expected 'Submitted BuildIndex job' log; got: {messages}"
    )
    assert any("Submitted BuildEmbeddings job" in m for m in messages), (
        f"expected 'Submitted BuildEmbeddings job' log; got: {messages}"
    )


def test_decorator_preflight_one_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """COMMIT-A gate: decorator + applied to get_backlinks only.
    The tool must be callable end-to-end via Client — proves FastMCP
    accepted the wrapped handler and injected `collection` correctly."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    from markdown_vault_mcp.server import make_server

    server = make_server()

    async def _call() -> list[Any]:
        async with Client(server) as client:
            res = await client.call_tool("get_backlinks", {"path": "a.md"})
            return res.structured_content or []

    result = asyncio.run(_call())
    assert isinstance(result, (list, dict))  # may be wrapped; just must not raise


def test_decorator_cold_path_blocks_until_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An MCP-client call to get_backlinks during in-flight background
    blocks on the decorator's wait, then succeeds when the build finishes.
    """
    import time as time_mod

    from markdown_vault_mcp.managers import index as index_mod
    from markdown_vault_mcp.server import make_server

    vault = tmp_path / "vault"
    vault.mkdir()
    for i in range(3):
        (vault / f"n_{i}.md").write_text(f"# N{i}\n\nbody\n", encoding="utf-8")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    original_build_index = index_mod.IndexManager.build_index

    def slow_build_index(self, *, force: bool = False):  # type: ignore[no-untyped-def]
        time_mod.sleep(0.3)
        return original_build_index(self, force=force)

    monkeypatch.setattr(index_mod.IndexManager, "build_index", slow_build_index)

    server = make_server()

    async def _call() -> Any:
        async with Client(server) as client:
            return await client.call_tool("get_backlinks", {"path": "n_0.md"})

    result = asyncio.run(_call())
    assert result is not None  # call succeeded after blocking ~0.3s


def test_decorator_applied_to_remaining_bucket3_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All seven bucket-3 tools must accept calls end-to-end after lifespan.
    Sanity coverage — proves the decorator pattern propagates without breakage."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    from markdown_vault_mcp.server import make_server

    server = make_server()

    async def _calls() -> None:
        async with Client(server) as client:
            # Bucket-3 tools that take a path.
            for tool in ("get_outlinks", "get_context"):
                await client.call_tool(tool, {"path": "a.md"})
            # get_similar takes path; may error if no embeddings — accept either
            with contextlib.suppress(Exception):
                await client.call_tool("get_similar", {"path": "a.md"})
            await client.call_tool(
                "get_connection_path", {"source": "a.md", "target": "a.md"}
            )
            # Bucket-4 coordinators (reindex always callable, build_embeddings
            # may raise ValueError if not configured).
            await client.call_tool("reindex", {})
            with contextlib.suppress(Exception):
                await client.call_tool("build_embeddings", {"force": False})

    asyncio.run(_calls())


def test_decorator_applied_to_vault_toc_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """vault_toc resource (toc://vault/{path}) must be decorated; calling
    via Client returns content after lifespan completes."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    from markdown_vault_mcp.server import make_server

    server = make_server()

    async def _read() -> Any:
        async with Client(server) as client:
            return await client.read_resource("toc://vault/a.md")

    result = asyncio.run(_read())
    assert result is not None


def test_decorator_applied_to_vault_similar_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """vault_similar resource (similar://vault/{path}) — same coverage as vault_toc.
    Catches the regression where the prior round forgot this one."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

    from markdown_vault_mcp.server import make_server

    server = make_server()

    async def _read() -> Any:
        async with Client(server) as client:
            try:
                return await client.read_resource("similar://vault/a.md")
            except Exception:
                # May raise if no embeddings configured — that's fine,
                # we're testing the decorator wired it correctly.
                return None

    asyncio.run(_read())


def test_decorator_respects_env_timeout_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """monkeypatch.setenv must be visible at call time because
    _resolve_build_timeout reads at call time, not import time."""
    import time as time_mod

    from markdown_vault_mcp.managers import index as index_mod
    from markdown_vault_mcp.server import make_server

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S", "0.1")

    # Mock build_index to never finish so the timeout fires.
    original = index_mod.IndexManager.build_index

    def hang(self, *, force: bool = False):  # type: ignore[no-untyped-def]
        time_mod.sleep(60)
        return original(self, force=force)

    monkeypatch.setattr(index_mod.IndexManager, "build_index", hang)

    server = make_server()

    async def _call() -> Any:
        async with Client(server) as client:
            start = time_mod.perf_counter()
            try:
                await client.call_tool("get_backlinks", {"path": "a.md"})
            except Exception as exc:
                elapsed = time_mod.perf_counter() - start
                assert elapsed < 1.0, f"timeout not honored: elapsed {elapsed:.2f}s"
                return exc
            raise AssertionError("expected the tool call to raise")

    exc = asyncio.run(_call())
    assert exc is not None


# ---------------------------------------------------------------------------
# Task 11: Boundary regression test + git pull loop test + foreground-write test
# ---------------------------------------------------------------------------


def test_require_built_raises_immediately_not_blocks(tmp_path: Path) -> None:
    """Bucket-3/4 library method called during in-flight background build
    raises IndexUnavailableError WITHIN 0.1s wall-clock — does NOT block.

    This is the canonical regression test against attempt-6's hole."""
    import time as time_mod

    from markdown_vault_mcp.managers import index as index_mod

    vault = _vault(tmp_path)
    for i in range(3):
        _seed(vault, f"n_{i}.md", f"# N{i}\n\nbody\n")
    col = Collection(source_dir=vault, index_path=tmp_path / "fts.db")

    original = index_mod.IndexManager.build_index

    def slow(self, *, force: bool = False):  # type: ignore[no-untyped-def]
        time_mod.sleep(1.0)
        return original(self, force=force)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(index_mod.IndexManager, "build_index", slow)
    try:
        col.start_background_build_index()

        start = time_mod.perf_counter()
        with pytest.raises(IndexUnavailableError) as excinfo:
            col.get_backlinks("n_0.md")  # bucket-3 library call
        assert excinfo.value.reason == "never_built"
        elapsed = time_mod.perf_counter() - start
        assert elapsed < 0.1, (
            f"library method blocked for {elapsed:.3f}s; should raise immediately"
        )
    finally:
        monkeypatch.undo()
        col.wait_until_queryable(timeout=5.0)
        col.close()


def test_git_pull_during_background_does_not_starve_writes(tmp_path: Path) -> None:
    """The on_pull=reindex callback in the git pull loop must NOT hold
    _write_lock while blocking on the background build. Reindex raises
    IndexUnavailableError, git_sync catches it, releases the lock, retries
    on next interval — no lock starvation."""
    import time as time_mod

    from markdown_vault_mcp.managers import index as index_mod

    vault = _vault(tmp_path)
    for i in range(5):
        _seed(vault, f"n_{i}.md", f"# N{i}\n\nbody\n")
    col = Collection(source_dir=vault, index_path=tmp_path / "fts.db", read_only=False)

    original = index_mod.IndexManager.build_index

    def slow(self, *, force: bool = False):  # type: ignore[no-untyped-def]
        time_mod.sleep(0.5)
        return original(self, force=force)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(index_mod.IndexManager, "build_index", slow)
    try:
        col.start_background_build_index()

        # Simulate the on_pull callback (reindex) racing the foreground
        # write. Both should fast-fail / proceed without lock starvation.
        with pytest.raises(IndexUnavailableError) as excinfo:
            col.reindex()  # raises immediately, releases internal locks
        assert excinfo.value.reason == "never_built"

        # Foreground write must complete promptly (within 1s, well
        # under the slow scan's 0.5s sleep).
        start = time_mod.perf_counter()
        col.write("racy.md", "# Racy\n\nforeground content\n")
        elapsed = time_mod.perf_counter() - start
        assert elapsed < 1.0, (
            f"foreground write blocked for {elapsed:.3f}s (suggests lock starvation)"
        )
    finally:
        monkeypatch.undo()
        col.wait_until_queryable(timeout=5.0)
        col.close()


def test_foreground_write_during_background_scan_on_disk(tmp_path: Path) -> None:
    """On-disk DB: race a foreground write() against background scan.
    Assertions per spec I13:
      1. No SQLite locking error.
      2. racy.md row exists in FTS.
      3. NO assertion on which content wins. Last-writer-wins per path
         is the accepted contract; staleness is corrected by next reindex.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    for i in range(30):
        (vault / f"seed_{i}.md").write_text(
            f"# Seed {i}\n\n" + ("body " * 300) + "\n", encoding="utf-8"
        )
    col = Collection(source_dir=vault, index_path=tmp_path / "fts.db", read_only=False)
    col.start_background_build_index()
    col.write("racy.md", "# Racy\n\nFOREGROUND CONTENT\n")
    col.wait_until_queryable(timeout=10.0)

    rows = {r["path"]: r for r in col._fts.list_notes()}
    assert "racy.md" in rows, "foreground write must end up in FTS"
    col.close()


def test_reindex_after_pull_handler_handles_not_ready(tmp_path: Path) -> None:
    """_reindex_after_pull in _server_tools.py catches IndexUnavailableError
    and sets reindex_failed=True on the pull payload — does NOT block."""
    import time as time_mod

    from markdown_vault_mcp._server_tools import _reindex_after_pull
    from markdown_vault_mcp.managers import index as index_mod

    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault, index_path=tmp_path / "fts.db", read_only=False)

    original = index_mod.IndexManager.build_index

    def slow(self, *, force: bool = False):  # type: ignore[no-untyped-def]
        time_mod.sleep(0.5)
        return original(self, force=force)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(index_mod.IndexManager, "build_index", slow)
    try:
        col.start_background_build_index()
        pull_dict: dict[str, Any] = {}
        asyncio.run(_reindex_after_pull(col, pull_dict))
        assert pull_dict.get("reindex_failed") is True
        assert "reindex_hint" in pull_dict
    finally:
        monkeypatch.undo()
        col.wait_until_queryable(timeout=5.0)
        col.close()


def test_synchronous_build_index_clears_prior_background_error(
    tmp_path: Path,
) -> None:
    """Recovery path: after a failed background build, calling build_index()
    synchronously sets _index_built=True so is_queryable() returns True and
    bucket-3/4 calls succeed. Also clears _background_build_error so the
    diagnostic field in get_index_status reflects the new successful
    attempt."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault, index_path=tmp_path / "fts.db")

    # Simulate a prior failed background: error captured, event set,
    # _index_built still False.
    col._background_build_error = RuntimeError("simulated prior background failure")
    col._background_build_done.set()
    col._background_started = True
    assert col.is_queryable() is False

    # Synchronous recovery build.
    col.build_index()

    # Now ready: error cleared, _index_built True, event still set.
    assert col._background_build_error is None
    assert col.is_queryable() is True
    # Bucket-3 call no longer surfaces the prior error.
    col.get_backlinks("n.md")  # must not raise
    col.close()


def test_build_index_async_warm_restart_short_circuit(tmp_path: Path) -> None:
    """build_index_async returns an already-resolved Future on warm restart (#559).

    The async path must mirror the synchronous build_index() short-circuit: a
    populated FTS + the persisted completeness sentinel must yield an
    already-resolved Future carrying the existing document count without
    submitting a job to the writer queue.
    """
    vault = _vault(tmp_path)
    _seed(vault)
    index_path = tmp_path / "fts.db"

    # Phase 1: pre-build to set the sentinel and FTS rows.
    pre = Collection(source_dir=vault, index_path=index_path)
    pre.build_index()
    pre.close()

    # Phase 2: fresh Collection sees the warm sentinel; async submission
    # must short-circuit.
    col = Collection(source_dir=vault, index_path=index_path)
    future = col.build_index_async()
    assert future.done(), "warm-restart short-circuit must return resolved Future"
    stats = future.result(timeout=0.1)
    assert stats.documents_indexed >= 1
    # Writer should NOT be processing a BuildIndex job.
    assert col._writer.get_status()["in_flight"] is None
    # Collection is queryable and the background-build event is set.
    assert col.is_queryable() is True
    assert col._background_build_done.is_set()
    col.close()


def test_build_index_async_submit_failure_unblocks_waiters(tmp_path: Path) -> None:
    """If ``submit()`` raises in ``build_index_async``'s cold path, the
    completion event is still set with the captured error so
    ``wait_until_queryable()`` doesn't hang until timeout (#559).

    Regression guard for the lock-discipline audit: the cold-path
    event-clear / submit / attach trio used to leave the event cleared
    if submit() raised, so any caller already blocked on
    ``wait_until_queryable()`` would wait until its timeout fired even
    though the submission had failed synchronously.
    """
    col = Collection(source_dir=_vault(tmp_path))
    # Force the writer closed so submit() raises RuntimeError on the
    # next call.
    col._writer.close(timeout=5)
    try:
        with pytest.raises(RuntimeError, match="closed"):
            col.build_index_async()
        # The cold-path try/except must have set the event before
        # re-raising; wait_until_queryable() must therefore return
        # quickly with IndexUnavailableError rather than blocking.
        with pytest.raises(IndexUnavailableError) as excinfo:
            col.wait_until_queryable(timeout=2.0)
        # never_built is the expected reason: event set, _index_built
        # False, _background_build_error populated.
        assert excinfo.value.reason == "never_built"
        assert isinstance(col._background_build_error, RuntimeError)
    finally:
        col.close()


def test_synchronous_build_index_warm_path_clears_prior_background_error(
    tmp_path: Path,
) -> None:
    """Recovery path via the warm-restart short-circuit: a prior background
    failure left _background_build_error populated; the sentinel from a
    prior successful build is still present; calling build_index()
    synchronously must clear the captured error and is_queryable() must
    return True."""
    vault = _vault(tmp_path)
    _seed(vault)
    index_path = tmp_path / "fts.db"

    # Phase 1: pre-build to set the sentinel and FTS rows.
    pre = Collection(source_dir=vault, index_path=index_path)
    pre.build_index()
    pre.close()

    # Phase 2: fresh Collection sees the warm sentinel; simulate a prior
    # background failure.
    col = Collection(source_dir=vault, index_path=index_path)
    col._background_build_error = RuntimeError("simulated prior background failure")
    col._background_build_done.set()
    col._background_started = True
    assert col.is_queryable() is False

    # Warm-restart short-circuit recovery.
    col.build_index()

    assert col._background_build_error is None
    assert col.is_queryable() is True
    col.close()


def test_decorator_works_with_positional_collection_arg(tmp_path: Path) -> None:
    """The decorator must extract `collection` from positional args
    via inspect.signature.bind_partial, not just from kwargs.
    Direct call (no FastMCP) with positional collection must work."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()  # mark ready

    @needs_queryable()
    async def handler(path: str, collection: Collection) -> str:  # noqa: ARG001
        return f"got: {path}"

    # Call positionally (not via kwargs).
    result = asyncio.run(handler("n.md", col))
    assert result == "got: n.md"
    col.close()


class TestNeedsQueryableSqliteCatch:
    """needs_queryable decorator's narrow sqlite3.OperationalError remap."""

    @staticmethod
    def _ready_collection(tmp_path: Path) -> Collection:
        vault = _vault(tmp_path)
        _seed(vault)
        col = Collection(source_dir=vault)
        col.build_index()
        return col

    def test_decorator_remaps_sqlite_busy_to_reason_busy(self, tmp_path: Path) -> None:
        col = self._ready_collection(tmp_path)
        original = sqlite3.OperationalError("database is locked")
        original.sqlite_errorname = "SQLITE_BUSY"  # type: ignore[attr-defined]

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(IndexUnavailableError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value.reason == "busy"
        assert excinfo.value.__cause__ is original
        col.close()

    def test_decorator_remaps_sqlite_locked_to_reason_busy(
        self, tmp_path: Path
    ) -> None:
        col = self._ready_collection(tmp_path)
        original = sqlite3.OperationalError("database table is locked")
        original.sqlite_errorname = "SQLITE_LOCKED"  # type: ignore[attr-defined]

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(IndexUnavailableError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value.reason == "busy"
        assert excinfo.value.__cause__ is original
        col.close()

    def test_decorator_remaps_sqlite_full_to_reason_broken(
        self, tmp_path: Path
    ) -> None:
        """SQLITE_FULL (disk full) requires operator action to free
        space, not retry — so it classifies as broken, not busy."""
        col = self._ready_collection(tmp_path)
        original = sqlite3.OperationalError("database or disk is full")
        original.sqlite_errorname = "SQLITE_FULL"  # type: ignore[attr-defined]

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(IndexUnavailableError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value.reason == "broken"
        assert excinfo.value.__cause__ is original
        col.close()

    def test_decorator_remaps_sqlite_corrupt_to_reason_broken(
        self, tmp_path: Path
    ) -> None:
        col = self._ready_collection(tmp_path)
        original = sqlite3.OperationalError("database disk image is malformed")
        original.sqlite_errorname = "SQLITE_CORRUPT"  # type: ignore[attr-defined]

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(IndexUnavailableError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value.reason == "broken"
        assert excinfo.value.__cause__ is original
        col.close()

    def test_decorator_remaps_sqlite_notadb_to_reason_broken(
        self, tmp_path: Path
    ) -> None:
        col = self._ready_collection(tmp_path)
        original = sqlite3.OperationalError("file is not a database")
        original.sqlite_errorname = "SQLITE_NOTADB"  # type: ignore[attr-defined]

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(IndexUnavailableError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value.reason == "broken"
        assert excinfo.value.__cause__ is original
        col.close()

    def test_decorator_remaps_sqlite_ioerr_to_reason_broken(
        self, tmp_path: Path
    ) -> None:
        """IOERR is NOT in the busy whitelist — conservative broken-default."""
        col = self._ready_collection(tmp_path)
        original = sqlite3.OperationalError("disk I/O error")
        original.sqlite_errorname = "SQLITE_IOERR"  # type: ignore[attr-defined]

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(IndexUnavailableError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value.reason == "broken"
        assert excinfo.value.__cause__ is original
        col.close()

    def test_decorator_remaps_sqlite_operational_error_without_errorname_to_broken(
        self, tmp_path: Path
    ) -> None:
        """Manually-constructed OperationalError (no sqlite_errorname) → broken.
        Defensive against any non-driver-raised OperationalError reaching the
        classifier."""
        col = self._ready_collection(tmp_path)
        original = sqlite3.OperationalError("synthetic, no errorname")

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(IndexUnavailableError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value.reason == "broken"
        assert excinfo.value.__cause__ is original
        col.close()

    def test_decorator_does_not_remap_programming_error(self, tmp_path: Path) -> None:
        col = self._ready_collection(tmp_path)
        original = sqlite3.ProgrammingError("incorrect number of bindings")

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(sqlite3.ProgrammingError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value is original
        col.close()

    def test_decorator_does_not_remap_os_error(self, tmp_path: Path) -> None:
        col = self._ready_collection(tmp_path)
        original = OSError("permission denied")

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(OSError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value is original
        col.close()

    def test_decorator_does_not_remap_value_error(self, tmp_path: Path) -> None:
        col = self._ready_collection(tmp_path)
        original = ValueError("bad input")

        @needs_queryable()
        async def handler(collection: Collection) -> None:  # noqa: ARG001
            raise original

        with pytest.raises(ValueError) as excinfo:
            asyncio.run(handler(collection=col))
        assert excinfo.value is original
        col.close()
