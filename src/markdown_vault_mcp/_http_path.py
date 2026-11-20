"""Normalising the HTTP mount path.

Ported from ``fastmcp_pvl_core._cli`` 10.2.0 (#2334); core's
``make_serve_parser`` has no caller here and stayed behind.
"""

from __future__ import annotations


def normalise_http_path(path: str | None, *, default: str = "/mcp") -> str:
    """Normalize an HTTP mount path.

    Ensures a leading ``/`` and strips a trailing ``/`` (except for root
    ``/``). ``None``, empty, or whitespace-only values fall back to
    ``default``.

    Args:
        path: Raw path from env var or CLI flag (may be ``None``).
        default: Value to return when ``path`` is empty. Defaults to
            ``/mcp``.

    Returns:
        A normalised mount path suitable for FastMCP streamable HTTP
        transport.
    """
    if path is None:
        return default
    normalised = path.strip()
    if not normalised:
        return default
    if not normalised.startswith("/"):
        normalised = f"/{normalised}"
    if len(normalised) > 1:
        normalised = normalised.rstrip("/")
    return normalised
