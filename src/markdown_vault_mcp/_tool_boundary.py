"""The tool boundary: no exception leaves a tool unclassified.

Ported from ``fastmcp_pvl_core._tool_boundary`` 10.2.0 (#2280).

A tool call ends in one of four outcomes (the ``designing-tool-outcomes``
skill). Three of them are deliberate and the tool raises them itself as a
``ToolError`` with a model-facing message and a ``log_level``. The fourth,
the server failing, is everything else: this module turns it into one ERROR
line with the traceback and a fixed message that tells the model the request
was fine. Without it, the exception reaches FastMCP's own handler, which logs
its own ERROR traceback and, under ``mask_error_details``, drops the reason.
How FastMCP and the MCP SDK treat each case is in
``docs/design/reference/mcp-tool-outcomes-and-errors.md``.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, NoReturn, TypeVar

from fastmcp.exceptions import FastMCPError, ToolError
from fastmcp.utilities.exceptions import get_http_status_code, is_timeout_error
from mcp.shared.exceptions import MCPError
from mcp.types import MISSING_REQUIRED_CLIENT_CAPABILITY

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

_MARKER = "_pvl_tool_boundary"
"""Attribute that marks a wrapper as a boundary.

The value is the name fastmcp-pvl-core's ``tool_boundary`` set, kept from the
port; ``_tools._outcomes.is_tool_boundary_wrapper`` reads the same literal.
"""

FAULT_MESSAGE = (
    "The call failed because of a server-side error; the request itself was "
    "fine. Retry later, and tell the user if it keeps failing."
)
"""What the model reads for outcome 4: the request was fine, and what to do."""

# FastMCP's own wording for the two faults it already singles out as
# transient, kept so a wrapped tool tells the model no less than a bare one.
_RATE_LIMITED_MESSAGE = "Rate limited by upstream API, please retry later"
_TIMED_OUT_MESSAGE = "Upstream request timed out, please retry"


def _passes_through(exc: Exception) -> bool:
    """Whether *exc* is a deliberate outcome the boundary must not reclassify.

    A ``FastMCPError`` carries the tool's own message and ``log_level``. A
    missing-client-capability ``MCPError`` must reach the wire as JSON-RPC
    error -32021 (SEP-2575); FastMCP re-raises it for the same reason, and
    turning it into a result would tell the client the call went through.
    """
    if isinstance(exc, FastMCPError):
        return True
    return (
        isinstance(exc, MCPError)
        and exc.error.code == MISSING_REQUIRED_CLIENT_CAPABILITY
    )


def _fault(fn: Callable[..., Any], exc: Exception) -> ToolError:
    """Log *exc* once and return the ``ToolError`` the model reads instead.

    Called inside the ``except`` block, so ``logger.exception`` attaches the
    traceback of *exc*. This is the one traceback the fault gets: the
    ``ToolError`` is raised ``from None`` so no later handler prints it again.
    """
    name, error_type = fn.__name__, type(exc).__name__
    # A rate limit or a timeout heals itself: WARNING, no traceback, and the
    # message FastMCP would have sent for it.
    if get_http_status_code(exc) == 429:
        message = _RATE_LIMITED_MESSAGE
    elif is_timeout_error(exc):
        message = _TIMED_OUT_MESSAGE
    else:
        logger.exception("tool_failed function=%s error_type=%s", name, error_type)
        return ToolError(FAULT_MESSAGE)
    logger.warning("tool_failed function=%s error_type=%s", name, error_type)
    return ToolError(message, log_level=logging.WARNING)


def _raise_classified(fn: Callable[..., Any], exc: Exception) -> NoReturn:
    """Re-raise *exc* if it passes through the boundary, else raise its fault.

    Shared by the sync and async wrappers, which call it from their
    ``except`` block.
    """
    if _passes_through(exc):
        raise exc
    raise _fault(fn, exc) from None


def tool_boundary(fn: _F) -> _F:
    """Turn any exception from *fn* that is not a ``FastMCPError`` into a fault.

    Apply it under ``@mcp.tool`` so FastMCP registers the wrapper::

        @mcp.tool
        @tool_boundary
        async def read_note(path: str) -> dict[str, Any]:
            try:
                note = await store.read(path)
            except FileNotFoundError:
                raise ToolError(
                    f"No note at '{path}'. Find the path with search_notes.",
                    log_level=logging.INFO,
                ) from None
            return {"text": note.text}

    A ``FastMCPError`` (``ToolError``, ``ResourceError``, ...) passes through
    unchanged: the tool raised it on purpose, with its own message and
    ``log_level``. So does an ``MCPError`` for a missing client capability,
    which must reach the wire as a protocol error. Any other ``Exception`` is
    logged once at ERROR with its traceback as ``tool_failed`` and replaced
    by a ``ToolError`` carrying :data:`FAULT_MESSAGE`, so the model never
    sees the exception's own text, with or without ``mask_error_details``.
    An upstream rate limit (HTTP 429) or timeout heals itself: it is logged
    at WARNING without a traceback and the ``ToolError`` says to retry, in
    the words FastMCP uses for the same cases. Cancellation and other
    ``BaseException`` subclasses pass through.

    ``functools.wraps`` keeps the signature and annotations, so the input and
    output schemas, ``Context`` injection and ``TaskConfig`` (which needs a
    coroutine function) behave as for *fn* itself.

    Args:
        fn: The tool function, sync or async.

    Returns:
        The wrapped function, of the same kind as *fn*.
    """
    wrapper: Callable[..., Any]
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                _raise_classified(fn, exc)

        wrapper = async_wrapper
    else:

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                _raise_classified(fn, exc)

        wrapper = sync_wrapper

    setattr(wrapper, _MARKER, True)
    return wrapper  # type: ignore[return-value]


def is_tool_boundary(fn: Callable[..., Any]) -> bool:
    """Report whether *fn* is wrapped by :func:`tool_boundary`.

    Follows ``__wrapped__`` through other ``functools.wraps`` decorators, so
    a boundary applied under another wrapper is still found. Meant for a
    test that enumerates a server's registered tools and fails any that can
    leak an unclassified exception.

    Args:
        fn: A tool function, e.g. a registered ``FunctionTool.fn``.

    Returns:
        ``True`` if *fn* or anything it wraps carries the boundary.
    """
    seen: set[int] = set()
    current: Any = fn
    while current is not None and id(current) not in seen:
        if getattr(current, _MARKER, False):
            return True
        seen.add(id(current))
        current = getattr(current, "__wrapped__", None)
    return False
