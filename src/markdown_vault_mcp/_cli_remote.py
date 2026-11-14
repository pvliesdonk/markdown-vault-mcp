"""Lazy native MCP transport for one hosted command invocation."""

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


def _payload(result: Any, tool: str, *, write: bool = False) -> RemoteReply:
    if result.is_error:
        if write:
            message = "\n".join(
                block.text for block in result.content if block.type == "text"
            )
            raise ClientError(message or "Remote tool failed.", 1)
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
    settings: ClientSettings,
    tool: str,
    arguments: dict[str, Any],
    *,
    write: bool = False,
    read_etag_from: str | None = None,
) -> RemoteReply:
    try:
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
    except ImportError:
        raise ClientError(
            'Install "markdown-vault-mcp[mcp]" to use remote commands.', 1
        ) from None
    if settings.url is None:
        raise ClientError("Remote commands require a server URL.")
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
        if write:
            requested = dict(arguments)
            if read_etag_from is not None:
                requested["if_match"] = ""
            _write_schema(tools, tool, requested)
            if read_etag_from is not None:
                _write_schema(tools, "read", {"path": read_etag_from})
                reply = _payload(
                    await client.call_tool_mcp("read", {"path": read_etag_from}),
                    "read",
                    write=True,
                )
                etag = reply.data.get("etag") if isinstance(reply.data, dict) else None
                if not isinstance(etag, str) or not etag:
                    raise ClientError(
                        "Remote read did not return a nonempty etag; no mutation submitted.",
                        1,
                    )
                arguments = {**arguments, "if_match": etag}
        else:
            if tool not in tools:
                raise ClientError(
                    "Remote server does not support this read tool; upgrade the server.",
                    1,
                )
            schema = tools[tool].input_schema
            if set(arguments) - set(schema.get("properties", {})):
                raise ClientError(
                    "Remote server does not support the requested options; upgrade the server.",
                    1,
                )
        return _payload(await client.call_tool_mcp(tool, arguments), tool, write=write)


def _write_schema(tools: dict[str, Any], tool: str, arguments: dict[str, Any]) -> None:
    if tool not in tools:
        raise ClientError(f"Remote server does not support tool '{tool}'.", 1)
    if set(arguments) - set(tools[tool].input_schema.get("properties", {})):
        raise ClientError(
            f"Remote tool '{tool}' does not support the requested options.", 1
        )


def _failure_kind(exc: BaseException) -> str:
    """Name the root exception type, through causes and single-member groups."""
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]
        elif exc.__cause__ is not None:
            exc = exc.__cause__
        else:
            break
    return type(exc).__name__


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
    return _execute(settings, tool, arguments)


def call_write(
    settings: ClientSettings,
    tool: str,
    arguments: dict[str, Any],
    *,
    read_etag_from: str | None = None,
) -> RemoteReply:
    """Execute one hosted mutation, optionally reading its concurrency token.

    Args:
        settings: Validated endpoint, credential and whole-operation deadline.
        tool: Existing MCP tool to invoke once.
        arguments: Native arguments checked against advertised properties.
        read_etag_from: Whole-note path whose live etag must precede mutation.

    Returns:
        Native structured result, including an accepted job envelope.

    Raises:
        ClientError: Unsupported capability, failed pre-read, tool or transport error.
        KeyboardInterrupt: Interrupted operation after connection cleanup.
    """
    return _execute(
        settings, tool, arguments, write=True, read_etag_from=read_etag_from
    )


def _execute(
    settings: ClientSettings,
    tool: str,
    arguments: dict[str, Any],
    *,
    write: bool = False,
    read_etag_from: str | None = None,
) -> RemoteReply:
    # Third-party debug/exception logs can contain Authorization or a redirect
    # location. The command emits safe diagnostics instead, including with -v.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(
            _call(settings, tool, arguments, write=write, read_etag_from=read_etag_from)
        )
    except ClientError:
        raise
    except TimeoutError:
        if write:
            raise ClientError(
                "Remote operation timed out; outcome may be unknown. Check the server before retrying.",
                1,
            ) from None
        raise ClientError(
            "Remote read timed out; increase --timeout if needed.", 1
        ) from None
    except Exception as exc:
        # The exception's type names the failure without its text, which may
        # carry a header or a redirect location.
        raise ClientError(
            f"Remote MCP connection failed ({_failure_kind(exc)}). Check the "
            "endpoint and bearer authentication; interactive login is not supported."
            + (
                " Mutation outcome may be unknown; check before retrying."
                if write
                else ""
            ),
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
