"""OKF timestamps follow RFC 3339 ``date-time`` and nothing looser (#2428).

OKF 0.3 §5: each timestamp value is an RFC 3339 ``date-time`` or
``full-date``; "No other form is valid." The server's reader and the
reference checker must agree on that grammar.
"""

from __future__ import annotations

import datetime as dt

import pytest
from scripts.check_references import _as_instant

from markdown_vault_mcp.okf import _parse_timestamp

UTC = dt.UTC


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-06-30T14:00:00Z", dt.datetime(2026, 6, 30, 14, tzinfo=UTC)),
        # §5.6 NOTE: "T" and "Z" may be lower case.
        ("2026-06-30t14:00:00z", dt.datetime(2026, 6, 30, 14, tzinfo=UTC)),
        (
            "2026-06-30T14:00:00.25+02:00",
            dt.datetime(2026, 6, 30, 12, 0, 0, 250000, tzinfo=UTC),
        ),
        # More fraction digits than a microsecond holds are truncated.
        (
            "2026-06-30T14:00:00.123456789Z",
            dt.datetime(2026, 6, 30, 14, 0, 0, 123456, tzinfo=UTC),
        ),
        ("2026-06-30T14:00:00-00:00", dt.datetime(2026, 6, 30, 14, tzinfo=UTC)),
        # time-second allows 60 at a leap second; it names the next second.
        ("2016-12-31T23:59:60Z", dt.datetime(2017, 1, 1, tzinfo=UTC)),
        ("2026-06-30", dt.datetime(2026, 6, 30, tzinfo=UTC)),
    ],
)
def test_rfc3339_forms_parse(text: str, expected: dt.datetime) -> None:
    assert _parse_timestamp(text) == expected
    assert _as_instant(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "2026-06-30T14:00Z",  # partial-time requires seconds
        "2026-06-30T14:00:00,5Z",  # time-secfrac is "." 1*DIGIT
        "2026-06-30 14:00:00Z",  # the date-time rule's separator is "T"
        "2026-06-30T14:00:00",  # no offset (§13.2: stays invalid)
        "2026-06-30T14:00:00+0200",  # time-numoffset needs the colon
        "2026-06-30T14:00:00+02",
        "20260630T140000Z",  # basic format is not RFC 3339
        "2026-06-30T24:00:00Z",  # time-hour is 00-23
        "2026-02-30T14:00:00Z",  # date-mday by month
        "2026-06-30T14:00:00.Z",  # 1*DIGIT after "."
        " 2026-06-30T14:00:00Z",  # no surrounding whitespace
        "2026-W26-2",
    ],
)
def test_non_rfc3339_forms_are_rejected(text: str) -> None:
    assert _parse_timestamp(text) is None
    assert _as_instant(text) is None


def test_yaml_native_timestamps_are_still_accepted() -> None:
    # §5: "Consumers MUST accept a quoted string and a YAML-native
    # timestamp" — a YAML datetime is a value, not text to re-check.
    native = dt.datetime(2026, 6, 30, 14, tzinfo=UTC)
    assert _parse_timestamp(native) == native
    assert _as_instant(native) == native
