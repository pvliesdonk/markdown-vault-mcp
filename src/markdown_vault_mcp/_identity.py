"""Request-scoped write identity: the Principal value and its propagation (#1160).

The vault's write path historically read "who is acting" from the MCP request
context in two places with two different rules: the git strategy pulled OIDC
claims via FastMCP's ``get_access_token`` per commit, and the OKF enforced-write
layer read ``fastmcp_pvl_core.get_subject`` per tool call. The git read ran on
the write-callback dispatcher's own daemon thread, where no request context
exists, so the configured name/email claims silently never applied (#1218).

This module replaces both reads with a single resolution at the tool edge:

- :class:`Principal` is a plain, frozen "who" — subject, display name, email,
  and kind. Credentials (``GIT_TOKEN``) and permissions (``read_only``) are
  deliberately excluded: they are service-level configuration, not properties
  of the caller, and a Principal must stay boundary-serializable.
- :func:`resolve_mcp_principal` performs the one MCP-context read (subject +
  claims), applying the claim keys registered via
  :func:`configure_identity_claims`.
- :func:`bound_principal` / :func:`current_principal` carry the resolved value
  across ``asyncio.to_thread`` on a contextvar (the same pattern as
  ``_okf_write``'s intent), and
  :class:`~markdown_vault_mcp.write_callback.WriteCallbackDispatcher` snapshots
  it into the queue item at ``fire()`` time so it survives the hop onto the
  dispatcher thread.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any

logger = logging.getLogger(__name__)

#: ``get_subject()`` sentinel for startup auth mode ``none`` — not a human.
_LOCAL_SUBJECT = "local"

# The OIDC claim keys used for display_name/email resolution. Registered once
# at startup (config assembly) via ``configure_identity_claims``; module-level
# because the claim keys are deployment configuration, not per-request state.
_name_claim: str | None = None
_email_claim: str | None = None


def configure_identity_claims(
    *, name_claim: str | None, email_claim: str | None
) -> None:
    """Register the OIDC claim keys used to resolve display name and email.

    Called once during config assembly with the
    ``GIT_COMMIT_NAME_CLAIM`` / ``GIT_COMMIT_EMAIL_CLAIM`` settings so
    :func:`resolve_mcp_principal` knows which claims to read.

    Args:
        name_claim: Claim key for the human-readable display name, or ``None``
            when unconfigured.
        email_claim: Claim key for the email address, or ``None``.
    """
    global _name_claim, _email_claim
    _name_claim = name_claim
    _email_claim = email_claim


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is performing a write, resolved once at the MCP tool edge.

    Attributes:
        subject: Stable authenticated identifier (OIDC ``sub`` or the mapped
            bearer subject), or ``None`` when the caller is unattributable.
        display_name: Human-readable name from the configured name claim, or
            ``None`` when the claim is unconfigured or absent.
        email: Email address from the configured email claim, or ``None``.
        kind: ``"human"`` when an authenticated subject is present;
            ``"local"`` otherwise (no auth, or a token with no usable
            subject).
    """

    subject: str | None
    display_name: str | None
    email: str | None
    kind: Literal["human", "local"]

    def okf_actor(self, version: str) -> str:
        """Return the OKF provenance actor this principal stamps.

        ``human:<subject>`` for an authenticated human, else the tool actor
        ``markdown-vault-mcp/<version>`` — the exact rules of
        the design-§6 actor rules, expressed over the resolved value.

        Args:
            version: Package version used for the tool actor.
        """
        # Function-local imports keep the module graph acyclic: _okf_write
        # imports this module at load time for current_principal().
        from markdown_vault_mcp._okf_write import tool_actor
        from markdown_vault_mcp.okf import _HUMAN_ACTOR_PREFIX

        if self.kind == "human" and self.subject:
            return f"{_HUMAN_ACTOR_PREFIX}{self.subject}"
        return tool_actor(version)


_principal_var: ContextVar[Principal | None] = ContextVar(
    "write_principal", default=None
)


@contextmanager
def bound_principal(principal: Principal) -> Iterator[None]:
    """Bind *principal* for the duration, resetting it afterward.

    Enter this **before** the ``asyncio.to_thread`` write call so the copied
    worker context — and the dispatcher's ``fire()`` snapshot taken there —
    sees it.
    """
    token = _principal_var.set(principal)
    try:
        yield
    finally:
        _principal_var.reset(token)


def current_principal() -> Principal | None:
    """Return the currently bound :class:`Principal`, or ``None``."""
    return _principal_var.get()


def _claim_value(claims: dict[str, Any] | None, key: str | None) -> str | None:
    """Return the non-empty string value of *key* in *claims*, else ``None``."""
    if key is None or claims is None:
        return None
    value = claims.get(key)
    return value if isinstance(value, str) and value else None


def resolve_mcp_principal() -> Principal:
    """Resolve the caller's :class:`Principal` from the MCP request context.

    Call inside a tool handler (it reads the request context). Subject rules
    match ``fastmcp_pvl_core.get_subject``: a subject other than the
    ``"local"`` sentinel makes a ``"human"`` principal; the sentinel or no
    subject makes a ``"local"`` one with ``subject=None``. Display name and
    email are read from the token claims using the keys registered via
    :func:`configure_identity_claims` (non-empty strings only).

    Returns:
        The resolved principal. Never ``None`` — an unauthenticated caller
        resolves to a ``"local"`` principal with every field ``None``.
    """
    # Function-local imports are deliberate and load-bearing: tests monkeypatch
    # ``fastmcp_pvl_core.get_subject`` / ``get_claims`` as package attributes,
    # which only takes effect when the lookup happens at call time (the same
    # pattern as ``_okf_write``).
    from fastmcp_pvl_core import get_claims, get_subject

    subject = get_subject()
    claims = get_claims()
    display_name = _claim_value(claims, _name_claim)
    email = _claim_value(claims, _email_claim)
    if subject and subject != _LOCAL_SUBJECT:
        return Principal(
            subject=subject, display_name=display_name, email=email, kind="human"
        )
    return Principal(subject=None, display_name=display_name, email=email, kind="local")
