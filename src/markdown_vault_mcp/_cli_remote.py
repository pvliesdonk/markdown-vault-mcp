"""Lazy native MCP transport for one read-only command invocation."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp._cli_client import (
    ClientError,
    ClientOptions,
    ClientSettings,
    resolve_settings,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class RemoteReply:
    """Protocol data plus out-of-band freshness, shared with local adapters."""

    data: Any
    meta: dict[str, Any] = field(default_factory=dict)


def _payload(result: Any, tool: str) -> RemoteReply:
    if result.is_error:
        raise ClientError(
            "Remote read failed. Check the server's query and semantic capabilities.", 1
        )
    data = result.structured_content
    if data is None:
        blocks = result.content
        if len(blocks) != 1 or blocks[0].type != "text":
            raise ClientError("Server returned an unsupported read payload.", 1)
        try:
            data = json.loads(blocks[0].text)
        except (ValueError, TypeError):
            raise ClientError(
                "Server returned an invalid JSON read payload.", 1
            ) from None
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    if tool == "search":
        from markdown_vault_mcp._read_payloads import ReadPayloadError, validate_search

        try:
            validate_search(data)
        except ReadPayloadError as exc:
            raise ClientError(str(exc), 1) from None
    return RemoteReply(data, result.meta or {})


async def _call(
    settings: ClientSettings, tool: str, arguments: dict[str, Any]
) -> RemoteReply:
    try:
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
    except ImportError:
        raise ClientError(
            'Install "markdown-vault-mcp[mcp]" to use remote reads.', 1
        ) from None
    if settings.url is None:
        raise ClientError("Remote reads require a server URL.")
    # Passing headers explicitly also disables automatic interactive OAuth.
    headers = {"Authorization": f"Bearer {settings.token}"} if settings.token else {}
    transport = StreamableHttpTransport(settings.url, headers=headers, auth=None)
    async with (
        asyncio.timeout(settings.timeout),
        Client(
            transport, timeout=settings.timeout, init_timeout=settings.timeout
        ) as client,
    ):
        tools = {item.name: item for item in await client.list_tools()}
        if tool not in tools:
            raise ClientError(
                "Remote server does not support this read tool; upgrade the server.", 1
            )
        schema = tools[tool].input_schema
        if set(arguments) - set(schema.get("properties", {})):
            raise ClientError(
                "Remote server does not support the requested options; upgrade the server.",
                1,
            )
        return _payload(await client.call_tool_mcp(tool, arguments), tool)


def call_read(
    settings: ClientSettings, tool: str, arguments: dict[str, Any]
) -> RemoteReply:
    """Execute one MCP read with initialization included in its deadline.

    Args:
        settings: Validated remote connection and optional bearer credential.
        tool: Read-only MCP tool name selected by a command adapter.
        arguments: Tool arguments, checked against the discovered schema.

    Returns:
        Original structured data, unwrapping FastMCP's collection envelope.

    Raises:
        ClientError: Unsupported server, timeout, transport or tool failure.
        KeyboardInterrupt: Interrupted operation, after transport cleanup.
    """
    # Third-party debug/exception logs can contain Authorization or a redirect
    # location. The command emits safe diagnostics instead, including with -v.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(_call(settings, tool, arguments))
    except ClientError:
        raise
    except TimeoutError:
        raise ClientError(
            "Remote read timed out; increase --timeout if needed.", 1
        ) from None
    except Exception:
        raise ClientError(
            "Remote MCP connection failed. Check the endpoint and bearer authentication; "
            "interactive login is not supported.",
            1,
        ) from None
    finally:
        logging.disable(previous)


def run_read(
    tool: str,
    arguments: dict[str, Any],
    options: ClientOptions,
    local_call: Callable[[], Any],
    *,
    settings: ClientSettings | None = None,
) -> RemoteReply:
    """Route a read without initializing local dependencies in remote mode.

    Args:
        tool: MCP read tool name.
        arguments: Validated arguments shared by both backends.
        options: Connection selection, independent of server configuration.
        local_call: Lazy local facet adapter returning JSON-compatible data.
        settings: Already resolved selection, when a command checks local-only options.

    Returns:
        Read payload and available freshness metadata.
    """
    settings = settings if settings is not None else resolve_settings(options)
    if settings.url is not None:
        return call_read(settings, tool, arguments)
    return RemoteReply(local_call())
