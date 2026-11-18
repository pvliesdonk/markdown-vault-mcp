"""Compatibility helpers and subject resolution for OKF review.

Ordinary writes preserve supplied provenance. Request identity remains available
for commit attribution and explicit review.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from markdown_vault_mcp._identity import current_principal, resolve_mcp_principal

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

#: The subject recorded for an unattributable elicit-mode verification. Shares
#: its spelling with the ``get_subject()`` sentinel that :mod:`._identity`
#: treats as *no human identity*, but the role here is the opposite: this is a
#: value deliberately stamped (``human:local``), not one detected and
#: discarded. Kept separate so narrowing one never silently moves the other.
_LOCAL_SUBJECT = "local"


@dataclass(frozen=True)
class OkfWriteIntent:
    """The current request's OKF write intent, carried across ``to_thread``.

    Attributes:
        actor: Legacy intent value; does not supply provenance.
        suppress: When true, automatic convention maintenance is suppressed.
    """

    actor: str
    suppress: bool = False


_intent_var: ContextVar[OkfWriteIntent | None] = ContextVar(
    "okf_write_intent", default=None
)


@contextmanager
def okf_write_intent(intent: OkfWriteIntent) -> Iterator[None]:
    """Bind *intent* for the duration of a write, resetting it afterward.

    Enter this **before** the ``asyncio.to_thread`` write call so the copied
    worker context sees it.
    """
    token = _intent_var.set(intent)
    try:
        yield
    finally:
        _intent_var.reset(token)


@contextmanager
def okf_write_suppressed() -> Iterator[None]:
    """Suppress automatic convention maintenance for the duration.

    Used by mechanical transforms and explicit review. Enter before the
    ``asyncio.to_thread`` call so the worker receives the context.
    """
    with okf_write_intent(OkfWriteIntent(actor="", suppress=True)):
        yield


def current_okf_intent() -> OkfWriteIntent | None:
    """Return the current request's :class:`OkfWriteIntent`, or ``None``."""
    return _intent_var.get()


def resolve_human_subject() -> str | None:
    """Return the authenticated human subject, or ``None`` if unattributable.

    Used by ``okf_verify`` to decide whether a verification is attributable
    (``None`` for service credentials or when no human subject is present).

    Prefers the principal bound at the tool edge (#1160), whose ``kind``
    distinguishes human attribution from a service identifier. With none bound —
    a driver that
    is not the MCP server, or a call outside ``write_identity_scope`` — it
    resolves one from the request context rather than re-deriving the rules
    here, so the rule that decides what counts as a human subject — including
    the ``"local"`` sentinel — is applied in exactly one place (#1231).
    """
    principal = current_principal()
    if principal is None:
        principal = resolve_mcp_principal()
    return principal.subject if principal.kind == "human" else None


def resolve_verify_subject() -> str:
    """Return the subject to stamp once an elicit-mode review is confirmed.

    The human principal's subject when present, else the ``local`` sentinel. Under
    ``OKF_VERIFY=elicit`` the elicitation — not the token — is the
    human-presence proof, so a human using no auth or a service credential
    records an attributable ``human:local`` entry after confirming through the client UI.
    """
    return resolve_human_subject() or _LOCAL_SUBJECT
