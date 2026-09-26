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
        logger.warning("tool_index_unavailable reason=%s", exc.reason)
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

    return sync_wrapper  # type: ignore[return-value]
