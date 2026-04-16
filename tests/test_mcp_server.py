"""Integration tests for mcp_server.py using FastMCP test client.

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

from markdown_vault_mcp.config import (
    CollectionConfig,
    build_bearer_auth,
    build_oidc_auth,
    build_remote_auth,
    resolve_auth_mode,
)
from markdown_vault_mcp.mcp_server import create_server

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
    """Set minimal env vars for create_server (read_only=true default)."""
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
        server = create_server()
        assert server.name == "markdown-vault-mcp"
        assert "READ-ONLY" in server.instructions
        assert "not available" in server.instructions

    @pytest.mark.usefixtures("_mcp_env")
    def test_defaults_read_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        server = create_server()
        assert "READ-WRITE" in server.instructions
        assert "'write'" in server.instructions
        assert "'edit'" in server.instructions
        assert "'rename'" in server.instructions
        assert "'delete'" in server.instructions

    @pytest.mark.usefixtures("_mcp_env")
    def test_default_instructions_content(self) -> None:
        server = create_server()
        assert "relative" in server.instructions
        assert "'search'" in server.instructions
        assert "'stats'" in server.instructions
        assert "MARKDOWN_VAULT_MCP_INSTRUCTIONS" not in server.instructions

    @pytest.mark.usefixtures("_mcp_env")
    def test_custom_server_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "my-vault")
        server = create_server()
        assert server.name == "my-vault"

    @pytest.mark.usefixtures("_mcp_env")
    def test_custom_instructions_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
            "Personal notes vault. Read-only.",
        )
        server = create_server()
        assert server.instructions == "Personal notes vault. Read-only."


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------


class TestToolListing:
    """Verify correct tools are registered based on read_only setting."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_write_tools_absent_when_readonly(self) -> None:
        server = create_server()
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
        server = create_server()
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
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
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
        server = create_server()
        async with Client(server) as client:
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "simple.md"})
        data = result.data
        assert isinstance(data, dict)
        assert data["path"] == "simple.md"
        assert "Simple Document" in data["content"]

    @pytest.mark.usefixtures("_mcp_env")
    async def test_read_nonexistent(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp("read", {"path": "nonexistent.md"})
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env")
    async def test_read_with_frontmatter(self) -> None:
        server = create_server()
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

        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "_templates/meeting.md"})
        data = result.data
        assert data["path"] == "_templates/meeting.md"
        assert "Meeting Template" in data["content"]


