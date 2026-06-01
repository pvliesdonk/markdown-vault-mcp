"""Tests for the MV-side file-exchange upload receiver.

Covers :mod:`markdown_vault_mcp.uploads`, which provides the callables
passed to pvl-core's ``register_file_exchange_upload(receiver=..., pre_link_validator=...)``.
The receiver dispatches by extension to ``Collection.write`` (for
``.md`` paths) or ``Collection.write_attachment`` (for binaries).  The
pre-link validator rejects bad ``target_id``s at link-creation time so
the agent gets ``ValueError`` in-band instead of after a wasted HTTP POST.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
import pytest
from fastmcp import Client
from fastmcp_pvl_core import UploadRecord

from markdown_vault_mcp import _server_deps, uploads
from markdown_vault_mcp.collection import Collection
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
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
    # Pollution from these would let the wiring "work" even if make_server
    # regressed back to transport="auto".  Cleared so transport-arg tests
    # exercise the explicit pass-through.
    "MARKDOWN_VAULT_MCP_TRANSPORT",
    "FASTMCP_TRANSPORT",
)


def _make_record(target_id: str) -> UploadRecord:
    """Build an UploadRecord with arbitrary-but-valid runtime guards.

    The receiver only consumes ``target_id`` — ``max_bytes`` and
    ``expires_at`` are enforced by the HTTP route before dispatch.
    """
    return UploadRecord(
        target_id=target_id,
        max_bytes=1024 * 1024,
        extra={},
        expires_at=1e12,
    )


class TestVaultUploadReceiver:
    """``_vault_upload_receiver`` dispatches by extension to write or write_attachment."""

    def test_md_path_dispatches_to_write(self, tmp_path: Path) -> None:
        """A ``.md`` target is decoded as utf-8 and committed via ``Collection.write``."""
        col = Collection(source_dir=tmp_path, read_only=False)
        col.build_index()
        saved = _server_deps._collection_singleton
        _server_deps.set_collection_singleton(col)
        try:
            payload = b"# Note\n\nbody\n"
            record = _make_record("note.md")
            result = uploads._vault_upload_receiver(record, payload)
            assert result == {"path": "note.md", "size_bytes": len(payload)}
            assert (tmp_path / "note.md").read_text() == "# Note\n\nbody\n"
        finally:
            col.close()
            _server_deps._collection_singleton = saved

    def test_binary_path_dispatches_to_write_attachment(self, tmp_path: Path) -> None:
        """A non-``.md`` target is committed verbatim via ``Collection.write_attachment``."""
        col = Collection(
            source_dir=tmp_path,
            read_only=False,
            attachment_extensions=["pdf"],
        )
        col.build_index()
        saved = _server_deps._collection_singleton
        _server_deps.set_collection_singleton(col)
        try:
            payload = b"%PDF-1.4 fake bytes \x00\x01\x02"
            record = _make_record("doc.pdf")
            result = uploads._vault_upload_receiver(record, payload)
            assert result == {"path": "doc.pdf", "size_bytes": len(payload)}
            assert (tmp_path / "doc.pdf").read_bytes() == payload
        finally:
            col.close()
            _server_deps._collection_singleton = saved

    def test_attachment_upload_bypasses_max_attachment_size_mb(
        self, tmp_path: Path
    ) -> None:
        """Receiver path commits attachments over MAX_ATTACHMENT_SIZE_MB.

        The cap protects LLM context for base64 callers of the MCP write
        tool.  Upload-link uploads flow over HTTP and are gated by
        UPLOAD_MAX_BYTES, so the inner cap must NOT apply here.
        """
        col = Collection(
            source_dir=tmp_path,
            read_only=False,
            attachment_extensions=["pdf"],
            max_attachment_size_mb=0.000001,
        )
        col.build_index()
        saved = _server_deps._collection_singleton
        _server_deps.set_collection_singleton(col)
        try:
            payload = b"%PDF-1.4 " + b"\x00" * 1024
            record = _make_record("big.pdf")
            result = uploads._vault_upload_receiver(record, payload)
            assert result == {"path": "big.pdf", "size_bytes": len(payload)}
            assert (tmp_path / "big.pdf").read_bytes() == payload
        finally:
            col.close()
            _server_deps._collection_singleton = saved


class TestValidateUploadTarget:
    """``_validate_upload_target`` rejects bad paths at link-creation time."""

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        """A ``..``-bearing target_id raises ``ValueError`` defensively.

        pvl-core's ``ExchangeURI.validate_segment`` would already block
        this upstream, but the validator must defend in depth in case the
        pre-link validator ever runs against an unvalidated source.
        """
        col = Collection(source_dir=tmp_path, read_only=False)
        col.build_index()
        saved = _server_deps._collection_singleton
        _server_deps.set_collection_singleton(col)
        try:
            with pytest.raises(ValueError):
                uploads._validate_upload_target("../etc/passwd", None)
        finally:
            col.close()
            _server_deps._collection_singleton = saved

    def test_disallowed_extension_rejected(self, tmp_path: Path) -> None:
        """An attachment whose extension is not in the allowlist is rejected."""
        col = Collection(
            source_dir=tmp_path,
            read_only=False,
            attachment_extensions=["pdf"],
        )
        col.build_index()
        saved = _server_deps._collection_singleton
        _server_deps.set_collection_singleton(col)
        try:
            with pytest.raises(ValueError):
                uploads._validate_upload_target("malware.exe", None)
        finally:
            col.close()
            _server_deps._collection_singleton = saved

    def test_md_path_accepted(self, tmp_path: Path) -> None:
        """A bare ``.md`` filename passes validation without raising."""
        col = Collection(source_dir=tmp_path, read_only=False)
        col.build_index()
        saved = _server_deps._collection_singleton
        _server_deps.set_collection_singleton(col)
        try:
            uploads._validate_upload_target("note.md", None)
        finally:
            col.close()
            _server_deps._collection_singleton = saved

    def test_allowed_attachment_accepted(self, tmp_path: Path) -> None:
        """An attachment whose extension is in the allowlist passes validation."""
        col = Collection(
            source_dir=tmp_path,
            read_only=False,
            attachment_extensions=["pdf"],
        )
        col.build_index()
        saved = _server_deps._collection_singleton
        _server_deps.set_collection_singleton(col)
        try:
            uploads._validate_upload_target("file.pdf", None)
        finally:
            col.close()
            _server_deps._collection_singleton = saved


class TestUploadEndToEnd:
    """``create_upload_link`` → POST → file in vault → readable via ``read``.

    Catches wiring bugs that the receiver/validator unit tests above
    cannot — for example, the route not being mounted, the receiver not
    being supplied to :func:`register_file_exchange_upload`, or the
    pre-link validator running at the wrong stage.

    Mirrors :class:`tests.test_artifacts.TestCreateServerArtifactRoute`'s
    end-to-end pattern for the inverse (download) direction.
    """

    @pytest.fixture
    def _upload_vault(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Writable vault and upload-enabling env vars for end-to-end tests."""
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        # BASE_URL is required by register_file_exchange_upload to mount
        # the route — without it the helper logs a warning and returns a
        # disabled handle.
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "http://test.invalid")
        # ``make_server`` derives ``fx_transport`` from its own ``transport``
        # arg and passes it to ``register_file_exchange_upload`` — no
        # TRANSPORT env var needed.  ``_CLEAR_VARS`` already strips
        # ``MARKDOWN_VAULT_MCP_TRANSPORT`` and ``FASTMCP_TRANSPORT`` to
        # make sure the test exercises the arg-driven path.
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        return vault

    @pytest.mark.skip(
        reason=(
            "MCP-layer search after upload races the writer thread until "
            "the readiness layer pairs with the writer drain (#559 Task 11/12)."
        ),
    )
    async def test_md_upload_round_trip(self, _upload_vault: Path) -> None:
        """Mint URL → POST bytes → file lands in vault → readable via ``read``.

        Uses the FastMCP in-process :class:`Client` to drive the lifespan
        (which sets the :class:`Collection` singleton the receiver needs)
        and to call ``create_upload_link`` / ``read``; uses an
        :class:`httpx.AsyncClient` over :class:`httpx.ASGITransport` to
        POST against the same FastMCP server's HTTP app.  Both paths
        share the module-level :class:`UploadStore` singleton captured
        by the route closure at registration time.
        """
        server = make_server(transport="http")
        async with Client(server) as client:
            # 1. Mint a one-time upload URL via the MCP tool.  pvl-core's
            #    ExchangeURI.validate_segment requires target_id to be a
            #    bare safe filename (no slashes, no ``..``); receivers
            #    that interpret it as a path build the full path
            #    themselves from the bare name.
            mint_result = await client.call_tool(
                "create_upload_link", {"target_id": "uploaded.md"}
            )
            data = json.loads(mint_result.content[0].text)
            assert data["target_id"] == "uploaded.md"
            assert data["expires_in_seconds"] > 0
            assert data["upload_url"].startswith(
                "http://test.invalid/markdown-vault-mcp/uploads/"
            )

            # 2. POST raw bytes to the minted URL.  The upload_url is built
            #    against the configured BASE_URL; strip it down to the path
            #    component so the in-process ASGI transport handles it.
            upload_path = urlsplit(data["upload_url"]).path
            payload = b"# Uploaded\n\nVia HTTP POST.\n"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.http_app()),
                base_url="http://test.invalid",
            ) as http:
                response = await http.post(upload_path, content=payload)

            # 3. Verify the receiver returned the expected JSON shape.
            assert response.status_code == 200
            body = response.json()
            assert body == {"path": "uploaded.md", "size_bytes": len(payload)}

            # 4. Verify the file actually landed in the vault on disk.
            assert (_upload_vault / "uploaded.md").read_bytes() == payload

            # 5. Verify it is now readable via the ``read`` MCP tool.
            #    ``read`` for a whole-document .md reads disk directly,
            #    so this only proves the file landed on disk.
            read_result = await client.call_tool("read", {"path": "uploaded.md"})
            read_data = json.loads(read_result.content[0].text)
            assert read_data["path"] == "uploaded.md"
            assert read_data["content"] == payload.decode("utf-8")

            # 6. Verify the upload is searchable — proves Collection.write
            #    upserted the FTS index synchronously.  A regression that
            #    drops ``self._fts.upsert_note(note)`` would fail this.
            search_result = await client.call_tool("search", {"query": "Uploaded"})
            search_data = json.loads(search_result.content[0].text)
            assert isinstance(search_data, list)
            search_paths = {r["path"] for r in search_data}
            assert "uploaded.md" in search_paths, (
                f"uploaded.md should be searchable after upload; got {search_paths}"
            )

    async def test_invalid_utf8_md_upload_returns_400(
        self, _upload_vault: Path
    ) -> None:
        """Non-UTF-8 bytes on a ``.md`` upload surface as HTTP 400 to the agent.

        ``_vault_upload_receiver`` calls ``body.decode("utf-8")`` on
        ``.md`` paths.  Invalid UTF-8 raises ``UnicodeDecodeError``,
        which is a subclass of ``ValueError``; pvl-core's
        ``_upload_handler`` maps ``ValueError`` to HTTP 400 with the
        exception message as body.  This test guards the docs claim at
        ``docs/tools/index.md`` that decode failures surface as 400 (not
        500): if pvl-core ever changes the mapping, this test fails and
        the docs need updating.
        """
        server = make_server(transport="http")
        async with Client(server) as client:
            mint_result = await client.call_tool(
                "create_upload_link", {"target_id": "bad.md"}
            )
            data = json.loads(mint_result.content[0].text)
            upload_path = urlsplit(data["upload_url"]).path
            invalid_utf8 = b"\xff\xfe\xfd not valid utf-8 \xc3\x28"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.http_app()),
                base_url="http://test.invalid",
            ) as http:
                response = await http.post(upload_path, content=invalid_utf8)
            assert response.status_code == 400, (
                f"expected 400 for invalid UTF-8 .md upload, "
                f"got {response.status_code}: {response.text}"
            )

    @pytest.mark.parametrize("http_transport", ["http", "sse", "streamable-http"])
    async def test_create_upload_link_registered_via_make_server_arg(
        self, _upload_vault: Path, http_transport: str
    ) -> None:
        """``create_upload_link`` is registered for every HTTP-flavoured transport.

        Regression test for the ``transport="auto"`` footgun: pvl-core's
        ``register_file_exchange_upload(transport="auto")`` reads
        ``MARKDOWN_VAULT_MCP_TRANSPORT`` / ``FASTMCP_TRANSPORT`` env vars
        — neither of which the CLI sets when the operator runs
        ``markdown-vault-mcp serve --transport http``.  Leaving ``"auto"``
        would silently disable upload in production.  ``make_server`` must
        derive ``fx_transport`` from its own ``transport`` arg and pass it
        through explicitly.

        Also a regression test for #459: ``"streamable-http"`` previously
        fell through to ``fx_transport="stdio"`` and disabled the upload
        route silently for third-party embedders calling ``make_server``
        with that value.

        ``_upload_vault`` clears both env vars (see ``_CLEAR_VARS``) so
        this test fails if ``make_server`` ever regresses to passing
        ``"auto"``.
        """
        server = make_server(transport=http_transport)
        async with Client(server) as client:
            tools = await client.list_tools()
            tool_names = {tool.name for tool in tools}
            assert "create_upload_link" in tool_names, (
                f"create_upload_link not registered for transport={http_transport!r}. "
                f"Registered tools: {sorted(tool_names)}"
            )
