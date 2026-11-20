"""The renderer — §3/§3a of the root-logging-ownership spec.

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_log_render.py`` (#2342).

Covers binding a record to its typed fields (never by re-parsing rendered
text), the two output shapes (Rich text, JSON), and the "a log call must
never raise" guarantee.
"""

from __future__ import annotations

import json
import logging

from markdown_vault_mcp._log_render import (
    _ACCESS_FIELDS_ATTR,
    JsonFormatter,
    _AccessLogFields,
    bind_record,
    render_rich,
    render_value,
)

_LOGGER_NAME = "test.log_render"


def _record(
    msg: object,
    args: object = (),
    *,
    exc_info: object = None,
    extra: dict[str, object] | None = None,
) -> logging.LogRecord:
    """A ``logging.LogRecord`` for renderer tests. Always level ``INFO`` —
    no test here exercises level-dependent behaviour, so it is not a knob.
    """
    record = logging.LogRecord(
        name=_LOGGER_NAME,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


# --- binding -----------------------------------------------------------------


def test_conforming_template_binds_placeholder_and_literal_fields():
    record = _record("cache_write ttl=%d hit=%s status=warm", (3600, True))
    bound = bind_record(record)
    assert bound is not None
    event, fields = bound
    assert event == "cache_write"
    assert [(f.name, f.value) for f in fields] == [
        ("ttl", 3600),
        ("hit", True),
        ("status", "warm"),
    ]


def test_bare_event_binds_with_no_fields():
    record = _record("server_started", ())
    bound = bind_record(record)
    assert bound is not None
    event, fields = bound
    assert event == "server_started"
    assert fields == ()


def test_non_conforming_template_returns_none():
    record = _record("Scanned %d style(s) from %s", (3, "/tmp"))
    assert bind_record(record) is None


def test_arg_count_mismatch_returns_none():
    record = _record("cache_write ttl=%d hit=%s", (3600,))
    assert bind_record(record) is None


def test_non_str_msg_returns_none():
    record = _record({"a": 1}, ())
    assert bind_record(record) is None


def test_mapping_args_returns_none():
    # Mirrors real ``Logger.info(msg, {"k": "v"})`` usage: the caller passes
    # one positional dict, which logging's own ``LogRecord.__init__``
    # unwraps from the args tuple onto ``record.args`` directly (stdlib's
    # ``%(key)s`` dict-style formatting).
    record = _record("event key=%(k)s", ({"k": "v"},))
    assert isinstance(record.args, dict)
    assert bind_record(record) is None


# --- rich ----------------------------------------------------------------


def test_rich_renders_typed_values_bare():
    record = _record("cache_write ttl=%d hit=%s", (3600, True))
    assert render_rich(record) == "cache_write ttl=3600 hit=True"


def test_rich_matches_the_documented_line_byte_for_byte():
    record = _record(
        "tool_call_failed tool=%s duration_ms=%s error=%s",
        ("read", 109.84, "Section '1.3' not found"),
    )
    expected = (
        "tool_call_failed tool=read duration_ms=109.84 "
        "error=\"Section '1.3' not found\""
    )
    assert render_rich(record) == expected


def test_rich_quotes_value_containing_whitespace():
    record = _record("event name=%s", ("has space",))
    assert render_rich(record) == 'event name="has space"'


def test_rich_quotes_value_containing_double_quote():
    record = _record("event name=%s", ('say"hi"',))
    assert render_rich(record) == r'event name="say\"hi\""'


def test_rich_falls_back_to_formatted_message_for_non_conforming_record():
    record = _record("Scanned %d style(s) from %s", (3, "/tmp"))
    assert render_rich(record) == "Scanned 3 style(s) from /tmp"


def test_rich_tuple_value_renders_as_one_field_not_unpacked():
    # A tuple argument for a single ``%s`` placeholder must not be treated
    # as multiple positional args to the ``%`` operator (which would raise
    # ``TypeError: not all arguments converted``).
    record = _record("batch_done items=%s", ((1, 2),))
    # "(1, 2)" contains a space, so render_value's quoting rule wraps it —
    # the point of the test is that it renders at all, as one field, rather
    # than raising or being unpacked into two.
    assert render_rich(record) == 'batch_done items="(1, 2)"'


def test_rich_never_raises_when_conversion_mismatches_value_type():
    # Grammar-conforming shape, but a runtime type mismatch (%d given a
    # string) that even ``record.getMessage()`` cannot format — must not
    # raise. Same mismatch defeats the plain-message fallback too, so the
    # exact fallback text is an implementation detail; only "does not
    # raise" is asserted.
    record = _record("event count=%d", ("not-a-number",))
    render_rich(record)


def test_render_value_bare_for_plain_token():
    assert render_value(3600) == "3600"
    assert render_value("plain") == "plain"


def test_render_value_quotes_whitespace_and_quotes():
    assert render_value("has space") == '"has space"'
    assert render_value('say"hi"') == r'"say\"hi\""'


# --- json ------------------------------------------------------------------


def test_json_conforming_record_carries_event_and_typed_fields():
    record = _record("cache_write ttl=%d hit=%s", (3600, True))
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["event"] == "cache_write"
    assert payload["ttl"] == 3600
    assert isinstance(payload["ttl"], int)
    assert payload["hit"] is True
    assert "message" not in payload


def test_json_envelope_has_ts_level_logger_first():
    record = _record("server_started", ())
    payload = json.loads(JsonFormatter().format(record))
    assert list(payload.keys())[:3] == ["ts", "level", "logger"]
    assert payload["level"] == "INFO"
    assert payload["logger"] == _LOGGER_NAME


def test_json_ts_is_iso8601_utc():
    record = _record("server_started", ())
    payload = json.loads(JsonFormatter().format(record))
    # Basic shape check: date "T" time, offset to UTC.
    assert "T" in payload["ts"]
    assert payload["ts"].endswith("+00:00") or payload["ts"].endswith("Z")


def test_json_non_conforming_record_carries_message():
    record = _record("Scanned %d style(s) from %s", (3, "/tmp"))
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "Scanned 3 style(s) from /tmp"
    assert "event" not in payload


def test_json_literal_field_is_a_string():
    record = _record("epo_ops status=configured", ())
    payload = json.loads(JsonFormatter().format(record))
    assert payload["status"] == "configured"


def test_json_non_serialisable_value_is_stringified():
    from pathlib import Path

    record = _record("event path=%s", (Path("/tmp/x"),))
    payload = json.loads(JsonFormatter().format(record))
    assert payload["path"] == str(Path("/tmp/x"))


def test_json_exception_value_is_stringified_not_raised():
    record = _record("event error=%s", (ValueError("boom"),))
    payload = json.loads(JsonFormatter().format(record))
    assert payload["error"] == "boom"


def test_json_carries_exception_traceback_string():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record("event_failed", (), exc_info=sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert "exception" in payload
    assert "ValueError: boom" in payload["exception"]


def _assert_correlated(record) -> None:
    """The envelope carries the trace ids, whichever attribute names carried them in.

    Two tests ask this: one for the ids the request middleware sets, one for
    the ``otel*`` names ``opentelemetry-instrumentation-logging`` injects.
    Same expected envelope either way — that is the point.
    """
    payload = json.loads(JsonFormatter().format(record))
    assert payload["trace_id"] == "a" * 32
    assert payload["span_id"] == "b" * 16


def test_json_carries_trace_and_span_ids_when_present_on_record():
    record = _record(
        "event",
        (),
        extra={"trace_id": "a" * 32, "span_id": "b" * 16},
    )
    _assert_correlated(record)


def test_json_omits_trace_and_span_ids_when_absent():
    record = _record("event", ())
    payload = json.loads(JsonFormatter().format(record))
    assert "trace_id" not in payload
    assert "span_id" not in payload


def test_json_key_order_is_envelope_then_event_then_trace_then_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record(
            "event_failed reason=%s",
            ("bad",),
            exc_info=sys.exc_info(),
            extra={"trace_id": "a" * 32, "span_id": "b" * 16},
        )
    payload = JsonFormatter().format(record)
    keys = list(json.loads(payload).keys())
    assert keys == [
        "ts",
        "level",
        "logger",
        "event",
        "reason",
        "trace_id",
        "span_id",
        "exception",
    ]


# --- uvicorn access ----------------------------------------------------------


def _access_record(
    client: str, method: str, path: str, status: int
) -> logging.LogRecord:
    # Same template/args shape uvicorn logs "uvicorn.access" with; the
    # attribute under test is attached by _AccessLogFilter, not derived by
    # JsonFormatter, so it is set explicitly here rather than parsed.
    record = _record(
        '%s - "%s %s HTTP/%s" %d',
        (client, method, path, "1.1", status),
    )
    setattr(
        record,
        _ACCESS_FIELDS_ATTR,
        _AccessLogFields(client=client, method=method, path=path, status=status),
    )
    return record


def test_json_access_record_carries_client_method_path_status_as_fields():
    record = _access_record("1.2.3.4:5678", "GET", "/mcp", 404)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["client"] == "1.2.3.4:5678"
    assert payload["method"] == "GET"
    assert payload["path"] == "/mcp"
    assert payload["status"] == 404
    assert isinstance(payload["status"], int)
    assert "message" not in payload


def test_json_access_record_carries_the_already_redacted_path():
    # The attribute is the single source: JsonFormatter must not re-derive
    # the path, only read whatever value the filter already redacted.
    record = _access_record("1.2.3.4:5678", "GET", "/transfer/<redacted>", 404)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["path"] == "/transfer/<redacted>"


def test_json_access_record_without_the_attribute_falls_back_to_message():
    # A uvicorn.access record the filter did not understand (or that never
    # passed through the filter) carries no attribute, and must render like
    # any other non-conforming record.
    record = _record(
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5678", "GET", "/mcp", "1.1", 404),
    )
    payload = json.loads(JsonFormatter().format(record))
    assert "client" not in payload
    assert "message" in payload
    assert payload["message"] == '1.2.3.4:5678 - "GET /mcp HTTP/1.1" 404'


def test_rich_ignores_the_access_fields_attribute():
    # Rich mode is unchanged: the attribute is a JSON-only concern.
    record = _access_record("1.2.3.4:5678", "GET", "/mcp", 404)
    assert render_rich(record) == record.getMessage()


# --- never raises ------------------------------------------------------------


class _RaisingStr:
    def __str__(self) -> str:
        raise RuntimeError("str exploded")


def test_rich_never_raises_when_message_str_raises():
    record = _record(_RaisingStr(), ())
    # Must not raise; the exact fallback text is an implementation detail.
    render_rich(record)


def test_json_never_raises_when_message_str_raises():
    record = _record(_RaisingStr(), ())
    JsonFormatter().format(record)


def test_json_tuple_value_renders_as_one_field_not_unpacked():
    record = _record("batch_done items=%s", ((1, 2),))
    payload = json.loads(JsonFormatter().format(record))
    assert payload["items"] == "(1, 2)"


def test_json_never_raises_when_conversion_mismatches_value_type():
    record = _record("event count=%d", ("not-a-number",))
    JsonFormatter().format(record)


def test_json_never_raises_when_field_value_str_raises():
    record = _record("event value=%s", (_RaisingStr(),))
    JsonFormatter().format(record)


def test_rich_never_raises_when_field_value_str_raises():
    record = _record("event value=%s", (_RaisingStr(),))
    render_rich(record)


# --- reserved-key collision (item 3: a conforming field must not clobber
# the envelope's own ts/level/logger/event) ----------------------------------


def test_json_conforming_field_names_do_not_overwrite_the_envelope():
    record = _record("e level=%s logger=%s ts=%s", ("L1", "other", "T"))
    payload = json.loads(JsonFormatter().format(record))
    # The record's real severity, logger and timestamp survive...
    assert payload["level"] == "INFO"
    assert payload["logger"] == _LOGGER_NAME
    assert payload["ts"] != "T"
    # ...and the caller's colliding field values are not dropped, just
    # renamed out of the way.
    assert payload["field_level"] == "L1"
    assert payload["field_logger"] == "other"
    assert payload["field_ts"] == "T"


def test_json_field_named_event_does_not_overwrite_the_event_name():
    # "event" collides with the key _conforming itself writes first, not
    # just with a later envelope.update — a field named "event" would
    # otherwise clobber the true event name inside the same dict literal.
    record = _record("e event=%s", ("other",))
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "e"
    assert payload["field_event"] == "other"


# --- trace correlation (item 4: the otel* attribute fallback) ---------------


def test_json_carries_otel_trace_and_span_ids_when_present_on_record():
    record = _record(
        "event", (), extra={"otelTraceID": "a" * 32, "otelSpanID": "b" * 16}
    )
    _assert_correlated(record)


def test_json_omits_otel_ids_that_are_the_no_span_sentinel():
    # opentelemetry-instrumentation-logging sets both to the literal "0"
    # when no valid span is in scope; that must not surface as a fake
    # correlation id on every untraced line.
    record = _record("event", (), extra={"otelTraceID": "0", "otelSpanID": "0"})
    payload = json.loads(JsonFormatter().format(record))
    assert "trace_id" not in payload
    assert "span_id" not in payload


def test_json_explicit_trace_id_wins_over_otel_trace_id():
    record = _record(
        "event", (), extra={"trace_id": "explicit", "otelTraceID": "a" * 32}
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["trace_id"] == "explicit"


# --- json.dumps funnel (item 5: NaN/Infinity and a suffix value whose
# str() raises must not break the one-JSON-object-per-line stream) ----------


def test_json_nan_field_value_does_not_emit_invalid_json():
    record = _record("event value=%s", (float("nan"),))
    line = JsonFormatter().format(record)
    assert "NaN" not in line
    payload = json.loads(line)
    assert "message" in payload


def test_json_suffix_value_str_raise_does_not_break_line_orientation():
    # trace_id/span_id come straight from extra= without going through
    # _conforming's per-field str() guard, so a raising __str__ here only
    # surfaces inside json.dumps's own default=str callback.
    record = _record("event", (), extra={"trace_id": _RaisingStr()})
    line = JsonFormatter().format(record)
    assert "\n" not in line
    payload = json.loads(line)
    assert "message" in payload


def test_json_reserved_rename_does_not_clobber_an_existing_field_name():
    """The rename must not lose a value it collides with in turn.

    A call carrying both ``level=`` and ``field_level=`` used to map both
    onto ``field_level``; whichever came second won and the other vanished
    from the envelope with no error.
    """
    record = _record("event level=%s field_level=%s", ("severity", "already-taken"))
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert sorted(v for k, v in payload.items() if k.startswith("field_")) == [
        "already-taken",
        "severity",
    ]
