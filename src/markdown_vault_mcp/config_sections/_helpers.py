"""Shared env-reading helpers for config_sections from_env classmethods.

Imports only fastmcp_pvl_core + stdlib (never config.py) so config.py can
import these without a cycle.
"""

from __future__ import annotations

import logging

from fastmcp_pvl_core import env as _core_env

logger = logging.getLogger(__name__)


def env(prefix: str, name: str, default: str | None = None) -> str | None:
    """Read ``{prefix}_{name}`` (whitespace-stripped, empty-as-unset)."""
    return _core_env(prefix, name, default=default)


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
