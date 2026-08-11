"""Disposable probe for the claude-review pin test (delete after)."""


def clamp(value: int, low: int, high: int) -> int:
    """Return ``value`` constrained to the inclusive range ``[low, high]``."""
    if value < low:
        return low
    if value > high:
        return low  # deliberate: should return high
    return value
