"""The transfer-link registration: route mount, link tools, minter, snippet (#2318).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_transfer_register.py``.

``register_transfer_routes`` is the transfer feature's one entry point: it
builds the shared store, mounts the ``/transfer/{token}`` route, and registers
the ``create_download_link`` / ``create_upload_link`` tools. These tests pin the
**wiring** it owns — the store's grace-settle timing is proven at the store /
routes layer (``test_transfer_store``, ``test_transfer_routes``):

- **base_url guard**: an unset/blank ``base_url`` fails fast at *registration*,
  not at the first tool call.
- **Link tools**: each mints via the validated handle, echoes ``{url,
  expires_in_s}``, builds the URL from ``base_url`` (trailing slash stripped),
  and passes the correct ``kind`` to the validator.
- **TTL clamp**: an omitted TTL uses the configured default; a request over the
  max is clamped to the max; an in-range request is honoured — observed via the
  returned ``expires_in_s``.
- **Validator rejection** surfaces from the tool call.
- **End-to-end**: a minted download link redeems over real ASGI (tool → store →
  route → handler → sink), and a second redeem within the grace window still
  serves — confirming the grace-settle path is wired, not just the store.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import FunctionTool
from starlette.routing import Route

from markdown_vault_mcp._instructions_builder import (
    finalize_instructions,
    instructions_for,
)
from markdown_vault_mcp._tool_boundary import FAULT_MESSAGE, is_tool_boundary
from markdown_vault_mcp._tool_visibility import apply_tool_visibility
from markdown_vault_mcp._transfer_contract import TransferReadResult
from markdown_vault_mcp._transfer_links import (
    TransferLinks,
    add_transfer_workflow,
    build_transfer_links,
    register_transfer_routes,
)
from markdown_vault_mcp.config_sections import TransferConfig
from markdown_vault_mcp.config_sections.server import ServerConfig
from markdown_vault_mcp.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping


class _RecordingSink:
    """A sink that serves canned bytes and records the handles it is called with."""

    def __init__(self) -> None:
        self.read_handles: list[str] = []
        self.write_handles: list[str] = []

    async def read(self, handle: str) -> TransferReadResult:
        self.read_handles.append(handle)
        return TransferReadResult(b"BODY", "text/plain", "f.txt")

    async def write(self, handle: str, _body: bytes) -> Mapping[str, Any]:
        self.write_handles.append(handle)
        return {"stored": handle}


class _RecordingValidator:
    """Records (ref, kind) calls; encodes the kind into the returned handle.

    A ``ref`` of ``"bad"`` rejects the way the contract asks, with a
    ``ToolError`` at INFO. ``"value-error"`` rejects the way hooks used to,
    and ``"boom"`` is a bug in the hook: both are server faults now. The
    handle embeds the kind so a downstream sink assertion can prove which kind
    was minted.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, ref: str, kind: str) -> str:
        self.calls.append((ref, kind))
        if ref == "bad":
            raise ToolError(
                "No file at 'bad'. Pass the path of an existing file.",
                log_level=logging.INFO,
            )
        if ref == "value-error":
            raise ValueError("validator rejected ref")
        if ref == "boom":
            raise RuntimeError("internal detail /srv/secret")
        return f"handle:{ref}:{kind}"


def _tconfig(**overrides: Any) -> TransferConfig:
    base = {
        "ttl_default_s": 100.0,
        "ttl_max_s": 200.0,
        "grace_ttl_s": 60.0,
        "lease_s": 60.0,
        "max_upload_bytes": 1024,
    }
    base.update(overrides)
    return TransferConfig(**base)  # type: ignore[arg-type]


def _register(  # noqa: PLR0913, RUF100 -- keyword-only knobs mirror register_transfer_routes
    *,
    base_url: str | None = "https://x.example.com",
    transfer_config: TransferConfig | None = None,
    sink: _RecordingSink | None = None,
    validate: _RecordingValidator | None = None,
    download_note: str | None = None,
    upload_note: str | None = None,
) -> tuple[FastMCP, _RecordingSink, _RecordingValidator]:
    mcp = FastMCP("t")
    sink = sink or _RecordingSink()
    validate = validate or _RecordingValidator()
    config = ServerConfig(base_url=base_url, kv_store_url="memory://")
    register_transfer_routes(
        mcp,
        config,
        transfer_config or _tconfig(),
        sink=sink,
        validate=validate,
        download_note=download_note,
        upload_note=upload_note,
    )
    return mcp, sink, validate


