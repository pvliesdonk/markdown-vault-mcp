"""The log-call grammar — §3a of the root-logging-ownership spec.

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_log_grammar.py`` (#2340).
"""

from __future__ import annotations

import pytest

from markdown_vault_mcp._log_grammar import parse_log_template

CONFORMING = [
    # (template, event, [(name, conversion, literal), ...], placeholder_count)
    ("server_started", "server_started", [], 0),
    (
        "standard_cache_hit identifier=%s",
        "standard_cache_hit",
        [("identifier", "%s", None)],
        1,
    ),
    (
        "cache_write ttl=%d hit=%s",
        "cache_write",
        [("ttl", "%d", None), ("hit", "%s", None)],
        2,
    ),
    ("epo_ops status=configured", "epo_ops", [("status", None, "configured")], 0),
    (
        "s2_keepalive_not_started reason=no_api_key",
        "s2_keepalive_not_started",
        [("reason", None, "no_api_key")],
        0,
    ),
    ("probe waiting_s=%.1f", "probe", [("waiting_s", "%.1f", None)], 1),
    ("probe payload=%r", "probe", [("payload", "%r", None)], 1),
    (
        "mixed a=%s b=literal c=%d",
        "mixed",
        [("a", "%s", None), ("b", None, "literal"), ("c", "%d", None)],
        2,
    ),
]

NON_CONFORMING = [
    "OllamaProvider initialised: host=%s model=%s",  # prose prefix
    "Service started",  # prose, capitalised
    "s2_rate_limited attempt=%d/%d",  # compound placeholder
    "probe waiting=%.1fs",  # unit suffix on placeholder
    "ratio pct=%s%%",  # %% escape
    "Scanned %d style(s) from %s",  # positional, no names
    "event stray",  # bare token, no "="
    "event =%s",  # empty field name
    "event Name=%s",  # non-snake_case field name
    "Event_Name key=%s",  # non-snake_case event
    "event key=",  # empty value
    "event key=with space",  # space inside literal
    "",  # empty template
    " event key=%s",  # leading space
    "event key=%s ",  # trailing space
    "event  key=%s",  # doubled separator
    "event k=% d",  # space flag: no longer a valid conversion character
]


@pytest.mark.parametrize(
    ("template", "event", "fields", "placeholders"),
    CONFORMING,
    ids=[case[0] or "<empty>" for case in CONFORMING],
)
def test_conforming_template_parses(template, event, fields, placeholders):
    parsed = parse_log_template(template)
    assert parsed is not None
    assert parsed.event == event
    assert [(f.name, f.conversion, f.literal) for f in parsed.fields] == fields
    assert parsed.placeholder_count == placeholders


@pytest.mark.parametrize("template", NON_CONFORMING, ids=range(len(NON_CONFORMING)))
def test_non_conforming_template_returns_none(template):
    assert parse_log_template(template) is None


def test_field_order_is_source_order():
    parsed = parse_log_template("event z=%s a=%s m=%s")
    assert parsed is not None
    assert [f.name for f in parsed.fields] == ["z", "a", "m"]


def test_repeated_parse_is_cached():
    first = parse_log_template("event key=%s")
    second = parse_log_template("event key=%s")
    assert first is second
