"""Integration tests for server.py using FastMCP test client.

Tests exercise all MCP tools via the in-memory Client transport,
verifying end-to-end behaviour through the full Collection stack.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from markdown_vault_mcp.server import make_server
from tests.conftest import wait_for_mcp_writer_drain

if TYPE_CHECKING:
    import mcp.types as mcp_types


def _parse_tool_data(result: Any) -> Any:
    """Extract data from a CallToolResult, handling FastMCP v2 serialization.

    FastMCP v2 serializes list[dict] as a single JSON TextContent blob.
    ``result.data`` works for simple types (dict, str, list[str]) but
    returns opaque ``Root()`` objects for list[dict].  This helper falls
    back to parsing the raw text content when needed.
    """
    data = result.data
    if isinstance(data, list) and data and not isinstance(data[0], (dict, str)):
        # Opaque Root objects — parse from raw text content.
        raw = result.content[0].text if result.content else "[]"
        return json.loads(raw)
    return data


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


@pytest.fixture
def _mcp_env(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set minimal env vars for make_server (read_only=true default)."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def _mcp_env_writable(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars with read_only=false."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def _mcp_env_with_fields(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars with indexed frontmatter fields."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    # Set after clearing so it's not wiped by _CLEAR_VARS.
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "cluster,tags")


# ---------------------------------------------------------------------------
# Server identity
# ---------------------------------------------------------------------------


class TestServerIdentity:
    """Verify SERVER_NAME and INSTRUCTIONS env vars are respected."""

    @pytest.mark.usefixtures("_mcp_env")
    def test_defaults_read_only(self) -> None:
        server = make_server()
        assert server.name == "markdown-vault-mcp"
        assert "READ-ONLY" in server.instructions
        assert "not available" in server.instructions

    @pytest.mark.usefixtures("_mcp_env")
    def test_defaults_read_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        server = make_server()
        assert "READ-WRITE" in server.instructions
        assert "'write'" in server.instructions
        assert "'edit'" in server.instructions
        assert "'rename'" in server.instructions
        assert "'delete'" in server.instructions

    @pytest.mark.usefixtures("_mcp_env")
    def test_default_instructions_content(self) -> None:
        server = make_server()
        assert "relative" in server.instructions
        assert "'search'" in server.instructions
        assert "'stats'" in server.instructions
        # Core's build_instructions appends an operator-override hint
        # (by design — tells anyone reading the instructions that the
        # env var exists to customise them).
        assert "MARKDOWN_VAULT_MCP_INSTRUCTIONS" in server.instructions
        assert "Operators:" in server.instructions

    @pytest.mark.usefixtures("_mcp_env")
    def test_custom_server_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "my-vault")
        server = make_server()
        assert server.name == "my-vault"

    @pytest.mark.usefixtures("_mcp_env")
    def test_custom_instructions_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
            "Personal notes vault. Read-only.",
        )
        server = make_server()
        assert server.instructions == "Personal notes vault. Read-only."


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------


class TestToolListing:
    """Verify correct tools are registered based on read_only setting."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_write_tools_absent_when_readonly(self) -> None:
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}

        # Read-only tools present
        assert "search" in names
        assert "read" in names
        assert "list_documents" in names
        assert "list_folders" in names
        assert "list_tags" in names
        assert "stats" in names
        assert "embeddings_status" in names
        assert "reindex" in names
        assert "build_embeddings" in names
        assert "get_backlinks" in names
        assert "get_outlinks" in names
        assert "get_broken_links" in names
        assert "get_similar" in names
        assert "get_recent" in names
        # Write tools absent when read_only=true (default)
        assert "write" not in names
        assert "edit" not in names
        assert "delete" not in names
        assert "rename" not in names

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_tools_present_when_writable(self) -> None:
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}

        # Write tools present when read_only=false
        assert "write" in names
        assert "edit" in names
        assert "delete" in names
        assert "rename" in names


class TestToolAnnotations:
    """Verify ToolAnnotations are set correctly per tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_annotations(self) -> None:
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            by_name = {t.name: t for t in tools}

        # Read-only tools
        for name in (
            "search",
            "read",
            "list_documents",
            "list_folders",
            "list_tags",
            "stats",
            "embeddings_status",
            "get_backlinks",
            "get_outlinks",
            "get_broken_links",
            "get_similar",
            "get_recent",
        ):
            ann = by_name[name].annotations
            assert ann is not None, f"{name} missing annotations"
            assert ann.readOnlyHint is True, f"{name} readOnlyHint"
            assert ann.destructiveHint is False, f"{name} destructiveHint"

        # Index management tools — not readOnly
        for name in ("reindex", "build_embeddings"):
            ann = by_name[name].annotations
            assert ann is not None
            assert ann.readOnlyHint is False, f"{name} readOnlyHint"

        # Write tools — not readOnly
        for name in ("write", "edit", "rename"):
            ann = by_name[name].annotations
            assert ann is not None
            assert ann.readOnlyHint is False, f"{name} readOnlyHint"
            assert ann.destructiveHint is False, f"{name} destructiveHint"

        # Delete is destructive
        ann = by_name["delete"].annotations
        assert ann is not None
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is True


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------


class TestSearchTool:
    """Test the search MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_keyword_search(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "search", {"query": "simple document", "limit": 5}
            )
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) > 0
        paths = {r["path"] for r in data}
        assert "simple.md" in paths

    @pytest.mark.usefixtures("_mcp_env")
    async def test_search_with_folder_filter(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "search",
                {"query": "subfolder nested", "folder": "subfolder"},
            )
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) > 0, (
            "expected at least one result for 'subfolder nested' in subfolder"
        )
        for r in data:
            assert r["path"].startswith("subfolder/")


class TestReadTool:
    """Test the read MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_read_existing(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "simple.md"})
        data = result.data
        assert isinstance(data, dict)
        assert data["path"] == "simple.md"
        assert "Simple Document" in data["content"]

    @pytest.mark.usefixtures("_mcp_env")
    async def test_read_nonexistent(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp("read", {"path": "nonexistent.md"})
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env")
    async def test_read_with_frontmatter(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "full_frontmatter.md"})
        data = result.data
        assert data["title"] == "Full Frontmatter Note"
        assert data["frontmatter"]["cluster"] == "fiction"

    @pytest.mark.usefixtures("_mcp_env")
    async def test_read_template_file(self, vault_path: Path) -> None:
        template_path = vault_path / "_templates" / "meeting.md"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text("# Meeting Template\n\n- Date:\n- Attendees:\n")

        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "_templates/meeting.md"})
        data = result.data
        assert data["path"] == "_templates/meeting.md"
        assert "Meeting Template" in data["content"]


class TestListDocumentsTool:
    """Test the list_documents MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_all(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("list_documents", {})
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) > 0
        paths = {d["path"] for d in data}
        assert "simple.md" in paths

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_by_folder(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("list_documents", {"folder": "subfolder"})
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) > 0
        for doc in data:
            assert doc["folder"] == "subfolder" or doc["folder"].startswith(
                "subfolder/"
            )

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_templates_folder(self, vault_path: Path) -> None:
        template_path = vault_path / "_templates" / "daily.md"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text("# Daily Template\n\n## Highlights\n\n- \n")

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("list_documents", {"folder": "_templates"})
        data = _parse_tool_data(result)
        paths = {doc["path"] for doc in data}
        assert "_templates/daily.md" in paths


