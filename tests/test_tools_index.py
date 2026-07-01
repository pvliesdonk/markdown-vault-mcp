"""End-to-end tests for the get_index_status tool skipped_files field (#775)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client

from markdown_vault_mcp.server import make_server
from tests.conftest import wait_for_mcp_writer_drain

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

# Mirrors tests/test_server.py's _CLEAR_VARS: env vars that must not leak
# from the shell/CI environment into a test that sets up its own minimal
# env via monkeypatch.
_CLEAR_VARS = (
    "MARKDOWN_VAULT_MCP_INDEX_PATH",
    "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
    "MARKDOWN_VAULT_MCP_STATE_PATH",
    "MARKDOWN_VAULT_MCP_INDEXED_FIELDS",
    "MARKDOWN_VAULT_MCP_REQUIRED_FIELDS",
    "MARKDOWN_VAULT_MCP_EXCLUDE",
    "MARKDOWN_VAULT_MCP_GIT_TOKEN",
    "MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER",
    "MARKDOWN_VAULT_MCP_SERVER_NAME",
    "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
    # Auth vars — ensure non-auth tests run unauthenticated
    "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
    "MARKDOWN_VAULT_MCP_AUTH_MODE",
    "MARKDOWN_VAULT_MCP_BASE_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
)


async def test_get_index_status_exposes_skipped_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "good.md").write_text("---\ntitle: ok\n---\nbody", encoding="utf-8")
    (tmp_path / "bad.md").write_text("---\ntitle: [unclosed\n---\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    server = make_server()
    async with Client(server) as client:
        # reindex submits an async job to the writer; wait for it to drain
        # so the skip is guaranteed recorded before get_index_status reads it.
        await client.call_tool("reindex", {})
        await wait_for_mcp_writer_drain(client)
        result = await client.call_tool("get_index_status", {})
    data = result.data
    paths = {e["path"]: e for e in data["skipped_files"]}
    assert "bad.md" in paths
    assert paths["bad.md"]["category"] == "parse_error"
