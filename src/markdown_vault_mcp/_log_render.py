"""Turn a log record into output — the only module that knows the shape.

Ported from ``fastmcp_pvl_core._log_render`` 10.2.0 (#2342).

The logging standard (the ``logging-standard`` skill) writes ``logger.info("event_name key=%s",
value)``. Standard library logging keeps the two halves of that call apart
on the record: ``record.msg`` is the template the developer wrote,
``record.args`` holds the values. :func:`bind_record` recovers a
conforming record's **typed** fields by parsing the template
(``_log_grammar``) and pairing each placeholder with its argument — never
by reading a value back out of rendered text. That is why a value
containing a space, an ``=`` or a quote cannot confuse the result, and why
an ``int`` stays an ``int`` in JSON.

Two renderers consume that binding: :func:`render_rich` for the
``key=value`` text mode, :class:`JsonFormatter` for the one-object-per-line
mode. Both fall back to ``record.getMessage()`` for a non-conforming
record — non-conforming is the normal case for third-party records, and is
never treated as an error.

A log call must never raise because of how it was rendered. ``record.msg``
may be any object, not a ``str``; ``record.args`` may be a ``Mapping``
(stdlib's single-dict ``%(key)s`` form) rather than a tuple; a field value
may not be JSON-serialisable. Each of those degrades to the formatted
message rather than propagating — *within* :func:`render_rich` and
:class:`JsonFormatter` themselves. That alone is not the whole guarantee:
``logging.Formatter.format`` calls ``record.getMessage()`` *before*
``formatMessage`` ever runs, and ``RichHandler.emit`` calls it a second
time on its ``exc_info`` branch — both bypass this module's renderers
entirely, so a ``msg``/``args`` pair that raises out of ``getMessage()``
still reaches the caller as an exception unless something rewrites the
record before either handler runs. :class:`_NeverRaiseFilter` is that
something: attached to every handler
``_logging_setup._install_root_handlers`` installs, it makes ``getMessage()``
safe for *any* subsequent caller — this module's renderers included — by
rewriting an unrenderable record in place before a handler's own
``format``/``emit`` ever touches it. The guarantee holds for the chain
the logging setup installs; a handler an operator attached to root before
:func:`~markdown_vault_mcp._logging_setup.configure_logging_from_env` ran, and that this module
tolerates rather than replaces, sees the raw record and is not covered.

One record never conforms to the grammar because it is not ours to
template: uvicorn's ``uvicorn.access`` line. ``_logging_setup._AccessLogFilter``
parses and redacts it anyway, since it is the one place that knows what
uvicorn's fixed template means, and attaches the result to the record as
:class:`_AccessLogFields`. :class:`JsonFormatter` reads that attribute
straight through when present, instead of falling back to ``message`` —
``render_rich`` does not, so Rich mode is unaffected.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from markdown_vault_mcp._log_grammar import parse_log_template

_T = TypeVar("_T")

# JSON-native types: passed through as-is in JSON mode so an ``int``/
# ``float``/``bool``/``None`` keeps its type instead of becoming a string.
_JSON_NATIVE = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class _BoundField:
    """One field of a conforming record, paired with its actual value.

    *value* is the raw argument for a placeholder field (whatever type the
    caller passed) or the literal token's text for a literal field.
    *conversion* is the field's ``%``-conversion string (e.g. ``"%d"``) for
    a placeholder field, and ``None`` for a literal field — it is only
    needed to render Rich's textual form; JSON mode uses *value* directly.
    """

    name: str
    value: object
    conversion: str | None


def bind_record(
    record: logging.LogRecord,
) -> tuple[str, tuple[_BoundField, ...]] | None:
    """Recover the event and fields a conforming record was logged with.

    Parses ``record.msg`` — the template written in source — and pairs each
    placeholder with its value from ``record.args``. Values are never read
    back out of a rendered string, so one containing a space, an ``=`` or a
    quote cannot confuse the result, and an ``int`` stays an ``int``.

    Returns ``None`` for anything that is not a conforming record, which is
    how every renderer falls back to the formatted message: a template that
    does not match the grammar, an argument count that disagrees with it, a
    ``msg`` that is not a ``str`` (a caller may log any object), or ``args``
    given as the stdlib's single-``Mapping`` form.
    """
    msg = record.msg
    if not isinstance(msg, str):
        return None

    template = parse_log_template(msg)
    if template is None:
        return None

    args = record.args
    if isinstance(args, Mapping):
        return None
    if args is None:
        args = ()
    if not isinstance(args, tuple):
        # Defensive: normal ``Logger.info(msg, *args)`` usage always leaves
        # ``record.args`` a tuple, a ``Mapping``, or ``None``; anything else
        # means the record was built by hand in a shape this grammar does
        # not recognise.
        return None
    if len(args) != template.placeholder_count:
        return None

    args_iter = iter(args)
    fields = tuple(
        _BoundField(name=field.name, value=next(args_iter), conversion=field.conversion)
        if field.conversion is not None
        else _BoundField(name=field.name, value=field.literal, conversion=None)
        for field in template.fields
    )
    return template.event, fields


@dataclass(frozen=True)
class _AccessLogFields:
    """uvicorn access-record fields, parsed and already redacted.

    Attached to a ``uvicorn.access`` record by ``_logging._AccessLogFilter``
    (under :data:`_ACCESS_FIELDS_ATTR`) for every record whose shape it
    understood and kept — uvicorn owns that record's template, so it never
    conforms to the log-call grammar and :func:`bind_record` would
    otherwise fall back to a formatted ``message``.

    *path* is the same value the filter already wrote back into
    ``record.args`` — redacted once, by the filter, never re-derived here —
    so the JSON field below and the Rich line can never disagree about what
    the path was. *status* stays an ``int``, never a formatted string.
    """

    client: str
    method: str
    path: str
    status: int


_ACCESS_FIELDS_ATTR = "_pvl_core_access"
"""Record attribute name :class:`_AccessLogFields` is attached under."""


_RESERVED_ENVELOPE_KEYS = frozenset({"ts", "level", "logger", "event"})
"""Envelope keys a conforming field's name must never overwrite.

