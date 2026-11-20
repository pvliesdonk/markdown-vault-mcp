"""Tests for the tool boundary (ported from pvl-core 10.2.0, #2280).

``from __future__ import annotations`` is deliberate: every annotation below
is a string, so FastMCP has to resolve ``_Query`` and ``Context`` through the
*tool's* module, not ``_tool_boundary``'s. That is the case where a wrapper
that loses ``__wrapped__`` breaks the schema.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from typing import Any

import httpx
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.utilities.tasks import TaskConfig
from mcp.shared.exceptions import MCPError
from mcp.types import MISSING_REQUIRED_CLIENT_CAPABILITY, TextContent
from pydantic import BaseModel

from markdown_vault_mcp._request_logging import wire_middleware_stack
from markdown_vault_mcp._tool_boundary import (
    FAULT_MESSAGE,
    is_tool_boundary,
    tool_boundary,
)

_BOUNDARY_LOGGER = "markdown_vault_mcp._tool_boundary"
_REQUESTS_LOGGER = "fastmcp.middleware.requests"


class _Query(BaseModel):
    text: str
    limit: int = 10


def _records(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == name]


class TestContract:
    """The boundary's own behaviour, without a server."""

    async def test_async_fault_becomes_fault_toolerror(self, caplog):
        @tool_boundary
        async def tool() -> str:
            raise ValueError("internal detail /srv/secret")

        with (
            caplog.at_level(logging.DEBUG, logger=_BOUNDARY_LOGGER),
            pytest.raises(ToolError) as info,
        ):
            await tool()

        assert str(info.value) == FAULT_MESSAGE
        assert info.value.log_level == logging.ERROR
        # Raised from None: the boundary's own line is the one traceback.
        assert info.value.__cause__ is None
        assert info.value.__suppress_context__ is True
        (record,) = _records(caplog, _BOUNDARY_LOGGER)
        assert record.levelno == logging.ERROR
        assert record.getMessage() == "tool_failed function=tool error_type=ValueError"
        assert record.exc_info is not None
        assert isinstance(record.exc_info[1], ValueError)

    def test_sync_fault_becomes_fault_toolerror(self, caplog):
        @tool_boundary
        def tool() -> str:
            raise KeyError("x")

        with (
            caplog.at_level(logging.DEBUG, logger=_BOUNDARY_LOGGER),
            pytest.raises(ToolError) as info,
        ):
            tool()

        assert str(info.value) == FAULT_MESSAGE
        (record,) = _records(caplog, _BOUNDARY_LOGGER)
        assert record.getMessage() == "tool_failed function=tool error_type=KeyError"
        assert record.exc_info is not None

    @pytest.mark.parametrize(
        "exc",
        [
            ToolError("No note at 'a.md'.", log_level=logging.INFO),
            ToolError("server fault"),
            ResourceError("gone", log_level=logging.INFO),
        ],
        ids=["tool-info", "tool-default", "resource"],
    )
    async def test_fastmcp_errors_pass_through_unchanged(self, caplog, exc):
        @tool_boundary
        async def tool() -> str:
            raise exc

        with (
            caplog.at_level(logging.DEBUG, logger=_BOUNDARY_LOGGER),
            pytest.raises(type(exc)) as info,
        ):
            await tool()

        assert info.value is exc
        assert _records(caplog, _BOUNDARY_LOGGER) == []

    async def test_missing_client_capability_passes_through(self, caplog):
        # SEP-2575 needs it on the wire as JSON-RPC -32021, not as a result.
        exc = MCPError(MISSING_REQUIRED_CLIENT_CAPABILITY, "needs elicitation")

        @tool_boundary
        async def tool() -> str:
            raise exc

        with (
            caplog.at_level(logging.DEBUG, logger=_BOUNDARY_LOGGER),
            pytest.raises(MCPError) as info,
        ):
            await tool()
        assert info.value is exc
        assert _records(caplog, _BOUNDARY_LOGGER) == []

    async def test_other_protocol_errors_are_faults(self):
        @tool_boundary
        async def tool() -> str:
            raise MCPError(-32603, "internal")

        with pytest.raises(ToolError) as info:
            await tool()
        assert str(info.value) == FAULT_MESSAGE

    @pytest.mark.parametrize(
        ("exc", "message"),
        [
            (
                httpx.HTTPStatusError(
                    "429 from https://user:pw@upstream.example/api",
                    request=httpx.Request("GET", "https://upstream.example/api"),
                    response=httpx.Response(429),
                ),
                "Rate limited by upstream API, please retry later",
            ),
            (
                httpx.ReadTimeout("timed out reading https://user:pw@up.example"),
                "Upstream request timed out, please retry",
            ),
        ],
        ids=["rate-limited", "timeout"],
    )
    async def test_transient_upstream_faults_are_warnings(self, caplog, exc, message):
        @tool_boundary
        async def tool() -> str:
            raise exc

        with (
            caplog.at_level(logging.DEBUG, logger=_BOUNDARY_LOGGER),
            pytest.raises(ToolError) as info,
        ):
            await tool()

        assert str(info.value) == message
        assert info.value.log_level == logging.WARNING
        (record,) = _records(caplog, _BOUNDARY_LOGGER)
        assert record.levelno == logging.WARNING
        assert record.exc_info is None
        # Only the type is logged: the exception's text quotes the URL.
        assert "pw" not in record.getMessage()

    async def test_other_http_status_errors_are_faults(self, caplog):
        @tool_boundary
        async def tool() -> str:
            raise httpx.HTTPStatusError(
                "500",
                request=httpx.Request("GET", "https://upstream.example/api"),
                response=httpx.Response(500),
            )

        with (
            caplog.at_level(logging.DEBUG, logger=_BOUNDARY_LOGGER),
            pytest.raises(ToolError) as info,
        ):
            await tool()
        assert str(info.value) == FAULT_MESSAGE
        (record,) = _records(caplog, _BOUNDARY_LOGGER)
        assert record.levelno == logging.ERROR

    async def test_cancellation_passes_through(self, caplog):
        @tool_boundary
        async def tool() -> str:
            raise asyncio.CancelledError

        with (
            caplog.at_level(logging.DEBUG, logger=_BOUNDARY_LOGGER),
            pytest.raises(asyncio.CancelledError),
        ):
            await tool()
        assert _records(caplog, _BOUNDARY_LOGGER) == []

    async def test_results_pass_through(self):
        @tool_boundary
        async def atool(x: int) -> int:
            return x + 1

        @tool_boundary
        def stool(x: int) -> int:
            return x * 2

        assert await atool(1) == 2
        assert stool(3) == 6

    def test_keeps_the_kind_of_function(self):
        async def atool() -> None: ...

        def stool() -> None: ...

        assert inspect.iscoroutinefunction(tool_boundary(atool))
        assert not inspect.iscoroutinefunction(tool_boundary(stool))

    def test_keeps_signature_and_annotations(self):
        async def tool(query: _Query, ctx: Context, flag: bool = False) -> dict:  # noqa: ARG001 - the signature is under test
            return {}

        wrapped = tool_boundary(tool)
        assert inspect.signature(wrapped) == inspect.signature(tool)
        assert wrapped.__annotations__ == tool.__annotations__
        assert wrapped.__name__ == "tool"


