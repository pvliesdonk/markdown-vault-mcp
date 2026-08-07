"""Shared env-reading helpers for config_sections from_env classmethods.

Imports only fastmcp_pvl_core + stdlib (never config.py) so config.py can
import these without a cycle.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from fastmcp_pvl_core import (
    env as _core_env,
)
from fastmcp_pvl_core import (
    env_float as _core_env_float,
)
from fastmcp_pvl_core import (
    env_int as _core_env_int,
)

# Type accepted for ProjectConfig's weight-map fields: a mapping (e.g. from
# parse_weight_map) or an already-frozen tuple of (key, weight) pairs.
WeightMap = Mapping[str, float] | Sequence[tuple[str, float]]


def env(prefix: str, name: str, default: str | None = None) -> str | None:
    """Read ``{prefix}_{name}`` (whitespace-stripped, empty-as-unset)."""
    return _core_env(prefix, name, default=default)


def env_int(prefix: str, name: str, default: int) -> int:
    """Read a strict int env var; raise ``ConfigurationError`` on invalid input.

    Returns *default* when unset. Range validation lives in each sub-config's
    ``__post_init__`` so direct construction is validated too (#638).
    """
    value = _core_env_int(prefix, name, default, strict=True)
    # A non-None default under strict=True never resolves to None (unset →
    # default; set → parsed int or raise); the guard only narrows the type.
    return value if value is not None else default


def env_float(prefix: str, name: str, default: float) -> float:
    """Read a strict float env var; raise ``ConfigurationError`` on invalid input.

    Returns *default* when unset. Range validation lives in each sub-config's
    ``__post_init__`` (#638).
    """
    value = _core_env_float(prefix, name, default, strict=True)
    return value if value is not None else default


def opt_int(prefix: str, name: str) -> int | None:
    """Read a strict optional int env var (``None`` when unset); raise on invalid."""
    return _core_env_int(prefix, name, None, strict=True)


def parse_weight_map(raw: str | None, var: str) -> dict[str, float] | None:
    """Parse a ``"key:float,key:float,..."`` value into a weight map.

    Entries are split on ``,``; each entry is split on its LAST ``:`` (so a
    key may itself contain ``:``). Whitespace around keys and values is
    stripped. Returns ``None`` when *raw* is ``None`` or empty. Semantic
    validation (allowed keys, weight ranges) lives in each sub-config's
    ``__post_init__`` so direct construction is validated too (#638).

    Args:
        raw: The already-read env value (or ``None`` when unset).
        var: Full env var name, used in error messages.

    Returns:
        Mapping of key to float weight, or ``None`` when unset.

    Raises:
        ConfigurationError: If an entry lacks a ``:``, has an empty key, or
            carries a non-numeric weight. The message names the env var.
    """
    from markdown_vault_mcp.exceptions import ConfigurationError

    if raw is None or not raw.strip():
        return None
    weights: dict[str, float] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        key, sep, value = entry.rpartition(":")
        if not sep or not key.strip():
            raise ConfigurationError(
                f"{var}: entry {entry!r} must be 'key:weight' with a non-empty key"
            )
        try:
            weight = float(value.strip())
        except ValueError as exc:
            raise ConfigurationError(
                f"{var}: weight {value.strip()!r} for key {key.strip()!r} "
                "is not a number"
            ) from exc
        weights[key.strip()] = weight
    return weights or None


def opt_path(raw: str | None) -> Path | None:
    """Convert an already-read env value into an optional :class:`Path`.

    Returns ``None`` when *raw* is ``None`` or whitespace-only.
    """
    cleaned = (raw or "").strip()
    return Path(cleaned) if cleaned else None


def opt_list(raw: str | None) -> tuple[str, ...] | None:
    """Parse an already-read comma-separated env value into a tuple.

    Returns ``None`` when *raw* is ``None``, empty, or parses to no items —
    the shared empty-as-unset convention for list-valued vars.
    """
    from fastmcp_pvl_core import parse_list

    items = parse_list(raw or "")
    return tuple(items) if items else None


def parse_opt_int(raw: str | None, var: str) -> int | None:
    """Parse an already-read optional int env value (``None`` when unset).

    Args:
        raw: The already-read env value (or ``None``).
        var: Full env var name, used in the error message.

    Returns:
        The parsed int, or ``None`` when unset/empty.

    Raises:
        ConfigurationError: If *raw* is set but not a valid integer.
    """
    from markdown_vault_mcp.exceptions import ConfigurationError

    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError as exc:
        raise ConfigurationError(f"{var} must be an integer, got {cleaned!r}") from exc