class TestListDocumentsTool:
    """Test the list_documents MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_all(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_documents", {})
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) > 0
        paths = {d["path"] for d in data}
        assert "simple.md" in paths

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_by_folder(self) -> None:
        server = create_server()
        async with Client(server) as client:
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

        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_documents", {"folder": "_templates"})
        data = _parse_tool_data(result)
        paths = {doc["path"] for doc in data}
        assert "_templates/daily.md" in paths


class TestListFoldersTool:
    """Test the list_folders MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_folders(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_folders", {})
        folders = result.data
        assert isinstance(folders, list)
        assert "subfolder" in folders


class TestListTagsTool:
    """Test the list_tags MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_list_tags(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_tags", {"field": "cluster"})
        tags = result.data
        assert isinstance(tags, list)
        assert "fiction" in tags


class TestStatsTool:
    """Test the stats MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_stats(self) -> None:
        server = create_server()
        async with Client(server) as client:
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("embeddings_status", {})
        data = result.data
        assert isinstance(data, dict)
        assert data["provider"] is None


class TestReindexTool:
    """Test the reindex MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_reindex_no_changes(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("reindex", {})
        data = result.data
        assert isinstance(data, dict)
        assert data["added"] == 0
        assert data["modified"] == 0
        assert data["deleted"] == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test structured error responses for invalid operations."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_semantic_search_without_embeddings_returns_error(self) -> None:
        """search with mode='semantic' when no embeddings configured returns error."""
        server = create_server()
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
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write", {"path": "simple.md", "content": "# Replaced\n"}
            )
        data = result.data
        assert data["created"] is False

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_with_frontmatter(self) -> None:
        """write tool with frontmatter parameter creates document and returns created=True."""
        server = create_server()
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
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "edit",
                {"path": "nonexistent.md", "old_text": "a", "new_text": "b"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_conflict_returns_error(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "edit",
                {"path": "simple.md", "old_text": "missing text", "new_text": "b"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_line_range(self) -> None:
        """MCP edit tool accepts line_start/line_end."""
        server = create_server()
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
        server = create_server()
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
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("delete", {"path": "simple.md"})
        data = result.data
        assert data["path"] == "simple.md"

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_delete_nonexistent_returns_error(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp("delete", {"path": "nonexistent.md"})
        assert result.isError is True


class TestRenameTool:
    """Test the rename MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_moves_document(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "rename", {"old_path": "simple.md", "new_path": "renamed.md"}
            )
        data = result.data
        assert data["old_path"] == "simple.md"
        assert data["new_path"] == "renamed.md"

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_nonexistent_returns_error(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "rename",
                {"old_path": "nonexistent.md", "new_path": "target.md"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_target_exists_returns_error(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "rename",
                {"old_path": "simple.md", "new_path": "no_frontmatter.md"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_to_same_path_returns_error(self) -> None:
        """rename to same old_path and new_path should return an error."""
        server = create_server()
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

        server = create_server()
        async with Client(server) as client:
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


def _oidc_config(**overrides: Any) -> CollectionConfig:
    """Build a CollectionConfig pre-filled with all required OIDC fields.

    Keyword arguments override any field on the config.
    """
    defaults: dict[str, Any] = {
        "source_dir": Path("/tmp"),
        "base_url": "https://mcp.example.com",
        "oidc_config_url": "https://auth.example.com/.well-known/openid-configuration",
        "oidc_client_id": "test-client",
        "oidc_client_secret": "test-secret",
    }
    defaults.update(overrides)
    return CollectionConfig(**defaults)


class TestBuildOidcAuth:
    """Unit tests for build_oidc_auth()."""

    def test_returns_none_when_no_vars_set(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert build_oidc_auth(config) is None

    @pytest.mark.parametrize(
        "missing_field",
        ["base_url", "oidc_config_url", "oidc_client_id", "oidc_client_secret"],
    )
    def test_returns_none_when_one_required_var_missing(
        self, missing_field: str
    ) -> None:
        """Any one missing required field disables auth."""
        config = _oidc_config(**{missing_field: None})
        assert build_oidc_auth(config) is None

    def test_returns_non_none_when_all_required_vars_set(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            result = build_oidc_auth(config)

        assert result is not None
        mock_cls.assert_called_once()

    def test_passes_required_kwargs_to_oidc_proxy(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        kw = mock_cls.call_args.kwargs
        assert kw["base_url"] == "https://mcp.example.com"
        assert (
            kw["config_url"]
            == "https://auth.example.com/.well-known/openid-configuration"
        )
        assert kw["client_id"] == "test-client"
        assert kw["client_secret"] == "test-secret"

    def test_default_required_scopes_is_openid(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["required_scopes"] == ["openid"]

    def test_empty_required_scopes_falls_back_to_openid(self) -> None:
        """Explicitly empty REQUIRED_SCOPES falls back to ['openid'], not []."""
        from unittest.mock import MagicMock, patch

        config = _oidc_config(oidc_required_scopes="")
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["required_scopes"] == ["openid"]

    def test_custom_required_scopes_parsed(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config(oidc_required_scopes="openid, profile, email")
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["required_scopes"] == [
            "openid",
            "profile",
            "email",
        ]

    def test_audience_forwarded_when_set(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config(oidc_audience="my-api")
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["audience"] == "my-api"

    def test_audience_is_none_when_not_set(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["audience"] is None

    def test_jwt_signing_key_forwarded_when_set(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config(oidc_jwt_signing_key="deadbeef1234")
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["jwt_signing_key"] == "deadbeef1234"

    def test_jwt_signing_key_is_none_when_not_set(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["jwt_signing_key"] is None

    def test_linux_warning_when_jwt_key_absent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            patch("markdown_vault_mcp.config.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            build_oidc_auth(config)

        assert any(
            "JWT_SIGNING_KEY" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )

    def test_no_warning_when_jwt_key_present_on_linux(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config(oidc_jwt_signing_key="some-key")
        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            patch("markdown_vault_mcp.config.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            build_oidc_auth(config)

        assert not any(
            "JWT_SIGNING_KEY" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )

    def test_no_warning_on_non_linux_without_jwt_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            patch("markdown_vault_mcp.config.sys") as mock_sys,
        ):
            mock_sys.platform = "darwin"
            build_oidc_auth(config)

        assert not any(
            "JWT_SIGNING_KEY" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )

    def test_default_verify_id_token_is_true(self) -> None:
        """By default, verify_id_token=True (works with opaque access tokens)."""
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["verify_id_token"] is True

    def test_verify_access_token_disables_verify_id_token(self) -> None:
        """OIDC_VERIFY_ACCESS_TOKEN=true reverts to access-token verification."""
        from unittest.mock import MagicMock, patch

        config = _oidc_config(oidc_verify_access_token=True)
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["verify_id_token"] is False

    def test_verify_id_token_log_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Default config logs that id_token verification is active."""
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            caplog.at_level(logging.INFO, logger="markdown_vault_mcp.config"),
        ):
            build_oidc_auth(config)

        assert any("verifying upstream id_token" in r.message for r in caplog.records)

    def test_verify_access_token_log_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """OIDC_VERIFY_ACCESS_TOKEN=true logs access-token verification mode."""
        from unittest.mock import MagicMock, patch

        config = _oidc_config(oidc_verify_access_token=True)
        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            caplog.at_level(logging.INFO, logger="markdown_vault_mcp.config"),
        ):
            build_oidc_auth(config)

        assert any(
            "verifying upstream access_token as JWT" in r.message
            for r in caplog.records
        )

    def test_warning_when_openid_scope_missing_with_verify_id_token(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Warn when verify_id_token=True but 'openid' is not in scopes."""
        from unittest.mock import MagicMock, patch

        config = _oidc_config(oidc_required_scopes="profile,email")
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert any(
            "openid" in r.message and r.levelname == "WARNING" for r in caplog.records
        )

    def test_no_warning_when_openid_scope_present(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No warning when 'openid' is in scopes (default)."""
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert not any(
            "openid" in r.message and r.levelname == "WARNING" for r in caplog.records
        )


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

        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "assets/image.png"})
        assert result.data["mime_type"] == "image/png"

    async def test_read_attachment_missing_raises(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
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
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("write", {"path": "assets/new.pdf"})

    async def test_write_attachment_invalid_base64_raises(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
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
            server = create_server()
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
            server = create_server()
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
        server = create_server()
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
        server = create_server()
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
            server = create_server()
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

        server = create_server()
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
            server = create_server()
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
            server = create_server()
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
        server = create_server()
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
        server = create_server()
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
        server = create_server()
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
            server = create_server()
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
        server = create_server()
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
            server = create_server()
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
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_documents", {})
        items = _parse_tool_data(result)
        paths = [item["path"] for item in items]
        assert not any(p.endswith(".pdf") or p.endswith(".png") for p in paths)

    async def test_list_documents_include_attachments_returns_both(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
        async with Client(server) as client:
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
        server = create_server()
        async with Client(server) as client:
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
        server = create_server()
        async with Client(server) as client:
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_backlinks", {"path": "notes/topic.md"})
        data = _parse_tool_data(result)
        assert len(data) == 1
        assert data[0]["source_path"] == "index.md"
        assert data[0]["link_text"] == "Topic"

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_backlinks_nonexistent_raises(self) -> None:
        server = create_server()
        async with Client(server) as client:
            with pytest.raises((ToolError, McpError)):
                await client.call_tool("get_backlinks", {"path": "nope.md"})

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_outlinks(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_outlinks", {"path": "index.md"})
        data = _parse_tool_data(result)
        assert len(data) == 2
        targets = {d["target_path"] for d in data}
        assert "notes/topic.md" in targets
        assert "ghost.md" in targets
        # notes/topic.md exists, ghost.md does not
        by_target = {d["target_path"]: d for d in data}
        assert by_target["notes/topic.md"]["exists"] is True
        assert by_target["ghost.md"]["exists"] is False

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_outlinks_nonexistent_path(self) -> None:
        server = create_server()
        async with Client(server) as client:
            with pytest.raises((ToolError, McpError)):
                await client.call_tool("get_outlinks", {"path": "nope.md"})

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_broken_links(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_broken_links", {})
        data = _parse_tool_data(result)
        assert len(data) == 1
        assert data[0]["target_path"] == "ghost.md"
        assert data[0]["source_path"] == "index.md"

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_get_broken_links_with_folder_filter(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_broken_links", {"folder": "notes"})
        data = _parse_tool_data(result)
        # notes/topic.md links to ../index.md which exists — no broken links
        assert data == []

    @pytest.mark.usefixtures("_mcp_env_linked")
    async def test_link_tools_available_in_readonly(self) -> None:
        """Link tools are read-only and available even when vault is read-only."""
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_similar", {"path": "simple.md"})
        data = _parse_tool_data(result)
        assert data == []

    @pytest.mark.usefixtures("_mcp_env")
    async def test_get_similar_nonexistent_raises(self) -> None:
        server = create_server()
        async with Client(server) as client:
            with pytest.raises((ToolError, McpError)):
                await client.call_tool("get_similar", {"path": "nonexistent.md"})


class TestRecentTool:
    """Integration tests for get_recent tool and recent://vault resource."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_get_recent_returns_notes(self) -> None:
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "get_recent", {"folder": "nonexistent_folder"}
            )
        data = _parse_tool_data(result)
        assert data == []

    @pytest.mark.usefixtures("_mcp_env")
    async def test_recent_resource(self) -> None:
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_context", {"path": "index.md"})
        data = _parse_tool_data(result)
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
        server = create_server()
        async with Client(server) as client:
            ctx = _parse_tool_data(
                await client.call_tool("get_context", {"path": "index.md"})
            )
            read = (await client.call_tool("read", {"path": "index.md"})).data
        assert ctx["modified_at"] == read["modified_at"]

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_backlinks(self) -> None:
        """notes/topic.md has a backlink from index.md."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_context", {"path": "notes/topic.md"})
        data = _parse_tool_data(result)
        sources = [b["source_path"] for b in data["backlinks"]]
        assert "index.md" in sources

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_outlinks(self) -> None:
        """index.md has an outlink to notes/topic.md."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_context", {"path": "index.md"})
        data = _parse_tool_data(result)
        targets = [o["target_path"] for o in data["outlinks"]]
        assert "notes/topic.md" in targets

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_folder_notes_excludes_self(self) -> None:
        """folder_notes for notes/topic.md contains peer.md but not topic.md."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_context", {"path": "notes/topic.md"})
        data = _parse_tool_data(result)
        assert "notes/topic.md" not in data["folder_notes"]
        assert "notes/peer.md" in data["folder_notes"]

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_tags(self) -> None:
        """Indexed frontmatter tags appear in context.tags."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_context", {"path": "index.md"})
        data = _parse_tool_data(result)
        assert "tags" in data["tags"]
        assert "ai" in data["tags"]["tags"]
        assert "research" in data["tags"]["tags"]

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_similar_empty_without_embeddings(self) -> None:
        """similar is empty when embeddings are not configured."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_context", {"path": "index.md"})
        data = _parse_tool_data(result)
        assert data["similar"] == []

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_nonexistent_raises(self) -> None:
        """get_context raises for a path not in the index."""
        server = create_server()
        async with Client(server) as client:
            with pytest.raises((ToolError, McpError)):
                await client.call_tool("get_context", {"path": "nope.md"})

    @pytest.mark.usefixtures("_mcp_env_context")
    async def test_get_context_in_tool_list(self) -> None:
        """get_context appears in the server tool list."""
        server = create_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "get_context" in names


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class TestResources:
    """Verify MCP resources return valid JSON with expected shapes."""

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_config_resource(self) -> None:
        server = create_server()
        async with Client(server) as client:
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
        server = create_server()
        async with Client(server) as client:
            resource_result = await client.read_resource("stats://vault")
            tool_result = await client.call_tool("stats", {})
        resource_data = json.loads(resource_result[0].text)
        tool_data = tool_result.data
        assert resource_data["document_count"] == tool_data["document_count"]
        assert resource_data["chunk_count"] == tool_data["chunk_count"]

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_tags_resource_grouped(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.read_resource("tags://vault")
        data = json.loads(result[0].text)
        # With indexed fields "cluster,tags", both keys should be present.
        assert isinstance(data, dict)
        assert "cluster" in data
        assert "tags" in data

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_tags_resource_by_field(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.read_resource("tags://vault/cluster")
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        # full_frontmatter.md has cluster: fiction
        assert "fiction" in data

    @pytest.mark.usefixtures("_mcp_env")
    async def test_folders_resource(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.read_resource("folders://vault")
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert "" in data  # root folder
        assert "subfolder" in data

    @pytest.mark.usefixtures("_mcp_env")
    async def test_toc_resource(self) -> None:
        server = create_server()
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
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt("summarize", {"path": "simple.md"})
        text = result.messages[0].content.text
        assert "simple.md" in text
        assert "`read`" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_research_prompt(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt("research", {"topic": "horror fiction"})
        text = result.messages[0].content.text
        assert "horror fiction" in text
        assert "`search`" in text
        assert "`write`" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_discuss_prompt(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt("discuss", {"path": "simple.md"})
        text = result.messages[0].content.text
        assert "simple.md" in text
        assert "`edit`" in text
        assert "Do not use `write`" in text

    @pytest.mark.usefixtures("_mcp_env")
    async def test_related_prompt(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt("related", {"path": "simple.md"})
        text = result.messages[0].content.text
        assert "simple.md" in text
        assert "`search`" in text
        assert "read-only" in text

    @pytest.mark.usefixtures("_mcp_env")
    async def test_compare_prompt(self) -> None:
        server = create_server()
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
        server = create_server()
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
        server = create_server()
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
        server = create_server()
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
        server = create_server()
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
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt("create_from_template", {})
        text = result.messages[0].content.text
        assert "Templates/" not in text
        assert "`Templates`" in text

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_create_from_template_prompt_registration_schema(self) -> None:
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "research" not in names
        assert "discuss" not in names
        assert "create_from_template" not in names

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_prompts_visible_when_writable(self) -> None:
        server = create_server()
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
        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()

        for prompt in prompts:
            assert prompt.icons is not None
            assert len(prompt.icons) > 0
            assert prompt.icons[0].mimeType == "image/svg+xml"

    @pytest.mark.usefixtures("_mcp_env")
    async def test_resources_have_icons(self) -> None:
        server = create_server()
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

        server = create_server()
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
        server = create_server()
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

        server = create_server()
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
        server = create_server()
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

        server = create_server()
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
        server = create_server()
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

        server = create_server()
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
        server = create_server()
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
        """With EMBEDDINGS_PATH set, startup builds vectors automatically."""
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
        # module level, patch "mcp_server.get_embedding_provider" instead.
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov,
        ):
            server = create_server()
            async with Client(server) as client:
                result = await client.call_tool_mcp("embeddings_status", {})
        data = json.loads(result.content[0].text)
        assert data["chunk_count"] > 0

    async def test_subsequent_startup_skips_rebuild(
        self,
        vault_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With existing embeddings on disk, startup loads them without rebuilding."""
        from unittest.mock import patch

        from .conftest import MockEmbeddingProvider

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        embeddings_path = str(tmp_path / "embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", embeddings_path)

        mock_prov = MockEmbeddingProvider()
        # First startup: build embeddings from scratch.
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov,
        ):
            server = create_server()
            async with Client(server) as client:
                r1 = await client.call_tool_mcp("embeddings_status", {})
        count1 = json.loads(r1.content[0].text)["chunk_count"]
        assert count1 > 0

        # Second startup: should load from disk, not re-embed.
        mock_prov2 = MockEmbeddingProvider()
        embed_calls: list[int] = []
        original_embed = mock_prov2.embed

        def tracking_embed(texts: list[str]) -> list[list[float]]:
            embed_calls.append(len(texts))
            return original_embed(texts)

        mock_prov2.embed = tracking_embed  # type: ignore[method-assign]
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov2,
        ):
            server2 = create_server()
            async with Client(server2) as client2:
                r2 = await client2.call_tool_mcp("embeddings_status", {})
        count2 = json.loads(r2.content[0].text)["chunk_count"]
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

        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp("stats", {})
        data = json.loads(result.content[0].text)
        assert data["semantic_search_available"] is False


# ---------------------------------------------------------------------------
# Bearer token auth configuration
# ---------------------------------------------------------------------------

_BEARER_VARS = ("MARKDOWN_VAULT_MCP_BEARER_TOKEN",)


class TestBuildBearerAuth:
    """Unit tests for build_bearer_auth()."""

    def test_returns_none_when_no_var_set(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert build_bearer_auth(config) is None

    def test_returns_none_when_empty_string(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), bearer_token="")
        assert build_bearer_auth(config) is None

    def test_returns_none_when_whitespace_only(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), bearer_token="   ")
        assert build_bearer_auth(config) is None

    def test_returns_static_token_verifier_when_set(self) -> None:
        from fastmcp.server.auth import StaticTokenVerifier

        config = CollectionConfig(
            source_dir=Path("/tmp"), bearer_token="test-secret-123"
        )
        result = build_bearer_auth(config)
        assert isinstance(result, StaticTokenVerifier)

    def test_token_dict_has_correct_structure(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), bearer_token="my-token")
        result = build_bearer_auth(config)
        assert "my-token" in result.tokens
        entry = result.tokens["my-token"]
        assert entry["client_id"] == "bearer"
        assert entry["scopes"] == ["read", "write"]

    async def test_verify_correct_token_returns_access_token(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), bearer_token="good-token")
        verifier = build_bearer_auth(config)
        access = await verifier.verify_token("good-token")
        assert access is not None
        assert access.client_id == "bearer"
        assert access.scopes == ["read", "write"]

    async def test_verify_wrong_token_returns_none(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), bearer_token="good-token")
        verifier = build_bearer_auth(config)
        access = await verifier.verify_token("wrong-token")
        assert access is None

    async def test_verify_empty_token_returns_none(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), bearer_token="good-token")
        verifier = build_bearer_auth(config)
        access = await verifier.verify_token("")
        assert access is None


class TestAuthModeSelection:
    """Tests for auth mode selection in create_server().

    These tests call ``create_server()`` directly so the real assembly
    logic is exercised — not just the individual builder functions.
    Covers all four modes: multi (both), bearer-only, OIDC-only, none.
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
            server = create_server()

        assert isinstance(server.auth, MultiAuth)
        assert "using bearer token auth" not in caplog.text
        assert "Multi-auth enabled" in caplog.text

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
            server = create_server()

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
            server = create_server()

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
            server = create_server()

        # Server must use the OIDC mock, not bearer
        assert server.auth is not None
        assert server.auth is mock_cls.return_value

    def test_no_auth_when_nothing_configured(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Without any auth vars, server runs unauthenticated and logs at WARNING."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))

        with caplog.at_level(logging.WARNING):
            server = create_server()

        assert server.auth is None
        assert "unauthenticated" in caplog.text
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("unauthenticated" in r.message for r in warning_records), (
            "No-auth message must be logged at WARNING level, not INFO"
        )


class TestAuthDebugLogging:
    """Tests for auth DEBUG logging (issue #181)."""

    def test_bearer_debug_logs_presence(self, caplog: pytest.LogCaptureFixture) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), bearer_token="secret-token")
        with caplog.at_level(logging.DEBUG):
            build_bearer_auth(config)
        assert "BEARER_TOKEN is set" in caplog.text
        assert "secret-token" not in caplog.text

    def test_bearer_debug_logs_absence(self, caplog: pytest.LogCaptureFixture) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        with caplog.at_level(logging.DEBUG):
            build_bearer_auth(config)
        assert "BEARER_TOKEN not set" in caplog.text

    def test_oidc_debug_logs_config(self, caplog: pytest.LogCaptureFixture) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            caplog.at_level(logging.DEBUG),
        ):
            build_oidc_auth(config)

        assert "OIDC auth config:" in caplog.text
        assert "config_url" in caplog.text
        assert "client_id" in caplog.text
        assert "<redacted>" in caplog.text  # client_secret is redacted
        assert "test-secret" not in caplog.text

    def test_oidc_debug_logs_missing_vars(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Only set BASE_URL, leave others missing
        config = CollectionConfig(
            source_dir=Path("/tmp"), base_url="https://example.com"
        )
        with caplog.at_level(logging.DEBUG):
            result = build_oidc_auth(config)
        assert result is None
        assert "missing env vars" in caplog.text

    def test_startup_summary_logged(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        with caplog.at_level(logging.INFO):
            create_server()
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
            "markdown_vault_mcp.mcp_server._pkg_version",
            lambda _name: (_ for _ in ()).throw(PackageNotFoundError("test")),
        )
        with caplog.at_level(logging.INFO):
            create_server()
        assert "version=unknown" in caplog.text


# ---------------------------------------------------------------------------
# _resolve_auth_mode() — OIDC mode detection
# ---------------------------------------------------------------------------


class TestResolveAuthMode:
    """Tests for resolve_auth_mode()."""

    def test_explicit_remote(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="remote")
        assert resolve_auth_mode(config) == "remote"

    def test_explicit_oidc_proxy(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="oidc-proxy")
        assert resolve_auth_mode(config) == "oidc-proxy"

    def test_explicit_case_insensitive(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="REMOTE")
        assert resolve_auth_mode(config) == "remote"

    def test_auto_detect_oidc_proxy(self) -> None:
        """All four OIDC fields set -> oidc-proxy."""
        config = _oidc_config()
        assert resolve_auth_mode(config) == "oidc-proxy"

    def test_auto_detect_remote(self) -> None:
        """Only base_url + oidc_config_url -> remote."""
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            base_url="https://mcp.example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
        )
        assert resolve_auth_mode(config) == "remote"

    def test_no_vars_returns_none(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert resolve_auth_mode(config) is None

    def test_invalid_mode_ignored(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="invalid")
        assert resolve_auth_mode(config) is None

    def test_invalid_mode_warns_and_falls_through(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Invalid AUTH_MODE with auto-detect fields logs warning, falls to remote."""
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            auth_mode="typo",
            base_url="https://mcp.example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
        )
        with caplog.at_level(logging.WARNING):
            result = resolve_auth_mode(config)
        assert result == "remote"
        assert "Unknown AUTH_MODE 'typo'" in caplog.text

    def test_only_base_url_returns_none(self) -> None:
        """base_url alone is not enough for any OIDC mode."""
        config = CollectionConfig(
            source_dir=Path("/tmp"), base_url="https://mcp.example.com"
        )
        assert resolve_auth_mode(config) is None

    def test_explicit_overrides_auto_detection(self) -> None:
        """AUTH_MODE=remote forces remote even when all four fields are set."""
        config = _oidc_config(auth_mode="remote")
        assert resolve_auth_mode(config) == "remote"


# ---------------------------------------------------------------------------
# _build_remote_auth() — RemoteAuthProvider construction
# ---------------------------------------------------------------------------


class TestBuildRemoteAuth:
    """Tests for build_remote_auth()."""

    def _remote_config(self, **overrides: Any) -> CollectionConfig:
        """Build a CollectionConfig with base_url + oidc_config_url."""
        defaults: dict[str, Any] = {
            "source_dir": Path("/tmp"),
            "base_url": "https://mcp.example.com",
            "oidc_config_url": "https://auth.example.com/.well-known/openid-configuration",
        }
        defaults.update(overrides)
        return CollectionConfig(**defaults)

    def test_missing_env_vars_returns_none(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert build_remote_auth(config) is None

    def test_missing_config_url_returns_none(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp"), base_url="https://mcp.example.com"
        )
        assert build_remote_auth(config) is None

    def test_missing_base_url_returns_none(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
        )
        assert build_remote_auth(config) is None

    def test_discovery_fetch_failure_returns_none(self) -> None:
        from unittest.mock import patch

        config = self._remote_config()
        with patch("httpx.get", side_effect=Exception("connection failed")):
            assert build_remote_auth(config) is None

    def test_httpx_import_error_returns_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Missing httpx gives a clear error, not a confusing discovery message."""
        import sys as _sys
        from unittest.mock import patch

        config = self._remote_config()
        with (
            patch.dict(_sys.modules, {"httpx": None}),
            caplog.at_level(logging.ERROR),
        ):
            assert build_remote_auth(config) is None
        assert "'httpx' is not installed" in caplog.text

    def test_happy_path_returns_non_none(self) -> None:
        from unittest.mock import MagicMock, patch

        config = self._remote_config()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
            "issuer": "https://auth.example.com",
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.get", return_value=mock_resp):
            result = build_remote_auth(config)
        assert result is not None

    def test_missing_jwks_uri_returns_none(self) -> None:
        from unittest.mock import MagicMock, patch

        config = self._remote_config()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"issuer": "https://auth.example.com"}
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.get", return_value=mock_resp):
            assert build_remote_auth(config) is None

    def test_missing_issuer_returns_none(self) -> None:
        from unittest.mock import MagicMock, patch

        config = self._remote_config()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jwks_uri": "https://auth.example.com/.well-known/jwks.json"
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.get", return_value=mock_resp):
            assert build_remote_auth(config) is None

    def test_audience_forwarded_when_set(self) -> None:
        from unittest.mock import MagicMock, patch

        config = self._remote_config(oidc_audience="my-api")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
            "issuer": "https://auth.example.com",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_jwt_verifier = MagicMock()
        mock_verifier_cls = MagicMock(return_value=mock_jwt_verifier)
        mock_remote_cls = MagicMock()

        with (
            patch("httpx.get", return_value=mock_resp),
            patch("fastmcp.server.auth.JWTVerifier", mock_verifier_cls),
            patch("fastmcp.server.auth.RemoteAuthProvider", mock_remote_cls),
        ):
            build_remote_auth(config)

        kw = mock_verifier_cls.call_args.kwargs
        assert kw["audience"] == "my-api"

    def test_audience_is_none_when_not_set(self) -> None:
        from unittest.mock import MagicMock, patch

        config = self._remote_config()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
            "issuer": "https://auth.example.com",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_jwt_verifier = MagicMock()
        mock_verifier_cls = MagicMock(return_value=mock_jwt_verifier)
        mock_remote_cls = MagicMock()

        with (
            patch("httpx.get", return_value=mock_resp),
            patch("fastmcp.server.auth.JWTVerifier", mock_verifier_cls),
            patch("fastmcp.server.auth.RemoteAuthProvider", mock_remote_cls),
        ):
            build_remote_auth(config)

        kw = mock_verifier_cls.call_args.kwargs
        assert kw["audience"] is None

    def test_required_scopes_parsed(self) -> None:
        from unittest.mock import MagicMock, patch

        config = self._remote_config(oidc_required_scopes="openid, profile, email")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
            "issuer": "https://auth.example.com",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_jwt_verifier = MagicMock()
        mock_verifier_cls = MagicMock(return_value=mock_jwt_verifier)
        mock_remote_cls = MagicMock()

        with (
            patch("httpx.get", return_value=mock_resp),
            patch("fastmcp.server.auth.JWTVerifier", mock_verifier_cls),
            patch("fastmcp.server.auth.RemoteAuthProvider", mock_remote_cls),
        ):
            build_remote_auth(config)

        kw = mock_verifier_cls.call_args.kwargs
        assert kw["required_scopes"] == ["openid", "profile", "email"]

    def test_empty_required_scopes_results_in_none(self) -> None:
        """Empty REQUIRED_SCOPES results in None (no scope enforcement)."""
        from unittest.mock import MagicMock, patch

        config = self._remote_config(oidc_required_scopes="")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
            "issuer": "https://auth.example.com",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_jwt_verifier = MagicMock()
        mock_verifier_cls = MagicMock(return_value=mock_jwt_verifier)
        mock_remote_cls = MagicMock()

        with (
            patch("httpx.get", return_value=mock_resp),
            patch("fastmcp.server.auth.JWTVerifier", mock_verifier_cls),
            patch("fastmcp.server.auth.RemoteAuthProvider", mock_remote_cls),
        ):
            build_remote_auth(config)

        kw = mock_verifier_cls.call_args.kwargs
        assert kw["required_scopes"] is None

    def test_debug_logging_on_success(self, caplog: pytest.LogCaptureFixture) -> None:
        from unittest.mock import MagicMock, patch

        config = self._remote_config()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
            "issuer": "https://auth.example.com",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_jwt_verifier = MagicMock()
        mock_verifier_cls = MagicMock(return_value=mock_jwt_verifier)
        mock_remote_cls = MagicMock()

        with (
            patch("httpx.get", return_value=mock_resp),
            patch("fastmcp.server.auth.JWTVerifier", mock_verifier_cls),
            patch("fastmcp.server.auth.RemoteAuthProvider", mock_remote_cls),
            caplog.at_level(logging.DEBUG),
        ):
            build_remote_auth(config)

        assert "Remote auth config:" in caplog.text
        assert "jwks_uri" in caplog.text


# ---------------------------------------------------------------------------
# Middleware stack
# ---------------------------------------------------------------------------


class TestMiddlewareStack:
    """Verify that create_server() wires the expected middleware."""

    @pytest.mark.usefixtures("_mcp_env")
    def test_default_middleware_stack(self) -> None:
        """Server has ErrorHandling, Timing, and Logging middleware by default."""
        from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
        from fastmcp.server.middleware.logging import LoggingMiddleware
        from fastmcp.server.middleware.timing import TimingMiddleware

        server = create_server()
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
        server = create_server()
        types = [type(m) for m in server.middleware]
        assert StructuredLoggingMiddleware in types

    @pytest.mark.usefixtures("_mcp_env")
    def test_middleware_order(self) -> None:
        """ErrorHandling is first, then Timing, then Logging."""
        from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
        from fastmcp.server.middleware.logging import LoggingMiddleware
        from fastmcp.server.middleware.timing import TimingMiddleware

        server = create_server()
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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_history", {})
        entries = _parse_tool_data(result)
        assert isinstance(entries, list)
        assert len(entries) == 2
        messages = [e["message"] for e in entries]
        assert "edit: alpha.md" in messages
        assert "write: alpha.md" in messages

    async def test_single_note_history(self) -> None:
        """get_history filtered by path returns commits for that note."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_history", {"path": "alpha.md"})
        entries = _parse_tool_data(result)
        assert isinstance(entries, list)
        assert len(entries) == 2

    async def test_history_entry_fields(self) -> None:
        """Each history entry has the expected fields."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_history", {"limit": 1})
        entries = _parse_tool_data(result)
        entry = entries[0]
        assert "sha" in entry and len(entry["sha"]) == 40
        assert "short_sha" in entry
        assert "timestamp" in entry
        assert "author" in entry
        assert "message" in entry
        assert "paths_changed" in entry

    async def test_limit_respected(self) -> None:
        """limit parameter restricts the number of results."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("get_history", {"limit": 1})
        entries = _parse_tool_data(result)
        assert len(entries) == 1

    async def test_invalid_path_raises_tool_error(self) -> None:
        """A path that escapes the vault raises ToolError."""
        from fastmcp.exceptions import ToolError

        server = create_server()
        with pytest.raises((ToolError, Exception)):
            async with Client(server) as client:
                await client.call_tool("get_history", {"path": "../escape.md"})

    async def test_get_history_in_tool_list(self) -> None:
        """get_history appears in the tool list."""
        server = create_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "get_history" in names

    async def test_get_diff_in_tool_list(self) -> None:
        """get_diff appears in the tool list."""
        server = create_server()
        async with Client(server) as client:
            tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "get_diff" in names


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
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "get_diff", {"path": "alpha.md", "since_sha": sha}
            )
        data = _parse_tool_data(result)
        assert isinstance(data, dict)
        assert "diff" in data
        assert "Version" in data["diff"] or data["diff"] == ""

    async def test_per_commit_diff(self, git_vault: Path) -> None:
        """get_diff with per_commit=True returns a list of commit diffs."""
        sha = self._first_sha(git_vault)
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "get_diff",
                {"path": "alpha.md", "since_sha": sha, "per_commit": True},
            )
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) == 1
        assert "sha" in data[0]
        assert "diff" in data[0]

    async def test_no_reference_raises_tool_error(self) -> None:
        """Calling get_diff without any reference raises ToolError."""
        from fastmcp.exceptions import ToolError

        server = create_server()
        with pytest.raises((ToolError, Exception)):
            async with Client(server) as client:
                await client.call_tool("get_diff", {"path": "alpha.md"})

    async def test_both_references_raises_tool_error(self, git_vault: Path) -> None:
        """Providing both since_sha and since_timestamp raises ToolError."""
        from fastmcp.exceptions import ToolError

        sha = self._first_sha(git_vault)
        server = create_server()
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

        server = create_server()
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
        server = create_server()
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
        assert isinstance(data, list)
        assert len(data) == 2
        # Newest-first ordering: the two most recent commit messages.
        assert [c["message"] for c in data] == ["edit v4", "edit v3"]


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
        server = create_server()

        async with Client(server) as client:
            future = await client.call_tool(
                "get_history", {"until": "2099-01-01T00:00:00"}
            )
        assert len(_parse_tool_data(future)) == 2

        async with Client(server) as client:
            past = await client.call_tool(
                "get_history", {"until": "2000-01-01T00:00:00"}
            )
        assert _parse_tool_data(past) == []
