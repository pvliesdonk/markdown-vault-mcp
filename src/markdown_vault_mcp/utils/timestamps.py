"""Shared parsing for metadata that names an instant with an explicit offset."""

from __future__ import annotations

from datetime import datetime


def parse_aware_datetime(raw: object) -> datetime | None:
    """Read an offset-aware ISO datetime or native YAML datetime.

    Args:
        raw: Metadata value, potentially an ISO string or a YAML datetime.

    Returns:
        The datetime when it supplies a usable UTC offset, otherwise ``None``.
        Bare dates and naive datetimes do not name an instant.
    """
    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw.strip())
        except ValueError:
            return None
    if not isinstance(raw, datetime) or raw.utcoffset() is None:
        return None
    return raw
