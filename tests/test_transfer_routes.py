"""Tests for transfer-route wiring (#979).

``fastmcp_pvl_core.register_transfer_routes`` registers the
``create_download_link`` / ``create_upload_link`` tools and the
``/transfer/{token}`` route, gated on an HTTP transport with ``base_url`` set.
markdown-vault-mcp supplies only the ``VaultTransferSink`` domain hook; the tool
names, titles, hints, icons, and the ``write`` tag come from pvl-core (v4.8.0),
adopted as-is with no downstream mutation. The sink logic itself is covered by
``test_transfer_sink.py``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp_pvl_core import ServerConfig
from httpx import ASGITransport, AsyncClient

from markdown_vault_mcp.config import ProjectConfig
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP


def _config(
    tmp_path: Path, base_url: str | None, *, read_only: bool = False
) -> ProjectConfig:
    return ProjectConfig(
        server=ServerConfig(base_url=base_url, kv_store_url="memory://"),
        source_dir=tmp_path,
        read_only=read_only,
    )


def _has_transfer_route(server: FastMCP) -> bool:
    return any(
        "/transfer/" in getattr(r, "path", "") for r in server._additional_http_routes
    )


async def _has_tool(server: FastMCP, name: str) -> bool:
    try:
        return await server.get_tool(name) is not None
    except Exception:
        return False


async def test_transfer_tools_and_route_on_http_with_base_url(tmp_path: Path) -> None:
    server = make_server(
        transport="http",
        config=_config(tmp_path, base_url="https://mcp.example.com"),
    )
    assert await _has_tool(server, "create_download_link")
    assert await _has_tool(server, "create_upload_link")
    assert _has_transfer_route(server)


async def test_transfer_absent_on_stdio(tmp_path: Path) -> None:
    server = make_server(
        transport="stdio",
        config=_config(tmp_path, base_url="https://mcp.example.com"),
    )
    assert not await _has_tool(server, "create_download_link")
    assert not await _has_tool(server, "create_upload_link")
    assert not _has_transfer_route(server)


async def test_transfer_absent_on_http_without_base_url(tmp_path: Path) -> None:
    server = make_server(transport="http", config=_config(tmp_path, base_url=None))
    assert not await _has_tool(server, "create_download_link")
    assert not _has_transfer_route(server)


async def test_transfer_tools_carry_title_hints_and_icons(tmp_path: Path) -> None:
    """pvl-core (v4.8.0) registers the transfer tools with full metadata; the
    domain adopts them as-is (no finalizer, no mutation)."""
    server = make_server(
        transport="http",
        config=_config(tmp_path, base_url="https://mcp.example.com"),
    )
    for name, read_only in (
        ("create_download_link", True),
        ("create_upload_link", False),
    ):
        tool = await server.get_tool(name)
        assert tool is not None
        assert tool.annotations is not None
        assert tool.annotations.title  # non-empty human-readable title
        assert tool.annotations.read_only_hint is read_only
        assert tool.icons


async def test_create_upload_link_hidden_in_read_only(tmp_path: Path) -> None:
    """create_upload_link carries pvl-core's ``write`` tag, so read-only mode
    (``mcp.disable(tags={"write"})``) must hide it while download stays."""
    server = make_server(
        transport="http",
        config=_config(tmp_path, base_url="https://mcp.example.com", read_only=True),
    )
    assert not await _has_tool(server, "create_upload_link")
    assert await _has_tool(server, "create_download_link")


@pytest.mark.parametrize("path", ["note.md", "pic.png"])
@pytest.mark.parametrize("protected", [True, False])
async def test_create_upload_link_checks_existing_destination(
    tmp_path: Path, path: str, protected: bool
) -> None:
    """The live tool refuses existing targets before minting a capability."""
    (tmp_path / path).write_bytes(b"original")
    config = replace(
        _config(tmp_path, base_url="https://mcp.example.com"),
        write_protect_existing=protected,
    )
    server = make_server(transport="http", config=config)
    async with Client(server) as client:
        if protected:
            with pytest.raises(ToolError, match="upload links require a new path"):
                await client.call_tool("create_upload_link", {"ref": path})
        else:
            result = await client.call_tool("create_upload_link", {"ref": path})
            assert result.data["url"].startswith("https://mcp.example.com/transfer/")
    assert (tmp_path / path).read_bytes() == b"original"


@pytest.mark.parametrize("path", ["new.md", "new.png"])
@pytest.mark.parametrize("replay", [False, True])
async def test_upload_conflict_returns_409(
    tmp_path: Path, path: str, replay: bool
) -> None:
    """Late creates and upload retries return a conflict without spending the link."""
    server = make_server(
        transport="http",
        config=_config(tmp_path, base_url="https://mcp.example.com"),
    )
    async with (
        Client(server) as client,
        AsyncClient(transport=ASGITransport(app=server.http_app())) as http,
    ):
        result = await client.call_tool("create_upload_link", {"ref": path})
        url = result.data["url"]
        if replay:
            response = await http.post(url, content=b"original")
            assert response.status_code == 200
        else:
            (tmp_path / path).write_bytes(b"original")

        response = await http.post(url, content=b"replacement")
        assert response.status_code == 409
        assert response.content == b""
        assert (tmp_path / path).read_bytes() == b"original"

        # The route releases the reservation on conflict. Once the destination
        # is free again, the same live token can complete the upload.
        (tmp_path / path).unlink()
        response = await http.put(url, content=b"replacement")
        assert response.status_code == 200
        assert response.json() == {"path": path, "bytes": len(b"replacement")}
        assert (tmp_path / path).read_bytes() == b"replacement"