def _build_links(
    *,
    base_url: str | None = "https://x.example.com",
    transfer_config: TransferConfig | None = None,
    sink: _RecordingSink | None = None,
) -> tuple[FastMCP, TransferLinks, _RecordingSink]:
    mcp = FastMCP("t")
    sink = sink or _RecordingSink()
    config = ServerConfig(base_url=base_url, kv_store_url="memory://")
    links = build_transfer_links(mcp, config, transfer_config or _tconfig(), sink=sink)
    return mcp, links, sink


def _transfer_route_count(mcp: FastMCP) -> int:
    """Count mounted ``/transfer/{token}`` routes in the assembled ASGI app."""
    app = mcp.http_app()
    return sum(
        1 for r in app.routes if isinstance(r, Route) and r.path == "/transfer/{token}"
    )


class TestBaseUrlGuard:
    def test_unset_base_url_raises_at_register(self) -> None:
        with pytest.raises(ConfigurationError, match="base_url"):
            _register(base_url=None)

    def test_blank_base_url_raises_at_register(self) -> None:
        # An empty string is as unusable as None for building link URLs.
        with pytest.raises(ConfigurationError, match="base_url"):
            _register(base_url="")


class TestToolRegistration:
    async def test_both_link_tools_registered(self) -> None:
        mcp, _, _ = _register()
        # get_tool raises KeyError if the name is not registered.
        assert await mcp.get_tool("create_download_link") is not None
        assert await mcp.get_tool("create_upload_link") is not None

    async def test_download_link_has_annotations(self) -> None:
        mcp, _, _ = _register()
        tool = await mcp.get_tool("create_download_link")
        assert tool.annotations is not None
        assert tool.annotations.title == "Create Download Link"
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False

    async def test_upload_link_has_annotations(self) -> None:
        mcp, _, _ = _register()
        tool = await mcp.get_tool("create_upload_link")
        assert tool.annotations is not None
        assert tool.annotations.title == "Create Upload Link"
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False

    async def test_download_link_has_icon(self) -> None:
        mcp, _, _ = _register()
        tool = await mcp.get_tool("create_download_link")
        assert tool.icons is not None
        assert len(tool.icons) == 1
        assert tool.icons[0].src.startswith("data:image/svg+xml;base64,")
        assert tool.icons[0].mime_type == "image/svg+xml"

    async def test_upload_link_has_icon(self) -> None:
        mcp, _, _ = _register()
        tool = await mcp.get_tool("create_upload_link")
        assert tool.icons is not None
        assert len(tool.icons) == 1
        assert tool.icons[0].src.startswith("data:image/svg+xml;base64,")
        assert tool.icons[0].mime_type == "image/svg+xml"

    async def test_upload_link_has_write_tag(self) -> None:
        mcp, _, _ = _register()
        tool = await mcp.get_tool("create_upload_link")
        assert "write" in tool.tags

    async def test_download_link_has_no_write_tag(self) -> None:
        mcp, _, _ = _register()
        tool = await mcp.get_tool("create_download_link")
        assert "write" not in tool.tags


