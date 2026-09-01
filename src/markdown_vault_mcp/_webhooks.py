"""Push-event webhook handlers for GitHub (#530) and GitLab (#1178).

Both hosts notify the same thing — "the remote moved" — and the response is
identical: ``force_pull`` then a conditional ``reindex``. Only the envelope
differs, so :class:`WebhookProvider` holds the per-host parts (how a delivery
authenticates, which header names the event and the delivery id) and
:func:`make_webhook_handler` holds the one shared body.

Each host gets its own route, mounted only when that host's credentials are
set and the transport is HTTP/SSE:

- ``POST /github-webhook`` — ``MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET``
- ``POST /gitlab-webhook`` — ``MARKDOWN_VAULT_MCP_GITLAB_WEBHOOK_SIGNING_TOKEN``
  and/or ``MARKDOWN_VAULT_MCP_GITLAB_WEBHOOK_SECRET_TOKEN``

Integration points
------------------
- :func:`register_webhook_routes` — the whole mounting decision; the one
  call ``server.py``'s wiring block makes.
- :func:`github_provider` / :func:`gitlab_provider` — build a provider from
  configured credentials.
- :func:`make_webhook_handler` — handler factory; produces the callable
  passed to ``mcp.custom_route()``.
- :func:`_verify_github_signature` / :func:`_verify_gitlab_signature` — pure
  credential checks, separate so tests can exercise them without a live
  HTTP server.
- :func:`get_vault_singleton` — reaches the live Vault from the module
  singleton set by the lifespan factory, since these handlers run outside
  FastMCP's ``Depends(get_vault)`` injection.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.responses import JSONResponse

from markdown_vault_mcp.domain import get_vault_singleton
from markdown_vault_mcp.git.types import PULL_REASON_PULL_DISABLED

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from starlette.requests import Request

logger = logging.getLogger(__name__)

#: How far `webhook-timestamp` may sit from now, in seconds, before a GitLab
#: signing-token delivery is rejected. GitLab's documentation requires a
#: freshness check without naming a window ("validate that the timestamp in
#: `webhook-timestamp` is recent"); 300 s is the Standard Webhooks convention
#: GitLab implements. Applied in both directions, so a clock ahead of ours is
#: rejected the same as one behind.
GITLAB_TIMESTAMP_TOLERANCE_S = 300


@dataclass(frozen=True)
class WebhookProvider:
    """The host-specific half of a push-webhook delivery.

    Attributes:
        name: Log prefix and route identity, e.g. ``"github_webhook"``.
        verify: Returns ``True`` when the delivery's headers and body
            authenticate against the configured credentials.
        event_header: Header naming the event type.
        push_event: Value of *event_header* that means "the remote moved".
        delivery_id_header: Header carrying the host's per-delivery id, used
            only for log correlation.
        ping_event: Value of *event_header* the host sends as a handshake,
            answered with a 200 and no pull. ``None`` for hosts that have no
            such event — GitLab's "Test" button sends a real ``Push Hook``.
    """

    name: str
    verify: Callable[[Mapping[str, str], bytes], bool]
    event_header: str
    push_event: str
    delivery_id_header: str
    ping_event: str | None = None


def _verify_github_signature(
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


def _timestamp_is_fresh(raw: str, *, now: float | None = None) -> bool:
    """Return ``True`` when *raw* is a Unix second count within the tolerance.

    A signature that never expires can be captured and replayed forever, so
    GitLab's documentation requires this check before the payload is
    processed. The comparison is symmetric: a timestamp from the future is as
    suspect as a stale one, since only clock skew explains it.

    Args:
        raw: Value of the ``webhook-timestamp`` header. The only caller
            checks the header is present first, so this is never ``None``.
        now: Current Unix time; injected by tests.

    Returns:
        ``True`` when *raw* parses as an integer within
        :data:`GITLAB_TIMESTAMP_TOLERANCE_S` of *now*.
    """
    try:
        sent = int(raw)
    except ValueError:
        return False
    reference = time.time() if now is None else now
    return abs(reference - sent) <= GITLAB_TIMESTAMP_TOLERANCE_S


def _verify_gitlab_signature(
    payload: bytes,
    signing_token: str,
    headers: Mapping[str, str],
    *,
    now: float | None = None,
) -> bool:
    """Return ``True`` when the Standard Webhooks signature authenticates.

    GitLab 19.0+ signs deliveries following the Standard Webhooks
    specification: the HMAC-SHA256 covers ``{webhook-id}.{webhook-timestamp}.``
    followed by the raw body — *not* the body alone — and the digest is
    base64, behind a ``v1,`` prefix. The header is documented as possibly
    carrying several space-separated signatures, so every candidate is
    checked and each comparison is constant-time.

    Args:
        payload: Raw request body bytes.
        signing_token: Value of
            ``MARKDOWN_VAULT_MCP_GITLAB_WEBHOOK_SIGNING_TOKEN``.
        headers: Request headers; ``webhook-id``, ``webhook-timestamp`` and
            ``webhook-signature`` are all inputs to the check.
        now: Current Unix time; injected by tests.

    Returns:
        ``True`` when a signature matches and the timestamp is fresh;
        ``False`` otherwise, including any header missing.
    """
    signature_header = headers.get("webhook-signature")
    webhook_id = headers.get("webhook-id")
    timestamp = headers.get("webhook-timestamp")
    if not signature_header or not webhook_id or not timestamp:
        return False
    if not _timestamp_is_fresh(timestamp, now=now):
        return False

    signed = f"{webhook_id}.{timestamp}.".encode() + payload
    digest = hmac.new(signing_token.encode(), signed, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()

    matched = False
    for candidate in signature_header.split():
        version, _, provided = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(expected, provided):
            matched = True
    return matched


def _verify_gitlab_secret_token(secret_token: str, provided: str | None) -> bool:
    """Return ``True`` when the plain ``X-Gitlab-Token`` header matches.

    GitLab's original webhook authentication, and the only one available
    below 19.0. It sends the shared secret in clear text rather than proving
    knowledge of it, so it offers no payload integrity and no replay
    protection — GitLab itself no longer recommends it for new webhooks. The
    comparison is still constant-time.

    Args:
        secret_token: Value of
            ``MARKDOWN_VAULT_MCP_GITLAB_WEBHOOK_SECRET_TOKEN``.
        provided: Value of the ``X-Gitlab-Token`` header, or ``None``.

    Returns:
        ``True`` when the header is present and matches.
    """
    if not provided:
        return False
    return hmac.compare_digest(secret_token, provided)


def github_provider(secret: str) -> WebhookProvider:
    """Build the GitHub provider from its configured secret.

    Args:
        secret: Value of ``MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET``.

    Returns:
        A :class:`WebhookProvider` for ``POST /github-webhook``.
    """

    def verify(headers: Mapping[str, str], body: bytes) -> bool:
        return _verify_github_signature(
            body, secret, headers.get("X-Hub-Signature-256")
        )

    return WebhookProvider(
        name="github_webhook",
        verify=verify,
        event_header="X-GitHub-Event",
        push_event="push",
        delivery_id_header="X-GitHub-Delivery",
        ping_event="ping",
    )


def gitlab_provider(
    signing_token: str | None,
    secret_token: str | None,
) -> WebhookProvider:
    """Build the GitLab provider from whichever credentials are configured.

    Both forms may be set at once, which is what makes a migration possible:
    the signing token is tried first, and a delivery still authenticating with
    the legacy header keeps working until the webhook is reconfigured.

    Args:
        signing_token: Value of
            ``MARKDOWN_VAULT_MCP_GITLAB_WEBHOOK_SIGNING_TOKEN``; GitLab 19.0+.
        secret_token: Value of
            ``MARKDOWN_VAULT_MCP_GITLAB_WEBHOOK_SECRET_TOKEN``; the plain-text
            form, and the only one older GitLab offers.

    Returns:
        A :class:`WebhookProvider` for ``POST /gitlab-webhook``.
    """

    def verify(headers: Mapping[str, str], body: bytes) -> bool:
        if signing_token and _verify_gitlab_signature(body, signing_token, headers):
            return True
        return bool(secret_token) and _verify_gitlab_secret_token(
            secret_token or "", headers.get("X-Gitlab-Token")
        )

    return WebhookProvider(
        name="gitlab_webhook",
        verify=verify,
        event_header="X-Gitlab-Event",
        push_event="Push Hook",
        delivery_id_header="X-Gitlab-Event-UUID",
    )


def _reindex_after_pull(vault: Any, provider_name: str) -> None:
    """Pause writes and reindex after a successful pull.

    Runs synchronously — intended to be called inside
    ``asyncio.to_thread`` from the async webhook handler.

    Failure is logged at ERROR and not re-raised so callers can return
    a 200 to the host regardless (a non-200 response causes a retry,
    which would trigger another pull on a potentially half-updated index).

    Args:
        vault: Live :class:`~markdown_vault_mcp.vault.Vault`.
        provider_name: Log prefix identifying the host.
    """
    try:
        with vault.pause_writes():
            vault.index.reindex()
    except Exception:
        logger.error(
            "%s: reindex after pull failed — FTS index is "
            "stale until the next reindex or write tick",
            provider_name,
            exc_info=True,
        )


async def _process_push(vault: Any, name: str, delivery_id: str) -> JSONResponse:
    """Pull, then reindex when HEAD moved, and map the outcome to a status.

    Split out of the handler so the request-shaped concerns (authenticate,
    classify the event) stay separate from the git-shaped ones, and so each
    stays inside the project's complexity budget.

    ``force_pull`` runs regardless of ``is_queryable()``: it is a pure git
    operation with no FTS or vector-index dependency, so blocking on a cold
    index would exhaust the host's retry budget (GitHub: ~5 s + ~25 s + ~90 s
    ≈ 2 min) before a large vault finishes its initial build, permanently
    losing the delivery.

    Args:
        vault: Live :class:`~markdown_vault_mcp.vault.Vault`.
        name: Provider name, used as the log prefix.
        delivery_id: The host's delivery id, for log correlation.

    Returns:
        200 when the delivery is settled — pulled, or nothing to pull; 503
        when a retry might succeed.
    """
    try:
        pull_result = await asyncio.to_thread(vault.force_pull)
    except Exception:
        # The handler's contract is 401 / 200 / 503; an exception escaping
        # here would surface as an unhandled 500 with a traceback (#1128).
        # A retry may still succeed, so 503 rather than 200.
        logger.error(
            "%s: force_pull raised delivery_id=%s", name, delivery_id, exc_info=True
        )
        return JSONResponse({"error": "pull failed"}, status_code=503)

    if pull_result is None:
        # Reachable for a library consumer that constructed Vault with
        # git_strategy=None; a config-driven server always has one.
        logger.info("%s: no git strategy configured delivery_id=%s", name, delivery_id)
        return JSONResponse({"ok": True, "message": "no git strategy"})

    if pull_result.reason == PULL_REASON_PULL_DISABLED:
        # A webhook credential set on a deployment with no managed remote
        # (#1128). Nothing was fetched and nothing can be: answer 200 so the
        # host records the delivery instead of retrying every push.
        logger.warning(
            "%s: delivery received but this deployment has no managed git "
            "remote, so there is nothing to pull — set "
            "MARKDOWN_VAULT_MCP_GIT_REPO_URL (or unset the webhook "
            "credentials) delivery_id=%s",
            name,
            delivery_id,
        )
        return JSONResponse({"ok": True, "message": "pull disabled"})

    if not pull_result.applied:
        # Transient failures (network, expired token) benefit from retry.
        # Permanent failures (no_remote, conflict) exhaust the retry budget
        # and fall back to the next periodic pull tick.
        logger.warning(
            "%s: force_pull not applied reason=%s delivery_id=%s",
            name,
            pull_result.reason,
            delivery_id,
        )
        return JSONResponse(
            {"error": "pull not applied", "reason": pull_result.reason},
            status_code=503,
        )

    if pull_result.from_sha != pull_result.to_sha:
        if vault.index.is_queryable():
            await asyncio.to_thread(_reindex_after_pull, vault, name)
        else:
            logger.info(
                "%s: pull applied but vault not queryable, skipping reindex "
                "delivery_id=%s",
                name,
                delivery_id,
            )

    logger.info(
        "%s: push processed commits_pulled=%s delivery_id=%s",
        name,
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


def make_webhook_handler(provider: WebhookProvider) -> Callable[[Request], Any]:
    """Return a Starlette-compatible async handler for *provider*'s route.

    The returned handler:

    - Verifies the delivery with ``provider.verify``.
    - Returns 401 on invalid or absent credentials.
    - Returns 200 for the host's ping event, when it has one, and for
      any event that is not a push.
    - Returns 503 when the server has not yet initialised (singleton not
      set), so the host retries rather than treating the delivery as
      successfully handled.
    - Hands push events to :func:`_process_push`, which owns the pull, the
      conditional reindex, and the remaining status mapping.

    Args:
        provider: The host's envelope, from :func:`github_provider` or
            :func:`gitlab_provider`.

    Returns:
        An ``async`` callable compatible with ``mcp.custom_route()``.
    """

    async def handle(request: Request) -> JSONResponse:
        body = await request.body()
        name = provider.name
        delivery_id = request.headers.get(provider.delivery_id_header, "unknown")

        if not provider.verify(request.headers, body):
            logger.warning(
                "%s: invalid or missing credentials delivery_id=%s", name, delivery_id
            )
            return JSONResponse({"error": "invalid signature"}, status_code=401)

        event = request.headers.get(provider.event_header, "")

        if provider.ping_event is not None and event == provider.ping_event:
            logger.info("%s: ping received delivery_id=%s", name, delivery_id)
            return JSONResponse({"ok": True, "message": "pong"})

        if event != provider.push_event:
            logger.debug(
                "%s: event=%s ignored delivery_id=%s", name, event, delivery_id
            )
            return JSONResponse({"ok": True, "message": "event ignored"})

        try:
            vault = get_vault_singleton()
        except RuntimeError:
            logger.info(
                "%s: vault not initialised, returning 503 delivery_id=%s",
                name,
                delivery_id,
            )
            return JSONResponse({"error": "vault not initialised"}, status_code=503)

        return await _process_push(vault, name, delivery_id)

    return handle


def register_webhook_routes(mcp: Any, config: Any, transport: str) -> None:
    """Mount each host's webhook route when its credentials are configured.

    Owns the whole decision — which routes exist, and the two startup
    warnings about credentials that are set but weak or inert — so
    ``server.py``'s wiring block makes one call rather than growing a second
    host's worth of branching.

    Nothing is mounted under stdio: there is no HTTP server to receive a POST,
    so the credentials have no effect there.

    Args:
        mcp: The ``FastMCP`` server to mount routes on.
        config: The project configuration; ``config.sync`` supplies the
            credentials and ``config.git`` the managed-remote check.
        transport: The resolved transport name.
    """
    if not config.sync.webhook_configured or transport == "stdio":
        return

    if config.git.repo_url is None and config.git.token is None:
        # The routes still mount and answer 200, but every delivery is a
        # no-op: this deployment has no managed remote to pull from.  Say so
        # at startup rather than leaving the operator to infer it from
        # per-delivery logs (#1128).
        logger.warning(
            "webhook_inert: webhook credentials are set but no managed git "
            "remote is configured, so push deliveries have nothing to pull — "
            "set GIT_REPO_URL to enable sync, or unset the webhook "
            "credentials to drop the endpoints"
        )

    if config.sync.github_webhook_secret:
        mcp.custom_route("/github-webhook", methods=["POST"])(
            make_webhook_handler(github_provider(config.sync.github_webhook_secret))
        )

    signing = config.sync.gitlab_webhook_signing_token
    secret = config.sync.gitlab_webhook_secret_token
    if signing or secret:
        if not signing:
            # Reachable only by choosing the weaker of two documented options,
            # which an operator on GitLab 19.0+ has no reason to do — so say it
            # once at startup rather than per delivery.
            logger.warning(
                "gitlab_webhook_secret_token_only: authenticating GitLab "
                "deliveries with the plain-text secret token, which proves "
                "nothing about the body and cannot expire — set "
                "GITLAB_WEBHOOK_SIGNING_TOKEN instead on GitLab 19.0+"
            )
        mcp.custom_route("/gitlab-webhook", methods=["POST"])(
            make_webhook_handler(gitlab_provider(signing, secret))
        )
