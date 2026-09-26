"""The library's deliberate outcomes, raised to the model as ``ToolError`` (#1608).

``docs/design/design.md`` (Error Handling) maps each library signal to one
tool outcome. :func:`library_outcomes` is that table in code, applied to every
tool under pvl-core's ``tool_boundary``:

- a request the caller must change (``InvalidRequestError`` and its
  subclasses, ``EditConflictError``, ``DocumentExistsError``,
  ``ReadOnlyError``) reaches the model with the library's message at INFO;
- a stale ``if_match`` (``ConcurrentModificationError``) tells the model to
  read again and retry, at INFO;
- an index that is busy or still building tells it to retry shortly, at
  WARNING, because it heals itself.

:func:`maps_library_outcomes` lets a test check that every tool carries it.

Anything else propagates to ``tool_boundary``, which logs it once at ERROR with
its traceback and tells the model the request was fine.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import TYPE_CHECKING, Any, TypeVar

from fastmcp.exceptions import ToolError

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    EditConflictError,
    IndexUnavailableError,
    InvalidRequestError,
    ReadOnlyError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound="Callable[..., Any]")

_MARKER = "_mvm_library_outcomes"

_CHANGE_THE_REQUEST = (
    InvalidRequestError,
    EditConflictError,
    DocumentExistsError,
    ReadOnlyError,
)

# The index reasons that heal themselves: lock contention, and a build that
# was still running when the bounded wait ended.
_TRANSIENT_INDEX = frozenset({"busy", "timeout"})


def outcome_error(exc: Exception) -> ToolError | None:
    """Return the ``ToolError`` a library signal maps to, or ``None``.

    Args:
        exc: An exception a tool's library call raised.

    Returns:
        The ``ToolError`` to raise in its place, or ``None`` when *exc* is the
        server failing and belongs to ``tool_boundary``.
    """
    if isinstance(exc, ConcurrentModificationError):
        return ToolError(
            f"{exc.path!r} changed since etag {exc.expected!r} was read, so "
            "resending this call fails again. Call read for the current text "
            "and etag, reapply the change, and pass the new etag as if_match.",
            log_level=logging.INFO,
        )
    if isinstance(exc, _CHANGE_THE_REQUEST):
        return ToolError(str(exc), log_level=logging.INFO)
    if isinstance(exc, IndexUnavailableError) and exc.reason in _TRANSIENT_INDEX:
        # The middleware logs the ToolError at WARNING; nothing to add here.
        return ToolError(
            "The search index is busy or still building; the request was fine. "
            "Retry in a minute.",
            log_level=logging.WARNING,
        )
    return None


def library_outcomes(fn: _F) -> _F:
    """Raise the library's deliberate outcomes from *fn* as ``ToolError``.

    Apply it directly under ``@tool_boundary`` (or under
    ``@register_long_running_tool``, which carries the boundary itself). Any
    exception :func:`outcome_error` does not map propagates unchanged.

    Args:
        fn: The tool function, sync or async.

    Returns:
        The wrapped function, of the same kind as *fn*.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                mapped = outcome_error(exc)
                if mapped is None:
                    raise
                raise mapped from None

        setattr(async_wrapper, _MARKER, True)
        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            mapped = outcome_error(exc)
            if mapped is None:
                raise
            raise mapped from None

    setattr(sync_wrapper, _MARKER, True)
    return sync_wrapper  # type: ignore[return-value]


def maps_library_outcomes(fn: Callable[..., Any]) -> bool:
    """Report whether *fn* carries :func:`library_outcomes` inside its boundary.

    Follows ``__wrapped__`` from the registered function down. The mapping
    must sit below ``tool_boundary``: above it, the boundary would already
    have turned every library signal into a server fault.

    Args:
        fn: A registered tool's function.

    Returns:
        ``True`` if :func:`library_outcomes` wraps *fn*'s body below the
        boundary.
    """
    seen: set[int] = set()
    current: Any = fn
    below_boundary = False
    while current is not None and id(current) not in seen:
        if below_boundary and getattr(current, _MARKER, False):
            return True
        if is_tool_boundary_wrapper(current):
            below_boundary = True
        seen.add(id(current))
        current = getattr(current, "__wrapped__", None)
    return False


def is_tool_boundary_wrapper(fn: Callable[..., Any]) -> bool:
    """Whether *fn* carries ``tool_boundary``'s marker attribute.

    pvl-core's public ``is_tool_boundary`` follows ``__wrapped__`` all the way
    down, so it cannot say where in the chain the boundary sits; this reads
    the same private marker at one level only.
    """
    return bool(getattr(fn, "_pvl_tool_boundary", False))
