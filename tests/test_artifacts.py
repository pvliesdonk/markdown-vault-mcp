"""Tests for the create_download_link tool and artifact HTTP endpoint.

Covers MV's integration with :mod:`fastmcp_pvl_core._artifacts`:

- Tool registration (HTTP/SSE only, not stdio)
- Tool behaviour: valid paths, missing BASE_URL, non-existent paths,
  path traversal
- Artifact HTTP handler: serves eager bytes, one-time use, 404 on
  unknown tokens

The :class:`ArtifactStore` and :class:`TokenRecord` internals (UUID
generation, TTL expiry, cleanup) are covered in ``fastmcp-pvl-core``'s
own test suite — we only exercise the MV-side wiring here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp_pvl_core import ArtifactStore
from starlette.testclient import TestClient

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
    "MARKDOWN_VAULT_MCP_SERVER_NAME",
    "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
    "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
    "MARKDOWN_VAULT_MCP_BASE_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
)


@pytest.fixture
def _artifact_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Writable vault with a note and an attachment for artifact tests."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nSome content.\n", encoding="utf-8")
    (vault / "assets").mkdir()
    (vault / "assets" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://mcp.example.com")
    for var in _CLEAR_VARS:
        if var != "MARKDOWN_VAULT_MCP_BASE_URL":
            monkeypatch.delenv(var, raising=False)
    return vault


# ---------------------------------------------------------------------------
# create_download_link tool: registration
# ---------------------------------------------------------------------------


class TestCreateDownloadLinkRegistration:
    """create_download_link is only registered for non-stdio transports."""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note\n")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)

    async def test_not_registered_on_stdio(self) -> None:
        server = make_server(transport="stdio")
        async with Client(server) as client:
            tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "create_download_link" not in names

    async def test_registered_on_http(self) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "create_download_link" in names

    async def test_registered_on_sse(self) -> None:
        server = make_server(transport="sse")
        async with Client(server) as client:
            tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "create_download_link" in names


# ---------------------------------------------------------------------------
# create_download_link tool: functional
# ---------------------------------------------------------------------------


class TestCreateDownloadLinkTool:
    """Functional tests for the create_download_link tool."""

    async def test_returns_json_with_download_url(self, _artifact_vault: Path) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool("create_download_link", {"path": "note.md"})
        data = json.loads(result.content[0].text)
        assert "download_url" in data
        assert "expires_in_seconds" in data
        assert "path" in data
        assert "content_type" in data

    async def test_download_url_contains_base_url(self, _artifact_vault: Path) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool("create_download_link", {"path": "note.md"})
        data = json.loads(result.content[0].text)
        assert data["download_url"].startswith("https://mcp.example.com/artifacts/")

    async def test_content_type_markdown_for_notes(self, _artifact_vault: Path) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool("create_download_link", {"path": "note.md"})
        data = json.loads(result.content[0].text)
        assert data["content_type"] == "text/markdown; charset=utf-8"

    async def test_content_type_for_attachment(self, _artifact_vault: Path) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool(
                "create_download_link", {"path": "assets/image.png"}
            )
        data = json.loads(result.content[0].text)
        assert data["content_type"] == "image/png"

    async def test_expires_in_seconds_echoes_requested_ttl(
        self, _artifact_vault: Path
    ) -> None:
        """The response echoes the requested ``ttl_seconds`` exactly.

        Core's ArtifactStore now supports per-token TTL via
        :meth:`ArtifactStore.put_ephemeral`, so the value the caller
        asked for is the value they get back — no more "requested vs
        effective" gap.
        """
        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool(
                "create_download_link",
                {"path": "note.md", "ttl_seconds": 120},
            )
        data = json.loads(result.content[0].text)
        assert data["expires_in_seconds"] == 120

    async def test_raises_on_missing_base_url(
        self, _artifact_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without BASE_URL the server skips store construction and the tool fails.

        The route + module-level singleton are gated on ``base_url`` at
        ``make_server`` time (see :func:`server.make_server`), so calling
        the tool surfaces core's "ArtifactStore not configured" error
        rather than a tool-side BASE_URL check.
        """
        # Clear the singleton — earlier tests (including the autouse
        # _artifact_vault fixture) may have left a store registered, and
        # the production make_server skips construction when base_url is
        # absent, so the leftover would mask the failure path.
        import fastmcp_pvl_core._artifacts as _core_artifacts

        monkeypatch.setattr(_core_artifacts, "_artifact_store", None)
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_BASE_URL", raising=False)
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match="ArtifactStore not configured"):
                await client.call_tool("create_download_link", {"path": "note.md"})

    async def test_raises_on_zero_ttl(self, _artifact_vault: Path) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match="positive integer"):
                await client.call_tool(
                    "create_download_link", {"path": "note.md", "ttl_seconds": 0}
                )

    async def test_raises_on_negative_ttl(self, _artifact_vault: Path) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match="positive integer"):
                await client.call_tool(
                    "create_download_link", {"path": "note.md", "ttl_seconds": -60}
                )

    async def test_raises_on_nonexistent_path(self, _artifact_vault: Path) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match=r"not found|does not exist"):
                await client.call_tool("create_download_link", {"path": "missing.md"})

    async def test_raises_on_path_traversal(self, _artifact_vault: Path) -> None:
        server = make_server(transport="http")
        async with Client(server) as client:
            with pytest.raises(ToolError, match="traversal"):
                await client.call_tool(
                    "create_download_link",
                    {"path": "../../../etc/shadow.md"},
                )


