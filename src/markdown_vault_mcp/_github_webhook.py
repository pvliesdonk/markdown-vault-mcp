"""GitHub push-event webhook handler (issue #530).

Mounts a ``POST /github-webhook`` route that verifies the GitHub
HMAC-SHA256 signature and triggers ``force_pull`` + ``reindex`` on
``push`` events.  The route is only registered when
``MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET`` is set and the transport is
HTTP/SSE.

Integration points
------------------
- :func:`make_webhook_handler` — handler factory; call from ``server.py``
  to produce the callable passed to ``mcp.custom_route()``.
- :func:`_verify_signature` — pure HMAC-SHA256 check; separate so tests
  can exercise it without a live HTTP server.
- :func:`get_collection_singleton` — reaches the live Collection from the
  module singleton (same pattern as the artifact download route).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from typing import TYPE_CHECKING, Any

from starlette.responses import JSONResponse

from markdown_vault_mcp._server_deps import get_collection_singleton

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request

logger = logging.getLogger(__name__)


def _verify_signature(
    payload: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    """Return ``True`` when *signature_header* matches the HMAC-SHA256 of *payload*.

    GitHub signs every webhook delivery with
    ``X-Hub-Signature-256: sha256=<hex>``.  This function validates that
    header using a constant-time comparison so the secret cannot be
    recovered via a timing side-channel.

    Args:
        payload: Raw request body bytes.
        secret: Shared secret configured via
            ``MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET``.
        signature_header: Value of the ``X-Hub-Signature-256`` header, or
            ``None`` when the header is absent.

    Returns:
        ``True`` when the signature is valid; ``False`` in all other cases
        (missing header, wrong prefix, digest mismatch).
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    provided = signature_header[len("sha256=") :]
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def _reindex_after_pull(collection: Any) -> None:
    """Pause writes and reindex after a successful pull.

    Runs synchronously — intended to be called inside
    ``asyncio.to_thread`` from the async webhook handler.

    Failure is logged at ERROR and not re-raised so callers can return
    a 200 to GitHub regardless (a non-200 response causes GitHub to retry
    the delivery, which would trigger another pull on a potentially
    half-updated index).

    Args:
        collection: Live :class:`~markdown_vault_mcp.collection.Collection`.
    """
    try:
        with collection.pause_writes():
            collection.reindex()
    except Exception:
        logger.error(
            "github_webhook: reindex after pull failed — FTS index is "
            "stale until the next reindex or write tick",
            exc_info=True,
        )


def make_webhook_handler(secret: str) -> Callable[[Request], Any]:
    """Return a Starlette-compatible async handler for ``POST /github-webhook``.

    The returned handler:

    - Verifies the ``X-Hub-Signature-256`` header (HMAC-SHA256, constant-time).
    - Returns 401 on invalid or absent signatures.
    - Returns 200 + ``{"ok": true}`` for ``ping`` events (GitHub handshake).
    - Returns 200 + ``{"ok": true}`` for non-``push`` events (ignored).
    - On ``push`` events: calls ``Collection.force_pull()``, then
      ``reindex()`` when HEAD actually moved.
    - Returns 200 for deferred cases (collection not yet queryable) so
      GitHub does not retry the delivery.

    Args:
        secret: HMAC secret configured via
            ``MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET``.

    Returns:
        An ``async`` callable compatible with ``mcp.custom_route()``.
    """

    async def handle(request: Request) -> JSONResponse:
        body = await request.body()
        sig = request.headers.get("X-Hub-Signature-256")

        if not _verify_signature(body, secret, sig):
            logger.warning("github_webhook: invalid or missing HMAC signature")
            return JSONResponse({"error": "invalid signature"}, status_code=401)

        event = request.headers.get("X-GitHub-Event", "")

        if event == "ping":
            logger.info("github_webhook: ping received")
            return JSONResponse({"ok": True, "message": "pong"})

        if event != "push":
            logger.debug("github_webhook: event=%s ignored", event)
            return JSONResponse({"ok": True, "message": "event ignored"})

        # push event — pull + conditional reindex
        try:
            collection = get_collection_singleton()
        except RuntimeError:
            logger.info("github_webhook: collection not initialised, deferring")
            return JSONResponse({"ok": True, "message": "deferred"})

        if not collection.is_queryable():
            logger.info("github_webhook: collection not queryable, deferring")
            return JSONResponse({"ok": True, "message": "deferred"})

        pull_result = await asyncio.to_thread(collection.force_pull)

        if pull_result is None:
            logger.info("github_webhook: no git strategy configured")
            return JSONResponse({"ok": True, "message": "no git strategy"})

        if pull_result.applied and pull_result.from_sha != pull_result.to_sha:
            await asyncio.to_thread(_reindex_after_pull, collection)

        logger.info(
            "github_webhook: push processed applied=%s commits_pulled=%s",
            pull_result.applied,
            pull_result.commits_pulled,
        )
        return JSONResponse(
            {
                "ok": True,
                "applied": pull_result.applied,
                "commits_pulled": pull_result.commits_pulled,
            }
        )

    return handle