``ts``/``level``/``logger`` are written by :meth:`JsonFormatter._prefix`
before the body runs; ``event`` is written by :meth:`JsonFormatter._body`
itself, in the same dict literal ``_conforming`` builds, so a field
literally named ``event`` would overwrite *that* entry before the body
even returns — not just at the later ``dict.update`` in
:meth:`JsonFormatter.format`. Nothing in this project's own log calls
collides today, but ``level=`` and the other three are ordinary field
names a downstream's conforming call could easily choose, and the
grammar has no way to reject them at parse time — the check has to live
here, where the envelope's own shape is known.
"""


def render_value(value: object) -> str:
    """Render a field value for the rich (text) output mode.

    Strings containing whitespace or a double quote are wrapped in double
    quotes — with embedded backslashes, double quotes, and control
    characters (newline, carriage return, tab) escaped — so the record
    stays on one unambiguous ``key=value`` line; everything else renders
    bare.
    """
    text = str(value)
    if any(char.isspace() for char in text) or '"' in text:
        escaped = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return '"' + escaped + '"'
    return text


def _render_fields(fields: tuple[_BoundField, ...]) -> str:
    """Join bound fields into ``key=value`` text, in source order.

    A placeholder field's own ``%``-conversion is applied to its argument
    first (so ``%d`` / ``%.1f`` / ``%r`` format the way the developer wrote
    them), then :func:`render_value`'s quoting rule is applied to the
    result. A literal field's token is already text and only needs
    quoting.

    The argument is wrapped in a one-element tuple before the ``%`` —
    ``conversion % (value,)`` rather than ``conversion % value`` — so a
    value that is itself a tuple (``logger.info("batch items=%s", (1,
    2))``) is treated as one formatting argument instead of being unpacked
    positionally, which is what ``%`` does with a bare tuple on its right
    and would otherwise raise ``TypeError``.
    """
    parts = []
    for field in fields:
        text = (
            field.conversion % (field.value,) if field.conversion else str(field.value)
        )
        parts.append(field.name + "=" + render_value(text))
    return " ".join(parts)


def _or_fallback(render: Callable[[], _T], fallback: Callable[[], _T]) -> _T:
    """Try *render*; on any failure, use *fallback* instead.

    A log call must never raise because of how it was phrased: ``record.msg``
    may be any object, not a ``str``; ``record.args`` may pair a placeholder
    with a value its conversion cannot format; a caller's ``__str__`` (or a
    ``%r``/``%s`` conversion of one of its arguments) may itself raise. None
    of that is foreseeable from here, so the catch is intentionally broad —
    this is the one place in the module that says so, instead of every call
    site repeating the justification. *fallback* itself is assumed not to
    raise.
    """
    try:
        return render()
    except Exception:  # broad: rendering must never raise; see docstring
        return fallback()


def _unrenderable_placeholder(record: logging.LogRecord) -> str:
    """The one fixed string every unrenderable-record fallback uses.

    Shared by :func:`_safe_message` and :class:`_NeverRaiseFilter` so the
    placeholder text has a single definition — the record's ``msg``/``args``
    may be inspected here (via ``record.name``) but never rendered, since
    rendering is exactly what already failed.
    """
    return f"<unrenderable log record: {record.name}>"


def _safe_message(record: logging.LogRecord) -> str:
    """``record.getMessage()``, guarded against a ``msg``/arg that raises.

    ``getMessage()`` calls ``str(self.msg)`` and, for a template, applies
    ``%`` formatting — either can raise if a caller logged an object whose
    ``__str__`` (or a ``%r``/``%s`` conversion of one of its arguments)
    raises. A log call must never raise because of how it was phrased, so
    this is the last line of defence within this module's own renderers: a
    formatted message when possible, a fixed placeholder when not. It does
    not, by itself, stop a handler that calls ``record.getMessage()``
    directly (see :class:`_NeverRaiseFilter`) from raising — the two are
    complementary, not redundant.
    """
    return _or_fallback(record.getMessage, lambda: _unrenderable_placeholder(record))


class _NeverRaiseFilter(logging.Filter):
    """Rewrite an unrenderable record before any handler can raise on it.

    ``logging.Formatter.format`` calls ``record.getMessage()`` before
    ``formatMessage`` runs, and ``rich.logging.RichHandler.emit`` calls it
    a second time on its ``exc_info`` branch — both bypass
    :func:`render_rich` and :class:`JsonFormatter` entirely, so a ``msg``
    that is not a ``str``, an argument count that disagrees with the
    template, or a value whose ``__str__``/conversion raises reaches the
    caller as an uncaught exception unless something intervenes first.

    A filter runs per *handler*, not per logger — a filter attached to the
    root logger itself is never consulted for a record logged on a child
    logger, only the originating logger's own filters and each handler's
    are. Attaching this filter to every handler
    ``_logging._install_root_handlers`` installs is therefore what makes a
    log call never raise, not a filter on the logger tree.

    On a record whose ``getMessage()`` raises, replaces ``record.msg``
    with :func:`_unrenderable_placeholder` and clears ``record.args`` —
    the rewrite fastmcp-pvl-core's ``SecretMaskFilter`` performed when masking, so
    this handler and every one after it, and
    this module's own renderers, all see a ``getMessage()`` that returns
    cleanly from then on. A record that renders fine passes through
    completely unchanged. Never suppresses a record: always returns
    ``True``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = _or_fallback(record.getMessage, lambda: None)
        if rendered is None:
            record.msg = _unrenderable_placeholder(record)
            record.args = ()
        return True


