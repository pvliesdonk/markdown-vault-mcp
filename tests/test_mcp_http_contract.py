"""Raw native HTTP contracts for whole-result consumers, including Executor shapes."""

from __future__ import annotations

import json
from contextlib import contextmanager
from itertools import count
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.server import make_server
from tests.fixtures.cli_http import serve_http

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_REQUEST_IDS = count(1)


def _rpc(client: httpx.Client, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Read one JSON-RPC reply, closing the native SSE stream after its event."""
    request_id = next(_REQUEST_IDS)
    with client.stream(
        "POST",
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
    ) as response:
        response.raise_for_status()
        if session := response.headers.get("mcp-session-id"):
            client.headers["Mcp-Session-Id"] = session
        if response.headers["content-type"].startswith("application/json"):
            reply = json.loads(response.read())
        else:
            data: list[str] = []
            for line in response.iter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    reply = json.loads("\n".join(data))
                    if reply.get("id") == request_id:
                        break
                    data.clear()
            else:
                raise AssertionError("No JSON-RPC reply in native HTTP stream")
        assert reply["jsonrpc"] == "2.0" and reply["id"] == request_id
        return dict(reply)


@contextmanager
def _session(origin: str) -> Iterator[tuple[httpx.Client, dict[str, Any]]]:
    """Initialize a raw HTTP client with negotiated protocol and optional session."""
    headers = {"Accept": "application/json, text/event-stream"}
    with httpx.Client(
        base_url=origin, headers=headers, timeout=10, trust_env=False
    ) as client:
        initialized = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "synthetic-http-contract", "version": "1"},
            },
        )
        result = initialized["result"]
        assert result["serverInfo"]["name"]
        assert "tools" in result["capabilities"]
        client.headers["MCP-Protocol-Version"] = result["protocolVersion"]
        response = client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert response.status_code == 202
        yield client, result


def test_mvm_http_initialize_discover_search_read(discovery_source: Path) -> None:
    """Expose the real vault's instructions, reader schemas and native payloads."""
    config = ProjectConfig(source_dir=discovery_source, embedding_provider=None)
    server = make_server(transport="http", config=config, configure_logging=False)
    with (
        serve_http(server.http_app()) as origin,
        _session(origin) as (client, initialization),
    ):
        assert initialization["instructions"]
        catalog = _rpc(client, "tools/list", {})["result"]["tools"]
        tools = {tool["name"]: tool for tool in catalog}
        for name, argument in (("search", "query"), ("read", "path")):
            tool = tools[name]
            assert argument in tool["inputSchema"]["required"]
            assert tool["inputSchema"]["properties"][argument]["type"] == "string"
            assert tool["outputSchema"]["type"] == "object"
            assert tool["annotations"]["readOnlyHint"] is True
            assert tool["annotations"]["destructiveHint"] is False
        search = _rpc(
            client,
            "tools/call",
            {
                "name": tools["search"]["name"],
                "arguments": {"query": "Alpha", "mode": "keyword"},
            },
        )["result"]
        assert search["isError"] is False
        hits = search["structuredContent"]["result"]
        assert hits[0]["path"] == "alpha.md"
        assert hits[0]["search_type"] == "keyword"
        assert json.loads(search["content"][0]["text"]) == hits
        assert isinstance(search["_meta"]["index_stale"], bool)
        note = _rpc(
            client,
            "tools/call",
            {"name": tools["read"]["name"], "arguments": {"path": hits[0]["path"]}},
        )["result"]
        assert note["isError"] is False
        assert "Details here." in note["structuredContent"]["content"]
        assert json.loads(note["content"][0]["text"]) == note["structuredContent"]


@pytest.mark.parametrize("kind", ["structured", "empty", "text", "error"])
def test_http_whole_result_shapes(kind: str) -> None:
    """Retain distinct text, empty structured objects, nested metadata and errors."""
    server = FastMCP("synthetic-result-contract")
    calls: list[str] = []

    @server.tool()
    def result_shape() -> ToolResult:
        """Return one controlled native result."""
        calls.append(kind)
        if kind == "error":
            raise ToolError("Synthetic request refused; choose another note.")
        structured = {"path": "alpha.md"} if kind == "structured" else {}
        return ToolResult(
            content=[TextContent(type="text", text="Explanation distinct from JSON")],
            structured_content=None if kind == "text" else structured,
            meta={
                "index_stale": False,
                "receipt": {"source": "synthetic", "counts": [0, 2]},
            },
        )

    with serve_http(server.http_app()) as origin, _session(origin) as (client, _):
        reply = _rpc(client, "tools/call", {"name": "result_shape", "arguments": {}})
    assert "error" not in reply
    result = reply["result"]
    # Executor-shaped caller: a returned remote error needs no thrown exception.
    if result["isError"]:
        assert kind == "error"
        assert "Synthetic request refused" in result["content"][0]["text"]
    else:
        assert kind != "error"
        assert result["content"] == [
            {"type": "text", "text": "Explanation distinct from JSON"}
        ]
        assert result["_meta"] == {
            "index_stale": False,
            "receipt": {"source": "synthetic", "counts": [0, 2]},
        }
        assert ("structuredContent" in result) == (kind != "text")
        # Presence, rather than truthiness, preserves an empty structured object.
        if "structuredContent" in result:
            assert result["structuredContent"] == (
                {"path": "alpha.md"} if kind == "structured" else {}
            )
        else:
            assert kind == "text"
            assert result["content"][0]["text"] == "Explanation distinct from JSON"
    assert calls == [kind]  # No implicit retry of the returned failure.


def test_http_protocol_error_is_not_a_tool_result() -> None:
    """Keep an unknown JSON-RPC method distinct from a returned tool failure."""
    server = FastMCP("synthetic-protocol-contract")
    with serve_http(server.http_app()) as origin, _session(origin) as (client, _):
        protocol_error = _rpc(client, "synthetic/unknown", {})
        assert protocol_error["error"]["code"] == -32601
        assert "result" not in protocol_error
