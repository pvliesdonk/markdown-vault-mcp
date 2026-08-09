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

``get_subject()`` returns the sentinel ``"local"`` under auth mode ``none``; that
is treated as *no human identity* for both actor resolution and ``okf_verify``.
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

from markdown_vault_mcp.okf import _HUMAN_ACTOR_PREFIX, apply_okf_write_stamp

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from markdown_vault_mcp.okf import OkfDetector
    from markdown_vault_mcp.types import WriteOperation

logger = logging.getLogger(__name__)

_LOCAL_SUBJECT = "local"  # get_subject() sentinel for startup auth mode "none"


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


def resolve_write_actor() -> str:
    """Resolve the content-write actor from the request identity.

    ``human:<subject>`` when an authenticated subject is present, else the tool
    actor. Call inside a tool handler (it reads the request context).
    """
    from fastmcp_pvl_core import get_subject

    subject = get_subject()
    if subject and subject != _LOCAL_SUBJECT:
        return f"{_HUMAN_ACTOR_PREFIX}{subject}"
    return tool_actor(package_version())


def resolve_human_subject() -> str | None:
    """Return the authenticated human subject, or ``None`` if unattributable.

    Used by ``okf_verify`` to decide whether a verification is attributable
    (``None`` under auth mode ``none`` or when no subject is present).
    """
    from fastmcp_pvl_core import get_subject

    subject = get_subject()
    if subject and subject != _LOCAL_SUBJECT:
        return subject
    return None


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
