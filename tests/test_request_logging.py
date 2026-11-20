"""Tests for RequestLoggingMiddleware.

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_logging_middleware.py`` (#2341).
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server.middleware.middleware import MiddlewareContext

from markdown_vault_mcp._log_render import JsonFormatter, bind_record, render_rich
from markdown_vault_mcp._request_logging import RequestLoggingMiddleware

_LOGGER_NAME = "fastmcp.middleware.requests"


class _ToolParams:
    """Minimal stand-in for CallToolRequestParams — only ``.name`` is read."""

    def __init__(self, name: str) -> None:
        self.name = name


def _context(*, method, message=None, type_="request", source="client"):
    return MiddlewareContext(message=message, method=method, type=type_, source=source)


async def _ok_call_next(_context):
    return "result"


def _failing_call_next(exc):
    async def _call_next(_context):
        raise exc

    return _call_next


async def test_tool_call_started_and_completed(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        result = await mw.on_message(ctx, _ok_call_next)
    assert result == "result"
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0].startswith("tool_call_started ")
    assert "tool=read" in messages[0]
    assert "method=tools/call" in messages[0]
    assert "source=client" in messages[0]
    assert messages[1].startswith("tool_call_completed ")
    assert "tool=read" in messages[1]
    assert "duration_ms=" in messages[1]


async def test_request_uses_method_vocabulary(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="initialize")
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(ctx, _ok_call_next)
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0].startswith("request_started ")
    assert "method=initialize" in messages[0]
    assert messages[1].startswith("request_completed ")
    assert "method=initialize" in messages[1]
    assert "duration_ms=" in messages[1]


async def test_notification_uses_notification_vocabulary(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="notifications/initialized", type_="notification")
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(ctx, _ok_call_next)
    started = caplog.records[0].getMessage()
    assert started.startswith("notification_started ")
    assert "method=notifications/initialized" in started
    assert "source=client" in started
    assert caplog.records[1].getMessage().startswith("notification_completed ")


async def test_tool_call_failed_line(caplog):
    # The middleware emits a record; render_rich (the root formatter, Tasks
    # 1-2) renders it — that rendered form, not record.getMessage(), is what
    # an operator actually sees, and it is the only place quoting happens.
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    call_next = _failing_call_next(ValueError("bad section here"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        pytest.raises(ValueError),
    ):
        await mw.on_message(ctx, call_next)
    failed = caplog.records[-1]
    msg = render_rich(failed)
    assert msg.startswith("tool_call_failed ")
    assert "tool=read" in msg
    assert "duration_ms=" in msg
    assert "error_type=ValueError" in msg
    assert 'error="bad section here"' in msg
    assert failed.levelno == logging.ERROR


async def test_failed_error_value_unquoted_when_no_whitespace(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    call_next = _failing_call_next(ValueError("oneword"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        pytest.raises(ValueError),
    ):
        await mw.on_message(ctx, call_next)
    assert "error=oneword" in caplog.records[-1].getMessage()


@pytest.mark.parametrize(
    ("exc", "level"),
    [
        # A request the model must change: the raiser chose INFO (#363).
        (ToolError("no note at a.md", log_level=logging.INFO), logging.INFO),
        (ToolError("index rebuilding", log_level=logging.WARNING), logging.WARNING),
        # FastMCPError's default level is ERROR.
        (ToolError("server fault"), logging.ERROR),
        (ResourceError("gone", log_level=logging.INFO), logging.INFO),
        # Not a FastMCPError: no level to read, so ERROR.
        (ValueError("boom"), logging.ERROR),
    ],
    ids=["tool-info", "tool-warning", "tool-default", "resource-info", "other"],
)
async def test_failed_line_level_follows_log_level(caplog, exc, level):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    call_next = _failing_call_next(exc)
    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        pytest.raises(type(exc)),
    ):
        await mw.on_message(ctx, call_next)
    failed = caplog.records[-1]
    assert failed.getMessage().startswith("tool_call_failed ")
    assert failed.levelno == level


async def test_failed_line_level_applies_to_non_tool_messages(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="resources/read")
    exc = ResourceError("gone", log_level=logging.INFO)
    call_next = _failing_call_next(exc)
    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        pytest.raises(ResourceError),
    ):
        await mw.on_message(ctx, call_next)
    failed = caplog.records[-1]
    assert failed.getMessage().startswith("request_failed ")
    assert failed.levelno == logging.INFO


async def test_include_traceback_attaches_exc_info(caplog):
    mw = RequestLoggingMiddleware(include_traceback=True)
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    call_next = _failing_call_next(ValueError("boom"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        pytest.raises(ValueError),
    ):
        await mw.on_message(ctx, call_next)
    assert caplog.records[-1].exc_info is not None


async def test_no_traceback_when_include_traceback_false(caplog):
    mw = RequestLoggingMiddleware(include_traceback=False)
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    call_next = _failing_call_next(ValueError("boom"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        pytest.raises(ValueError),
    ):
        await mw.on_message(ctx, call_next)
    assert caplog.records[-1].exc_info is None


async def test_unknown_tool_name_falls_back(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=object())
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(ctx, _ok_call_next)
    assert "tool=unknown" in caplog.records[0].getMessage()


async def test_json_formatter_renders_tool_call_pair(caplog):
    """The middleware no longer builds JSON itself — ``JsonFormatter`` from
    ``_log_render`` (Tasks 1-2) renders its conforming record, same as any
    other conforming record in the process."""
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(ctx, _ok_call_next)
    formatter = JsonFormatter()
    started = json.loads(formatter.format(caplog.records[0]))
    assert started["event"] == "tool_call_started"
    assert started["tool"] == "read"
    assert started["method"] == "tools/call"
    assert started["source"] == "client"
    completed = json.loads(formatter.format(caplog.records[1]))
    assert completed["event"] == "tool_call_completed"
    assert completed["tool"] == "read"
    assert "duration_ms" in completed


async def test_json_formatter_renders_failed_record(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    call_next = _failing_call_next(ValueError("bad section here"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        pytest.raises(ValueError),
    ):
        await mw.on_message(ctx, call_next)
    failed = json.loads(JsonFormatter().format(caplog.records[-1]))
    assert failed["event"] == "tool_call_failed"
    assert failed["error_type"] == "ValueError"
    assert failed["error"] == "bad section here"


async def test_exception_is_reraised_unchanged(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    sentinel = ValueError("propagate me")
    call_next = _failing_call_next(sentinel)
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        pytest.raises(ValueError) as excinfo,
    ):
        await mw.on_message(ctx, call_next)
    assert excinfo.value is sentinel


async def test_custom_logger_is_used(caplog):
    mw = RequestLoggingMiddleware(logger=logging.getLogger("test.custom.requests"))
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with caplog.at_level(logging.INFO, logger="test.custom.requests"):
        await mw.on_message(ctx, _ok_call_next)
    assert caplog.records
    assert all(record.name == "test.custom.requests" for record in caplog.records)


# --- logs through the grammar, not its own renderer (Task 3) -----------------


async def test_emitted_record_is_conforming(caplog):
    """``bind_record`` recovers the same event and fields the old
    ``structured`` payload carried, straight from the template/args pair —
    proof the middleware now speaks the shared grammar instead of building
    its own JSON."""
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(ctx, _ok_call_next)
    bound = bind_record(caplog.records[0])
    assert bound is not None
    event, fields = bound
    assert event == "tool_call_started"
    assert [f.name for f in fields] == ["tool", "method", "source"]
    assert [f.value for f in fields] == ["read", "tools/call", "client"]


async def test_field_order_is_tool_then_duration_then_trace_ids_last(caplog):
    """README documents ``tool=`` before ``duration_ms=``, with trace ids
    trailing when present — the field order the grammar recovers must match."""
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        _tracer().start_as_current_span("outer"),
    ):
        await mw.on_message(ctx, _ok_call_next)
    completed = _records(caplog)[1]
    bound = bind_record(completed)
    assert bound is not None
    _, fields = bound
    assert [f.name for f in fields] == ["tool", "duration_ms", "trace_id", "span_id"]


async def _rich_started_line_for_whitespace_tool(caplog) -> str:
    """Run a ``read section`` tool call and return its rendered started line.

    Shared by ``test_whitespace_value_renders_quoted_via_render_rich`` and
    ``test_tool_name_with_whitespace_is_quoted`` — both assert the exact
    same quoting behaviour, probed the exact same way; only the
    docstring/comment explaining why differed.
    """
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read section"))
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(ctx, _ok_call_next)
    return render_rich(caplog.records[0])


async def test_whitespace_value_renders_quoted_via_render_rich(caplog):
    """The sibling of ``test_tool_name_with_whitespace_is_quoted`` below,
    probed at the point where quoting now actually happens:
    ``render_rich(record)`` — the root formatter (Tasks 1-2) — rather than
    ``record.getMessage()``, which is now plain unquoted ``%s`` substitution
    since the middleware stopped pre-rendering its own text. Same expected
    bytes as the old assertion; only the probe moved downstream with the
    responsibility."""
    assert 'tool="read section"' in await _rich_started_line_for_whitespace_tool(caplog)


async def test_tool_name_with_whitespace_is_quoted(caplog):
    # See the comment on test_tool_call_failed_line: the middleware emits a
    # record, render_rich renders it, and that rendered form — not
    # record.getMessage() — is the line an operator actually sees.
    assert 'tool="read section"' in await _rich_started_line_for_whitespace_tool(caplog)


async def test_render_value_escapes_embedded_quotes(caplog):
    # See the comment on test_tool_call_failed_line: render_rich, not
    # record.getMessage(), is where quoting/escaping happens.
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    call_next = _failing_call_next(ValueError('say"hi"'))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        pytest.raises(ValueError),
    ):
        await mw.on_message(ctx, call_next)
    assert r'error="say\"hi\""' in render_rich(caplog.records[-1])


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")],
)
async def test_render_value_escapes_control_chars_to_one_line(caplog, raw, escaped):
    # See the comment on test_tool_call_failed_line: render_rich, not
    # record.getMessage(), is where quoting/escaping happens.
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    err = ValueError("line one" + raw + "line two")
    call_next = _failing_call_next(err)
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        pytest.raises(ValueError),
    ):
        await mw.on_message(ctx, call_next)
    msg = render_rich(caplog.records[-1])
    assert raw not in msg
    assert 'error="line one' + escaped + 'line two"' in msg


# --- trace correlation (#319) -------------------------------------------------


def _records(caplog):
    """Only the middleware's own records.

    ``caplog`` installs its handler on the root logger, so a sibling
    logger's record would otherwise shift the indices below — turning an
    assertion failure into a confusing ``AttributeError``.
    """
    return [r for r in caplog.records if r.name == _LOGGER_NAME]


def _tracer():
    """A tracer backed by a real SDK provider, without touching global state.

    ``trace.set_tracer_provider`` is set-once per process, so tests use a
    local provider; ``start_as_current_span`` still attaches to the ambient
    context, which is what the middleware reads.
    """
    from opentelemetry.sdk.trace import TracerProvider

    return TracerProvider().get_tracer("test")


async def test_tool_call_lines_carry_trace_and_span_ids(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        _tracer().start_as_current_span("outer"),
    ):
        await mw.on_message(ctx, _ok_call_next)

    started = _records(caplog)[0].getMessage()
    assert re.search(r"\btrace_id=[0-9a-f]{32}\b", started), started
    assert re.search(r"\bspan_id=[0-9a-f]{16}\b", started), started


async def test_started_and_completed_share_the_same_trace(caplog):
    """The whole point: the pair must be joinable to one trace."""
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        _tracer().start_as_current_span("outer"),
    ):
        await mw.on_message(ctx, _ok_call_next)

    ids = [
        re.search(r"trace_id=([0-9a-f]{32})", r.getMessage()).group(1)
        for r in _records(caplog)
    ]
    assert len(ids) == 2
    assert ids[0] == ids[1]


async def test_json_formatter_carries_trace_ids_as_json_keys(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        _tracer().start_as_current_span("outer"),
    ):
        await mw.on_message(ctx, _ok_call_next)

    payload = json.loads(JsonFormatter().format(_records(caplog)[0]))
    assert re.fullmatch(r"[0-9a-f]{32}", payload["trace_id"])
    assert re.fullmatch(r"[0-9a-f]{16}", payload["span_id"])


async def test_failed_line_carries_trace_ids(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    call_next = _failing_call_next(ValueError("boom"))
    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        _tracer().start_as_current_span("outer"),
        pytest.raises(ValueError),
    ):
        await mw.on_message(ctx, call_next)

    failed = _records(caplog)[-1].getMessage()
    assert failed.startswith("tool_call_failed ")
    assert re.search(r"\btrace_id=[0-9a-f]{32}\b", failed), failed


async def test_no_trace_fields_when_no_span_is_active(caplog):
    """Servers without tracing must see byte-identical output to before."""
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(ctx, _ok_call_next)

    for record in _records(caplog):
        assert "trace_id=" not in record.getMessage()
        assert "span_id=" not in record.getMessage()


async def test_no_trace_fields_in_json_output_without_span(caplog):
    mw = RequestLoggingMiddleware()
    ctx = _context(method="tools/call", message=_ToolParams("read"))
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(ctx, _ok_call_next)

    payload = json.loads(JsonFormatter().format(_records(caplog)[0]))
    assert "trace_id" not in payload
    assert "span_id" not in payload


async def test_inbound_traceparent_correlates_without_an_sdk(caplog):
    """A propagated trace must correlate even with no SDK configured.

    FastMCP extracts ``traceparent`` from request ``_meta`` without
    gating on an SDK, so "no SDK" does not imply "no trace ids".
    """
    from opentelemetry import context as otel_context
    from opentelemetry import propagate

    carrier = {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
    token = otel_context.attach(propagate.extract(carrier))
    try:
        mw = RequestLoggingMiddleware()
        ctx = _context(method="tools/call", message=_ToolParams("read"))
        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            await mw.on_message(ctx, _ok_call_next)
    finally:
        otel_context.detach(token)

    started = _records(caplog)[0].getMessage()
    assert "trace_id=4bf92f3577b34da6a3ce929d0e0e4736" in started
    assert "span_id=00f067aa0ba902b7" in started


# --- protocol revision and client identity (#419) ----------------------------


def _bound_fields(record) -> dict[str, object]:
    bound = bind_record(record)
    assert bound is not None
    return {field.name: field.value for field in bound[1]}


async def _client_records(caplog, mode: str, client_info=None) -> list:
    """Run one ``tools/call`` from a real client on *mode*'s era."""
    from fastmcp import Client, FastMCP

    mcp = FastMCP("t")
    mcp.add_middleware(RequestLoggingMiddleware())

    @mcp.tool
    def echo() -> str:
        return "ok"

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        async with Client(mcp, mode=mode, client_info=client_info) as client:
            await client.call_tool("echo", {})
    return [r for r in caplog.records if r.name == _LOGGER_NAME]