class TestDomainNotes:
    # A verbatim fragment from the *second* paragraph of both tool docstrings.
    # Asserting a second-paragraph fragment (not just the opening sentence) is
    # what makes these tests catch a regression that drops the whole body or
    # truncates it to the first paragraph.
    _BODY = "\n\nUse it to "

    async def test_download_note_appended_after_body(self) -> None:
        mcp, _, _ = _register(download_note="Refs are vault-relative paths.")
        desc = (await mcp.get_tool("create_download_link")).description
        assert desc.startswith("Create a download URL for a file")
        assert self._BODY in desc  # generic body survives in full
        assert "Args:" not in desc  # the Args: entries became parameter text
        assert desc.endswith("\n\nRefs are vault-relative paths.")

    async def test_upload_note_appended_after_body(self) -> None:
        mcp, _, _ = _register(upload_note="Dest must be an allowed extension.")
        desc = (await mcp.get_tool("create_upload_link")).description
        assert desc.startswith("Create an upload URL for a file")
        assert self._BODY in desc
        assert desc.endswith("\n\nDest must be an allowed extension.")

    async def test_notes_do_not_cross_tools(self) -> None:
        mcp, _, _ = _register(download_note="DOWN_ONLY", upload_note="UP_ONLY")
        down = (await mcp.get_tool("create_download_link")).description
        up = (await mcp.get_tool("create_upload_link")).description
        assert "DOWN_ONLY" in down
        assert "DOWN_ONLY" not in up
        assert "UP_ONLY" in up
        assert "UP_ONLY" not in down

    async def test_omitted_notes_leave_generic_description(self) -> None:
        # No note: the description is exactly the docstring, no trailing blank
        # paragraph, no injected text.
        mcp, _, _ = _register()
        desc = (await mcp.get_tool("create_download_link")).description
        assert desc.startswith("Create a download URL for a file")
        assert self._BODY in desc
        assert desc == desc.strip()  # no stray leading/trailing whitespace

    async def test_blank_note_is_ignored(self) -> None:
        # Whitespace-only note is treated as absent — no dangling "\n\n".
        mcp, _, _ = _register(download_note="   ")
        desc = (await mcp.get_tool("create_download_link")).description
        assert desc == desc.strip()
        assert not desc.endswith("\n")


class TestLinkMinting:
    async def test_download_link_shape_and_kind(self) -> None:
        mcp, _, validate = _register()
        res = await mcp.call_tool("create_download_link", {"ref": "doc1"})
        payload = res.structured_content
        assert payload["url"].startswith("https://x.example.com/transfer/")
        assert payload["expires_in_s"] == 100.0  # the configured default
        assert validate.calls == [("doc1", "download")]

    async def test_upload_link_uses_upload_kind(self) -> None:
        mcp, _, validate = _register()
        await mcp.call_tool("create_upload_link", {"ref": "dest1"})
        assert validate.calls == [("dest1", "upload")]

    async def test_base_url_trailing_slash_stripped(self) -> None:
        mcp, _, _ = _register(base_url="https://x.example.com/")
        res = await mcp.call_tool("create_download_link", {"ref": "doc1"})
        # Exactly one slash between host and the route — no "//transfer".
        assert "/transfer/" in res.structured_content["url"]
        assert "com//transfer" not in res.structured_content["url"]


class TestTtlClamp:
    async def test_omitted_ttl_uses_default(self) -> None:
        mcp, _, _ = _register()
        res = await mcp.call_tool("create_download_link", {"ref": "doc"})
        assert res.structured_content["expires_in_s"] == 100.0

    async def test_over_max_ttl_is_clamped(self) -> None:
        mcp, _, _ = _register()
        res = await mcp.call_tool("create_download_link", {"ref": "doc", "ttl_s": 9999})
        assert res.structured_content["expires_in_s"] == 200.0  # the max

    async def test_in_range_ttl_is_honoured(self) -> None:
        mcp, _, _ = _register()
        res = await mcp.call_tool("create_download_link", {"ref": "doc", "ttl_s": 150})
        assert res.structured_content["expires_in_s"] == 150.0

    @pytest.mark.parametrize("tool", ["create_download_link", "create_upload_link"])
    @pytest.mark.parametrize("ttl_s", [0, -5, float("nan")])
    async def test_non_positive_ttl_is_a_request_to_change(
        self, tool: str, ttl_s: float
    ) -> None:
        # The clamp only bounds the ceiling; a non-positive request would mint a
        # dead link. The tool rejects it before validating, as a request the
        # model must change (INFO), naming the argument to fix.
        mcp, _, validate = _register()
        with pytest.raises(ToolError) as info:
            await mcp.call_tool(tool, {"ref": "doc", "ttl_s": ttl_s})
        assert str(info.value) == (
            "ttl_s must be greater than 0; omit it to use the server's default "
            "link lifetime."
        )
        assert info.value.log_level == logging.INFO
        assert validate.calls == []


