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
            collection.index.reindex()
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
    - Returns 200 for ``ping`` events (GitHub handshake) and ignored events.
    - On ``push`` events: calls ``Collection.force_pull()`` unconditionally
      (it is a pure git operation with no FTS dependency), then reindexes when
      HEAD moved and the collection is queryable.
    - Returns 503 when the server has not yet initialised (singleton not set),
      or when ``force_pull`` fails (``applied=False``), so GitHub retries the
      delivery rather than permanently marking it as delivered.

    Args:
        secret: HMAC secret configured via
            ``MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET``.

    Returns:
        An ``async`` callable compatible with ``mcp.custom_route()``.
    """

    async def handle(request: Request) -> JSONResponse:
        body = await request.body()
        sig = request.headers.get("X-Hub-Signature-256")
        delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")

        if not _verify_signature(body, secret, sig):
            logger.warning(
                "github_webhook: invalid or missing HMAC signature delivery_id=%s",
                delivery_id,
            )
            return JSONResponse({"error": "invalid signature"}, status_code=401)

        event = request.headers.get("X-GitHub-Event", "")

        if event == "ping":
            logger.info("github_webhook: ping received delivery_id=%s", delivery_id)
            return JSONResponse({"ok": True, "message": "pong"})

        if event != "push":
            logger.debug(
                "github_webhook: event=%s ignored delivery_id=%s", event, delivery_id
            )
            return JSONResponse({"ok": True, "message": "event ignored"})

        # push event — pull then conditional reindex.
        #
        # force_pull() is a pure git operation (fetch + merge/rebase) with no
        # FTS or vector-index dependency.  Run it regardless of is_queryable()
        # so that cold-start deliveries are not permanently lost — blocking here
        # would exhaust GitHub's retry budget (~5 s + ~25 s + ~90 s ≈ 2 min)
        # before a large vault finishes its initial index build.
        #
        # Return 503 only when the Collection singleton itself hasn't been set
        # yet (server lifespan not complete) or when the pull fails, so GitHub
        # retries rather than treating the delivery as successfully handled.
        try:
            collection = get_collection_singleton()
        except RuntimeError:
            logger.info(
                "github_webhook: collection not initialised, returning 503 "
                "delivery_id=%s",
                delivery_id,
            )
            return JSONResponse(
                {"error": "collection not initialised"}, status_code=503
            )

        pull_result = await asyncio.to_thread(collection.force_pull)

        if pull_result is None:
            logger.info(
                "github_webhook: no git strategy configured delivery_id=%s",
                delivery_id,
            )
            return JSONResponse({"ok": True, "message": "no git strategy"})

        if not pull_result.applied:
            # Transient failures (network, expired token) benefit from retry.
            # Permanent failures (no_remote, conflict) exhaust the 3-attempt
            # budget and fall back to the next periodic pull tick.
            logger.warning(
                "github_webhook: force_pull not applied reason=%s delivery_id=%s",
                pull_result.reason,
                delivery_id,
            )
            return JSONResponse(
                {"error": "pull not applied", "reason": pull_result.reason},
                status_code=503,
            )

        if pull_result.from_sha != pull_result.to_sha:
            if collection.index.is_queryable():
                await asyncio.to_thread(_reindex_after_pull, collection)
            else:
                logger.info(
                    "github_webhook: pull applied but collection not queryable, "
                    "skipping reindex delivery_id=%s",
                    delivery_id,
                )

        logger.info(
            "github_webhook: push processed commits_pulled=%s delivery_id=%s",
            pull_result.commits_pulled,
            delivery_id,
        )
        return JSONResponse(
            {
                "ok": True,
                "applied": pull_result.applied,
                "commits_pulled": pull_result.commits_pulled,
            }
        )

    return handle