@pytest.mark.parametrize(
    ("mode", "version"), [("legacy", "2025-11-25"), ("2026-07-28", "2026-07-28")]
)
async def test_started_line_carries_protocol_and_client(caplog, mode, version):
    import mcp.types as mt

    info = mt.Implementation(name="probe client", version="9.9")
    records = await _client_records(caplog, mode, info)
    started = next(r for r in records if r.getMessage().startswith("tool_call_started"))
    fields = _bound_fields(started)
    assert list(fields) == [
        "tool",
        "method",
        "source",
        "protocol_version",
        "client_name",
        "client_version",
    ]
    assert fields["protocol_version"] == version
    assert fields["client_name"] == "probe client"
    assert fields["client_version"] == "9.9"


async def test_terminal_lines_do_not_repeat_connection_fields(caplog):
    records = await _client_records(caplog, "2026-07-28")
    completed = next(
        r for r in records if r.getMessage().startswith("tool_call_completed")
    )
    assert "protocol_version" not in _bound_fields(completed)


async def test_initialize_line_carries_no_connection_fields(caplog):
    """``initialize`` precedes negotiation: the SDK's value then is a seed."""
    records = await _client_records(caplog, "legacy")
    started = next(
        r
        for r in records
        if r.getMessage().startswith("request_started method=initialize")
    )
    assert set(_bound_fields(started)) == {"method", "source"}


async def test_no_connection_fields_without_request_context(caplog):
    mw = RequestLoggingMiddleware()
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(_context(method="tools/list"), _ok_call_next)
    assert set(_bound_fields(caplog.records[0])) == {"method", "source"}


async def test_unidentified_client_gets_protocol_version_only(caplog):
    """A modern client may omit its identity; the line says nothing about it
    rather than inventing a value."""
    from types import SimpleNamespace

    rc = SimpleNamespace(
        protocol_version="2026-07-28",
        session=SimpleNamespace(client_params=None),
        meta={},
    )
    context = MiddlewareContext(
        message=None,
        method="tools/list",
        fastmcp_context=SimpleNamespace(request_context=rc),  # type: ignore[arg-type]
    )
    mw = RequestLoggingMiddleware()
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await mw.on_message(context, _ok_call_next)
    assert _bound_fields(caplog.records[0]) == {
        "method": "tools/list",
        "source": "client",
        "protocol_version": "2026-07-28",
    }