def render_rich(record: logging.LogRecord) -> str:
    """Render *record* as the logging standard's ``event key=value`` text line.

    A conforming record renders its event name followed by its fields; a
    non-conforming one renders its normal formatted message, unchanged.
    Never raises: a conforming record whose grammar-conforming shape hides
    a runtime mismatch — a ``%d`` conversion given a value ``int()`` cannot
    format, or a value whose ``__str__`` itself raises — falls back to
    :func:`_safe_message` exactly like a non-conforming one.
    """
    bound = bind_record(record)
    if bound is None:
        return _safe_message(record)

    event, fields = bound

    def _rendered_line() -> str | None:
        rendered = _render_fields(fields)
        return f"{event} {rendered}" if rendered else event

    line = _or_fallback(_rendered_line, lambda: None)
    return line if line is not None else _safe_message(record)


def _conforming_body(event: str, fields: tuple[_BoundField, ...]) -> dict[str, object]:
    """The JSON body of a conforming record: its ``event`` and typed fields.

    Raises whatever ``str()`` of an unrenderable field value raises; the
    caller, :meth:`JsonFormatter._body`, falls back to the message then.
    """
    conforming: dict[str, object] = {"event": event}
    # Every name the call carries, so a rename can dodge a name
    # that has not been placed yet — ``level`` is renamed before
    # a later ``field_level`` exists, and checking only what is
    # already in the dict would miss it.
    taken = {field.name for field in fields} | {"event"}
    for field in fields:
        value = field.value
        name = field.name
        if name in _RESERVED_ENVELOPE_KEYS:
            # A field named e.g. ``level`` would otherwise
            # overwrite the record's real severity — either
            # right here (a field named ``event``, colliding
            # with the entry this dict literal just set) or
            # later, when JsonFormatter.format merges this
            # dict into the envelope. Prefixed, not dropped:
            # the value still reaches the consumer, just not
            # under the name that would shadow the envelope's own.
            name = f"field_{name}"
            # Prefixing can collide in turn, if the same call
            # also carries a field literally named
            # ``field_level``. Keep prefixing until the name is
            # free: losing one of two caller-supplied values
            # silently is worse than an ugly key.
            while name in taken:
                name = f"field_{name}"
            taken.add(name)
        conforming[name] = value if isinstance(value, _JSON_NATIVE) else str(value)
    return conforming


