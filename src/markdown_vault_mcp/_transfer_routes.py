"""The ``/transfer/{token}`` HTTP handler.

Ported from ``fastmcp_pvl_core._transfer.routes`` 10.2.0 (#2319).

:func:`make_transfer_handler` builds the Starlette handler that drives the
capability-link state machine over HTTP:

- **GET** downloads: claim the token, ``sink.read`` the bytes, serve them with
  an RFC 6266 ``Content-Disposition``, then ``store.complete`` (grace-settle).
- **POST/PUT** uploads: claim the token, read the request body under a size
  cap (aborting early past it), ``sink.write`` it, return the sink's payload
  as JSON, then ``store.complete`` (grace-settle).

The link is settled on success (``store.complete``, grace-settle, so a
served-but-stalled transfer can retry within the grace window) and released on
any failure (``store.release``: a transient failure must not spend the link).
A sink may raise :class:`~markdown_vault_mcp._transfer_contract.TransferSinkError`
from ``read``/``write`` to return a specific 4xx/5xx status instead of the
default 500; the link is still released. ``_transfer_links`` mounts the handler
via ``mcp.custom_route`` and wires the operator caps from config.

The token store and its errors still come from pvl-core through the shim, until
#2320 moves them.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import cast
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from markdown_vault_mcp._pvl_core import (
    TokenInFlightError,
    TransferStore,
    TransferToken,
    TransferTokenError,
)
from markdown_vault_mcp._transfer_contract import TransferSink, TransferSinkError

logger = logging.getLogger(__name__)

_Handler = Callable[[Request], Awaitable[Response]]

# A response that abandons an unread request body (an upload rejected before or
# during the body read, or a method the handler does not serve) must close the
# connection: leaving the body undrained on a keep-alive socket desyncs the next
# request. Read-only so the shared instance can't be mutated through a Response.
_CLOSE_CONN = MappingProxyType({"connection": "close"})


class _BodyTooLargeError(Exception):
    """Internal signal: the request body exceeded the upload cap."""


def make_transfer_handler(
    store: TransferStore,
    sink: TransferSink,
    *,
    max_upload_bytes: int,
) -> _Handler:
    """Build the ``/transfer/{token}`` handler over *store* and *sink*.

    Args:
        store: The capability-link token store minted links are claimed from.
        sink: Where bytes are read from and written to. The handler never
            interprets the ``sink_handle``.
        max_upload_bytes: Operator per-upload size cap, wired from config by
            ``_transfer_links``. A body exceeding it is rejected 413.

    Returns:
        A Starlette async handler: ``async (Request) -> Response``.

    Raises:
        ValueError: If ``max_upload_bytes`` is not positive.

    Note:
        The handler serves ``GET``/``POST``/``PUT`` and answers any *other*
        method routed to it with 405 + ``Connection: close`` (an unserved method
        may carry an unread body). For that close to cover **every** unsupported
        method, ``_transfer_links`` must register the route so all methods
        reach this handler (e.g. omit ``methods=`` or pass a
        superset) — otherwise Starlette's own 405 handles unregistered methods,
        and *it* does not send ``Connection: close``. ``HEAD`` reaches this
        branch (Starlette auto-adds it for ``GET``) but is bodyless, so its close
        is harmless.
    """
    if max_upload_bytes <= 0:
        raise ValueError(f"max_upload_bytes must be positive, got {max_upload_bytes}")

    async def handler(request: Request) -> Response:
        token = request.path_params["token"]
        if request.method == "GET":
            return await _download(store, sink, token)
        if request.method in ("POST", "PUT"):
            return await _upload(store, sink, token, request, max_upload_bytes)
        # A method the handler does not serve may carry a body it never reads →
        # close the connection (same undrained-body class as an upload rejected
        # before the read). Allow lists the served methods (RFC 7231 §6.5.5).
        return Response(
            status_code=405, headers={**_CLOSE_CONN, "allow": "GET, POST, PUT"}
        )

    return handler


def _claim_error_status(exc: TransferTokenError) -> int:
    """Map a claim failure to an HTTP status.

    A live concurrent reservation is a 409 (retry later); every other claim
    failure — unknown, expired, consumed, wrong-kind — reads to the client as
    "no such link" (404), so the handler never discloses which of those it was.
    """
    if isinstance(exc, TokenInFlightError):
        return 409
    return 404


async def _release_quietly(store: TransferStore, claim: TransferToken) -> None:
    """Release *claim* on a failure path without masking the original error.

    An ordinary failure inside ``store.release`` (e.g. a KV backend error) is
    logged and swallowed so the transfer's own exception — the one the caller
    needs — still propagates. A ``BaseException`` (e.g. ``CancelledError``) from
    release is left to propagate, as cancellation must.
    """
    try:
        await store.release(claim.token, claim.fence)
    except Exception as exc:
        # Log the error class only — never a traceback / message, which for some
        # KV backends embeds the token-derived key (the token is a secret).
        logger.warning(
            "transfer_release_failed error_class=%s",
            type(exc).__name__,
        )


# The sink contract types ``handle`` as ``str``; the store types the stored
# ``sink_handle`` as opaque ``object``. The ``str``-ness is guaranteed by the
# TransferValidator, which returns the ``str`` handle stored at mint time — so
# the cast is a trusted-boundary narrowing, not an unchecked guess.
async def _download(store: TransferStore, sink: TransferSink, token: str) -> Response:
    try:
        claim = await store.claim(token, "download")
    except TransferTokenError as exc:
        logger.info(
            "transfer_download_claim_rejected error_class=%s", type(exc).__name__
        )
        return Response(status_code=_claim_error_status(exc))
    try:
        body, media_type, filename = await sink.read(cast("str", claim.sink_handle))
    except TransferSinkError as exc:
        # A deliberate domain signal: release the link and return the sink's
        # chosen status. Log the status and class name only — never the message,
        # which is authored from the domain sink_handle and may embed a domain
        # path or other domain data (the sink never sees the token).
        await _release_quietly(store, claim)
        logger.info(
            "transfer_download_sink_signalled status=%d error_class=%s",
            exc.status_code,
            type(exc).__name__,
        )
        return Response(status_code=exc.status_code)
    except BaseException:
        # Release-on-failure: the link survives a transient failure. BaseException
        # (not Exception) so a cancelled request also releases; then the error /
        # cancellation propagates (Starlette → generic 500 for an ordinary error).
        await _release_quietly(store, claim)
        raise
    await store.complete(claim.token, claim.fence)
    return Response(
        content=body,
        media_type=media_type,
        headers={"content-disposition": _content_disposition(filename)},
    )


async def _upload(
    store: TransferStore,
    sink: TransferSink,
    token: str,
    request: Request,
    max_upload_bytes: int,
) -> Response:
    try:
        claim = await store.claim(token, "upload")
    except TransferTokenError as exc:
        logger.info("transfer_upload_claim_rejected error_class=%s", type(exc).__name__)
        # No claim held, and the request body is unread → close the connection.
        return Response(status_code=_claim_error_status(exc), headers=_CLOSE_CONN)
    try:
        body = await _read_capped(request, max_upload_bytes)
    except _BodyTooLargeError:
        await _release_quietly(store, claim)
        logger.info(
            "transfer_upload_rejected reason=body_too_large max_bytes=%d",
            max_upload_bytes,
        )
        # Body read aborted early → the rest is undrained → close the connection.
        return Response(status_code=413, headers=_CLOSE_CONN)
    except BaseException:
        await _release_quietly(store, claim)
        raise
    try:
        payload = await sink.write(cast("str", claim.sink_handle), body)
    except TransferSinkError as exc:
        # Deliberate domain signal. The body was fully read above, so nothing is
        # left undrained → no Connection: close needed (unlike the 413 path).
        await _release_quietly(store, claim)
        logger.info(
            "transfer_upload_sink_signalled status=%d error_class=%s",
            exc.status_code,
            type(exc).__name__,
        )
        return Response(status_code=exc.status_code)
    except BaseException:
        await _release_quietly(store, claim)
        raise
    await store.complete(claim.token, claim.fence)
    return JSONResponse(dict(payload))


async def _read_capped(request: Request, max_bytes: int) -> bytes:
    """Read the request body in chunks, aborting once it exceeds *max_bytes*.

    Reads the receive-side stream chunk by chunk so an oversize body is rejected
    early rather than fully buffered. The accepted body is still held in
    memory, bounded by the cap, because the sink is byte-oriented.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise _BodyTooLargeError
        chunks.append(chunk)
    return b"".join(chunks)


def _content_disposition(filename: str) -> str:
    """Build an RFC 6266 ``attachment`` Content-Disposition for *filename*.

    Emits both a plain ``filename`` (ASCII fallback) and an RFC 5987
    ``filename*`` (percent-encoded UTF-8) so non-ASCII names survive. Strips
    control characters (including CR/LF) and quoted-string breakers first, so a
    hostile filename cannot inject a second header or break out of the value.
    """
    safe = "".join(c for c in filename if c.isprintable() and c not in '"\\')
    ascii_fallback = safe.encode("ascii", "replace").decode("ascii")
    encoded = quote(safe, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
