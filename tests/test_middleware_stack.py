"""``wire_middleware_stack`` (#2341).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_middleware.py``.
"""

from __future__ import annotations

import logging

import pytest
from fastmcp import Client, FastMCP

from markdown_vault_mcp._request_logging import (
    RequestLoggingMiddleware,
    wire_middleware_stack,
)


def _request_logging_mws(mcp: FastMCP) -> list[RequestLoggingMiddleware]:
    """Return the RequestLoggingMiddleware instances wire_middleware_stack added.

    FastMCP may pre-install its own middlewares (e.g. DereferenceRefsMiddleware);
    this filters to only the one wire_middleware_stack is responsible for.
    """
    return [m for m in mcp.middleware if isinstance(m, RequestLoggingMiddleware)]


def test_installs_single_request_logging_middleware():
    mcp = FastMCP(name="t")
    wire_middleware_stack(mcp)
    assert len(_request_logging_mws(mcp)) == 1


@pytest.mark.parametrize("rich_raw", [None, "true", "false"])
def test_rich_logging_env_var_no_longer_read(monkeypatch, rich_raw):
    """``wire_middleware_stack`` no longer selects an output mode itself —
    the request middleware always logs through the shared grammar, and the
    root handler's formatter (installed elsewhere) picks Rich vs. JSON. So
    ``FASTMCP_ENABLE_RICH_LOGGING`` is inert: every value installs the same
    middleware, unconditionally."""
    if rich_raw is None:
        monkeypatch.delenv("FASTMCP_ENABLE_RICH_LOGGING", raising=False)
    else:
        monkeypatch.setenv("FASTMCP_ENABLE_RICH_LOGGING", rich_raw)
    mcp = FastMCP(name="t")
    wire_middleware_stack(mcp)
    mws = _request_logging_mws(mcp)
    assert len(mws) == 1
    assert not hasattr(mws[0], "structured")


def test_include_traceback_inferred_from_debug_log_level(caplog):
    """include_traceback is inferred True when the root logger is at DEBUG."""
    with caplog.at_level(logging.DEBUG):
        mcp = FastMCP(name="t")
        wire_middleware_stack(mcp)
    assert _request_logging_mws(mcp)[0].include_traceback is True


def test_include_traceback_inferred_off_when_root_above_debug(caplog):
    """include_traceback is inferred False when the root logger sits above DEBUG."""
    with caplog.at_level(logging.WARNING):
        mcp = FastMCP(name="t")
        wire_middleware_stack(mcp)
    assert _request_logging_mws(mcp)[0].include_traceback is False


async def test_real_tool_dispatch_logs_conforming_tool_pair(caplog):
    mcp = FastMCP(name="t")

    @mcp.tool
    def echo(value: str) -> str:
        return value

    wire_middleware_stack(mcp)
    logger_name = "fastmcp.middleware.requests"
    with caplog.at_level(logging.INFO, logger=logger_name):
        async with Client(mcp) as client:
            await client.call_tool("echo", {"value": "hello"})

    tool_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == logger_name and record.getMessage().startswith("tool_call_")
    ]
    assert len(tool_messages) == 2
    assert tool_messages[0].startswith(
        "tool_call_started tool=echo method=tools/call source=client"
    )
    assert tool_messages[1].startswith("tool_call_completed tool=echo duration_ms=")


async def _era_messages(mcp: FastMCP, caplog, mode: str | None) -> list[str]:
    """Connect a client on the given era and return the middleware records."""
    logger_name = "fastmcp.middleware.requests"
    kwargs = {} if mode is None else {"mode": mode}
    with caplog.at_level(logging.INFO, logger=logger_name):
        async with Client(mcp, **kwargs) as client:
            await client.list_tools()
    return [
        record.getMessage() for record in caplog.records if record.name == logger_name
    ]


async def test_legacy_era_negotiation_traffic_logs_conforming_pairs(caplog):
    """FastMCP 4 middleware sees *all* inbound traffic — on the legacy era
    that includes the ``notifications/initialized`` notification, which
    did not reach middleware on FastMCP 3 (the ``initialize`` request
    already did). Both must come out as ordinary conforming
    started/completed pairs under the generic vocabulary."""
    mcp = FastMCP(name="t")
    wire_middleware_stack(mcp)

    messages = await _era_messages(mcp, caplog, "legacy")
    assert "request_started method=initialize source=client" in messages
    assert any(
        m.startswith("request_completed method=initialize duration_ms=")
        for m in messages
    )
    # The connection fields join from the first message after the handshake.
    assert any(
        m.startswith(
            "notification_started method=notifications/initialized source=client "
            "protocol_version=2025-11-25 client_name="
        )
        for m in messages
    )
    assert any(
        m.startswith(
            "notification_completed method=notifications/initialized duration_ms="
        )
        for m in messages
    )


async def test_auto_negotiation_reaches_sessionless_discover_pair(caplog):
    """Auto negotiation against a v4 server reaches the sessionless era,
    which replaces the ``initialize`` handshake with ``server/discover`` —
    logged as an ordinary request pair, with no ``initialize`` traffic."""
    mcp = FastMCP(name="t")
    wire_middleware_stack(mcp)

    messages = await _era_messages(mcp, caplog, None)
    assert any(
        m.startswith(
            "request_started method=server/discover source=client "
            "protocol_version=2026-07-28"
        )
        for m in messages
    )
    assert any(
        m.startswith("request_completed method=server/discover duration_ms=")
        for m in messages
    )
    assert not any(m.startswith("request_started method=initialize") for m in messages)
