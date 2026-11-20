"""Uniform subject extraction across all auth modes.

Ported from ``fastmcp_pvl_core._subject`` 10.2.0 (#2350). Code that wants to
know "who is making this request?" calls :func:`get_subject` without caring
which auth mode is active; look it up on this module at call time
(``_subject.get_subject()``) so a test can patch it here.

The per-mode complexity lives in the builders (see :mod:`_auth`); this
module is a thin extractor.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from fastmcp.server.dependencies import get_access_token

if TYPE_CHECKING:
    from typing import Any

    # Imported only for typing; runtime import would create a cycle
    # (``_auth`` imports ``set_current_auth_mode`` from this module).
    from markdown_vault_mcp._auth import AuthMode

# Per-context pointer to the auth mode resolved at server startup.
# ``build_auth`` calls ``set_current_auth_mode`` once after resolving
# the mode; ``get_subject`` reads it to decide whether the absence of
# an access token means "stdio/no-auth" (returns "local") or "auth
# configured but no valid token" (returns None).
#
# Implemented as a :class:`ContextVar` rather than a module global so
# that the suite-wide autouse fixture in ``tests/conftest.py``
# (``_reset_auth_mode``, which calls ``set_current_auth_mode(None)``
# before/after every test) and explicitly-isolated harnesses
# (``contextvars.copy_context().run(...)``) can reset / scope the
# value cleanly.  Note: pytest does not auto-reset ``ContextVar``
# values between tests on its own; the autouse fixture is what makes
# isolation work.  In the natural single-context production pattern
# (two ``build_auth`` calls in the same ``main()`` body) last-writer
# still wins — caller code wishing to compose multiple ``FastMCP``
# instances with distinct auth modes must wrap each ``build_auth`` in
# its own ``copy_context().run(...)``.  In standard server-startup-
# then-serve deployments, asyncio's task context inherits the
# startup value uniformly across requests.
_current_auth_mode: ContextVar[AuthMode | None] = ContextVar(
    "markdown_vault_mcp_current_auth_mode",
    default=None,
)


def set_current_auth_mode(mode: AuthMode | None) -> None:
    """Record the auth mode resolved at server startup.

    Called by :func:`markdown_vault_mcp._auth.build_auth`. Tests that exercise
    :func:`get_subject` without going through ``build_auth`` may call
    this directly. Passing ``None`` resets the pointer (useful between
    tests).
    """
    _current_auth_mode.set(mode)


def get_current_auth_mode() -> AuthMode | None:
    """Return the auth mode :func:`markdown_vault_mcp._auth.build_auth` resolved.

    Lets a caller report or branch on the mode without calling
    :func:`markdown_vault_mcp._auth.resolve_auth_mode` a second time
    (fastmcp-pvl-core#310: ``build_auth`` returns the provider, not the mode).

    Reads the same pointer :func:`get_subject` does, so the module
    comment above applies in full: it is a :class:`~contextvars.ContextVar`,
    last writer wins, and a caller composing two ``FastMCP`` instances in
    one process sees only the mode of the most recent ``build_auth``
    call unless each is wrapped in its own ``copy_context().run(...)``.

    Returns:
        One of the :data:`AuthMode` literals once ``build_auth`` has run
        in this context, or ``None`` if it has not run — or was reset by
        ``set_current_auth_mode(None)``. ``"none"`` is a resolved mode
        and is distinct from ``None``.
    """
    return _current_auth_mode.get()


def _extract_claims(access_token: object) -> dict[str, Any]:
    raw_claims = getattr(access_token, "claims", None)
    return raw_claims if isinstance(raw_claims, dict) else {}


def get_claims() -> dict[str, Any] | None:
    """Return the full claims dict for the current request, or ``None``.

    Returns the raw claims from FastMCP's access token when one is
    present for the current request. Returns ``{}`` when a token is
    present but carries no claims. Returns ``None`` when no token is
    present (regardless of auth mode).

    Callers read whichever standard OIDC claims they need (``name``,
    ``email``, ``sub``, etc.).
    """
    access_token = get_access_token()
    if access_token is None:
        return None
    return _extract_claims(access_token)


def get_subject() -> str | None:
    """Return the subject of the current request, or ``None``.

    Resolution order:

    1. If FastMCP's :func:`get_access_token` returns a token, prefer
       ``claims["sub"]`` (OIDC's standard subject claim); fall back to
       ``token.client_id`` when ``sub`` is absent or non-string. The
       builders normalise ``client_id`` per bearer mode (mapped subject
       for ``bearer-mapped``, ``bearer_default_subject`` for
       ``bearer-single``).
    2. If there is no access token and ``set_current_auth_mode`` was
       called with ``"none"``, return the literal ``"local"``.
    3. Otherwise return ``None`` and let the caller decide whether to
       fall back or error.
    """
    access_token = get_access_token()
    if access_token is None:
        return "local" if _current_auth_mode.get() == "none" else None
    claims = _extract_claims(access_token)
    sub = claims.get("sub")
    if isinstance(sub, str) and sub:
        return sub
    client_id = getattr(access_token, "client_id", None)
    if isinstance(client_id, str) and client_id:
        return client_id
    return None