class TestValidatorRejection:
    """The ``validate`` hook's contract (ADR 0005, #364)."""

    @pytest.mark.parametrize("tool", ["create_download_link", "create_upload_link"])
    async def test_tool_error_is_the_rejection(self, tool: str, caplog) -> None:
        mcp, _, _ = _register()
        with (
            caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp"),
            pytest.raises(ToolError) as info,
        ):
            await mcp.call_tool(tool, {"ref": "bad"})
        assert str(info.value) == (
            "No file at 'bad'. Pass the path of an existing file."
        )
        assert info.value.log_level == logging.INFO
        assert [r for r in caplog.records if r.name.endswith("_tool_boundary")] == []

    @pytest.mark.parametrize("ref", ["value-error", "boom"])
    async def test_any_other_exception_is_a_fault(self, ref: str, caplog) -> None:
        mcp, _, _ = _register()
        with (
            caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp"),
            pytest.raises(ToolError) as info,
        ):
            await mcp.call_tool("create_download_link", {"ref": ref})
        assert str(info.value) == FAULT_MESSAGE
        (record,) = [r for r in caplog.records if r.name.endswith("_tool_boundary")]
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None

    @pytest.mark.parametrize("tool", ["create_download_link", "create_upload_link"])
    async def test_link_tools_carry_the_boundary(self, tool: str) -> None:
        mcp, _, _ = _register()
        registered = await mcp.get_tool(tool)
        assert isinstance(registered, FunctionTool)
        assert is_tool_boundary(registered.fn)


@asynccontextmanager
async def _client(mcp: FastMCP):
    """Yield an httpx client bound to the FastMCP ASGI app with lifespan active."""
    app = mcp.http_app()
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://tsvr") as client,
        app.router.lifespan_context(app),
    ):
        yield client


def _path_of(url: str) -> str:
    """Return the ``/transfer/{token}`` path from a minted absolute link URL."""
    return "/" + url.split("/", 3)[3]


class TestEndToEnd:
    async def test_minted_download_link_redeems_over_http(self) -> None:
        mcp, sink, _ = _register()
        res = await mcp.call_tool("create_download_link", {"ref": "doc"})
        path = _path_of(res.structured_content["url"])
        async with _client(mcp) as client:
            resp = await client.get(path)
        assert resp.status_code == 200
        assert resp.content == b"BODY"
        assert 'filename="f.txt"' in resp.headers["content-disposition"]
        # The handle the route handed the sink is the validator's download handle.
        assert sink.read_handles == ["handle:doc:download"]

    async def test_minted_upload_link_redeems_over_http(self) -> None:
        mcp, sink, _ = _register()
        res = await mcp.call_tool("create_upload_link", {"ref": "dest"})
        path = _path_of(res.structured_content["url"])
        async with _client(mcp) as client:
            resp = await client.put(path, content=b"PAYLOAD")
        assert resp.status_code == 200
        # The handler committed the body to the validator's upload handle.
        assert sink.write_handles == ["handle:dest:upload"]

    async def test_over_cap_upload_is_rejected_413(self) -> None:
        # Pins that register threads transfer_config.max_upload_bytes into the
        # handler: a tiny cap must reject an over-size body. If register dropped
        # or hardcoded the cap, this would not 413.
        mcp, sink, _ = _register(transfer_config=_tconfig(max_upload_bytes=4))
        res = await mcp.call_tool("create_upload_link", {"ref": "dest"})
        path = _path_of(res.structured_content["url"])
        async with _client(mcp) as client:
            resp = await client.put(path, content=b"way-too-long")
        assert resp.status_code == 413
        assert sink.write_handles == []  # over-cap body never reached the sink

    @pytest.mark.parametrize("method", ["DELETE", "PATCH"])
    async def test_body_carrying_unserved_method_gets_closing_405(
        self, method: str
    ) -> None:
        # DELETE/PATCH are in _ROUTE_METHODS so they reach the handler's own
        # 405 + ``Connection: close`` (an unserved method may carry an unread
        # body). Pins the register-side wiring: if a refactor dropped them from
        # the set, they would fall to Starlette's router 405 without the close.
        mcp, _, _ = _register()
        res = await mcp.call_tool("create_download_link", {"ref": "doc"})
        path = _path_of(res.structured_content["url"])
        async with _client(mcp) as client:
            resp = await client.request(method, path, content=b"body")
        assert resp.status_code == 405
        assert resp.headers["connection"] == "close"

    async def test_second_redeem_within_grace_still_serves(self) -> None:
        # Grace-settle wiring: complete() shrinks the TTL to the grace window
        # rather than burning the link, so a retry inside that window re-serves.
        # The test runs in milliseconds, far inside the 60s grace default.
        mcp, _, _ = _register()
        res = await mcp.call_tool("create_download_link", {"ref": "doc"})
        path = _path_of(res.structured_content["url"])
        async with _client(mcp) as client:
            first = await client.get(path)
            second = await client.get(path)
        assert first.status_code == 200
        assert second.status_code == 200


