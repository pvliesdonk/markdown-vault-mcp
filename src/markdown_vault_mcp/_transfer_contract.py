"""The transfer sink contract: what the routes hand bytes to, and its errors.

Ported from ``fastmcp_pvl_core._transfer.sink`` 10.2.0 (#2319). Only the error
classes this project raises came along: :class:`TransferSinkError` and the
410 and 503 subclasses.

A sink implements two things: where bytes land (:class:`TransferSink`) and
which refs are acceptable (:data:`TransferValidator`). The stored token
carries an opaque ``sink_handle`` string that only the sink interprets (here,
a vault-relative path or an ``okf-bundle`` ref); the routes store it and hand
it back to :meth:`TransferSink.read` / :meth:`TransferSink.write` but never
parse it.

A sink may raise :class:`TransferSinkError` (or a subclass) to return a
specific 4xx/5xx status for one transfer instead of the default 500.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, NamedTuple, Protocol

TransferKind = Literal["download", "upload"]
"""The two link kinds; matched by the store on claim and passed to a validator."""


class TransferReadResult(NamedTuple):
    """What :meth:`TransferSink.read` returns: the bytes plus how to serve them.

    A ``NamedTuple``, not a bare 3-tuple, so ``media_type`` and ``filename``
    (both ``str``) cannot be swapped silently at the construction site; it
    still unpacks positionally.
    """

    body: bytes
    media_type: str
    filename: str


class TransferSink(Protocol):
    """Where transferred bytes come from and go to.

    ``handle`` is the opaque ``sink_handle`` the link was minted with. Both
    methods are byte-oriented and bounded by the size caps: the whole body is
    materialised in memory and capped, not streamed.
    """

    async def read(self, handle: str) -> TransferReadResult:
        """Return the body, media type, and filename for a download *handle*."""
        ...

    async def write(self, handle: str, body: bytes) -> Mapping[str, Any]:
        """Commit an uploaded *body* to *handle*; return the tool result payload."""
        ...


TransferValidator = Callable[[str, TransferKind], Awaitable[str]]
"""Map a caller-facing ref and kind to a validated, opaque ``sink_handle``.

To reject a ref (bad extension, missing file, wrong kind) raise
``fastmcp.exceptions.ToolError(msg, log_level=logging.INFO)``. The message
reaches the model as the link tool's error, so say what was wrong and what to
pass instead. Any other exception reaches the link tool's ``tool_boundary`` as
a server fault: the model is told the request was fine and to retry later, so
a rejection raised as ``ValueError`` would be reported as a fault. It runs at
link creation, not in the route handler. ``kind`` lets a validator apply
different rules to upload and download. The returned string is stored
verbatim as the token's ``sink_handle``.
"""


class TransferSinkError(Exception):
    """Raised by a :class:`TransferSink` to signal a specific HTTP error status.

    A sink's ``read`` / ``write`` normally lets an unexpected failure
    propagate, which the ``/transfer/{token}`` handler turns into an opaque
    500 after releasing the token. Raising this instead returns a specific
    status, a judgment only the sink can make (the resource is gone, the
    backend is briefly down). ``status_code`` must be a client- or
    server-error status (400-599); the handler still releases the token.

    Prefer a named subclass for a common case; use the base directly for a
    status without one (``TransferSinkError(401)``).

    Args:
        status_code: The HTTP status to return, in 400-599.
        *args: An optional message, preserved on ``str(exc)``.

    Raises:
        ValueError: If ``status_code`` is not in 400-599, surfaced at the raise
            site rather than silently mis-mapped.
    """

    status_code: int

    def __init__(self, status_code: int, *args: object) -> None:
        if not 400 <= status_code <= 599:
            raise ValueError(
                f"TransferSinkError status_code must be a 4xx/5xx HTTP error "
                f"status (400-599), got {status_code}"
            )
        super().__init__(*args)
        self.status_code = status_code

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Keep the exception picklable and copyable.

        ``Exception.__reduce__`` replays ``self.args`` (the message only)
        through ``type(self)(...)``; for the base that would call
        ``TransferSinkError(<message>)`` and fail the status check. The base
        needs ``status_code`` first; a fixed-status subclass takes only the
        message args.

        Returns:
            The class and constructor arguments that rebuild this exception.
        """
        if type(self) is TransferSinkError:
            return (self.__class__, (self.status_code, *self.args))
        return (self.__class__, self.args)


class TransferResourceGoneError(TransferSinkError):
    """The resource the link pointed at existed and is now gone (**410 Gone**).

    Distinct from the claim-time 404: the sink runs only after a successful
    claim, so the caller held a valid link; 410 says the resource vanished, not
    that the link is bad.
    """

    def __init__(self, *args: object) -> None:
        super().__init__(410, *args)


class TransferUnavailableError(TransferSinkError):
    """The backend is temporarily unavailable; the caller may retry (**503**)."""

    def __init__(self, *args: object) -> None:
        super().__init__(503, *args)