class TestListFoldersTool:
    """Test the list_folders MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_folders(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("list_folders", {})
        folders = result.data
        assert isinstance(folders, list)
        assert "subfolder" in folders


class TestListTagsTool:
    """Test the list_tags MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_list_tags(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("list_tags", {"field": "cluster"})
        tags = result.data
        assert isinstance(tags, list)
        assert "fiction" in tags


class TestStatsTool:
    """Test the stats MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_stats(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("stats", {})
        data = result.data
        assert isinstance(data, dict)
        assert data["document_count"] > 0
        assert data["chunk_count"] > 0
        assert "semantic_search_available" in data


class TestEmbeddingsStatusTool:
    """Test the embeddings_status MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_embeddings_status_no_provider(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("embeddings_status", {})
        data = result.data
        assert isinstance(data, dict)
        assert data["provider"] is None


class TestReindexTool:
    """Test the reindex MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_reindex_returns_queued_immediately(self) -> None:
        """reindex submits a job to the writer and returns {'status': 'queued'}."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("reindex", {})
        data = result.data
        assert data == {"status": "queued"}


class TestBuildEmbeddingsTool:
    """Test the build_embeddings MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_build_embeddings_returns_queued_immediately(self) -> None:
        """build_embeddings submits a job and returns {'status': 'queued'}."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("build_embeddings", {})
        data = result.data
        assert data == {"status": "queued"}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test structured error responses for invalid operations."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_semantic_search_without_embeddings_returns_error(self) -> None:
        """search with mode='semantic' when no embeddings configured returns error."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "search", {"query": "test", "mode": "semantic"}
            )
        assert result.isError is True


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


class TestWriteTool:
    """Test the write MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_creates_document(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write", {"path": "new_note.md", "content": "# New\n\nBody.\n"}
            )
        data = result.data
        assert isinstance(data, dict)
        assert data["path"] == "new_note.md"
        assert data["created"] is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_overwrites_existing(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write", {"path": "simple.md", "content": "# Replaced\n"}
            )
        data = result.data
        assert data["created"] is False

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_with_frontmatter(self) -> None:
        """write tool with frontmatter parameter creates document and returns created=True."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write",
                {
                    "path": "fm_note.md",
                    "content": "# Frontmatter Note\n\nBody.\n",
                    "frontmatter": {"title": "Frontmatter Note", "tags": ["x", "y"]},
                },
            )
        data = result.data
        assert isinstance(data, dict)
        assert data["created"] is True
        assert data["path"] == "fm_note.md"


class TestEditTool:
    """Test the edit MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_patches_document(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "edit",
                {
                    "path": "simple.md",
                    "old_text": "Simple Document",
                    "new_text": "Updated Document",
                },
            )
        data = result.data
        assert data["path"] == "simple.md"
        assert data["replacements"] == 1

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_nonexistent_returns_error(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "edit",
                {"path": "nonexistent.md", "old_text": "a", "new_text": "b"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_conflict_returns_error(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "edit",
                {"path": "simple.md", "old_text": "missing text", "new_text": "b"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_line_range(self) -> None:
        """MCP edit tool accepts line_start/line_end."""
        server = make_server()
        async with Client(server) as client:
            await client.call_tool(
                "write",
                {"path": "lines.md", "content": "line1\nline2\nline3\n"},
            )
            result = await client.call_tool(
                "edit",
                {
                    "path": "lines.md",
                    "new_text": "replaced\n",
                    "line_start": 2,
                    "line_end": 2,
                },
            )
        data = result.data
        assert data["path"] == "lines.md"
        assert data["replacements"] == 1

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_normalized_match(self) -> None:
        """MCP edit response includes match_type."""
        server = make_server()
        async with Client(server) as client:
            await client.call_tool(
                "write",
                {"path": "norm.md", "content": "hello \u2014 world\n"},
            )
            result = await client.call_tool(
                "edit",
                {
                    "path": "norm.md",
                    "old_text": "hello - world",
                    "new_text": "goodbye",
                },
            )
        data = result.data
        assert data["match_type"] == "normalized"

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_diagnostic_error(self) -> None:
        """MCP edit error includes diagnostic info."""
        server = make_server()
        async with Client(server) as client:
            await client.call_tool(
                "write",
                {"path": "diag.md", "content": "the quick brown fox\n"},
            )
            result = await client.call_tool_mcp(
                "edit",
                {
                    "path": "diag.md",
                    "old_text": "the quick brown fax",
                    "new_text": "x",
                },
            )
        assert result.isError is True
        error_text = cast("mcp_types.TextContent", result.content[0]).text
        assert "closest_match_line" in error_text


class TestDeleteTool:
    """Test the delete MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_delete_removes_document(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("delete", {"path": "simple.md"})
        data = result.data
        assert data["path"] == "simple.md"

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_delete_nonexistent_returns_error(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp("delete", {"path": "nonexistent.md"})
        assert result.isError is True


class TestRenameTool:
    """Test the rename MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_moves_document(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "rename", {"old_path": "simple.md", "new_path": "renamed.md"}
            )
        data = result.data
        assert data["old_path"] == "simple.md"
        assert data["new_path"] == "renamed.md"

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_nonexistent_returns_error(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "rename",
                {"old_path": "nonexistent.md", "new_path": "target.md"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_target_exists_returns_error(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "rename",
                {"old_path": "simple.md", "new_path": "no_frontmatter.md"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_to_same_path_returns_error(self) -> None:
        """rename to same old_path and new_path should return an error."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "rename",
                {"old_path": "simple.md", "new_path": "simple.md"},
            )
        assert result.isError is True


# ---------------------------------------------------------------------------
# Exclude patterns
# ---------------------------------------------------------------------------


class TestMCPExcludePatterns:
    """Test that MARKDOWN_VAULT_MCP_EXCLUDE env var is respected by the MCP server."""

    async def test_exclude_patterns_hides_subfolder_docs(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """list_documents does not return docs matching MARKDOWN_VAULT_MCP_EXCLUDE."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EXCLUDE", "subfolder/**")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            if var != "MARKDOWN_VAULT_MCP_EXCLUDE":
                monkeypatch.delenv(var, raising=False)

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("list_documents", {})

        data = _parse_tool_data(result)
        assert isinstance(data, list)
        paths = [d["path"] for d in data]

        # Root-level docs should be present.
        assert "simple.md" in paths
        # Subfolder docs should be excluded.
        assert not any(p.startswith("subfolder/") for p in paths)


# ---------------------------------------------------------------------------
# OIDC auth configuration
# ---------------------------------------------------------------------------

_OIDC_VARS = (
    "MARKDOWN_VAULT_MCP_AUTH_MODE",
    "MARKDOWN_VAULT_MCP_BASE_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
    "MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN",
)

_OIDC_REQUIRED = {
    "MARKDOWN_VAULT_MCP_BASE_URL": "https://mcp.example.com",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL": "https://auth.example.com/.well-known/openid-configuration",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID": "test-client",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET": "test-secret",
}


# ---------------------------------------------------------------------------
# MCP attachment tool tests
# ---------------------------------------------------------------------------


@pytest.fixture
def _mcp_env_writable_with_attachments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Writable vault with a PDF and PNG attachment pre-created."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nSome content.\n", encoding="utf-8")
    (vault / "assets").mkdir()
    (vault / "assets" / "report.pdf").write_bytes(b"%PDF-1.4 fake content")
    (vault / "assets" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)

    return vault


class TestMCPReadAttachment:
    """MCP read() tool dispatches to attachment path for non-.md files."""

    async def test_read_attachment_returns_base64_content(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        import base64

        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "assets/report.pdf"})
        data = result.data
        assert data["path"] == "assets/report.pdf"
        assert data["mime_type"] == "application/pdf"
        assert "size_bytes" in data
        assert "content_base64" in data
        decoded = base64.b64decode(data["content_base64"])
        assert decoded == b"%PDF-1.4 fake content"

    async def test_read_attachment_returns_mime_type(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "assets/image.png"})
        assert result.data["mime_type"] == "image/png"

    async def test_read_attachment_missing_raises(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("read", {"path": "assets/missing.pdf"})


class TestMCPWriteAttachment:
    """MCP write() tool dispatches to attachment path for non-.md files."""

    async def test_write_attachment_creates_file(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        import base64

        raw = b"new pdf binary content"
        b64 = base64.b64encode(raw).decode("ascii")
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write",
                {"path": "assets/new.pdf", "content_base64": b64},
            )
        data = result.data
        assert data["path"] == "assets/new.pdf"
        assert data["created"] is True
        assert (
            _mcp_env_writable_with_attachments / "assets" / "new.pdf"
        ).read_bytes() == raw

    async def test_write_attachment_missing_base64_raises(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("write", {"path": "assets/new.pdf"})

    async def test_write_attachment_invalid_base64_raises(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "write",
                    {"path": "assets/new.pdf", "content_base64": "!!!invalid!!!"},
                )


class TestFetchTool:
    """Test the fetch MCP tool — downloads from URL and writes to vault."""

    @staticmethod
    def _mock_httpx_stream(raw: bytes, headers: dict[str, str]) -> Any:
        """Build a mock httpx.AsyncClient that streams *raw* bytes."""
        from unittest.mock import AsyncMock, MagicMock

        async def mock_aiter_bytes(chunk_size: int = 65536):  # noqa: ARG001
            yield raw

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = headers
        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    async def test_fetch_markdown_note(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """fetch with .md path decodes UTF-8 and writes a note."""
        from unittest.mock import patch

        import httpx

        body = b"# Fetched\n\nContent from remote.\n"
        mock_client = self._mock_httpx_stream(
            body, {"content-type": "text/markdown; charset=utf-8"}
        )

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            server = make_server()
            async with Client(server) as client:
                result = await client.call_tool(
                    "fetch",
                    {"url": "https://example.com/note.md", "path": "fetched.md"},
                )
        data = result.data
        assert data["path"] == "fetched.md"
        assert data["created"] is True
        assert data["content_length"] == len(body)
        assert "text/markdown" in data["content_type"]
        # Verify file written to disk.
        written = (_mcp_env_writable_with_attachments / "fetched.md").read_text(
            encoding="utf-8"
        )
        assert "# Fetched" in written

    async def test_fetch_attachment(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """fetch with non-.md path writes binary attachment."""
        from unittest.mock import patch

        import httpx

        raw = b"\x89PNG\r\n\x1a\nfake-image-data"
        mock_client = self._mock_httpx_stream(raw, {"content-type": "image/png"})

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            server = make_server()
            async with Client(server) as client:
                result = await client.call_tool(
                    "fetch",
                    {
                        "url": "https://example.com/image.png",
                        "path": "assets/fetched.png",
                    },
                )
        data = result.data
        assert data["path"] == "assets/fetched.png"
        assert data["created"] is True
        assert data["content_length"] == len(raw)
        assert data["content_type"] == "image/png"
        # Verify binary written to disk.
        written = (
            _mcp_env_writable_with_attachments / "assets" / "fetched.png"
        ).read_bytes()
        assert written == raw

    async def test_fetch_rejects_file_scheme(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """file:// URLs are rejected (SSRF protection)."""
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="http and https"):
                await client.call_tool(
                    "fetch",
                    {"url": "file:///etc/passwd", "path": "stolen.md"},
                )

    async def test_fetch_rejects_ftp_scheme(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """ftp:// URLs are rejected."""
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="http and https"):
                await client.call_tool(
                    "fetch",
                    {"url": "ftp://example.com/file.txt", "path": "file.md"},
                )

    async def test_fetch_size_limit(
        self, _mcp_env_writable_with_attachments: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Downloads exceeding MAX_ATTACHMENT_SIZE_MB are rejected during streaming."""
        from unittest.mock import patch

        import httpx

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "0.001")

        # ~2 KB payload > 0.001 MB (~1 KB)
        mock_client = self._mock_httpx_stream(
            b"x" * 2048, {"content-type": "application/octet-stream"}
        )

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            server = make_server()
            async with Client(server) as client:
                with pytest.raises(ToolError, match="exceeded"):
                    await client.call_tool(
                        "fetch",
                        {
                            "url": "https://example.com/big.pdf",
                            "path": "assets/big.pdf",
                        },
                    )

    async def test_fetch_httpx_not_installed(
        self, _mcp_env_writable_with_attachments: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Graceful error when httpx is not available."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "httpx":
                raise ImportError("No module named 'httpx'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="httpx"):
                await client.call_tool(
                    "fetch",
                    {"url": "https://example.com/note.md", "path": "note.md"},
                )

    async def test_fetch_frontmatter_applied(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """frontmatter dict is applied when writing .md files."""
        from unittest.mock import patch

        import httpx

        body = b"# Report\n\nGenerated content.\n"
        mock_client = self._mock_httpx_stream(body, {"content-type": "text/markdown"})

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            server = make_server()
            async with Client(server) as client:
                result = await client.call_tool(
                    "fetch",
                    {
                        "url": "https://example.com/report.md",
                        "path": "report.md",
                        "frontmatter": {
                            "title": "Report",
                            "source": "https://example.com/report.md",
                        },
                    },
                )
        data = result.data
        assert data["created"] is True
        # Verify frontmatter was written.
        written = (_mcp_env_writable_with_attachments / "report.md").read_text(
            encoding="utf-8"
        )
        assert "title: Report" in written
        assert "source: https://example.com/report.md" in written

    async def test_fetch_http_error_status(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """Non-2xx HTTP response surfaces as ToolError."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Not Found",
                request=httpx.Request("GET", "https://example.com/missing.md"),
                response=httpx.Response(404),
            )
        )
        mock_response.headers = {"content-type": "text/html"}

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client_instance = AsyncMock()
        mock_client_instance.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch.object(httpx, "AsyncClient", return_value=mock_client_instance):
            server = make_server()
            async with Client(server) as client:
                with pytest.raises(ToolError, match="Not Found"):
                    await client.call_tool(
                        "fetch",
                        {
                            "url": "https://example.com/missing.md",
                            "path": "missing.md",
                        },
                    )

    async def test_fetch_rejects_private_ip(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """Private/loopback IPs are rejected (SSRF protection)."""
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="private"):
                await client.call_tool(
                    "fetch",
                    {"url": "http://127.0.0.1/secret", "path": "stolen.md"},
                )

    async def test_fetch_rejects_metadata_endpoint(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """Cloud metadata endpoint IPs are rejected."""
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="private"):
                await client.call_tool(
                    "fetch",
                    {
                        "url": "http://169.254.169.254/latest/meta-data/",
                        "path": "meta.md",
                    },
                )

    async def test_fetch_rejects_unspecified_ip(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """0.0.0.0 is rejected (routes to localhost on most systems)."""
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="private"):
                await client.call_tool(
                    "fetch",
                    {"url": "http://0.0.0.0/admin", "path": "stolen.md"},
                )

    async def test_fetch_unicode_decode_error(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """Binary content with .md path gives clear UTF-8 error."""
        from unittest.mock import patch

        import httpx

        raw = b"\x89PNG\r\n\x1a\n\xff\xfe"
        mock_client = self._mock_httpx_stream(raw, {"content-type": "image/png"})

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            server = make_server()
            async with Client(server) as client:
                with pytest.raises(ToolError, match="not valid UTF-8"):
                    await client.call_tool(
                        "fetch",
                        {
                            "url": "https://example.com/binary.md",
                            "path": "binary.md",
                        },
                    )

    @pytest.mark.usefixtures("_mcp_env")
    async def test_fetch_hidden_in_read_only_mode(self) -> None:
        """fetch tool should not appear when server is read-only."""
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        assert "fetch" not in tool_names

    async def test_fetch_timeout(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """httpx timeout surfaces as a ToolError."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import httpx

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            server = make_server()
            async with Client(server) as client:
                with pytest.raises(ToolError, match="timed out"):
                    await client.call_tool(
                        "fetch",
                        {
                            "url": "https://example.com/slow.md",
                            "path": "slow.md",
                        },
                    )

    async def test_fetch_rejects_localhost_hostname(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        """Hostname blocklist rejects 'localhost'."""
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="private"):
                await client.call_tool(
                    "fetch",
                    {"url": "http://localhost/secret", "path": "stolen.md"},
                )


class TestMCPListDocumentsAttachments:
    """MCP list_documents() with include_attachments flag."""

    async def test_list_documents_default_excludes_attachments(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("list_documents", {})
        items = _parse_tool_data(result)
        paths = [item["path"] for item in items]
        assert not any(p.endswith(".pdf") or p.endswith(".png") for p in paths)

    async def test_list_documents_include_attachments_returns_both(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "list_documents", {"include_attachments": True}
            )
        items = _parse_tool_data(result)
        kinds = {item.get("kind") for item in items}
        # All entries must carry a kind field
        assert "note" in kinds
        assert "attachment" in kinds
        paths = [item["path"] for item in items]
        assert any(p.endswith(".pdf") for p in paths)
        assert any(p.endswith(".png") for p in paths)
        assert "note.md" in paths

    async def test_list_documents_attachments_have_mime_type(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "list_documents", {"include_attachments": True}
            )
        items = _parse_tool_data(result)
        pdf_items = [i for i in items if i.get("path", "").endswith(".pdf")]
        assert len(pdf_items) >= 1
        assert pdf_items[0].get("mime_type") == "application/pdf"
        assert pdf_items[0].get("kind") == "attachment"


class TestMCPStatsAttachmentExtensions:
    """MCP stats() includes attachment_extensions field."""

    async def test_stats_includes_attachment_extensions(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("stats", {})
        data = result.data
        assert "attachment_extensions" in data
        assert isinstance(data["attachment_extensions"], list)
        assert "pdf" in data["attachment_extensions"]


# ---------------------------------------------------------------------------
# Link tools
# ---------------------------------------------------------------------------


class TestLinkTools:
    """Integration tests for get_backlinks, get_outlinks, get_broken_links."""

    @pytest.fixture
    def _mcp_env_linked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Create a vault with interlinked notes for link tool tests."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "index.md").write_text(
            "# Index\n\nSee [Topic](notes/topic.md) and [Ghost](ghost.md).\n",
            encoding="utf-8",
        )
        (vault / "notes").mkdir()
        (vault / "notes" / "topic.md").write_text(
            "# Topic\n\nBack to [Index](../index.md).\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_backlinks(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_backlinks", {"path": "notes/topic.md"})
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert len(data) == 1
        assert data[0]["source_path"] == "index.md"
        assert data[0]["link_text"] == "Topic"

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_backlinks_with_wait_for_drain(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_backlinks",
                {"path": "notes/topic.md", "wait_for_drain": True},
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        assert len(envelope["data"]) == 1

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_backlinks_nonexistent_raises(self) -> None:
        server = make_server()
        async with Client(server) as client:
            with pytest.raises((ToolError, McpError)):
                await client.call_tool("get_backlinks", {"path": "nope.md"})

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_outlinks(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_outlinks", {"path": "index.md"})
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert len(data) == 2
        targets = {d["target_path"] for d in data}
        assert "notes/topic.md" in targets
        assert "ghost.md" in targets
        # notes/topic.md exists, ghost.md does not
        by_target = {d["target_path"]: d for d in data}
        assert by_target["notes/topic.md"]["exists"] is True
        assert by_target["ghost.md"]["exists"] is False

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_outlinks_with_wait_for_drain(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_outlinks",
                {"path": "index.md", "wait_for_drain": True},
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        assert len(envelope["data"]) == 2

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_outlinks_nonexistent_path(self) -> None:
        server = make_server()
        async with Client(server) as client:
            with pytest.raises((ToolError, McpError)):
                await client.call_tool("get_outlinks", {"path": "nope.md"})

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_broken_links(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_broken_links", {})
        data = _parse_tool_data(result)
        assert len(data) == 1
        assert data[0]["target_path"] == "ghost.md"
        assert data[0]["source_path"] == "index.md"

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_broken_links_with_folder_filter(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_broken_links", {"folder": "notes"})
        data = _parse_tool_data(result)
        # notes/topic.md links to ../index.md which exists — no broken links
        assert data == []

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_link_tools_available_in_readonly(self) -> None:
        """Link tools are read-only and available even when vault is read-only."""
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
        assert "get_backlinks" in names
        assert "get_outlinks" in names
        assert "get_broken_links" in names


class TestSimilarTool:
    """Integration tests for get_similar tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_get_similar_no_embeddings_returns_empty(self) -> None:
        """get_similar returns empty list when embeddings not configured."""
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_similar", {"path": "simple.md"})
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert data == []

    @pytest.mark.usefixtures("_mcp_env")
    async def test_get_similar_nonexistent_raises(self) -> None:
        server = make_server()
        async with Client(server) as client:
            with pytest.raises((ToolError, McpError)):
                await client.call_tool("get_similar", {"path": "nonexistent.md"})

    @pytest.mark.usefixtures("_mcp_env")
    async def test_get_similar_tool_accepts_chunks_per_file(self) -> None:
        """The `get_similar` MCP tool surfaces the chunks_per_file kwarg."""
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            # Without embeddings this returns []; the assertion is that the
            # call_tool schema accepts the chunks_per_file kwarg without raising.
            result = await client.call_tool(
                "get_similar",
                {"path": "simple.md", "limit": 5, "chunks_per_file": 1},
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert isinstance(data, list)

    @pytest.mark.usefixtures("_mcp_env")
    async def test_get_similar_with_wait_for_drain(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_similar",
                {"path": "simple.md", "wait_for_drain": True},
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        assert isinstance(envelope["data"], list)


class TestRecentTool:
    """Integration tests for get_recent tool and recent://vault resource."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_get_recent_returns_notes(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("get_recent", {"limit": 5})
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) <= 5
        assert all("path" in d for d in data)
        assert all("modified_at" in d for d in data)
        # Verify ordering: most recent first
        mtimes = [d["modified_at"] for d in data]
        assert mtimes == sorted(mtimes, reverse=True)

    @pytest.mark.usefixtures("_mcp_env")
    async def test_get_recent_empty_folder(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "get_recent", {"folder": "nonexistent_folder"}
            )
        data = _parse_tool_data(result)
        assert data == []

    @pytest.mark.usefixtures("_mcp_env")
    async def test_recent_resource(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.read_resource("recent://vault")
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert len(data) <= 20
        if data:
            assert "modified_at_iso" in data[0]


# ---------------------------------------------------------------------------
# Context dossier tool
# ---------------------------------------------------------------------------


class TestContextTool:
    """Integration tests for get_context tool."""

    @pytest.fixture
    def _mcp_env_context(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Vault with interlinked notes and a folder peer for context tests."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "index.md").write_text(
            "---\ntags: [ai, research]\n---\n# Index\n\nSee [Topic](notes/topic.md).\n",
            encoding="utf-8",
        )
        (vault / "notes").mkdir()
        (vault / "notes" / "topic.md").write_text(
            "# Topic\n\nBack to [Index](../index.md).\n",
            encoding="utf-8",
        )
        (vault / "notes" / "peer.md").write_text(
            "# Peer\n\nA sibling note.\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        # Set after clearing so it is not wiped by _CLEAR_VARS.
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "tags")

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_basic_fields(self) -> None:
        """get_context returns expected top-level fields."""
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_context", {"path": "index.md"})
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert data["path"] == "index.md"
        assert data["title"] == "Index"
        assert "modified_at" in data
        assert isinstance(data["modified_at"], float)
        assert isinstance(data["backlinks"], list)
        assert isinstance(data["outlinks"], list)
        assert isinstance(data["similar"], list)
        assert isinstance(data["folder_notes"], list)
        assert isinstance(data["tags"], dict)

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_modified_at_matches_read(self) -> None:
        """modified_at in context matches the value from read()."""
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            envelope = _parse_tool_data(
                await client.call_tool("get_context", {"path": "index.md"})
            )
            read = (await client.call_tool("read", {"path": "index.md"})).data
        assert envelope["stale"] is False
        ctx = envelope["data"]
        assert ctx["modified_at"] == read["modified_at"]

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_backlinks(self) -> None:
        """notes/topic.md has a backlink from index.md."""
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_context", {"path": "notes/topic.md"})
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        sources = [b["source_path"] for b in data["backlinks"]]
        assert "index.md" in sources

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_outlinks(self) -> None:
        """index.md has an outlink to notes/topic.md."""
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_context", {"path": "index.md"})
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        targets = [o["target_path"] for o in data["outlinks"]]
        assert "notes/topic.md" in targets

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_folder_notes_excludes_self(self) -> None:
        """folder_notes for notes/topic.md contains peer.md but not topic.md."""
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_context", {"path": "notes/topic.md"})
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert "notes/topic.md" not in data["folder_notes"]
        assert "notes/peer.md" in data["folder_notes"]

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_tags(self) -> None:
        """Indexed frontmatter tags appear in context.tags."""
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_context", {"path": "index.md"})
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert "tags" in data["tags"]
        assert "ai" in data["tags"]["tags"]
        assert "research" in data["tags"]["tags"]

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_similar_empty_without_embeddings(self) -> None:
        """similar is empty when embeddings are not configured."""
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_context", {"path": "index.md"})
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert data["similar"] == []

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_nonexistent_raises(self) -> None:
        """get_context raises for a path not in the index."""
        server = make_server()
        async with Client(server) as client:
            with pytest.raises((ToolError, McpError)):
                await client.call_tool("get_context", {"path": "nope.md"})

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_in_tool_list(self) -> None:
        """get_context appears in the server tool list."""
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "get_context" in names

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_with_wait_for_drain(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_context",
                {"path": "index.md", "wait_for_drain": True},
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        assert envelope["data"]["path"] == "index.md"


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class TestResources:
    """Verify MCP resources return valid JSON with expected shapes."""

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_config_resource(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.read_resource("config://vault")
        data = json.loads(result[0].text)
        assert "source_dir" in data
        assert isinstance(data["read_only"], bool)
        assert isinstance(data["indexed_fields"], list)
        assert isinstance(data["required_fields"], list)
        assert isinstance(data["exclude_patterns"], list)
        assert isinstance(data["templates_folder"], str)
        assert isinstance(data["semantic_search_available"], bool)
        assert isinstance(data["attachment_extensions"], list)

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_stats_resource(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            resource_result = await client.read_resource("stats://vault")
            tool_result = await client.call_tool("stats", {})
        resource_data = json.loads(resource_result[0].text)
        tool_data = tool_result.data
        assert resource_data["document_count"] == tool_data["document_count"]
        assert resource_data["chunk_count"] == tool_data["chunk_count"]

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_tags_resource_grouped(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.read_resource("tags://vault")
        data = json.loads(result[0].text)
        # With indexed fields "cluster,tags", both keys should be present.
        assert isinstance(data, dict)
        assert "cluster" in data
        assert "tags" in data

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_tags_resource_by_field(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.read_resource("tags://vault/cluster")
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        # full_frontmatter.md has cluster: fiction
        assert "fiction" in data

    @pytest.mark.usefixtures("_mcp_env")
    async def test_folders_resource(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.read_resource("folders://vault")
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert "" in data  # root folder
        assert "subfolder" in data

    @pytest.mark.usefixtures("_mcp_env")
    async def test_toc_resource(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.read_resource("toc://vault/simple.md")
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert len(data) >= 1
        # First entry is always the synthetic H1 for the document title.
        assert data[0]["level"] == 1
        assert "heading" in data[0]
        assert data[0]["heading"] == "Simple Document"

    @pytest.mark.usefixtures("_mcp_env")
    async def test_toc_resource_missing_path(self) -> None:
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(McpError, match="Document not found"):
                await client.read_resource("toc://vault/does_not_exist.md")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


class TestPrompts:
    """Verify MCP prompt templates return expected text."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_summarize_prompt(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("summarize", {"path": "simple.md"})
        text = result.messages[0].content.text
        assert "simple.md" in text
        assert "`read`" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_research_prompt(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("research", {"topic": "horror fiction"})
        text = result.messages[0].content.text
        assert "horror fiction" in text
        assert "`search`" in text
        assert "`write`" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_discuss_prompt(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("discuss", {"path": "simple.md"})
        text = result.messages[0].content.text
        assert "simple.md" in text
        assert "`edit`" in text
        assert "Do not use `write`" in text

    @pytest.mark.usefixtures("_mcp_env")
    async def test_related_prompt(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("related", {"path": "simple.md"})
        text = result.messages[0].content.text
        assert "simple.md" in text
        assert "`search`" in text
        assert "read-only" in text

    @pytest.mark.usefixtures("_mcp_env")
    async def test_compare_prompt(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt(
                "compare", {"path1": "simple.md", "path2": "no_frontmatter.md"}
            )
        text = result.messages[0].content.text
        assert "simple.md" in text
        assert "no_frontmatter.md" in text
        assert "`read`" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_create_from_template_prompt_with_name(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt(
                "create_from_template", {"template_name": "meeting.md"}
            )
        text = result.messages[0].content.text
        assert "create_from_template" not in text  # prompt body, not function name
        assert "_templates" in text
        assert "meeting.md" in text
        assert "`read`" in text
        assert "`write`" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_create_from_template_prompt_sanitizes_template_name(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt(
                "create_from_template",
                {"template_name": "/../notes/meeting.md"},
            )
        text = result.messages[0].content.text
        assert "read(path='_templates/notes/meeting.md')" in text
        assert "read(path='/../notes/meeting.md')" not in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_create_from_template_prompt_resolves_dotdot_in_template_name(
        self,
    ) -> None:
        """.. segments collapse into parent rather than being dropped as isolated parts."""
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt(
                "create_from_template",
                {"template_name": "team/../daily.md"},
            )
        text = result.messages[0].content.text
        # team/.. resolves to nothing, leaving just daily.md under templates_folder
        assert "read(path='_templates/daily.md')" in text
        assert "read(path='_templates/team/daily.md')" not in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_create_from_template_prompt_normalizes_windows_separators(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates\\Notes\\")
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt(
                "create_from_template",
                {"template_name": "daily\\standup.md"},
            )
        text = result.messages[0].content.text
        assert "`Templates/Notes`" in text
        assert "read(path='Templates/Notes/daily/standup.md')" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_create_from_template_prompt_discovery_mode(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("create_from_template", {})
        text = result.messages[0].content.text
        assert "list_documents" in text
        assert "discover -> read -> fill -> write" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_create_from_template_prompt_normalizes_templates_folder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates/")
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("create_from_template", {})
        text = result.messages[0].content.text
        assert "Templates/" not in text
        assert "`Templates`" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_create_from_template_prompt_registration_schema(self) -> None:
        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()

        prompt = next((p for p in prompts if p.name == "create_from_template"), None)
        assert prompt is not None
        assert prompt.meta is not None
        assert prompt.meta.get("fastmcp", {}).get("tags") == ["write"]

        arg = next((a for a in prompt.arguments if a.name == "template_name"), None)
        assert arg is not None
        assert arg.required is False


# ---------------------------------------------------------------------------
# Prompt visibility
# ---------------------------------------------------------------------------


class TestPromptVisibility:
    """Verify write-tagged prompts are hidden in read-only mode."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_write_prompts_hidden_when_readonly(self) -> None:
        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "research" not in names
        assert "discuss" not in names
        assert "create_from_template" not in names

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_prompts_visible_when_writable(self) -> None:
        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "summarize" in names
        assert "research" in names
        assert "discuss" in names
        assert "create_from_template" in names
        assert "related" in names
        assert "compare" in names

    @pytest.mark.usefixtures("_mcp_env")
    async def test_readonly_prompts_always_visible(self) -> None:
        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "summarize" in names
        assert "related" in names
        assert "compare" in names


# ---------------------------------------------------------------------------
# Prompt/resource icons
# ---------------------------------------------------------------------------


class TestPromptAndResourceIcons:
    """Verify prompts and resources expose icon metadata."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_prompts_have_icons(self) -> None:
        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()

        for prompt in prompts:
            assert prompt.icons is not None
            assert len(prompt.icons) > 0
            assert prompt.icons[0].mimeType == "image/svg+xml"

    @pytest.mark.usefixtures("_mcp_env")
    async def test_resources_have_icons(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resources = await client.list_resources()

        for resource in resources:
            assert resource.icons is not None
            assert len(resource.icons) > 0
            assert resource.icons[0].mimeType == "image/svg+xml"


# ---------------------------------------------------------------------------
# Optimistic concurrency — if_match on MCP tools
# ---------------------------------------------------------------------------


class TestIfMatchParameter:
    """MCP tools accept if_match and propagate it to Collection."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_accepts_if_match_when_correct(self, vault_path: Path) -> None:
        """write tool succeeds when if_match matches the current file etag."""
        from markdown_vault_mcp.hashing import compute_file_hash

        server = make_server()
        current_etag = compute_file_hash(vault_path / "simple.md")
        async with Client(server) as client:
            result = await client.call_tool(
                "write",
                {
                    "path": "simple.md",
                    "content": "# Updated\n\nBody.\n",
                    "if_match": current_etag,
                },
            )
        data = result.data
        assert data["path"] == "simple.md"
        assert data["created"] is False

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_rejects_stale_if_match(self) -> None:
        """write tool returns an error when if_match does not match."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "write",
                {
                    "path": "simple.md",
                    "content": "# Bad write\n",
                    "if_match": "stale-etag-value",
                },
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_accepts_if_match_when_correct(self, vault_path: Path) -> None:
        """edit tool succeeds when if_match matches the current file etag."""
        from markdown_vault_mcp.hashing import compute_file_hash

        server = make_server()
        current_etag = compute_file_hash(vault_path / "simple.md")
        async with Client(server) as client:
            result = await client.call_tool(
                "edit",
                {
                    "path": "simple.md",
                    "old_text": "Simple Document",
                    "new_text": "Updated Document",
                    "if_match": current_etag,
                },
            )
        data = result.data
        assert data["replacements"] == 1

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_rejects_stale_if_match(self) -> None:
        """edit tool returns an error when if_match does not match."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "edit",
                {
                    "path": "simple.md",
                    "old_text": "Simple Document",
                    "new_text": "Updated Document",
                    "if_match": "stale-etag-value",
                },
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_delete_accepts_if_match_when_correct(self, vault_path: Path) -> None:
        """delete tool succeeds when if_match matches the current file etag."""
        from markdown_vault_mcp.hashing import compute_file_hash

        server = make_server()
        current_etag = compute_file_hash(vault_path / "simple.md")
        async with Client(server) as client:
            result = await client.call_tool(
                "delete",
                {"path": "simple.md", "if_match": current_etag},
            )
        data = result.data
        assert data["path"] == "simple.md"

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_delete_rejects_stale_if_match(self) -> None:
        """delete tool returns an error when if_match does not match."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "delete",
                {"path": "simple.md", "if_match": "stale-etag-value"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_accepts_if_match_when_correct(self, vault_path: Path) -> None:
        """rename tool succeeds when if_match matches the current file etag."""
        from markdown_vault_mcp.hashing import compute_file_hash

        server = make_server()
        current_etag = compute_file_hash(vault_path / "simple.md")
        async with Client(server) as client:
            result = await client.call_tool(
                "rename",
                {
                    "old_path": "simple.md",
                    "new_path": "renamed_simple.md",
                    "if_match": current_etag,
                },
            )
        data = result.data
        assert data["old_path"] == "simple.md"
        assert data["new_path"] == "renamed_simple.md"

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_rejects_stale_if_match(self) -> None:
        """rename tool returns an error when if_match does not match."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "rename",
                {
                    "old_path": "simple.md",
                    "new_path": "renamed_simple.md",
                    "if_match": "stale-etag-value",
                },
            )
        assert result.isError is True


# ---------------------------------------------------------------------------
# Lifespan: auto-build embeddings on startup
# ---------------------------------------------------------------------------


class TestLifespanAutoEmbeddings:
    """Verify that the lifespan auto-builds embeddings when configured."""

    async def test_embeddings_auto_built_on_startup(
        self,
        vault_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With EMBEDDINGS_PATH set, startup submits a BuildEmbeddings job
        that the writer drains in the background (#559)."""
        import asyncio as _asyncio
        from unittest.mock import patch

        from .conftest import MockEmbeddingProvider

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        embeddings_path = str(tmp_path / "embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", embeddings_path)

        mock_prov = MockEmbeddingProvider()
        # Patch at providers module — the lifespan uses a local import so the
        # patched attribute is resolved at call time.  If the import moves to
        # module level, patch "markdown_vault_mcp.server.get_embedding_provider" instead.
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov,
        ):
            server = make_server()
            async with Client(server) as client:
                # BuildEmbeddings is fire-and-forget on the writer FIFO;
                # poll until the writer drains and chunks are present.
                data: dict[str, Any] = {}
                for _ in range(50):
                    result = await client.call_tool_mcp("embeddings_status", {})
                    data = json.loads(result.content[0].text)
                    if data.get("chunk_count", 0) > 0:
                        break
                    await _asyncio.sleep(0.1)
        assert data["chunk_count"] > 0

    async def test_subsequent_startup_skips_rebuild(
        self,
        vault_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With existing embeddings on disk, startup loads them without rebuilding."""
        import asyncio as _asyncio
        from unittest.mock import patch

        from .conftest import MockEmbeddingProvider

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        embeddings_path = str(tmp_path / "embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", embeddings_path)

        mock_prov = MockEmbeddingProvider()
        count1 = 0
        # First startup: build embeddings from scratch (writer-async).
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov,
        ):
            server = make_server()
            async with Client(server) as client:
                for _ in range(50):
                    r1 = await client.call_tool_mcp("embeddings_status", {})
                    count1 = json.loads(r1.content[0].text)["chunk_count"]
                    if count1 > 0:
                        break
                    await _asyncio.sleep(0.1)
        assert count1 > 0

        # Second startup: should load from disk, not re-embed.
        mock_prov2 = MockEmbeddingProvider()
        embed_calls: list[int] = []
        original_embed = mock_prov2.embed

        def tracking_embed(texts: list[str]) -> list[list[float]]:
            embed_calls.append(len(texts))
            return original_embed(texts)

        mock_prov2.embed = tracking_embed  # type: ignore[method-assign]
        count2 = 0
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov2,
        ):
            server2 = make_server()
            async with Client(server2) as client2:
                for _ in range(50):
                    r2 = await client2.call_tool_mcp("embeddings_status", {})
                    count2 = json.loads(r2.content[0].text)["chunk_count"]
                    if count2 == count1:
                        break
                    await _asyncio.sleep(0.1)
        assert count2 == count1
        # embed() must NOT have been called — vectors were loaded from disk.
        assert embed_calls == [], f"embed() was called with {embed_calls} texts"

    async def test_no_embeddings_without_config(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without EMBEDDINGS_PATH, startup does not build embeddings."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)

        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp("stats", {})
        data = json.loads(result.content[0].text)
        assert data["semantic_search_available"] is False


# ---------------------------------------------------------------------------
# Bearer token auth configuration
# ---------------------------------------------------------------------------

_BEARER_VARS = ("MARKDOWN_VAULT_MCP_BEARER_TOKEN",)


class TestAuthModeSelection:
    """Tests for auth mode selection in make_server().

    Exercises the real make_server() assembly for the modes MV is
    responsible for wiring: multi, bearer-only, oidc-proxy, and none, plus
    the OIDC security defaults (blank scopes -> ["openid"], verify_id_token).
    Remote-mode discovery and RemoteAuthProvider assembly are pvl-core-owned
    (tested upstream); MV's only remote responsibility — the OIDC env vars
    reaching config.server — is covered by TestServerConfigComposition.
    """

    @pytest.fixture(autouse=True)
    def _clear_all_auth_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (*_BEARER_VARS, *_OIDC_VARS):
            monkeypatch.delenv(var, raising=False)

    def test_multi_auth_when_both_configured(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When both bearer and OIDC are configured, MultiAuth is used."""
        from unittest.mock import MagicMock, patch

        from fastmcp.server.auth import MultiAuth

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BEARER_TOKEN", "my-token")
        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            caplog.at_level(logging.INFO),
        ):
            server = make_server()

        assert isinstance(server.auth, MultiAuth)
        assert "using bearer token auth" not in caplog.text
        assert "Auth enabled: mode=multi" in caplog.text

    def test_multi_auth_contains_both_verifiers(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MultiAuth instance includes one StaticTokenVerifier and one OIDCProxy."""
        from unittest.mock import MagicMock, patch

        from fastmcp.server.auth import MultiAuth, StaticTokenVerifier

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BEARER_TOKEN", "my-token")
        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_oidc = MagicMock()
        mock_cls = MagicMock(return_value=mock_oidc)
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            server = make_server()

        assert isinstance(server.auth, MultiAuth)
        # OIDCProxy is an OAuthProvider — must be server=, not in verifiers=,
        # so that MultiAuth.get_routes() delegates OAuth endpoints to it.
        assert server.auth.server is mock_oidc
        verifiers = server.auth.verifiers
        assert len(verifiers) == 1
        assert isinstance(verifiers[0], StaticTokenVerifier)

    def test_multi_auth_required_scopes_empty(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MultiAuth must have empty required_scopes.

        OIDC sets required_scopes=["openid"] on itself.  If MultiAuth
        inherits that, the HTTP middleware rejects bearer tokens that
        lack "openid" — effectively requiring OIDC for *every* request
        and breaking the "either" contract.  Overriding to [] lets each
        verifier enforce its own scope requirements independently.
        """
        from unittest.mock import MagicMock, patch

        from fastmcp.server.auth import MultiAuth

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BEARER_TOKEN", "my-token")
        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_oidc = MagicMock()
        mock_oidc.required_scopes = ["openid"]
        mock_cls = MagicMock(return_value=mock_oidc)
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            server = make_server()

        assert isinstance(server.auth, MultiAuth)
        assert server.auth.required_scopes == []

    def test_falls_through_to_oidc_when_no_bearer(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without bearer token, OIDC is used if configured."""
        from unittest.mock import MagicMock, patch

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            server = make_server()

        # Server must use the OIDC mock, not bearer
        assert server.auth is not None
        assert server.auth is mock_cls.return_value

    def test_bearer_only_when_no_oidc(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only BEARER_TOKEN set (no OIDC) -> server.auth is a StaticTokenVerifier."""
        from fastmcp.server.auth import StaticTokenVerifier

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BEARER_TOKEN", "secret")

        server = make_server()

        assert isinstance(server.auth, StaticTokenVerifier)

    def test_oidc_blank_required_scopes_defaults_to_openid(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Blank OIDC_REQUIRED_SCOPES -> OIDCProxy enforces ["openid"].

        Security default: leaving scopes unset must still enforce openid
        (core maps the empty tuple to ["openid"]); the assembled server must
        not silently accept any-scope tokens.
        """
        from unittest.mock import MagicMock, patch

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES", "")

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            make_server()

        assert mock_cls.call_args.kwargs["required_scopes"] == ["openid"]

    def test_oidc_verify_id_token_follows_verify_access_token(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OIDCProxy verifies the id_token by default; verify_access_token flips it.

        Security default: unset OIDC_VERIFY_ACCESS_TOKEN must verify the
        id_token (verify_id_token=True); setting it true switches verification
        to the access token (verify_id_token=False).
        """
        from unittest.mock import MagicMock, patch

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            make_server()
        assert mock_cls.call_args.kwargs["verify_id_token"] is True

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN", "true")
        mock_cls.reset_mock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            make_server()
        assert mock_cls.call_args.kwargs["verify_id_token"] is False

    def test_no_auth_when_nothing_configured(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Without any auth vars, server runs unauthenticated and logs at WARNING."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))

        with caplog.at_level(logging.WARNING):
            server = make_server()

        assert server.auth is None
        assert "unauthenticated" in caplog.text
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("unauthenticated" in r.message for r in warning_records), (
            "No-auth message must be logged at WARNING level, not INFO"
        )

    def test_auth_mode_reports_none_when_build_auth_fails(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If build_auth returns None despite OIDC config, log ``mode=none``.

        Guards against the log drifting from reality when e.g. OIDC
        discovery fails: core's ``resolve_auth_mode`` would still report
        ``oidc-proxy`` from field presence, but the actual auth object
        is ``None`` so the startup summary must say ``none``.
        """
        from unittest.mock import patch

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        with (
            patch("markdown_vault_mcp.server.build_auth", return_value=None),
            caplog.at_level(logging.INFO),
        ):
            server = make_server()

        assert server.auth is None
        assert "unauthenticated" in caplog.text
        # Guard against the misleading "Auth enabled: mode=<flavor>" log
        # that the stale auth_mode could otherwise produce.
        assert "Auth enabled" not in caplog.text


class TestStartupSummaryLogging:
    """Tests for the make_server() startup-summary log line.

    The two remaining cases assert the INFO startup summary (which
    reports auth mode, version, and read-only state) and the version
    fallback when the package metadata is unavailable.
    """

    def test_startup_summary_logged(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        with caplog.at_level(logging.INFO):
            make_server()
        assert "Server config:" in caplog.text
        assert "version=" in caplog.text
        assert "auth=none" in caplog.text
        assert "read-only" in caplog.text

    def test_startup_version_fallback(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Version falls back to 'unknown' when package is not installed."""
        from importlib.metadata import PackageNotFoundError

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setattr(
            "markdown_vault_mcp.server._pkg_version",
            lambda _name: (_ for _ in ()).throw(PackageNotFoundError("test")),
        )
        with caplog.at_level(logging.INFO):
            make_server()
        assert "version=unknown" in caplog.text


# ---------------------------------------------------------------------------
# Middleware stack
# ---------------------------------------------------------------------------


class TestMiddlewareStack:
    """Verify that make_server() wires the expected middleware."""

    @pytest.mark.usefixtures("_mcp_env")
    def test_default_middleware_stack(self) -> None:
        """Server has ErrorHandling, Timing, and Logging middleware by default."""
        from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
        from fastmcp.server.middleware.logging import LoggingMiddleware
        from fastmcp.server.middleware.timing import TimingMiddleware

        server = make_server()
        types = [type(m) for m in server.middleware]
        assert ErrorHandlingMiddleware in types
        assert TimingMiddleware in types
        assert LoggingMiddleware in types

    @pytest.mark.usefixtures("_mcp_env")
    def test_structured_logging_when_rich_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FASTMCP_ENABLE_RICH_LOGGING=false selects StructuredLoggingMiddleware."""
        from fastmcp.server.middleware.logging import StructuredLoggingMiddleware

        monkeypatch.setenv("FASTMCP_ENABLE_RICH_LOGGING", "false")
        server = make_server()
        types = [type(m) for m in server.middleware]
        assert StructuredLoggingMiddleware in types

    @pytest.mark.usefixtures("_mcp_env")
    def test_middleware_order(self) -> None:
        """ErrorHandling is first, then Timing, then Logging."""
        from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
        from fastmcp.server.middleware.logging import LoggingMiddleware
        from fastmcp.server.middleware.timing import TimingMiddleware

        server = make_server()
        types = [type(m) for m in server.middleware]
        assert ErrorHandlingMiddleware in types, "ErrorHandlingMiddleware missing"
        assert TimingMiddleware in types, "TimingMiddleware missing"
        assert LoggingMiddleware in types, "LoggingMiddleware missing"
        eh_idx = types.index(ErrorHandlingMiddleware)
        tm_idx = types.index(TimingMiddleware)
        lg_idx = types.index(LoggingMiddleware)
        assert eh_idx < tm_idx < lg_idx


# ---------------------------------------------------------------------------
# Git history tools
# ---------------------------------------------------------------------------


@pytest.fixture
def git_vault(tmp_path: Path) -> Path:
    """A minimal git-backed vault with two commits touching alpha.md."""
    import subprocess

    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "-C", str(vault), "init"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(vault), "config", "user.email", "test@test.com"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(vault), "config", "user.name", "Test"],
        capture_output=True,
        check=True,
    )
    # First commit
    (vault / "alpha.md").write_text("# Alpha\n\nVersion 1.\n")
    subprocess.run(
        ["git", "-C", str(vault), "add", "."], capture_output=True, check=True
    )
    subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "write: alpha.md"],
        capture_output=True,
        check=True,
    )
    # Second commit
    (vault / "alpha.md").write_text("# Alpha\n\nVersion 2.\n")
    subprocess.run(
        ["git", "-C", str(vault), "add", "."], capture_output=True, check=True
    )
    subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "edit: alpha.md"],
        capture_output=True,
        check=True,
    )
    return vault


@pytest.fixture
def _mcp_env_git(git_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars pointing to a git-backed vault."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(git_vault))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


class TestGetHistoryTool:
    @pytest.fixture(autouse=True)
    def _setup(self, _mcp_env_git: None) -> None:
        pass

    async def test_vault_wide_history(self) -> None:
        """get_history with no path returns vault-wide commits."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("get_history", {})
        data = _parse_tool_data(result)
        assert isinstance(data, dict)
        entries = data["commits"]
        assert isinstance(entries, list)
        assert len(entries) == 2
        assert data["total"] == 2
        messages = [e["message"] for e in entries]
        assert "edit: alpha.md" in messages
        assert "write: alpha.md" in messages

    async def test_single_note_history(self) -> None:
        """get_history filtered by path returns commits for that note."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("get_history", {"path": "alpha.md"})
        data = _parse_tool_data(result)
        assert isinstance(data, dict)
        assert isinstance(data["commits"], list)
        assert len(data["commits"]) == 2
        assert data["total"] == 2

    async def test_history_entry_fields(self) -> None:
        """Each history entry has the expected fields."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("get_history", {"limit": 1})
        data = _parse_tool_data(result)
        entry = data["commits"][0]
        assert "sha" in entry and len(entry["sha"]) == 40
        assert "short_sha" in entry
        assert "timestamp" in entry
        assert "author" in entry
        assert "message" in entry
        assert "paths_changed" in entry

    async def test_limit_respected(self) -> None:
        """limit parameter restricts the number of results."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("get_history", {"limit": 1})
        data = _parse_tool_data(result)
        assert len(data["commits"]) == 1
        assert data["total"] == 1

    async def test_invalid_path_raises_tool_error(self) -> None:
        """A path that escapes the vault raises ToolError."""
        from fastmcp.exceptions import ToolError

        server = make_server()
        with pytest.raises((ToolError, Exception)):
            async with Client(server) as client:
                await client.call_tool("get_history", {"path": "../escape.md"})

    async def test_get_history_in_tool_list(self) -> None:
        """get_history appears in the tool list."""
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "get_history" in names

    async def test_get_diff_in_tool_list(self) -> None:
        """get_diff appears in the tool list."""
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "get_diff" in names

    async def test_get_history_envelope_wire_shape(self) -> None:
        """structured_content uses the `commits` envelope, not FastMCP's
        synthetic `result` wrap key."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("get_history", {})
        assert result.structured_content is not None
        assert "commits" in result.structured_content
        assert "total" in result.structured_content
        # The whole point of the refactor: payload is self-describing on
        # the wire — no opaque `result` key from auto-wrapping a list.
        assert "result" not in result.structured_content

    async def test_get_history_output_schema_not_auto_wrapped(self) -> None:
        """outputSchema must not carry FastMCP's `x-fastmcp-wrap-result`
        marker — it appears only when FastMCP auto-wraps a list/primitive
        return under a synthetic `result` key; the dict envelope skips it."""
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        gh = next(t for t in tools if t.name == "get_history")
        schema = gh.outputSchema
        assert schema is not None
        assert schema.get("type") == "object"
        assert "x-fastmcp-wrap-result" not in schema
        # And no synthetic `result` key in any declared properties.
        assert "result" not in schema.get("properties", {})


class TestGetDiffTool:
    @pytest.fixture(autouse=True)
    def _setup(self, _mcp_env_git: None) -> None:
        pass

    def _first_sha(self, git_vault: Path) -> str:
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(git_vault), "log", "--format=%H", "--reverse"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().splitlines()[0]

    async def test_single_diff_from_sha(self, git_vault: Path) -> None:
        """get_diff with since_sha returns a unified diff dict."""
        sha = self._first_sha(git_vault)
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "get_diff", {"path": "alpha.md", "since_sha": sha}
            )
        data = _parse_tool_data(result)
        assert isinstance(data, dict)
        assert "diff" in data
        assert "Version" in data["diff"] or data["diff"] == ""

    async def test_per_commit_diff(self, git_vault: Path) -> None:
        """get_diff with per_commit=True returns a {commits, total} envelope."""
        sha = self._first_sha(git_vault)
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "get_diff",
                {"path": "alpha.md", "since_sha": sha, "per_commit": True},
            )
        data = _parse_tool_data(result)
        assert isinstance(data, dict)
        assert isinstance(data["commits"], list)
        assert len(data["commits"]) == 1
        assert data["total"] == 1
        assert "sha" in data["commits"][0]
        assert "diff" in data["commits"][0]

    async def test_no_reference_raises_tool_error(self) -> None:
        """Calling get_diff without any reference raises ToolError."""
        from fastmcp.exceptions import ToolError

        server = make_server()
        with pytest.raises((ToolError, Exception)):
            async with Client(server) as client:
                await client.call_tool("get_diff", {"path": "alpha.md"})

    async def test_both_references_raises_tool_error(self, git_vault: Path) -> None:
        """Providing both since_sha and since_timestamp raises ToolError."""
        from fastmcp.exceptions import ToolError

        sha = self._first_sha(git_vault)
        server = make_server()
        with pytest.raises((ToolError, Exception)):
            async with Client(server) as client:
                await client.call_tool(
                    "get_diff",
                    {
                        "path": "alpha.md",
                        "since_sha": sha,
                        "since_timestamp": "2020-01-01T00:00:00",
                    },
                )

    async def test_invalid_sha_raises_tool_error(self) -> None:
        """An invalid SHA raises ToolError."""
        from fastmcp.exceptions import ToolError

        server = make_server()
        with pytest.raises((ToolError, Exception)):
            async with Client(server) as client:
                await client.call_tool(
                    "get_diff", {"path": "alpha.md", "since_sha": "not_valid!"}
                )

    async def test_per_commit_respects_limit(self, git_vault: Path) -> None:
        """get_diff tool honors the new `limit` kwarg in per_commit mode."""
        import subprocess

        # Add a third and fourth commit to alpha.md.
        for i in (3, 4):
            (git_vault / "alpha.md").write_text(f"# Alpha\n\nVersion {i}.\n")
            subprocess.run(
                ["git", "-C", str(git_vault), "add", "."],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(git_vault), "commit", "-m", f"edit v{i}"],
                capture_output=True,
                check=True,
            )
        sha = self._first_sha(git_vault)
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "get_diff",
                {
                    "path": "alpha.md",
                    "since_sha": sha,
                    "per_commit": True,
                    "limit": 2,
                },
            )
        data = _parse_tool_data(result)
        assert isinstance(data, dict)
        commits = data["commits"]
        assert len(commits) == 2
        assert data["total"] == 2
        # Newest-first ordering: the two most recent commit messages.
        assert [c["message"] for c in commits] == ["edit v4", "edit v3"]

    async def test_get_diff_envelope_wire_shape_per_commit_true(
        self, git_vault: Path
    ) -> None:
        """structured_content uses the `commits` envelope, not FastMCP's
        synthetic `result` wrap key."""
        sha = self._first_sha(git_vault)
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "get_diff",
                {"path": "alpha.md", "since_sha": sha, "per_commit": True},
            )
        assert result.structured_content is not None
        assert "commits" in result.structured_content
        assert "total" in result.structured_content
        # The whole point of the refactor: payload is self-describing on
        # the wire — no opaque `result` key from auto-wrapping a list.
        assert "result" not in result.structured_content

    async def test_get_diff_output_schema_not_auto_wrapped(self) -> None:
        """outputSchema must not carry FastMCP's `x-fastmcp-wrap-result`
        marker — it appears only when FastMCP auto-wraps a list/primitive
        return under a synthetic `result` key; the dict envelope skips it."""
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        gd = next(t for t in tools if t.name == "get_diff")
        schema = gd.outputSchema
        assert schema is not None
        assert schema.get("type") == "object"
        assert "x-fastmcp-wrap-result" not in schema
        # And no synthetic `result` key in any declared properties.
        assert "result" not in schema.get("properties", {})


async def test_search_tool_accepts_chunks_per_file_and_snippet_words(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `search` MCP tool surfaces chunks_per_file and snippet_words."""
    # Multi-line bodies so the docs split (clears the 30-line short-doc bypass).
    long_body = (
        "# Top\n"
        + "\n".join(["world a"] * 12)
        + "\n## A\n"
        + "\n".join(["world a"] * 12)
        + "\n## B\n"
        + "\n".join(["world b"] * 12)
        + "\n## C\n"
        + "\n".join(["world c"] * 12)
        + "\n"
    )
    (tmp_path / "long.md").write_text(long_body, encoding="utf-8")
    (tmp_path / "short.md").write_text("# S\nworld\n", encoding="utf-8")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)

    server = make_server()
    async with Client(server) as client:
        await wait_for_mcp_writer_drain(client)
        result = await client.call_tool(
            "search",
            {
                "query": "world",
                "mode": "keyword",
                "chunks_per_file": 1,
                "snippet_words": 5,
                "limit": 10,
            },
        )
    results = _parse_tool_data(result)
    paths = [r["path"] for r in results]
    assert len(set(paths)) == len(paths)
    # GroupedResult shape: each result holds sections[].content; with
    # chunks_per_file=1 each result has exactly one section.
    for r in results:
        assert len(r["sections"]) == 1
        assert len((r["sections"][0]["content"] or "").split()) <= 8


async def test_read_tool_returns_only_named_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `read` MCP tool accepts section= and returns only that chunk."""
    # 16 body lines per section: 2 heading + 16 + 2 heading + 16 + 1 = 37 lines
    # which is above the 30-line short-doc bypass so the doc splits into chunks.
    body = (
        "# A\n## One\n"
        + "\n".join(["first body"] * 16)
        + "\n## Two\n"
        + "\n".join(["second body"] * 16)
        + "\n"
    )
    (tmp_path / "a.md").write_text(body, encoding="utf-8")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)

    server = make_server()
    async with Client(server) as client:
        await wait_for_mcp_writer_drain(client)
        whole = await client.call_tool("read", {"path": "a.md"})
        assert "first body" in whole.data["content"]
        assert "second body" in whole.data["content"]

        partial = await client.call_tool("read", {"path": "a.md", "section": "One"})
        assert "first body" in partial.data["content"]
        assert "second body" not in partial.data["content"]

        with pytest.raises(Exception) as excinfo:
            await client.call_tool("read", {"path": "a.md", "section": "Nope"})
        assert "Nope" in str(excinfo.value)


class TestGitToolsUntilParam:
    """Tool-level tests for the `until` param on get_history (issue #340)."""

    @pytest.fixture(autouse=True)
    def _setup(self, _mcp_env_git: None) -> None:
        pass

    async def test_until_filter_passthrough(self) -> None:
        """The tool accepts `until` and scopes results accordingly."""
        # The existing fixture has two commits with real (recent) timestamps.
        # A far-future cutoff must include both; a far-past cutoff must exclude
        # both. This proves the `until` kwarg is plumbed through to git log.
        server = make_server()

        async with Client(server) as client:
            future = await client.call_tool(
                "get_history", {"until": "2099-01-01T00:00:00"}
            )
        future_data = _parse_tool_data(future)
        assert len(future_data["commits"]) == 2
        assert future_data["total"] == 2

        async with Client(server) as client:
            past = await client.call_tool(
                "get_history", {"until": "2000-01-01T00:00:00"}
            )
        past_data = _parse_tool_data(past)
        assert past_data["commits"] == []
        assert past_data["total"] == 0


class TestB3StaleSignal:
    """Tests for the writer-state staleness signal on B3 tools (#534)."""

    @pytest.fixture
    def _mcp_env_linked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Create a vault with interlinked notes for B3 tool stale-signal tests."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "index.md").write_text(
            "# Index\n\nSee [Topic](notes/topic.md) and [Ghost](ghost.md).\n",
            encoding="utf-8",
        )
        (vault / "notes").mkdir()
        (vault / "notes" / "topic.md").write_text(
            "# Topic\n\nBack to [Index](../index.md).\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_stale_true_when_writer_has_pending_dirty_paths(
        self,
    ) -> None:
        from markdown_vault_mcp._server_deps import get_collection_singleton

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            # Make the writer non-idle by marking a path dirty directly
            # on the writer (no submit, so no in-flight job, just a
            # non-empty dirty-paths set — that alone should set stale=True).
            col = get_collection_singleton()
            col._coordinator.writer.mark_dirty(["sentinel.md"])
            try:
                result = await client.call_tool(
                    "get_backlinks", {"path": "notes/topic.md"}
                )
            finally:
                # Clear the sentinel so subsequent tests are not affected
                # by leaked dirty-set state.
                col._coordinator.writer.drain_dirty_paths()
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is True
        # The data is still returned despite stale=True.
        assert isinstance(envelope["data"], list)

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_wait_for_drain_clears_stale_when_writer_idle(
        self,
    ) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_backlinks",
                {"path": "notes/topic.md", "wait_for_drain": True},
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_wait_for_drain_timeout_reports_stale_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp._server_deps import get_collection_singleton

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S", "0.05")
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            col = get_collection_singleton()
            col._coordinator.writer.mark_dirty(["sentinel.md"])
            try:
                result = await client.call_tool(
                    "get_backlinks",
                    {"path": "notes/topic.md", "wait_for_drain": True},
                )
            finally:
                col._coordinator.writer.drain_dirty_paths()
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is True
        assert isinstance(envelope["data"], list)

    @pytest.mark.usefixtures("_mcp_env_linked")
    @pytest.mark.parametrize(
        ("tool_name", "tool_args"),
        [
            ("get_backlinks", {"path": "notes/topic.md"}),
            ("get_outlinks", {"path": "notes/topic.md"}),
            ("get_similar", {"path": "notes/topic.md"}),
            ("get_context", {"path": "notes/topic.md"}),
            (
                "get_connection_path",
                {"source": "notes/topic.md", "target": "index.md"},
            ),
        ],
    )
    async def test_stale_true_uniform_across_b3_tools(
        self, tool_name: str, tool_args: dict[str, str]
    ) -> None:
        """Each B3 tool independently ORs stale; catch copy-paste regressions."""
        from markdown_vault_mcp._server_deps import get_collection_singleton

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            col = get_collection_singleton()
            col._coordinator.writer.mark_dirty(["sentinel.md"])
            try:
                result = await client.call_tool(tool_name, tool_args)
            finally:
                col._coordinator.writer.drain_dirty_paths()
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is True
        assert "data" in envelope
