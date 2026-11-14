"""Properties of the shared offset-aware metadata clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from markdown_vault_mcp.utils.timestamps import parse_aware_datetime

pytest.importorskip("hypothesis")
from hypothesis import given
from hypothesis import strategies as st


@given(
    instant=st.datetimes(
        min_value=datetime(1971, 1, 1), max_value=datetime(2099, 1, 1)
    ),
    offset=st.integers(min_value=-840, max_value=840),
)
def test_offsets_preserve_the_same_instant(instant: datetime, offset: int) -> None:
    """Offset conversion must preserve fractional seconds and the UTC clock."""
    utc = instant.replace(tzinfo=UTC)
    local = utc.astimezone(timezone(timedelta(minutes=offset)))
    for value in (local, local.isoformat(), "  " + local.isoformat() + "  "):
        parsed = parse_aware_datetime(value)
        assert parsed is not None
        assert parsed.timestamp() == utc.timestamp()