# ---------------------------------------------------------------------------
# Artifact HTTP endpoint handler
# ---------------------------------------------------------------------------


class TestArtifactHandler:
    """Tests for the ``GET /artifacts/{token}`` route registered by core.

    The route is mounted by :meth:`ArtifactStore.register_route` at
    ``make_server`` time.  We build a minimal Starlette app with the
    same route + a shared :class:`ArtifactStore`, seed the store with
    :meth:`ArtifactStore.add`, and exercise the HTTP contract directly.
    """

    def _make_app(self, store: ArtifactStore) -> TestClient:
        from starlette.applications import Starlette
        from starlette.responses import Response
        from starlette.routing import Route

        async def handler(request):  # type: ignore[no-untyped-def]
            token = request.path_params.get("token", "")
            record = store.pop(token)
            if record is None:
                return Response(content="Not Found", status_code=404)
            return Response(
                content=record.content,
                media_type=record.mime_type,
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{record.filename}"'
                    ),
                },
            )

        app = Starlette(
            routes=[Route("/artifacts/{token}", endpoint=handler, methods=["GET"])]
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_serves_note_bytes(self) -> None:
        store = ArtifactStore()
        token = store.add(
            b"# Hello\n\nWorld.\n",
            filename="hello.md",
            mime_type="text/markdown; charset=utf-8",
        )
        client = self._make_app(store)

        response = client.get(f"/artifacts/{token}")

        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]
        assert (
            response.headers["content-disposition"] == 'attachment; filename="hello.md"'
        )
        assert response.text == "# Hello\n\nWorld.\n"

    def test_serves_attachment_bytes(self) -> None:
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        store = ArtifactStore()
        token = store.add(raw, filename="image.png", mime_type="image/png")
        client = self._make_app(store)

        response = client.get(f"/artifacts/{token}")

        assert response.status_code == 200
        assert "image/png" in response.headers["content-type"]
        assert (
            response.headers["content-disposition"]
            == 'attachment; filename="image.png"'
        )
        assert response.content == raw

    def test_one_time_use(self) -> None:
        store = ArtifactStore()
        token = store.add(b"hi", filename="f.md", mime_type="text/markdown")
        client = self._make_app(store)

        first = client.get(f"/artifacts/{token}")
        second = client.get(f"/artifacts/{token}")

        assert first.status_code == 200
        assert second.status_code == 404

    def test_unknown_token_returns_404(self) -> None:
        client = self._make_app(ArtifactStore())
        response = client.get("/artifacts/" + "deadbeef" * 4)
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# make_server: artifact route mounted on HTTP but not stdio
# ---------------------------------------------------------------------------


class TestCreateServerArtifactRoute:
    """make_server mounts the artifact route for HTTP transport only."""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note\n")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
        # The route is gated on base_url at make_server time, so the
        # registration tests need BASE_URL set even though they don't
        # invoke the tool.
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://mcp.example.com")
        for var in _CLEAR_VARS:
            if var != "MARKDOWN_VAULT_MCP_BASE_URL":
                monkeypatch.delenv(var, raising=False)

    def test_artifact_route_not_registered_for_stdio(self) -> None:
        server = make_server(transport="stdio")
        routes = server._additional_http_routes
        paths = [getattr(r, "path", "") for r in routes]
        assert not any("/artifacts/" in p for p in paths)

    def test_artifact_route_registered_for_http(self) -> None:
        server = make_server(transport="http")
        routes = server._additional_http_routes
        paths = [getattr(r, "path", "") for r in routes]
        assert any("/artifacts/" in p for p in paths)

    def test_artifact_route_end_to_end_via_http_app(self) -> None:
        """End-to-end smoke test that exercises core's register_route behaviour.

        Builds the ASGI app via ``mcp.http_app()`` — the same call path
        ``_cmd_serve`` uses — seeds the store via ``get_artifact_store().add``
        and hits the real mounted route.  Guards against divergence between
        TestArtifactHandler's hand-rolled handler and core's
        ArtifactStore.register_route implementation.
        """
        from fastmcp_pvl_core import get_artifact_store

        server = make_server(transport="http")
        store = get_artifact_store()
        token = store.add(
            b"integration",
            filename="it.md",
            mime_type="text/markdown; charset=utf-8",
        )
        with TestClient(server.http_app()) as client:
            response = client.get(f"/artifacts/{token}")

        assert response.status_code == 200
        assert response.content == b"integration"
        assert "text/markdown" in response.headers["content-type"]


class TestGetArtifactStoreUninitialised:
    """get_artifact_store() must error rather than silently return None."""

    def test_raises_runtime_error_when_store_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Any prior test / make_server call may have set the singleton;
        # clear it explicitly to exercise the uninitialised path. The
        # ``set_artifact_store`` helper only accepts a real store, so
        # poke the underlying module global directly via monkeypatch
        # (which restores it after the test).
        import fastmcp_pvl_core._artifacts as _core_artifacts
        from fastmcp_pvl_core import get_artifact_store

        monkeypatch.setattr(_core_artifacts, "_artifact_store", None)
        with pytest.raises(RuntimeError, match="ArtifactStore not configured"):
            get_artifact_store()