class JsonFormatter(logging.Formatter):
    """One JSON object per record, for log aggregators.

    Envelope, in order: ``ts`` (ISO-8601 UTC), ``level``, ``logger``, then
    one of: ``client``/``method``/``path``/``status`` (a ``uvicorn.access``
    record the access filter understood — see :class:`_AccessLogFields`),
    ``event`` plus one key per field (a conforming record), or ``message``
    (the formatted message, for everything else) — then ``trace_id``/
    ``span_id`` when present on the record, then ``exception`` when the
    record carries a traceback.

    A conforming field whose name collides with a reserved envelope key
    (``ts``/``level``/``logger``/``event``) is emitted under a
    ``field_``-prefixed name instead — see :data:`_RESERVED_ENVELOPE_KEYS`
    — so a caller's ``level=`` cannot overwrite the record's real
    severity, and so on for the other three.

    Uses ``json.dumps(..., default=str, allow_nan=False)`` so a field
    value that is not JSON-native (a ``Path``, an exception instance, ...)
    stringifies instead of raising, and a ``NaN``/``Infinity`` float —
    valid Python, not valid JSON — raises instead of emitting a bare
    token a strict consumer would reject. Both the stringify-on-``default``
    call and the ``allow_nan`` check happen inside ``json.dumps`` itself,
    which sits outside every ``_or_fallback`` funnel this module otherwise
    uses (``_conforming`` stringifies each field's value up front, so a
    field's own ``str()`` failure is already caught there; what reaches
    ``json.dumps`` unguarded is ``_suffix``'s ``trace_id``/``span_id``,
    taken from ``extra=`` verbatim). :meth:`format` funnels the ``dumps``
    call itself through ``_or_fallback`` so either failure degrades to a
    ``message``-only envelope — built fresh from ``str``-only values, so
    the fallback ``dumps`` call cannot itself fail — rather than letting
    ``json.dumps`` raise out of a handler's ``emit`` and print logging's
    multi-line ``--- Logging error ---`` block onto what is supposed to be
    a one-JSON-object-per-line stream.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Assemble the envelope from three steps, in emission order."""
        prefix = self._prefix(record)
        envelope = {**prefix, **self._body(record), **self._suffix(record)}
        return _or_fallback(
            lambda: json.dumps(envelope, default=str, allow_nan=False),
            lambda: json.dumps(
                {**prefix, "message": _safe_message(record)}, default=str
            ),
        )

    def _prefix(self, record: logging.LogRecord) -> dict[str, object]:
        """``ts``/``level``/``logger`` — always present, always first."""
        return {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }

    def _body(self, record: logging.LogRecord) -> dict[str, object]:
        """The record's payload: access fields, bound event+fields, or ``message``.

        Exactly one of the three shapes applies, checked in that order: a
        ``uvicorn.access`` record the access filter already understood (see
        :class:`_AccessLogFields`), a conforming record's typed event and
        fields, or — for everything else, including a conforming record
        whose fields turn out not to be renderable — the formatted message.
        """
        access = getattr(record, _ACCESS_FIELDS_ATTR, None)
        if isinstance(access, _AccessLogFields):
            # The filter already parsed and redacted this record; read its
            # values straight off the attribute rather than re-deriving them
            # from record.args, so this can never disagree with the filter.
            return {
                "client": access.client,
                "method": access.method,
                "path": access.path,
                "status": access.status,
            }

        bound = bind_record(record)
        if bound is not None:
            event, fields = bound
            conforming = _or_fallback(
                lambda: _conforming_body(event, fields), lambda: None
            )
            if conforming is not None:
                return conforming

        return {"message": _safe_message(record)}

    def _suffix(self, record: logging.LogRecord) -> dict[str, object]:
        """``trace_id``/``span_id`` when present, then ``exception`` when traced.

        Two sources are recognised, in this order: ``record.trace_id``/
        ``record.span_id`` — set via ``extra=``, by a caller wiring its
        own correlation ids straight onto the record — then
        ``record.otelTraceID``/``record.otelSpanID``, the attribute names
        ``opentelemetry-instrumentation-logging`` injects into every
        record once ``OTEL_PYTHON_LOG_CORRELATION=true`` (see the Telemetry
        recipe in fastmcp-pvl-core's ``README.md``). Nothing in this project sets
        ``trace_id``/``span_id`` itself outside a test — a first-party
        conforming record carries them as ordinary *fields* instead (see
        ``_request_logging._trace_fields``), which reach the envelope
        through ``_body``, not here. Without the ``otel*`` fallback this
        branch was unreachable in a production JSON-mode process running
        the documented recipe: nothing else ever sets the plain
        ``trace_id``/``span_id`` attributes this method originally looked
        for.

        That instrumentor sets both ``otelTraceID``/``otelSpanID`` to the
        literal string ``"0"`` when no span is in scope — its own
        placeholder for "no valid context", the same convention
        ``_logging_middleware._trace_fields`` follows for the API's own
        ``INVALID_SPAN`` — so ``"0"`` is treated as absent here too,
        rather than emitted as a fake correlation id on every untraced
        line.
        """
        suffix: dict[str, object] = {}
        trace_id = getattr(record, "trace_id", None)
        if trace_id is None:
            otel_trace_id = getattr(record, "otelTraceID", None)
            trace_id = otel_trace_id if otel_trace_id not in (None, "0") else None
        span_id = getattr(record, "span_id", None)
        if span_id is None:
            otel_span_id = getattr(record, "otelSpanID", None)
            span_id = otel_span_id if otel_span_id not in (None, "0") else None
        if trace_id is not None:
            suffix["trace_id"] = trace_id
        if span_id is not None:
            suffix["span_id"] = span_id
        if record.exc_info:
            suffix["exception"] = self.formatException(record.exc_info)
        return suffix
