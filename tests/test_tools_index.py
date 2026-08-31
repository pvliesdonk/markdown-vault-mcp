"""End-to-end tests for the index-management tools (#775, #1124)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client

from tests.conftest import wait_for_mcp_writer_drain
from tests.server_factory import make_server

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
    "MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA",
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


async def test_reindex_reports_incremental_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain reindex applies the delta and says it was not a full rebuild."""
    (tmp_path / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    server = make_server()
    async with Client(server) as client:
        await wait_for_mcp_writer_drain(client)
        data = (await client.call_tool("reindex", {})).data
    assert data["status"] == "completed"
    assert data["full_rebuild"] is False


async def test_reindex_force_reparses_every_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force=True re-parses the whole vault, not the hash-detected delta (#1124).

    Change detection would report every unchanged file as ``unchanged``; the
    forced rebuild drops the index instead, so each document comes back as an
    add. That is the operator-facing repair for rows derived by an older
    extractor.
    """
    (tmp_path / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    server = make_server()
    async with Client(server) as client:
        await wait_for_mcp_writer_drain(client)
        data = (await client.call_tool("reindex", {"force": True})).data
        await wait_for_mcp_writer_drain(client)
        status = (await client.call_tool("get_index_status", {})).data
    assert data["status"] == "completed"
    assert data["full_rebuild"] is True
    assert data["added"] == 2
    assert data["modified"] == data["deleted"] == data["unchanged"] == 0
    assert status["documents_indexed"] == 2
