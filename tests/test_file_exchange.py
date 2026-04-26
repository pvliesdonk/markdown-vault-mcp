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

    async def test_capability_absent_when_no_transfers_available(
        self, _no_exchange_env: Path
    ) -> None:
        """stdio + no MCP_EXCHANGE_DIR = no transfers, so don't advertise.

        Spec §3.9 frames the capability as "I can move bytes via at
        least one transport".  Registering with an empty
        ``transfer_methods`` would tell peers "I speak v0.3" while
        offering them no way to actually transfer — misleading.
        """
        server = make_server(transport="stdio")
        async with Client(server) as client:
            init = client.initialize_result
            assert init is not None
            caps = init.capabilities.experimental or {}
        assert "file_exchange" not in caps


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

    async def test_extension_extracted_from_filename_not_path(
        self,
        _exchange_env: tuple[Path, Path],
    ) -> None:
        """Dotted directory names don't break the exchange URI's extension.

        Regression for Gemini's e92e98b finding: ``path.rsplit(".", 1)``
        would have given ``"assets/diagram"`` (with a slash!) as the
        "extension" for ``my.assets/diagram.png``.  ``PurePosixPath(path).name``
        isolates the basename first.
        """
        vault, exchange_dir = _exchange_env
        # Plant a file under a dotted directory name.
        dotted_dir = vault / "my.assets"
        dotted_dir.mkdir()
        (dotted_dir / "diagram.png").write_bytes(b"\x89PNG" + b"\x00" * 16)

        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "my.assets/diagram.png"})
        data = result.structured_content
        assert data is not None
        assert "file_ref" in data
        uri = data["file_ref"]["transfer"]["exchange"]["uri"]
        # URI must end in `.png`, not `.assets/diagram.png` or similar
        # mangled form.
        assert uri.endswith(".png"), f"unexpected exchange URI: {uri!r}"
        # And the on-disk file the URI points at should exist.
        files = list((exchange_dir / "markdown-vault-mcp").glob("*.png"))
        assert len(files) == 1

    async def test_stdio_file_ref_omits_http_transfer(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        """On stdio, ``create_download_link`` isn't registered.

        The per-result ``file_ref.transfer`` must therefore omit the
        ``http`` entry so consumers don't try to invoke a tool that
        doesn't exist on this server.  ``test_capability_stdio_omits_http``
        covers the capability declaration; this asserts the same gate
        applies to individual ``read`` results.
        """
        server = make_server(transport="stdio")
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "assets/image.png"})
        data = result.structured_content
        assert data is not None
        assert "file_ref" in data
        assert "exchange" in data["file_ref"]["transfer"]
        assert "http" not in data["file_ref"]["transfer"]

    async def test_http_no_base_url_file_ref_omits_http_transfer(
        self,
        _exchange_env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP transport without BASE_URL: tool not registered, http omitted."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_BASE_URL", raising=False)
        server = make_server(transport="http")
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "assets/image.png"})
        data = result.structured_content
        assert data is not None
        assert "file_ref" in data
        assert "http" not in data["file_ref"]["transfer"]
        assert "exchange" in data["file_ref"]["transfer"]


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

    async def test_fetch_exchange_size_limit_pre_flight_blocks_oversize(
        self,
        _exchange_env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Oversized exchange files are rejected BEFORE reading into memory.

        Regression for the security-high gemini-code-assist finding: a
        malicious peer publishing a multi-GB file would exhaust this
        server's memory if we read the bytes before checking size.
        Stat the on-disk file first so the limit is enforced at the
        syscall layer.
        """
        _vault, exchange_dir = _exchange_env
        # 1 KB cap; write a 100 KB exchange file directly.
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "0.001")
        # Build the server first so FileExchange writes ``.exchange-id``.
        server = make_server(transport="http")
        exchange_id = (exchange_dir / ".exchange-id").read_text().strip()
        ns_dir = exchange_dir / "markdown-vault-mcp"
        ns_dir.mkdir(exist_ok=True)
        (ns_dir / "huge.png").write_bytes(b"\x00" * 100_000)
        uri = f"exchange://{exchange_id}/markdown-vault-mcp/huge.png"

        async with Client(server) as client:
            with pytest.raises(ToolError, match="exceeds the attachment size limit"):
                await client.call_tool("fetch", {"url": uri, "path": "assets/huge.png"})

    async def test_fetch_exchange_size_limit_skipped_for_markdown(
        self,
        _exchange_env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Markdown targets bypass the attachment size cap (notes are text)."""
        _vault, exchange_dir = _exchange_env
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "0.001")
        server = make_server(transport="http")
        exchange_id = (exchange_dir / ".exchange-id").read_text().strip()
        ns_dir = exchange_dir / "markdown-vault-mcp"
        ns_dir.mkdir(exist_ok=True)
        body = b"# Big Note\n\n" + (b"text " * 1000)
        (ns_dir / "big.md").write_bytes(body)
        uri = f"exchange://{exchange_id}/markdown-vault-mcp/big.md"

        async with Client(server) as client:
            result = await client.call_tool(
                "fetch", {"url": uri, "path": "imported/big.md"}
            )
        assert result.structured_content is not None
        assert result.structured_content["content_length"] == len(body)


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


