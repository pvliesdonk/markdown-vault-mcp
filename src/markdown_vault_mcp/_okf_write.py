"""OKF enforced-write layer runtime (#964): request-scoped actor + enrichment.

The enforced-write layer (design §6) stamps ``generated`` provenance and clears
``verified`` on content writes, attributing them to the authenticated identity.
That identity is a FastMCP request-context dependency (``get_subject``), so it is
resolvable only inside a tool handler — but the write path runs deep under
``asyncio.to_thread``. The actor (and a *suppress* flag for ``okf_verify``, which
sets ``verified`` deliberately and must not have it cleared) therefore travel via
a contextvar: ``asyncio.to_thread`` copies the current context into its worker, so
a value set around the ``to_thread`` call is visible to the enricher. This mirrors
``fastmcp_pvl_core``'s own ``_current_auth_mode`` contextvar.

Actor resolution rules (design §6):

- ``human:<subject>`` when an authenticated subject is present.
- a tool actor ``markdown-vault-mcp/<version>`` otherwise (unauthenticated, or a
  non-tool write with no request identity).

Those rules are applied once, in :mod:`markdown_vault_mcp._identity` —
including that ``get_subject()`` returns the sentinel ``"local"`` under auth
mode ``none``, which counts as *no human identity* for both actor resolution
and ``okf_verify``. This module reads a ``Principal`` and never the request
context directly (#1231); a second copy of the rules is a second thing to keep
in agreement.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING

from markdown_vault_mcp._identity import current_principal, resolve_mcp_principal
from markdown_vault_mcp.okf import apply_okf_write_stamp

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from markdown_vault_mcp.okf import OkfDetector
    from markdown_vault_mcp.types import WriteOperation

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
        actor: The provenance actor to stamp (``human:<subject>`` or a tool actor).
        suppress: When ``True`` the enricher is a no-op — used by ``okf_verify``,
            which sets ``verified`` on purpose and must not have it cleared.
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
    """Bind a suppressing intent so the enricher is a no-op for the duration.

    For mechanical writes that must not be re-stamped or have ``verified``
    cleared — the one-shot migration transforms and ``okf_verify`` (which sets
    ``verified`` deliberately). Enter it **before** the ``asyncio.to_thread``
    write call.
    """
    with okf_write_intent(OkfWriteIntent(actor="", suppress=True)):
        yield


def current_okf_intent() -> OkfWriteIntent | None:
    """Return the current request's :class:`OkfWriteIntent`, or ``None``."""
    return _intent_var.get()


@lru_cache(maxsize=1)
def package_version() -> str:
    """Return the installed package version, or ``"unknown"`` if undeterminable."""
    try:
        return _pkg_version("markdown-vault-mcp")
    except PackageNotFoundError:  # pragma: no cover - packaging edge
        return "unknown"


def tool_actor(version: str) -> str:
    """The non-human provenance actor: ``markdown-vault-mcp/<version>``."""
    return f"markdown-vault-mcp/{version}"


def resolve_human_subject() -> str | None:
    """Return the authenticated human subject, or ``None`` if unattributable.

    Used by ``okf_verify`` to decide whether a verification is attributable
    (``None`` under auth mode ``none`` or when no subject is present).

    Prefers the principal bound at the tool edge (#1160), whose ``subject`` is
    already ``None`` for a non-human caller.  With none bound — a driver that
    is not the MCP server, or a call outside ``write_identity_scope`` — it
    resolves one from the request context rather than re-deriving the rules
    here, so the rule that decides what counts as a human subject — including
    the ``"local"`` sentinel — is applied in exactly one place (#1231).
    """
    principal = current_principal()
    if principal is None:
        principal = resolve_mcp_principal()
    return principal.subject


def resolve_verify_subject() -> str:
    """Return the subject to stamp once an elicit-mode review is confirmed.

    The authenticated subject when present, else the ``local`` sentinel. Under
    ``OKF_VERIFY=elicit`` the elicitation — not the token — is the
    human-presence proof, so a no-auth local human still records an attributable
    ``human:local`` entry after confirming through the client UI.
    """
    return resolve_human_subject() or _LOCAL_SUBJECT


def build_okf_write_enrich(
    *, okf_write: bool, detector: OkfDetector, version: str
) -> Callable[[str, WriteOperation], str] | None:
    """Build the ``DocumentManager`` write-enrichment hook, or ``None`` when off.

    Returns ``None`` (zero overhead, no detector probe) when ``okf_write`` is
    disabled. Otherwise returns a callable that, for ``write`` / ``edit`` on an
    OKF-active vault, stamps provenance and clears verification — unless the
    current request carries a suppressing :class:`OkfWriteIntent` (``okf_verify``).

    Args:
        okf_write: The ``OKF_WRITE`` operator flag.
        detector: The vault's OKF detector (active-state probe).
        version: The package version for the tool actor default.

    Returns:
        The enrichment callable, or ``None``.
    """
    if not okf_write:
        return None
    default_actor = tool_actor(version)

    def enrich(text: str, operation: WriteOperation) -> str:
        if operation not in ("write", "edit"):
            return text
        intent = current_okf_intent()
        if intent is not None and intent.suppress:
            return text
        if not detector.state().active:
            return text
        actor = intent.actor if intent is not None else default_actor
        return apply_okf_write_stamp(text, actor=actor, today=date.today())

    return enrich
