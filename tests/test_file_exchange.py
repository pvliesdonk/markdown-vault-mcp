"""Tests for the MCP File Exchange v0.3 wiring on markdown-vault-mcp.

The protocol surface, runtime, and capability helper are tested
upstream in ``fastmcp-pvl-core``; this module covers MV-side
adoption:

- ``fetch`` resolves both ``http(s)://`` and ``exchange://`` URIs
- ``fetch`` accepts a ``file_ref`` parameter, preferring the
  ``exchange`` transfer entry when present
- ``read`` augments binary attachment results with a ``file_ref``
  block when ``MCP_EXCHANGE_DIR`` is configured (and omits it
  otherwise)
- ``create_download_link`` accepts ``origin_id`` as an alias for
  ``path`` and rejects mismatched values
- ``experimental.file_exchange`` appears in the MCP ``initialize``
  response with this server's namespace + advertised transfer methods
- ``ExchangeGroupMismatch`` surfaces a clear error
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from pathlib import Path


_CLEAR_VARS = (
    "MARKDOWN_VAULT_MCP_INDEX_PATH",
    "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
    "MARKDOWN_VAULT_MCP_STATE_PATH",
    "MARKDOWN_VAULT_MCP_INDEXED_FIELDS",
    "MARKDOWN_VAULT_MCP_REQUIRED_FIELDS",
    "MARKDOWN_VAULT_MCP_EXCLUDE",
    "MARKDOWN_VAULT_MCP_GIT_TOKEN",
    "MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER",
    "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
    "MCP_EXCHANGE_DIR",
    "MCP_EXCHANGE_ID",
    "MCP_EXCHANGE_NAMESPACE",
)


@pytest.fixture
def _exchange_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Vault + configured MCP_EXCHANGE_DIR for file-exchange tests."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nBody.\n", encoding="utf-8")
    (vault / "assets").mkdir()
    (vault / "assets" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    exchange_dir = tmp_path / "exchange"
    exchange_dir.mkdir()

    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://mcp.example.com")
    monkeypatch.setenv("MCP_EXCHANGE_DIR", str(exchange_dir))
    monkeypatch.setenv("MCP_EXCHANGE_NAMESPACE", "markdown-vault-mcp")
    return vault, exchange_dir


@pytest.fixture
def _no_exchange_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Vault with file-exchange disabled (MCP_EXCHANGE_DIR unset)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n", encoding="utf-8")
    (vault / "assets").mkdir()
    (vault / "assets" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://mcp.example.com")
    return vault


# ---------------------------------------------------------------------------
# Capability declaration
# ---------------------------------------------------------------------------


class TestFileExchangeCapability:
    """``experimental.file_exchange`` shows up in the initialize response."""

    async def test_capability_present_when_configured(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        """When MCP_EXCHANGE_DIR is set, capability advertises namespace + group."""
        server = make_server(transport="http")
        async with Client(server) as client:
            init = client.initialize_result
            assert init is not None
            caps = init.capabilities.experimental or {}
        assert "file_exchange" in caps
        payload = caps["file_exchange"]
        assert payload["namespace"] == "markdown-vault-mcp"
        assert isinstance(payload["exchange_id"], str)
        assert "exchange" in payload["transfer_methods"]
        assert payload["transfer_methods"]["http"]["tool"] == "create_download_link"
        assert payload["version"] == "0.3"

    async def test_capability_present_without_exchange_only_http(
        self, _no_exchange_env: Path
    ) -> None:
        """Without MCP_EXCHANGE_DIR, only the http transfer method is offered."""
        server = make_server(transport="http")
        async with Client(server) as client:
            init = client.initialize_result
            assert init is not None
            caps = init.capabilities.experimental or {}
        payload = caps["file_exchange"]
        assert "exchange" not in payload["transfer_methods"]
        assert "http" in payload["transfer_methods"]

    async def test_capability_stdio_omits_http(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        """stdio servers can't host the artifact endpoint, so http is omitted."""
        server = make_server(transport="stdio")
        async with Client(server) as client:
            init = client.initialize_result
            assert init is not None
            caps = init.capabilities.experimental or {}
        payload = caps["file_exchange"]
        assert "http" not in payload["transfer_methods"]
        assert "exchange" in payload["transfer_methods"]


# ---------------------------------------------------------------------------
# read augmentation
# ---------------------------------------------------------------------------


