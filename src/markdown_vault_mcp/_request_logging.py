"""Conforming, tool-aware request-logging middleware and its installation.

Ported from ``fastmcp_pvl_core._logging_middleware`` and ``_middleware`` 10.2.0
(#2341).

Emits log lines in the logging standard for every MCP message: a bare
snake_case event name as the first token, followed by ``key=value`` pairs.
Tool calls surface the tool name via ``tool=<name>``; request duration is
carried inline on the terminal (``*_completed`` / ``*_failed``) line. This
middleware replaces FastMCP's ``LoggingMiddleware`` and ``TimingMiddleware``;
:func:`wire_middleware_stack` installs it.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import FastMCPError
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from opentelemetry import trace

from markdown_vault_mcp._connection import connection_identity

if TYPE_CHECKING:
    from fastmcp import FastMCP

_DEFAULT_LOGGER_NAME = "fastmcp.middleware.requests"


def _trace_fields() -> dict[str, object]:
    """Return ``trace_id`` / ``span_id`` when a valid span is in scope.

    Reads the ambient OpenTelemetry span context — the middleware runs
    inside FastMCP's own MCP span, so these ids join a log line to the
    trace containing it.

    Returns an empty dict when no valid span context is in scope. That
    covers the common untraced case — with no OpenTelemetry SDK the API
    hands back ``INVALID_SPAN``, whose context reports
    ``is_valid == False`` — so such servers keep byte-identical log
    output.

    It is *not* the same as "no SDK installed". FastMCP extracts an
    inbound ``traceparent`` from request ``_meta`` without gating on an
    SDK, and the resulting remote ``NonRecordingSpan`` has a valid
    context. A client that propagates trace context therefore gets
    correlated log lines out of an otherwise untraced server, which is
    the desirable behaviour. Note the referent shifts in that case:
    ``span_id`` is the *caller's* span, since the server created none of
    its own.

    Ids use the W3C hex forms (32 and 16 lowercase hex digits) rather
    than the ints the API exposes, so they can be pasted straight into a
    trace backend's search box.
    """
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }


def _connection_fields(context: MiddlewareContext[Any]) -> dict[str, object]:
    """Return ``protocol_version`` / ``client_name`` / ``client_version`` when known.

    Each field is present only when the request establishes it, so an
    absent field means "not known", never a placeholder. ``initialize`` gets
    none: while it is in flight the SDK holds a pre-handshake seed, not the
    negotiated revision, and no client params yet. The next message on the
    connection (``notifications/initialized``) carries the negotiated values.
    """
    if context.method == "initialize":
        return {}
    identity = connection_identity(context.fastmcp_context)
    fields = {
        "protocol_version": identity.protocol_version,
        "client_name": identity.client_name,
        "client_version": identity.client_version,
    }
    return {name: value for name, value in fields.items() if value is not None}


def _duration_ms(start: float) -> float:
    """Elapsed wall-clock milliseconds since *start*, rounded to 2 dp."""
    return round((time.perf_counter() - start) * 1000, 2)


class RequestLoggingMiddleware(Middleware):
    """Logs every MCP message as a conforming, tool-aware event pair.

    Overrides only :meth:`on_message` — the single outermost dispatch
    hook — so each message produces exactly one ``*_started`` line and
    one terminal (``*_completed`` / ``*_failed``) line. Overriding a
    method-specific hook in addition would double-log.

    Tool calls (``tools/call``) use the ``tool_call_*`` event vocabulary
    and carry ``tool=<name>``; every other message uses ``request_*`` or
    ``notification_*`` keyed by ``method=``.

    The ``*_started`` line also carries ``protocol_version``,
    ``client_name`` and ``client_version`` for the connection the message
    arrived over, each only when known: a modern (2026-07-28) client may
    leave its identity out, and ``initialize`` precedes negotiation.

    ``*_completed`` is INFO. ``*_failed`` is logged at the exception's
    ``log_level`` when it is a ``FastMCPError`` and at ERROR otherwise, so a
    ``ToolError(msg, log_level=logging.INFO)`` for a request the model must
    change is not recorded as a server fault.

    Every record is logged through the shared log-call grammar — one
    template built from the event name and field names, with the field
    values passed as ``args`` — rather than rendered here. The root
    handler's formatter (the log renderer) decides whether that
    renders as Rich text or JSON; this middleware no longer picks an
    output shape.

    Args:
        include_traceback: When ``True``, ``*_failed`` records carry
            ``exc_info`` so the log handler renders a traceback.
        logger: Logger to emit through. Defaults to a logger named
            ``fastmcp.middleware.requests``.
    """

    def __init__(
        self,
        *,
        include_traceback: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        self.include_traceback = include_traceback
        self.logger = logger or logging.getLogger(_DEFAULT_LOGGER_NAME)

    async def on_message(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> Any:
        is_tool_call = context.method == "tools/call"
        if is_tool_call:
            event_base = "tool_call"
            id_key = "tool"
            id_val: str = getattr(context.message, "name", None) or "unknown"
        else:
            event_base = context.type
            id_key = "method"
            id_val = context.method or "unknown"

        started_fields: dict[str, object] = {id_key: id_val}
        if is_tool_call:
            started_fields["method"] = "tools/call"
        started_fields["source"] = context.source
        started_fields.update(_connection_fields(context))
        self._emit(event_base + "_started", started_fields, logging.INFO)

        start = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as exc:
            # The level follows how the call ended, as the rest of the stack
            # classifies it: a FastMCPError carries the level its raiser chose,
            # anything else is ERROR. An exception from a tool's own body
            # arrives as a ToolError (FastMCP converts it), so a tool picks
            # the level of this line; a failure FastMCP raises before the body
            # runs (an unknown tool name, arguments that fail the schema) keeps
            # its own type and level (fastmcp-pvl-core ADR 0005 §2.4).
            level = exc.log_level if isinstance(exc, FastMCPError) else logging.ERROR
            self._emit(
                event_base + "_failed",
                {
                    id_key: id_val,
                    "duration_ms": _duration_ms(start),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                level,
                exc_info=self.include_traceback,
            )
            raise
        self._emit(
            event_base + "_completed",
            {id_key: id_val, "duration_ms": _duration_ms(start)},
            logging.INFO,
        )
        return result

    def _emit(
        self,
        event: str,
        fields: dict[str, object],
        level: int,
        *,
        exc_info: bool = False,
    ) -> None:
        """Emit one conforming record through the shared log-call grammar.

        Builds a template from the event name and field names and passes
        the field values as ``args``, so the record is rendered by
        whichever formatter is installed on the root handler (Rich text
        or JSON), not by this middleware.

        ``exc_info=True`` attaches the current exception triple; ``False``
        (default) passes ``None`` to logging so the record's ``exc_info``
        attribute is ``None`` rather than ``False``.
        """
        # logging stores exc_info=False as False on the record, which breaks
        # ``assert record.exc_info is None`` assertions.  Always pass None for
        # the "no traceback" case; logging treats None identically to False.
        effective_exc_info = sys.exc_info() if exc_info else None
        # Appended last so the leading shape of every existing line is
        # unchanged for servers that do have tracing configured.
        fields = {**fields, **_trace_fields()}
        # Field names come from this middleware's own fixed vocabulary
        # (tool, method, source, protocol_version, client_name,
        # client_version, duration_ms, error_type, error, trace_id, span_id),
        # never from caller-controlled data, so none of them can
        # ever contain a "%" or a space — either of which would break the
        # template below.
        #
        # Built dynamically, deliberately: `find_nonconforming_log_calls`
        # only recognises a literal template on a level-method call
        # (`logger.info("event key=%s", ...)`), so a template assembled at
        # runtime like this one is invisible to it either way, conforming
        # or not — the static checker cannot see this line either
        # attesting or objecting. Runtime construction was still the right
        # call: the event/field-name vocabulary above is genuinely
        # data-driven (tool vs. request/notification, trace fields present
        # or not), and hand-writing every combination as a literal to buy
        # checker visibility would just move the risk of drift into
        # keeping N literals in sync with this dict instead. What actually
        # backs the conformance claim is the record `bind_record` recovers
        # at runtime matching the grammar — proved by
        # `tests/test_request_logging.py::test_emitted_record_is_conforming`
        # and `::test_field_order_is_tool_then_duration_then_trace_ids_last`,
        # not by anything `find_nonconforming_log_calls` can confirm ahead
        # of time.
        template = f"{event} " + " ".join(f"{name}=%s" for name in fields)
        self.logger.log(level, template, *fields.values(), exc_info=effective_exc_info)


def wire_middleware_stack(mcp: FastMCP) -> None:
    """Install the standard logging middleware on a FastMCP instance.

    Installs a single :class:`RequestLoggingMiddleware`, which emits
    conforming, tool-aware log lines — a bare event name as the
    first token, then ``key=value`` pairs — with request timing carried
    inline on the terminal line. Every record is logged through the
    shared log-call grammar; the process-wide output format (Rich text
    or JSON) is chosen once at the root handler, not here.

    Traceback inclusion on failure records is inferred from the root
    logger — tracebacks are emitted when it is at ``DEBUG`` or below.
    Call this *after* CLI / log-level setup so the inference sees the
    right level.

    Args:
        mcp: The :class:`FastMCP` instance to install middleware on.
    """
    include_traceback = logging.getLogger().isEnabledFor(logging.DEBUG)
    mcp.add_middleware(RequestLoggingMiddleware(include_traceback=include_traceback))