class TestMarker:
    def test_marks_the_wrapper(self):
        def tool() -> None: ...

        assert not is_tool_boundary(tool)
        assert is_tool_boundary(tool_boundary(tool))

    def test_found_under_another_wraps_decorator(self):
        def outer(fn: Any) -> Any:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return fn(*args, **kwargs)

            return wrapper

        def tool() -> None: ...

        assert is_tool_boundary(outer(tool_boundary(tool)))
        assert not is_tool_boundary(outer(tool))

    def test_survives_a_wrapped_cycle(self):
        def tool() -> None: ...

        tool.__wrapped__ = tool  # type: ignore[attr-defined]
        assert not is_tool_boundary(tool)


class TestRegistration:
    """What FastMCP builds from a wrapped tool."""

    async def test_schema_matches_the_unwrapped_tool(self):
        async def search(query: _Query, ctx: Context, flag: bool = False) -> dict:  # noqa: ARG001 - the schema is under test
            """Search."""
            return {}

        plain, wrapped = FastMCP("plain"), FastMCP("wrapped")
        plain.tool(search)
        wrapped.tool(tool_boundary(search))

        (p,) = await plain.list_tools()
        (w,) = await wrapped.list_tools()
        assert w.parameters == p.parameters
        assert w.output_schema == p.output_schema
        # Context is injected, not exposed as a parameter.
        assert "ctx" not in w.parameters["properties"]
        assert "query" in w.parameters["properties"]

    async def test_context_is_injected(self):
        mcp = FastMCP("t")

        @mcp.tool
        @tool_boundary
        async def whoami(ctx: Context) -> str:
            return type(ctx).__name__

        async with Client(mcp) as client:
            result = await client.call_tool("whoami", {})
        assert result.data == "Context"

    def test_task_registration_accepts_a_wrapped_coroutine(self):
        mcp = FastMCP("t")

        async def work() -> dict:
            return {}

        # TaskConfig requires a coroutine function; the wrapper must stay one.
        mcp.tool(task=TaskConfig(mode="optional"))(tool_boundary(work))


class TestOnTheWire:
    """End to end: what the model receives and which lines are logged."""

    @staticmethod
    def _server(*, mask: bool) -> FastMCP:
        mcp = FastMCP("t", mask_error_details=mask)
        wire_middleware_stack(mcp)

        @mcp.tool
        @tool_boundary
        async def crash() -> str:
            raise RuntimeError("internal detail /srv/secret")

        @mcp.tool
        @tool_boundary
        async def refuse() -> str:
            raise ToolError(
                "No note at 'a.md'. Find the path with search_notes.",
                log_level=logging.INFO,
            )

        return mcp

    @pytest.mark.parametrize("mask", [False, True], ids=["unmasked", "masked"])
    async def test_fault_reaches_the_model_as_the_fault_message(self, caplog, mask):
        async with Client(self._server(mask=mask)) as client:
            with caplog.at_level(logging.DEBUG):
                result = await client.call_tool_mcp("crash", {})

        assert result.is_error is True
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == FAULT_MESSAGE

        (boundary,) = _records(caplog, _BOUNDARY_LOGGER)
        assert boundary.levelno == logging.ERROR
        assert boundary.exc_info is not None
        failed = [
            r
            for r in _records(caplog, _REQUESTS_LOGGER)
            if r.getMessage().startswith("tool_call_failed ")
        ]
        assert [r.levelno for r in failed] == [logging.ERROR]
        # FastMCP's own record never gets the traceback (it was a ToolError).
        # FastMCP passes exc_info=False, which logging stores as False.
        assert not any(
            r.exc_info for r in caplog.records if r.name.startswith("fastmcp.server")
        )

    async def test_refusal_stays_below_error_on_every_line(self, caplog):
        async with Client(self._server(mask=True)) as client:
            with caplog.at_level(logging.DEBUG):
                result = await client.call_tool_mcp("refuse", {})

        assert result.is_error is True
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == (
            "No note at 'a.md'. Find the path with search_notes."
        )
        assert _records(caplog, _BOUNDARY_LOGGER) == []
        errors = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and r.name.startswith("fastmcp")
        ]
        assert errors == []