# ---------------------------------------------------------------------------
# Sweep timer lifecycle
# ---------------------------------------------------------------------------


class TestSweepTimerLifecycle:
    """The sweep timer cleanly stops even when a tick is mid-execution.

    Regression for the race gemini-code-assist flagged: ``stop_sweep_timer``
    must beat ``_arm`` re-installing a fresh timer when ``_tick`` is in
    the middle of ``fx.sweep()``.  The stop event makes that win
    deterministic.
    """

    def test_start_sweep_timer_rejects_non_positive_interval(self) -> None:
        """``start_sweep_timer`` validates ``interval_s`` explicitly.

        Without the check, a zero or negative interval would surface as
        a ``ValueError`` from inside ``threading.Timer`` (deep in the
        lifespan), which is much harder to trace back to the bad
        config.  Fail loudly at the entry point instead.
        """
        from unittest.mock import MagicMock

        from markdown_vault_mcp import _file_exchange as fx_mod

        fx = MagicMock()
        fx.is_configured = True
        with pytest.raises(ValueError, match="strictly positive"):
            fx_mod.start_sweep_timer(fx, interval_s=0)
        with pytest.raises(ValueError, match="strictly positive"):
            fx_mod.start_sweep_timer(fx, interval_s=-5.0)

    def test_stat_exchange_uri_rejects_cross_group_uri(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Group-id mismatch returns None — mirrors ``read_exchange_uri``.

        Even if a peer in a different exchange group happens to share
        our base_dir (operator misconfig), exposing its file metadata
        would leak existence across groups.  ``stat_exchange_uri``
        guards against this with the same group check ``read_exchange_uri``
        applies, instead of just relying on the layout/path checks.
        """
        from markdown_vault_mcp import _file_exchange as fx_mod

        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()
        ns_dir = exchange_dir / "markdown-vault-mcp"
        ns_dir.mkdir()
        (ns_dir / "neighbour.png").write_bytes(b"\x00" * 100)

        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("MCP_EXCHANGE_DIR", str(exchange_dir))
        monkeypatch.setenv("MCP_EXCHANGE_NAMESPACE", "markdown-vault-mcp")
        from fastmcp_pvl_core import FileExchange

        fx = FileExchange.from_env(default_namespace="markdown-vault-mcp")
        assert fx.is_configured
        fx_mod.set_file_exchange(fx)

        # Build a URI with a fake exchange-id from a different group,
        # but pointing at a real on-disk file under our base_dir.
        wrong_group = "00000000-0000-0000-0000-000000000000"
        assert wrong_group != fx.exchange_id
        uri = f"exchange://{wrong_group}/markdown-vault-mcp/neighbour.png"

        assert fx_mod.stat_exchange_uri(uri) is None

    def test_stat_exchange_uri_returns_none_for_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-regular files (directories, FIFOs, etc.) return None.

        A directory's ``st_size`` is platform-dependent garbage; a
        FIFO's stat would block.  Only sizes for actual files are
        meaningful to the size pre-flight callers.
        """
        from markdown_vault_mcp import _file_exchange as fx_mod

        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()
        ns_dir = exchange_dir / "markdown-vault-mcp"
        ns_dir.mkdir()
        # Filename-shaped subdirectory inside the namespace dir — passes
        # spec §6.3 segment validation but is not a regular file.
        (ns_dir / "looksfile.png").mkdir()

        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("MCP_EXCHANGE_DIR", str(exchange_dir))
        monkeypatch.setenv("MCP_EXCHANGE_NAMESPACE", "markdown-vault-mcp")
        from fastmcp_pvl_core import FileExchange

        fx = FileExchange.from_env(default_namespace="markdown-vault-mcp")
        assert fx.is_configured
        fx_mod.set_file_exchange(fx)

        exchange_id = (exchange_dir / ".exchange-id").read_text().strip()
        uri = f"exchange://{exchange_id}/markdown-vault-mcp/looksfile.png"

        assert fx_mod.stat_exchange_uri(uri) is None

    def test_stat_exchange_uri_blocks_traversal_via_symlink(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Defence-in-depth: a symlink in a namespace dir can't escape base_dir.

        ``ExchangeURI.parse`` already rejects ``..`` segments (spec §6.3),
        but a symlink under ``$base_dir/{namespace}/`` could still
        redirect a stat to outside the exchange root.  The
        ``is_relative_to`` check after ``resolve()`` catches this.
        """
        from markdown_vault_mcp import _file_exchange as fx_mod

        # Create an exchange dir + a real file outside it.
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()
        ns_dir = exchange_dir / "markdown-vault-mcp"
        ns_dir.mkdir()
        outside = tmp_path / "secret.png"
        outside.write_bytes(b"\x00" * 9999)

        # Plant a symlink inside the namespace dir that escapes the root.
        attack = ns_dir / "evil.png"
        attack.symlink_to(outside)

        # Boot a configured FileExchange pointed at the dir.
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("MCP_EXCHANGE_DIR", str(exchange_dir))
        monkeypatch.setenv("MCP_EXCHANGE_NAMESPACE", "markdown-vault-mcp")
        from fastmcp_pvl_core import FileExchange

        fx = FileExchange.from_env(default_namespace="markdown-vault-mcp")
        assert fx.is_configured
        fx_mod.set_file_exchange(fx)

        exchange_id = (exchange_dir / ".exchange-id").read_text().strip()
        uri = f"exchange://{exchange_id}/markdown-vault-mcp/evil.png"

        # Symlink resolves outside base_dir → returns None instead of leaking
        # the file size (which would prove the file's existence to a probe).
        assert fx_mod.stat_exchange_uri(uri) is None

    async def test_stop_during_sweep_does_not_re_arm(
        self, _exchange_env: tuple[Path, Path]
    ) -> None:
        from unittest.mock import patch

        from markdown_vault_mcp import _file_exchange as fx_mod

        # Build a server so the singleton is wired up.
        server = make_server(transport="http")
        async with Client(server) as client:
            # Ensure the lifespan has started — first call materialises it.
            await client.list_tools()

            fx = fx_mod.get_file_exchange()
            assert fx is not None and fx.is_configured

            # Inject a sweep that calls stop_sweep_timer mid-flight,
            # mirroring the lifespan teardown firing while a tick is
            # already running.
            real_sweep = fx.sweep
            sweep_calls = 0

            def _swept_then_stop() -> int:
                nonlocal sweep_calls
                sweep_calls += 1
                fx_mod.stop_sweep_timer()
                return real_sweep()

            with patch.object(fx, "sweep", side_effect=_swept_then_stop):
                # Arm a very short timer; the tick will fire, sweep,
                # stop itself, and MUST NOT re-arm.
                fx_mod.start_sweep_timer(fx, interval_s=0.01)
                # Give the timer thread time to fire.
                import time

                time.sleep(0.2)

            # The stop-event should be set; no live timer reference.
            assert fx_mod._sweep_stopped.is_set()
            assert fx_mod._sweep_timer is None
            # Sweep was called exactly once — no re-arm after stop.
            assert sweep_calls == 1
