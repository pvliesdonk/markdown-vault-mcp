"""Shared env-reading helpers for config_sections from_env classmethods.

Imports only fastmcp_pvl_core + stdlib (never config.py) so config.py can
import these without a cycle.
"""

from __future__ import annotations

import logging

from fastmcp_pvl_core import (
    env as _core_env,
)
from fastmcp_pvl_core import (
    env_float as _core_env_float,
)
from fastmcp_pvl_core import (
    env_int as _core_env_int,
)

logger = logging.getLogger(__name__)


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


def parse_int_env(prefix: str, name: str, default: int) -> int:
    """Read an int env var; warn-and-default on absence/parse error."""
    raw = (env(prefix, name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s_%s=%r, using default %s", prefix, name, raw, default)
        return default


def parse_float_env(prefix: str, name: str, default: float) -> float:
    """Read a float env var; warn-and-default on absence/parse error."""
    raw = (env(prefix, name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s_%s=%r, using default %s", prefix, name, raw, default)
        return default
