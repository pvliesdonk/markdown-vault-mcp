"""Tests for ``register_server_info_tool``.

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_server_info.py`` (#2333).
"""

from __future__ import annotations

import logging

import mcp.types as mt
import pytest
from fastmcp import Client, FastMCP
from mcp.types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

from markdown_vault_mcp._pvl_core import CORE_VERSION as core_version
from markdown_vault_mcp._server_info import register_server_info_tool


async def _call(mcp: FastMCP, tool_name: str = "get_server_info") -> dict:
    result = await mcp.call_tool(tool_name, {})
    return result.structured_content


class TestRegisterServerInfoTool:
    async def test_required_fields_only(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.2.3",
            server_name="my-mcp",
        )
        payload = await _call(mcp)
        assert payload == {
            "server_name": "my-mcp",
            "server_version": "1.2.3",
            "core_version": core_version,
            "protocol": {
                "supported_versions": [
                    *HANDSHAKE_PROTOCOL_VERSIONS,
                    *MODERN_PROTOCOL_VERSIONS,
                ],
                # An in-process call has no connection to report.
                "version": None,
                "client": None,
            },
        }

    async def test_no_upstream_block_when_provider_omitted(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            upstream_label="paperless",  # ignored without provider
        )
        payload = await _call(mcp)
        assert "paperless" not in payload
        assert "upstream" not in payload

    async def test_upstream_string_wrapped_under_label(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="paperless-mcp",
            upstream_version=lambda: "v2.20.14",
            upstream_label="paperless",
        )
        payload = await _call(mcp)
        assert payload["paperless"] == {"version": "v2.20.14"}
        assert payload["server_name"] == "paperless-mcp"
        assert payload["server_version"] == "1.0.0"
        assert payload["core_version"] == core_version

    async def test_upstream_dict_used_as_is(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="paperless-mcp",
            upstream_version=lambda: {
                "version": "v2.20.14",
                "update_available": False,
            },
            upstream_label="paperless",
        )
        payload = await _call(mcp)
        assert payload["paperless"] == {
            "version": "v2.20.14",
            "update_available": False,
        }

    async def test_upstream_async_callable(self):
        async def fetch():
            return "v9.9.9"

        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            upstream_version=fetch,
            upstream_label="up",
        )
        payload = await _call(mcp)
        assert payload["up"] == {"version": "v9.9.9"}

    async def test_upstream_failure_returns_structured_error(self):
        def boom():
            raise RuntimeError("upstream is on fire")

        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            upstream_version=boom,
            upstream_label="paperless",
        )
        payload = await _call(mcp)
        # Wrapper info still present.
        assert payload["server_name"] == "my-mcp"
        assert payload["server_version"] == "1.0.0"
        assert payload["core_version"] == core_version
        # Upstream block carries the error instead of a version.
        assert payload["paperless"] == {"error": "upstream is on fire"}

    async def test_async_upstream_failure_returns_structured_error(self):
        """Failures raised from the awaited path are caught the same way."""

        async def boom_async():
            raise RuntimeError("async upstream on fire")

        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            upstream_version=boom_async,
            upstream_label="up",
        )
        payload = await _call(mcp)
        assert payload["server_name"] == "my-mcp"
        assert payload["up"] == {"error": "async upstream on fire"}

    async def test_upstream_failure_redacts_url_credentials(self, caplog):
        """An HTTP client's error quotes its URL; userinfo reaches neither path.

        Both emit paths are checked: the payload the model reads, and the
        log record, including the absence of a traceback, which would render
        the unredacted message again (#371).
        """

        def boom():
            raise RuntimeError(
                "GET https://user:hunter2@upstream.example/api?token=abc failed"
            )

        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            upstream_version=boom,
        )
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp"):
            payload = await _call(mcp)

        assert payload["upstream"] == {
            "error": "GET https://upstream.example/api failed"
        }
        records = [
            r for r in caplog.records if r.name == "markdown_vault_mcp._server_info"
        ]
        assert len(records) == 1
        record = records[0]
        assert record.getMessage() == (
            "server_info_upstream_lookup_failed error_type=RuntimeError "
            "error=GET https://upstream.example/api failed"
        )
        assert record.exc_info is None
        for secret in ("hunter2", "token=abc"):
            assert secret not in record.getMessage()
            assert secret not in str(payload)

    async def test_upstream_returns_none(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            upstream_version=lambda: None,
            upstream_label="up",
        )
        payload = await _call(mcp)
        assert payload["up"] == {"version": None}

    async def test_default_upstream_label(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            upstream_version=lambda: "v1",
        )
        payload = await _call(mcp)
        assert payload["upstream"] == {"version": "v1"}

    async def test_custom_tool_name(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            tool_name="version_info",
        )
        payload = await _call(mcp, tool_name="version_info")
        assert payload["server_name"] == "my-mcp"

    async def test_tool_is_marked_read_only(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
        )
        tools = await mcp.list_tools()
        target = next(t for t in tools if t.name == "get_server_info")
        assert target.annotations is not None
        assert target.annotations.read_only_hint is True

    async def test_tool_has_default_title(self):
        """Title-aware clients (e.g. VS Code) need a human-readable label."""
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
        )
        tools = await mcp.list_tools()
        target = next(t for t in tools if t.name == "get_server_info")
        assert target.annotations is not None
        assert target.annotations.title == "Server Info"

    async def test_custom_title(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            title="About This Server",
        )
        tools = await mcp.list_tools()
        target = next(t for t in tools if t.name == "get_server_info")
        assert target.annotations is not None
        assert target.annotations.title == "About This Server"

    async def test_empty_string_title_not_replaced_by_default(self):
        """Only None triggers the default; an explicit "" stays empty."""
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            title="",
        )
        tools = await mcp.list_tools()
        target = next(t for t in tools if t.name == "get_server_info")
        assert target.annotations is not None
        assert target.annotations.title == ""

    async def test_custom_description(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            description="My custom blurb.",
        )
        tools = await mcp.list_tools()
        target = next(t for t in tools if t.name == "get_server_info")
        assert target.description == "My custom blurb."

    async def test_default_description_mentions_server_name(self):
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="paperless-mcp",
        )
        tools = await mcp.list_tools()
        target = next(t for t in tools if t.name == "get_server_info")
        assert target.description is not None
        assert "paperless-mcp" in target.description

    async def test_empty_string_description_not_replaced_by_default(self):
        """Only None triggers the default; an explicit "" stays empty."""
        mcp = FastMCP("t")
        register_server_info_tool(
            mcp,
            server_version="1.0.0",
            server_name="my-mcp",
            description="",
        )
        tools = await mcp.list_tools()
        target = next(t for t in tools if t.name == "get_server_info")
        # FastMCP may normalize "" → None, but it must not contain the
        # default-description boilerplate.
        assert not target.description or "Report wrapper" not in target.description

    @pytest.mark.parametrize(
        "label", ["server_name", "server_version", "core_version", "protocol"]
    )
    def test_upstream_label_collision_with_reserved_key_raises(self, label):
        with pytest.raises(ValueError, match="reserved"):
            register_server_info_tool(
                FastMCP("t"),
                server_version="1.0.0",
                server_name="my-mcp",
                upstream_version=lambda: "v1",
                upstream_label=label,
            )

    def test_reserved_label_check_runs_even_without_upstream(self):
        """Misconfiguration is caught at registration, not deferred."""
        with pytest.raises(ValueError, match="reserved"):
            register_server_info_tool(
                FastMCP("t"),
                server_version="1.0.0",
                server_name="my-mcp",
                upstream_label="server_name",
            )


@pytest.mark.parametrize("upstream_value", [123, 4.2, True])
async def test_upstream_non_string_non_dict_coerced_to_str(upstream_value):
    mcp = FastMCP("t")
    register_server_info_tool(
        mcp,
        server_version="1.0.0",
        server_name="my-mcp",
        upstream_version=lambda: upstream_value,
        upstream_label="up",
    )
    result = await mcp.call_tool("get_server_info", {})
    assert result.structured_content["up"] == {"version": str(upstream_value)}


@pytest.mark.parametrize(
    ("mode", "version"), [("legacy", "2025-11-25"), ("2026-07-28", "2026-07-28")]
)
async def test_protocol_block_reports_the_calling_connection(mode, version):
    """#419: the revision and client of the connection the call came over."""
    mcp = FastMCP("t")
    register_server_info_tool(mcp, server_version="1.0.0", server_name="my-mcp")
    info = mt.Implementation(name="probe client", version="9.9")
    async with Client(mcp, mode=mode, client_info=info) as client:
        result = await client.call_tool("get_server_info", {})
    protocol = result.structured_content["protocol"]
    assert protocol["version"] == version
    assert protocol["client"] == {"name": "probe client", "version": "9.9"}
    assert version in protocol["supported_versions"]
