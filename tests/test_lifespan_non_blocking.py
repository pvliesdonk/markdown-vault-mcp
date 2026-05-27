"""Integration test: MCP `initialize` handshake must not block on indexing
(issue #513)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client

from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_initialize_does_not_block_on_index_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handshake completes promptly even on a vault where synchronous
    indexing would take measurable time."""
    vault = tmp_path / "vault"
    vault.mkdir()
    for i in range(50):
        (vault / f"note-{i:03d}.md").write_text(
            f"# Note {i}\n\nBody {i} with some content to index.\n",
            encoding="utf-8",
        )

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_STATE_PATH",
        str(tmp_path / ".state" / "state.json"),
    )
    # Strip vars that could pull in unrelated configuration from the host.
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

    server = make_server()

    start = time.monotonic()
    async with Client(server) as client:
        elapsed = time.monotonic() - start
        # Handshake should complete in well under a second; a 3s budget
        # tolerates CI noise without hiding regressions.
        assert elapsed < 3.0, f"handshake took {elapsed:.2f}s"
        tools = await client.list_tools()
        assert any(t.name == "search" for t in tools)
