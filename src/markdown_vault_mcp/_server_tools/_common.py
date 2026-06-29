from __future__ import annotations

import asyncio
import logging
import os
from typing import TypeVar

from fastmcp.tools import ToolResult

from markdown_vault_mcp.vault import Vault

logger = logging.getLogger(__name__)

# Bridges a read tool's data-shaped return annotation (which drives the
# advertised output schema) to the ToolResult it returns at runtime.
# Return-only: _staleness_result() is declared `-> _T` but returns a
# ToolResult, so its result must be `return`ed directly from a tool, never
# stored or processed as `_T` (mypy would not catch the mismatch).
_T = TypeVar("_T")


def _resolve_drain_timeout() -> float:
    """Read env var at call time so tests can monkeypatch.setenv it."""
    return float(os.environ.get("MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S", "60"))


async def _maybe_wait_for_drain(
    vault: Vault, wait_for_drain: bool, tool_name: str
) -> bool:
    """Wait for the writer to drain when requested. Log on timeout.

    Polls :meth:`IndexFacet.is_drained` directly with ``asyncio.sleep``
    so concurrent waiters yield to the event loop instead of occupying
    ``asyncio.to_thread`` slots for the full timeout (would starve the
    default thread pool at moderate concurrency).

    Returns True when the writer was drained at the point of asking
    (or when no wait was requested), False when the bounded wait
    timed out.
    """
    if not wait_for_drain:
        return True
    timeout = _resolve_drain_timeout()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    poll_interval = 0.05
    while True:
        if vault.index.is_drained():
            return True
        if loop.time() >= deadline:
            logger.warning(
                "wait_for_drain_timeout tool=%s timeout_s=%s",
                tool_name,
                timeout,
            )
            return False
        await asyncio.sleep(poll_interval)


def _staleness_result(
    vault: Vault,
    data: _T,
    *,
    drained_on_request: bool,
    gen_before: int,
    force_result_wrap: bool = False,
) -> _T:
    """Wrap a read tool's payload with index-freshness metadata.

    The payload is returned unchanged (a bare list/dict) as the tool's
    content and structured output; freshness rides out-of-band in the MCP
    ``_meta`` channel as ``index_stale``. Clients that do not care ignore
    ``_meta`` and read the data exactly as before; the 1% that need a
    fresh-read guarantee inspect ``result.meta["index_stale"]``.

    ``index_stale`` is True when the IndexWriter had pending or in-flight
    work at any of three observation points: the optional ``wait_for_pending_writes``
    timed out (``drained_on_request`` False), a write completed inside the
    read window (``write_generation`` advanced past ``gen_before``), or the
    writer was non-idle at response time.

    ``structured_content`` mirrors FastMCP's wrap-result convention (a
    primitive/collection payload is nested under ``{"result": ...}``) so the
    client still deserializes ``result.data`` to the bare shape, matching the
    tool's data-shaped return annotation. The annotation drives the advertised
    output schema; the ``ToolResult`` is the runtime payload — hence the typed
    bridge below (the function is declared to return the data type ``_T`` while
    actually returning a ``ToolResult`` FastMCP unwraps to that same shape).

    ``content`` is the bare ``data`` value: FastMCP's ``ToolResult.content``
    accepts ``Any`` serializable value and converts it to a JSON ``TextContent``
    block, which is the documented path for non-content-block payloads (it is
    not the ``# type: ignore`` target — that is solely the ``-> _T`` bridge).

    ``force_result_wrap``: pass ``True`` for union-return tools (e.g.
    ``list | dict``) whose output schema requires the ``{"result": ...}``
    envelope for every branch, overriding the default dict-passthrough.
    """
    index_stale = (
        (not drained_on_request)
        or (vault.index.write_generation() != gen_before)
        or (not vault.index.is_drained())
    )
    structured = (
        {"result": data}
        if force_result_wrap
        else (data if isinstance(data, dict) else {"result": data})
    )
    return ToolResult(  # type: ignore[return-value]
        content=data,
        structured_content=structured,
        meta={"index_stale": index_stale},
    )