class TestReadFileRefAugmentation:
    """``read`` adds a ``file_ref`` block for binary attachments when configured."""

    async def test_attachment_includes_file_ref_when_configured(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        _vault, exchange_dir = _exchange_env
        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "assets/image.png"})
        data = result.structured_content
        assert data is not None
        assert "file_ref" in data
        ref = data["file_ref"]
        assert ref["origin_server"] == "markdown-vault-mcp"
        assert ref["origin_id"] == "assets/image.png"
        assert ref["mime_type"] == "image/png"
        assert ref["transfer"]["exchange"]["uri"].startswith("exchange://")
        # The bytes must actually be present in the exchange dir.
        ns_dir = exchange_dir / "markdown-vault-mcp"
        assert ns_dir.is_dir()
        files = list(ns_dir.glob("*.png"))
        assert len(files) == 1

    async def test_attachment_omits_file_ref_when_not_configured(
        self, _no_exchange_env: Path
    ) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "assets/image.png"})
        data = result.structured_content
        assert data is not None
        assert "file_ref" not in data
        # Legacy content_base64 path still works.
        assert "content_base64" in data

    async def test_note_does_not_get_file_ref(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        """Markdown notes are returned as text — no file_ref block."""
        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "note.md"})
        data = result.structured_content
        assert data is not None
        assert "file_ref" not in data


# ---------------------------------------------------------------------------
# fetch consumer side
# ---------------------------------------------------------------------------


class TestFetchExchangeURI:
    """``fetch`` resolves ``exchange://`` URIs against the local FileExchange."""

    async def test_fetch_writes_attachment_from_exchange_uri(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        """Round-trip: read → file_ref → fetch via exchange URI → new attachment."""
        _vault, _exchange_dir = _exchange_env
        server = make_server(transport="http")
        async with Client(server) as client:
            read_result = await client.call_tool("read", {"path": "assets/image.png"})
            data = read_result.structured_content
            assert data is not None
            uri = data["file_ref"]["transfer"]["exchange"]["uri"]

            fetch_result = await client.call_tool(
                "fetch", {"url": uri, "path": "assets/copy.png"}
            )
            written = fetch_result.structured_content
            assert written is not None
            assert written["content_length"] == len(
                base64.b64decode(data["content_base64"])
            )

    async def test_fetch_via_file_ref_prefers_exchange(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        """Passing the file_ref block directly resolves the exchange transfer."""
        server = make_server(transport="http")
        async with Client(server) as client:
            read_result = await client.call_tool("read", {"path": "assets/image.png"})
            data = read_result.structured_content
            assert data is not None
            file_ref = data["file_ref"]
            fetch_result = await client.call_tool(
                "fetch",
                {"file_ref": file_ref, "path": "assets/copy2.png"},
            )
            assert fetch_result.structured_content is not None

    async def test_fetch_exchange_group_mismatch_raises(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        """A URI from a different exchange group surfaces ExchangeGroupMismatch."""
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match="exchange group mismatch"):
                await client.call_tool(
                    "fetch",
                    {
                        "url": ("exchange://other-group-id/markdown-vault-mcp/x.png"),
                        "path": "assets/copy3.png",
                    },
                )

    async def test_fetch_requires_url_or_file_ref(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match=r"url.*file_ref"):
                await client.call_tool("fetch", {"path": "x.md"})

    async def test_fetch_file_ref_http_only_errors_clearly(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        """A file_ref offering only http transfer asks the LLM to call the tool."""
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match="http transfer"):
                await client.call_tool(
                    "fetch",
                    {
                        "file_ref": {
                            "origin_server": "image-mcp",
                            "origin_id": "abc",
                            "mime_type": "image/png",
                            "transfer": {
                                "http": {"tool": "create_download_link"},
                            },
                        },
                        "path": "assets/x.png",
                    },
                )


# ---------------------------------------------------------------------------
# create_download_link origin_id alias
# ---------------------------------------------------------------------------


class TestCreateDownloadLinkOriginIdAlias:
    """``create_download_link`` accepts ``origin_id`` per spec §3.1."""

    async def test_origin_id_alias_matches_path(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        """origin_id and path produce identical results."""
        server = make_server(transport="http")
        async with Client(server) as client:
            via_path = await client.call_tool(
                "create_download_link", {"path": "assets/image.png"}
            )
            via_origin_id = await client.call_tool(
                "create_download_link",
                {"origin_id": "assets/image.png"},
            )
        a = json.loads(via_path.content[0].text)
        b = json.loads(via_origin_id.content[0].text)
        # download_url contains a fresh random token per call — compare
        # the rest.
        assert a["path"] == b["path"]
        assert a["content_type"] == b["content_type"]
        assert a["expires_in_seconds"] == b["expires_in_seconds"]

    async def test_path_origin_id_mismatch_rejected(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match="different values"):
                await client.call_tool(
                    "create_download_link",
                    {
                        "path": "assets/image.png",
                        "origin_id": "assets/other.png",
                    },
                )

    async def test_neither_arg_rejected(self, _exchange_env: tuple[Path, Path]) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match=r"path.*origin_id"):
                await client.call_tool("create_download_link", {})
