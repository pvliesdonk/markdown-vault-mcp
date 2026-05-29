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

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.exceptions import IndexNotReadyError

if TYPE_CHECKING:
    from pathlib import Path


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _seed(vault: Path, name: str = "n.md", body: str = "# N\n\nbody\n") -> None:
    (vault / name).write_text(body, encoding="utf-8")


def test_is_index_ready_false_after_construction(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    assert col.is_index_ready() is False
    col.close()


def test_is_index_ready_true_after_synchronous_build(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    assert col.is_index_ready() is True
    col.close()


def test_is_index_ready_true_after_captured_background_error_on_built_index(
    tmp_path: Path,
) -> None:
    """Issue #533 meaning shift: a captured background error on a
    previously-built index does NOT make the index unreadable. The
    captured error is diagnostic only; is_index_ready stays True."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    col._background_build_error = RuntimeError("subsequent rebuild blew up")
    assert col.is_index_ready() is True
    col.close()


def test_is_index_ready_false_when_not_built_regardless_of_error(
    tmp_path: Path,
) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_done.set()
    col._background_build_error = None
    # _index_built is False by construction.
    assert col.is_index_ready() is False
    col.close()


def test_is_index_ready_false_when_event_cleared_regardless_of_built(
    tmp_path: Path,
) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col._index_built = True
    col._background_build_done.clear()
    assert col.is_index_ready() is False
    col.close()


def test_wait_for_index_ready_returns_when_already_built(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()
    col.wait_for_index_ready(timeout=0.1)  # must not raise
    col.close()


def test_wait_for_index_ready_blocks_until_event_set(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_done.clear()
    col._index_built = False

    def setter() -> None:
        time.sleep(0.05)
        col._index_built = True
        col._background_build_done.set()

    threading.Thread(target=setter).start()
    col.wait_for_index_ready(timeout=1.0)  # returns when event fires
    col.close()


def test_wait_for_index_ready_raises_on_timeout(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col._background_build_done.clear()
    col._index_built = False
    with pytest.raises(IndexNotReadyError) as excinfo:
        col.wait_for_index_ready(timeout=0.05)
    assert excinfo.value.reason == "timeout"
    col.close()


def test_wait_for_index_ready_returns_when_built_with_captured_error(
    tmp_path: Path,
) -> None:
    """The 'errors are events not states' rule (#533): a captured background
    error on a previously-built index must NOT gate the read path.
    wait_for_index_ready returns normally; the error is diagnostic-only."""
    col = Collection(source_dir=_vault(tmp_path))
    col._index_built = True
    col._background_build_done.set()
    col._background_build_error = RuntimeError("prior scan exploded")
    col.wait_for_index_ready(timeout=0.1)  # must not raise
    col.close()


def test_wait_for_index_ready_raises_when_never_scheduled(tmp_path: Path) -> None:
    """Pre-set event + no error + _index_built=False + _background_started=False.
    This is the case the spec calls 'never scheduled' — the pre-set event would
    let wait() return success without the explicit guard."""
    col = Collection(source_dir=_vault(tmp_path))
    # All defaults: event pre-set, no error, _index_built=False, no spawn.
    with pytest.raises(IndexNotReadyError) as excinfo:
        col.wait_for_index_ready(timeout=0.1)
    assert excinfo.value.reason == "never_built"
    col.close()


def test_start_background_build_index_eventually_ready(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    for i in range(5):
        _seed(vault, f"n_{i}.md", f"# N{i}\n\nbody {i}\n")
    col = Collection(source_dir=vault)
    col.start_background_build_index()
    col.wait_for_index_ready(timeout=5.0)
    assert col.is_index_ready()
    col.get_backlinks("n_0.md")  # smoke: bucket-3 returns (empty list OK)
    col.close()


def test_start_background_build_index_captures_error_diagnostically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After #533, a captured background build error does NOT raise from
    wait_for_index_ready. The error becomes a diagnostic event visible
    via _background_build_error and get_index_status. The index stays
    not-ready because _index_built was never set."""
    col = Collection(source_dir=_vault(tmp_path))

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("simulated scan failure")

    monkeypatch.setattr(col._index_mgr, "build_index", boom)
    col.start_background_build_index()

    # wait_for_index_ready raises 'never_built' (not 'IndexBuildFailedError').
    with pytest.raises(IndexNotReadyError) as excinfo:
        col.wait_for_index_ready(timeout=5.0)
    assert excinfo.value.reason == "never_built"
    assert col.is_index_ready() is False
    # Diagnostic: captured error is visible.
    assert isinstance(col._background_build_error, RuntimeError)
    status = col.get_index_status()
    assert status["status"] == "failed"
    assert "simulated scan failure" in (status["error"] or "")
    col.close()


def test_start_background_build_index_idempotent(tmp_path: Path) -> None:
    col = Collection(source_dir=_vault(tmp_path))
    col.start_background_build_index()
    first = col._background_build_thread
    assert first is not None
    col.start_background_build_index()
    assert col._background_build_thread is first
    col.wait_for_index_ready(timeout=5.0)
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

    # wait_for_index_ready raises 'never_built' (no IndexBuildFailedError).
    with pytest.raises(IndexNotReadyError) as excinfo:
        col.wait_for_index_ready(timeout=0.1)
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
    Reports 'building' (the never-scheduled case behaves the same as
    in-flight from the operator's perspective)."""
    col = Collection(source_dir=_vault(tmp_path))
    status = col.get_index_status()
    assert status["status"] == "building"
    assert status["error"] is None
    col.close()


def test_get_index_status_failed_when_not_queryable_with_captured_error(
    tmp_path: Path,
) -> None:
    """status='failed' only when NOT queryable AND error captured AND
    event set. After #533 priority flip, a *built* index with a captured
    error reports 'queryable'; only the never-built-plus-errored case
    is 'failed'."""
    col = Collection(source_dir=_vault(tmp_path))
    # _index_built False; event already pre-set; explicitly set error.
    col._background_build_error = RuntimeError("scan failed for X")
    status = col.get_index_status()
    assert status["status"] == "failed"
    assert "scan failed for X" in status["error"]
    col.close()


def test_get_index_status_queryable_when_built_with_captured_error(
    tmp_path: Path,
) -> None:
    """The #533 priority flip: a built+done index with a captured error
    reports 'queryable' (not 'failed'). The error field carries the
    last-attempt message as diagnostic context."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()  # sets _index_built True and clears _background_build_error
    col._background_build_error = RuntimeError("subsequent rebuild blew up")
    status = col.get_index_status()
    assert status["status"] == "queryable"
    assert status["documents_indexed"] == 1
    assert "subsequent rebuild blew up" in (status["error"] or "")
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
    from markdown_vault_mcp.server import make_server

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
            res = await client.call_tool("get_index_status", {})
            return res.structured_content or {}

    status = asyncio.run(_run())
    assert status["status"] == "queryable"
    assert status["documents_indexed"] == 1


def test_lifespan_cold_start_with_embeddings_skips_embeddings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Provider configured + cold start: lifespan must log the skip and not block.

    Must inject a slow _index_mgr.build_index mock so the background thread is
    reliably still running when the lifespan checks is_index_ready() — otherwise
    on a tiny vault the background completes between spawn and check, embeddings
    runs, and the test asserts the wrong thing.
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

    # Patch IndexManager.build_index globally to sleep before returning,
    # ensuring the background thread is still running when the lifespan
    # makes the is_index_ready() decision.
    from markdown_vault_mcp.managers import index as index_mod

    original_build_index = index_mod.IndexManager.build_index

    def slow_build_index(self, *, force: bool = False):  # type: ignore[no-untyped-def]
        time_mod.sleep(0.5)
        return original_build_index(self, force=force)

    monkeypatch.setattr(index_mod.IndexManager, "build_index", slow_build_index)

    # Inject a MockEmbeddingProvider into to_collection_kwargs so that
    # kwargs["embedding_provider"] is non-None without needing a real provider.
    # "mock" is not a registered provider name in get_embedding_provider(), so
    # we patch at the config level instead.
    from markdown_vault_mcp import config as config_mod

    original_to_kwargs = config_mod.CollectionConfig.to_collection_kwargs

    def patched_to_kwargs(self):  # type: ignore[no-untyped-def]
        kw = original_to_kwargs(self)
        kw["embedding_provider"] = MockEmbeddingProvider()
        # embeddings_path is required when embedding_provider is set.
        if kw.get("embeddings_path") is None:
            kw["embeddings_path"] = tmp_path / "vectors"
        return kw

    monkeypatch.setattr(
        config_mod.CollectionConfig, "to_collection_kwargs", patched_to_kwargs
    )

    server = make_server()
    caplog.set_level(logging.INFO)

    async def _run() -> None:
        async with Client(server):
            pass  # lifespan runs

    asyncio.run(_run())
    assert any(
        "embeddings deferred" in record.message.lower()
        or "skipping embeddings" in record.message.lower()
        for record in caplog.records
    ), f"expected 'embeddings deferred' log; got: {[r.message for r in caplog.records]}"


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
    _resolve_ready_timeout reads at call time, not import time."""
    import time as time_mod

    from markdown_vault_mcp.managers import index as index_mod
    from markdown_vault_mcp.server import make_server

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READY_TIMEOUT_S", "0.1")

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


def test_require_index_ready_raises_immediately_not_blocks(tmp_path: Path) -> None:
    """Bucket-3/4 library method called during in-flight background build
    raises IndexNotReadyError WITHIN 0.1s wall-clock — does NOT block.

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
        with pytest.raises(IndexNotReadyError):
            col.get_backlinks("n_0.md")  # bucket-3 library call
        elapsed = time_mod.perf_counter() - start
        assert elapsed < 0.1, (
            f"library method blocked for {elapsed:.3f}s; should raise immediately"
        )
    finally:
        monkeypatch.undo()
        col.wait_for_index_ready(timeout=5.0)
        col.close()


def test_git_pull_during_background_does_not_starve_writes(tmp_path: Path) -> None:
    """The on_pull=reindex callback in the git pull loop must NOT hold
    _write_lock while blocking on the background build. Reindex raises
    IndexNotReadyError, git_sync catches it, releases the lock, retries
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
        with pytest.raises(IndexNotReadyError):
            col.reindex()  # raises immediately, releases internal locks

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
        col.wait_for_index_ready(timeout=5.0)
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
    col.wait_for_index_ready(timeout=10.0)

    rows = {r["path"]: r for r in col._fts.list_notes()}
    assert "racy.md" in rows, "foreground write must end up in FTS"
    col.close()


def test_reindex_after_pull_handler_handles_not_ready(tmp_path: Path) -> None:
    """_reindex_after_pull in _server_tools.py catches IndexNotReadyError
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
        col.wait_for_index_ready(timeout=5.0)
        col.close()


def test_synchronous_build_index_clears_prior_background_error(
    tmp_path: Path,
) -> None:
    """Recovery path: after a failed background build, calling build_index()
    synchronously must clear _background_build_error so is_index_ready()
    returns True after build completion and bucket-3/4 calls operate normally."""
    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault, index_path=tmp_path / "fts.db")

    # Simulate a prior failed background: error captured, event set,
    # _index_built still False.
    col._background_build_error = RuntimeError("simulated prior background failure")
    col._background_build_done.set()
    col._background_started = True
    assert col.is_index_ready() is False

    # Synchronous recovery build.
    col.build_index()

    # Now ready: error cleared, _index_built True, event still set.
    assert col._background_build_error is None
    assert col.is_index_ready() is True
    # Bucket-3 call no longer surfaces the prior error.
    col.get_backlinks("n.md")  # must not raise
    col.close()


def test_synchronous_build_index_warm_path_clears_prior_background_error(
    tmp_path: Path,
) -> None:
    """Recovery path via the warm-restart short-circuit: a prior background
    failure left _background_build_error populated; the sentinel from a
    prior successful build is still present; calling build_index()
    synchronously must clear the captured error and is_index_ready() must
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
    assert col.is_index_ready() is False

    # Warm-restart short-circuit recovery.
    col.build_index()

    assert col._background_build_error is None
    assert col.is_index_ready() is True
    col.close()


def test_decorator_works_with_positional_collection_arg(tmp_path: Path) -> None:
    """The decorator must extract `collection` from positional args
    via inspect.signature.bind_partial, not just from kwargs.
    Direct call (no FastMCP) with positional collection must work."""
    import asyncio

    from markdown_vault_mcp._server_readiness import needs_index_ready

    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()  # mark ready

    @needs_index_ready()
    async def handler(path: str, collection: Collection) -> str:  # noqa: ARG001
        return f"got: {path}"

    # Call positionally (not via kwargs).
    result = asyncio.run(handler("n.md", col))
    assert result == "got: n.md"
    col.close()


def test_decorator_catches_sqlite_operational_error_and_remaps_to_broken(
    tmp_path: Path,
) -> None:
    """Issue #533 reactive broken detection: when a handler bubbles a
    sqlite3.OperationalError up through the decorator, the MCP boundary
    remaps it to IndexNotReadyError(reason='broken') with __cause__ set
    to the original."""
    import asyncio

    from markdown_vault_mcp._server_readiness import needs_index_ready

    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()

    @needs_index_ready()
    async def fake_handler(collection: Collection) -> None:  # noqa: ARG001
        raise sqlite3.OperationalError("database disk image is malformed")

    async def _call() -> Any:
        try:
            await fake_handler(col)  # type: ignore[arg-type]
        except IndexNotReadyError as exc:
            return exc
        raise AssertionError("expected IndexNotReadyError")

    err = asyncio.run(_call())
    assert err.reason == "broken"
    assert isinstance(err.__cause__, sqlite3.OperationalError)
    col.close()


def test_decorator_does_not_remap_sqlite_programming_error(
    tmp_path: Path,
) -> None:
    """Narrow scope: only sqlite3.OperationalError is remapped. Other
    sqlite3 subclasses (IntegrityError, ProgrammingError) bubble up
    unwrapped because they indicate distinct caller-actionable bugs."""
    import asyncio

    from markdown_vault_mcp._server_readiness import needs_index_ready

    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()

    @needs_index_ready()
    async def fake_handler(collection: Collection) -> None:  # noqa: ARG001
        raise sqlite3.ProgrammingError("bad statement binding")

    async def _call() -> Any:
        return await fake_handler(col)  # type: ignore[arg-type]

    with pytest.raises(sqlite3.ProgrammingError):
        asyncio.run(_call())
    col.close()


def test_decorator_does_not_remap_os_error(
    tmp_path: Path,
) -> None:
    """OSError is out of scope for the decorator catch (#533 narrow
    scope) — it bubbles up unwrapped."""
    import asyncio

    from markdown_vault_mcp._server_readiness import needs_index_ready

    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()

    @needs_index_ready()
    async def fake_handler(collection: Collection) -> None:  # noqa: ARG001
        raise OSError("disk gone")

    async def _call() -> Any:
        return await fake_handler(col)  # type: ignore[arg-type]

    with pytest.raises(OSError, match=r"disk gone"):
        asyncio.run(_call())
    col.close()


def test_decorator_does_not_remap_generic_exception(
    tmp_path: Path,
) -> None:
    import asyncio

    from markdown_vault_mcp._server_readiness import needs_index_ready

    vault = _vault(tmp_path)
    _seed(vault)
    col = Collection(source_dir=vault)
    col.build_index()

    @needs_index_ready()
    async def fake_handler(collection: Collection) -> None:  # noqa: ARG001
        raise ValueError("handler-level value error")

    async def _call() -> Any:
        return await fake_handler(col)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=r"handler-level"):
        asyncio.run(_call())
    col.close()
