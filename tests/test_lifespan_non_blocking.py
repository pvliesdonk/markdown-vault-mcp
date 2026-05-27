"""Integration tests: MCP lifespan must not block on indexing, and must shut
down cleanly even if indexing is still in progress (issue #513)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from pathlib import Path


def _isolate_vault_env(
    monkeypatch: pytest.MonkeyPatch, vault: Path, state_dir: Path
) -> None:
    """Point the server config at *vault* and strip unrelated host env vars."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state_dir / "state.json"))
    for var in (
        "MARKDOWN_VAULT_MCP_READ_ONLY",
        "MARKDOWN_VAULT_MCP_INDEX_PATH",
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
        "MARKDOWN_VAULT_MCP_GIT_TOKEN",
        "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
        "MARKDOWN_VAULT_MCP_AUTH_MODE",
        "MARKDOWN_VAULT_MCP_BASE_URL",
        "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.asyncio
async def test_initialize_does_not_block_on_index_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synthetic delay injected into ``Collection.build_index`` would
    block the handshake under a synchronous lifespan but does not block it
    under ``BackgroundIndexer``. The test fails (with a > 3s elapsed time)
    if the lifespan ever reverts to ``await asyncio.to_thread(build_index)``.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nBody.\n", encoding="utf-8")

    original_build_index = Collection.build_index

    def slow_build_index(self: Collection, *, force: bool = False) -> object:
        # 4-second synthetic delay: comfortably exceeds the 3s handshake
        # budget so a synchronous regression is unambiguous on any hardware.
        time.sleep(4.0)
        return original_build_index(self, force=force)

    monkeypatch.setattr(Collection, "build_index", slow_build_index)

    _isolate_vault_env(monkeypatch, vault, tmp_path / ".state")
    server = make_server()

    start = time.monotonic()
    async with Client(server) as client:
        elapsed = time.monotonic() - start
        assert elapsed < 3.0, (
            f"handshake took {elapsed:.2f}s — lifespan may have reverted to "
            f"awaiting build_index synchronously"
        )
        # Tools are reachable immediately. The index itself may not be
        # populated yet because the slow build is still running in the
        # background — that's the point of the non-blocking design.
        tools = await client.list_tools()
        assert any(t.name == "search" for t in tools)


@pytest.mark.asyncio
async def test_shutdown_during_indexing_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lifespan teardown is called while ``build_index`` is still
    running on the daemon thread. The teardown must complete without
    raising and the warning ``background_indexer_join_timed_out`` does not
    need to fire (because the join honours the timeout) but the test
    primarily asserts that we *don't* crash on this path — the exact
    failure mode PRs #515 and #516 were abandoned over.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nBody.\n", encoding="utf-8")

    original_build_index = Collection.build_index

    def long_build_index(self: Collection, *, force: bool = False) -> object:
        # Indexing takes a few seconds — long enough that the daemon is
        # still active when the test's ``async with`` block exits.
        time.sleep(2.0)
        return original_build_index(self, force=force)

    monkeypatch.setattr(Collection, "build_index", long_build_index)

    _isolate_vault_env(monkeypatch, vault, tmp_path / ".state")
    server = make_server()

    async with Client(server) as client:
        tools = await client.list_tools()
        assert any(t.name == "search" for t in tools)
        # Exit the context immediately, while the daemon is still
        # mid-build_index. The lifespan finally-block runs here.
    # If we reach this line without an exception, the shutdown was clean.