class TestBuildTransferLinksGuard:
    def test_unset_base_url_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="base_url"):
            _build_links(base_url=None)

    def test_blank_base_url_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="base_url"):
            _build_links(base_url="")


class TestBuildTransferLinksNoTools:
    async def test_registers_no_tools(self) -> None:
        # Path 2 mounts the route but registers no tools — the whole point.
        mcp, _, _ = _build_links()
        assert await mcp.list_tools() == []

    async def test_route_mounted_once(self) -> None:
        mcp, _, _ = _build_links()
        assert _transfer_route_count(mcp) == 1


class TestTransferLinksMinting:
    async def test_mint_download_shape(self) -> None:
        _, links, _ = _build_links()
        res = await links.mint_download("handle:doc:download")
        assert res["url"].startswith("https://x.example.com/transfer/")
        assert res["expires_in_s"] == 100.0  # the configured default

    async def test_mint_upload_shape(self) -> None:
        _, links, _ = _build_links()
        res = await links.mint_upload("handle:dest:upload")
        assert res["url"].startswith("https://x.example.com/transfer/")
        assert res["expires_in_s"] == 100.0

    async def test_base_url_trailing_slash_stripped(self) -> None:
        _, links, _ = _build_links(base_url="https://x.example.com/")
        res = await links.mint_download("h")
        assert "/transfer/" in res["url"]
        assert "com//transfer" not in res["url"]


class TestTransferLinksTtlClamp:
    async def test_omitted_ttl_uses_default(self) -> None:
        _, links, _ = _build_links()
        res = await links.mint_download("h")
        assert res["expires_in_s"] == 100.0

    async def test_over_max_ttl_is_clamped(self) -> None:
        _, links, _ = _build_links()
        res = await links.mint_download("h", ttl_s=9999)
        assert res["expires_in_s"] == 200.0  # the max

    async def test_in_range_ttl_is_honoured(self) -> None:
        _, links, _ = _build_links()
        res = await links.mint_download("h", ttl_s=150)
        assert res["expires_in_s"] == 150.0

    async def test_non_positive_ttl_is_rejected(self) -> None:
        # The clamp only bounds the ceiling; store.mint rejects a dead link.
        _, links, _ = _build_links()
        with pytest.raises(ValueError):
            await links.mint_download("h", ttl_s=0)


class TestPurePath2EndToEnd:
    async def test_minted_link_redeems_over_http(self) -> None:
        mcp, links, sink = _build_links()
        res = await links.mint_download("handle:doc:download")
        async with _client(mcp) as client:
            resp = await client.get(_path_of(res["url"]))
        assert resp.status_code == 200
        assert resp.content == b"BODY"
        assert sink.read_handles == ["handle:doc:download"]


class TestMixedMode:
    async def test_register_returns_transfer_links(self) -> None:
        mcp = FastMCP("t")
        config = ServerConfig(
            base_url="https://x.example.com", kv_store_url="memory://"
        )
        links = register_transfer_routes(
            mcp,
            config,
            _tconfig(),
            sink=_RecordingSink(),
            validate=_RecordingValidator(),
        )
        assert isinstance(links, TransferLinks)

    async def test_path1_and_path2_links_redeem_same_store(self) -> None:
        mcp = FastMCP("t")
        sink = _RecordingSink()
        config = ServerConfig(
            base_url="https://x.example.com", kv_store_url="memory://"
        )
        links = register_transfer_routes(
            mcp, config, _tconfig(), sink=sink, validate=_RecordingValidator()
        )
        p1 = await mcp.call_tool("create_download_link", {"ref": "doc1"})
        p2 = await links.mint_download("handle:doc2:download")
        async with _client(mcp) as client:
            r1 = await client.get(_path_of(p1.structured_content["url"]))
            r2 = await client.get(_path_of(p2["url"]))
        assert r1.status_code == 200
        assert r1.content == b"BODY"
        assert r2.status_code == 200
        assert r2.content == b"BODY"
        # Both links resolved against the one shared store/route/sink.
        assert sink.read_handles == ["handle:doc1:download", "handle:doc2:download"]

    async def test_register_mounts_route_once(self) -> None:
        mcp, _, _ = _register()
        assert _transfer_route_count(mcp) == 1


class TestAddTransferWorkflow:
    """The core-shaped capability-link prose, reusable by path-2 servers (#288)."""

    _TAIL_PAIR = (
        "Each link is a single-use capability URL that expires: do not reuse "
        "it, share it, or call the link tools speculatively."
    )
    _TAIL_ONE = (
        "The link is a single-use capability URL that expires: do not reuse "
        "it, share it, or call the tool speculatively."
    )

    @staticmethod
    def _finalize(mcp: FastMCP, **cfg: Any) -> str:
        instructions_for(mcp).identity("t", "T.")
        config = ServerConfig(kv_store_url="memory://", **cfg)
        apply_tool_visibility(mcp, config)
        return finalize_instructions(mcp, config, env_prefix="T")

    @staticmethod
    def _server(*tool_names: str) -> FastMCP:
        mcp = FastMCP("t")
        for name in tool_names:
            mcp.tool(name=name)(lambda: "x")
        return mcp

    def test_both_directions(self):
        mcp = self._server("push", "pull")
        add_transfer_workflow(mcp, upload_tool="push", download_tool="pull")
        text = self._finalize(mcp)
        assert text == (
            "t: T.\n\nTo upload a file, call push and then PUT the bytes to the "
            "returned URL; to download, call pull and GET the returned URL. "
            f"{self._TAIL_PAIR}"
        )

    def test_download_only(self):
        mcp = self._server("share_document")
        add_transfer_workflow(mcp, download_tool="share_document")
        text = self._finalize(mcp)
        assert text == (
            "t: T.\n\nTo download a file, call share_document and then GET the "
            f"returned URL. {self._TAIL_ONE}"
        )
        assert "upload" not in text

    def test_upload_only(self):
        mcp = self._server("ingest")
        add_transfer_workflow(mcp, upload_tool="ingest")
        text = self._finalize(mcp)
        assert text == (
            "t: T.\n\nTo upload a file, call ingest and then PUT the bytes to the "
            f"returned URL. {self._TAIL_ONE}"
        )
        assert "download" not in text

    def test_neither_name_is_a_configuration_error(self):
        with pytest.raises(ConfigurationError, match="at least one"):
            add_transfer_workflow(FastMCP("t"))

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_name_is_a_configuration_error(self, blank: str):
        with pytest.raises(ConfigurationError, match="at least one"):
            add_transfer_workflow(FastMCP("t"), upload_tool=blank)

    def test_snippet_is_pruned_when_a_named_tool_is_hidden(self):
        mcp = self._server("push", "pull")
        add_transfer_workflow(mcp, upload_tool="push", download_tool="pull")
        text = self._finalize(mcp, tools_deny=("pull",))
        assert text == "t: T."

    def test_snippet_is_pruned_when_the_named_tool_is_never_registered(self):
        mcp = self._server()
        add_transfer_workflow(mcp, download_tool="ghost")
        assert self._finalize(mcp) == "t: T."


class TestInstructionsSnippet:
    def test_workflow_snippet_present_when_both_tools_exposed(self) -> None:
        mcp, _, _ = _register()
        instructions_for(mcp).identity("t", "T.")
        text = finalize_instructions(
            mcp,
            ServerConfig(base_url="https://x.example.com", kv_store_url="memory://"),
            env_prefix="T",
        )
        assert "create_upload_link" in text
        assert "create_download_link" in text
        assert "single-use" in text

    @pytest.mark.parametrize(
        "hidden_tool", ["create_upload_link", "create_download_link"]
    )
    def test_snippet_dropped_when_either_tool_hidden(self, hidden_tool: str) -> None:
        mcp, _, _ = _register()
        instructions_for(mcp).identity("t", "T.")
        cfg = ServerConfig(
            base_url="https://x.example.com",
            kv_store_url="memory://",
            tools_deny=(hidden_tool,),
        )
        apply_tool_visibility(mcp, cfg)
        text = finalize_instructions(mcp, cfg, env_prefix="T")
        assert "create_upload_link" not in text
        assert "create_download_link" not in text
