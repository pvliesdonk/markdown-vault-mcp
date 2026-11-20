"""SSRF-hardened URL-to-bytes fetch.

Ported from ``fastmcp_pvl_core._transfer.fetch`` 10.2.0 (#2282). It pulls the
bytes at a caller-supplied HTTP(S) URL (the ``fetch`` tool's ``url``) without
becoming a server-side request-forgery vector.

Hardening:

- **Scheme allowlist** — only ``http``/``https``, fixed rather than
  configurable: loosening it is an SSRF policy change.
- **Address validation + DNS-rebind pin** — the host is resolved, *every*
  resolved address is rejected if non-public, and the validated IP is pinned
  into the connection (we dial the IP literal, carrying the real hostname only
  in the ``Host`` header and TLS SNI). This closes the TOCTOU window between
  validation and connect. Fails **closed** on any resolution error.
- **Redirects, revalidated per hop** — redirects are followed, but the guard
  above is not a pre-flight check done once in :func:`fetch_url`; it lives in
  :class:`_GuardedTransport`. httpx sends every redirect hop back through the
  transport, so each hop repeats the scheme check, the address validation, and
  the pin by construction — a ``Location`` pointing at an internal target is
  refused exactly as a directly-supplied one is. httpx bounds the chain
  (``max_redirects``) and drops ``Authorization`` on a cross-origin hop.
- **No ambient environment** — the client runs with ``trust_env=False`` so an
  ``HTTP(S)_PROXY`` env var cannot divert the request past the pinned IP and
  ``.netrc`` cannot attach ambient credentials.
- **Streaming size cap** — the body is read in chunks and aborted the moment it
  exceeds ``max_bytes``; an oversize payload never fully materialises.
- **Credential hygiene** — embedded ``user:pass@`` userinfo is dropped from the
  dialled request (never relayed to the origin), and every URL this primitive
  *itself* emits — its log line and the exceptions it raises, including the
  ``HTTPStatusError`` re-raised on a 4xx/5xx — is redacted of userinfo + query
  via :func:`_redact_url`: redaction covers every emit path this module owns,
  not only logs. httpx's own internal request logger also emits the URL; that
  is the logging setup's concern, not something this module should globally
  reconfigure.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlunparse

import httpx

from markdown_vault_mcp._url import UNPARSEABLE, safe_netloc, try_parse_url

logger = logging.getLogger(__name__)

DEFAULT_FETCH_MAX_BYTES = 100 * 1024 * 1024  # 100 MiB
DEFAULT_FETCH_TIMEOUT_S = 30.0

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "metadata.google.internal"}
)
_SCHEME_DEFAULT_PORTS = {"http": 80, "https": 443}
_BLOCKED_ADDR_MSG = (
    "URLs targeting private, loopback, link-local, or reserved addresses "
    "are not allowed."
)
_CHUNK_SIZE = 65536
# Namespaced key under which _GuardedTransport stashes each hop's real URL
# on the request, so fetch_url can report where the bytes came from.
_FINAL_URL_KEY = "markdown_vault_mcp_fetch_url"
# RFC 6052 NAT64 well-known prefix. stdlib does not decode the IPv4 embedded in
# its low 32 bits for is_global, so we validate that IPv4 explicitly.
_NAT64_WKP = ipaddress.ip_network("64:ff9b::/96")


@dataclass(frozen=True)
class FetchResult:
    """The outcome of a successful :func:`fetch_url`.

    Attributes:
        body: The full response body (materialised in memory, bounded by the
            ``max_bytes`` cap the caller passed).
        content_type: The response ``Content-Type`` header, or ``None``.
        final_url: The URL the bytes actually came from. Equal to the requested
            URL when nothing redirected; otherwise the last hop. Userinfo is
            stripped, but the query string is **not** — a redirect target's
            query is often load-bearing (pre-signed links), so it is returned
            intact and callers must not log it verbatim.

    The byte count is exposed as the derived :attr:`size` property rather than a
    stored field, so it can never drift from ``len(body)``.
    """

    body: bytes
    content_type: str | None
    final_url: str

    @property
    def size(self) -> int:
        """Number of bytes downloaded — ``len(body)``."""
        return len(self.body)


def _ip_is_blocked(ip: str) -> bool:
    """Return ``True`` if *ip* (an IP literal) is not a public, routable address.

    Uses a **permit-list** model — block anything that is not globally routable
    (``not is_global``) — which fails *closed* for every non-public range
    without enumerating them one by one: private, loopback, link-local,
    unspecified, reserved, benchmarking (198.18/15), and CGNAT / shared address
    space (100.64/10, RFC 6598) are all covered structurally, on IPv4, IPv6,
    and their IPv4-mapped forms. Multicast is blocked explicitly because the
    stdlib classifies it as ``is_global`` on the supported interpreters. This is
    the stricter posture the repo's earlier SSRF guard settled on (a plain
    deny-list of ``is_private``/``is_loopback``/… silently allowed CGNAT).

    NAT64 (RFC 6052 well-known prefix ``64:ff9b::/96``) is validated by decoding
    its embedded IPv4 (the low 32 bits) and recursing — otherwise a deployed
    NAT64 translator would route ``[64:ff9b::10.0.0.1]`` to a private/metadata
    target, and ``is_global`` does not decode it. A NAT64 address embedding a
    *public* IPv4 stays allowed. NAT64 is the **only** IPv4-embedding IPv6 form
    decoded here, deliberately: ``ipv4_mapped`` (``::ffff:a.b.c.d``) and 6to4
    (``2002::/16``) are already classified correctly by ``is_global``, and other
    embedding literals (e.g. the deprecated IPv4-compatible ``::/96``) have no
    routing guarantee — a modern stack dials the literal IPv6 address, which is
    unreachable, rather than translating to the embedded IPv4 — so they are not
    a practical SSRF vector.
    """
    addr = ipaddress.ip_address(ip)
    if addr.version == 6 and addr in _NAT64_WKP:
        embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        return _ip_is_blocked(str(embedded))
    return addr.is_multicast or not addr.is_global


async def _resolve_addresses(hostname: str, port: int) -> list[str]:
    """Resolve *hostname* to a list of IP-literal strings via the event loop.

    A seam kept module-level so tests can inject resolution without real DNS.

    Raises:
        OSError: If resolution fails (``socket.gaierror`` is an ``OSError``).
        UnicodeError: If the hostname fails IDNA encoding (e.g. an over-long
            label). The caller converts both into a redacted ``ValueError``.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def _resolve_pinned_ip(hostname: str, port: int) -> str:
    """Resolve *hostname* and return one validated public IP to pin to.

    Fails **closed**: raises :class:`ValueError` if the host is blocklisted,
    resolution errors, returns no records, or **any** resolved address is
    non-public. An IP-literal *hostname* is validated directly, without DNS.

    Args:
        hostname: The URL host (an IP literal or a domain name).
        port: Target port, used as the ``getaddrinfo`` service hint.

    Returns:
        A validated, public IP literal to connect to.
    """
    if hostname in _BLOCKED_HOSTNAMES:
        raise ValueError(_BLOCKED_ADDR_MSG)

    # IP-literal fast path: validate directly, no DNS lookup.
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if _ip_is_blocked(hostname):
            raise ValueError(_BLOCKED_ADDR_MSG)
        return hostname

    # Domain name: resolve and validate every returned address. Fail closed on
    # any resolution error so a DNS hiccup can never become an SSRF bypass.
    try:
        resolved = await _resolve_addresses(hostname, port)
    except (OSError, UnicodeError) as exc:
        # OSError = socket.gaierror (real resolution failure); UnicodeError =
        # IDNA encoding failure on a malformed/over-long hostname label. Both
        # funnel into one redacted ValueError so every resolution-failure class
        # fails closed with the same shape.
        raise ValueError(
            f"Could not resolve host {hostname!r} for the fetch URL: {exc}"
        ) from exc
    if not resolved:
        raise ValueError(f"Host {hostname!r} did not resolve to any address.")
    for ip in resolved:
        if _ip_is_blocked(ip):
            raise ValueError(
                f"Host {hostname!r} resolves to a non-public address ({ip}); "
                "refusing to fetch."
            )
    return resolved[0]


def _redact_url(url: str) -> str:
    """Return *url* with userinfo and query/fragment stripped.

    Operator-/caller-supplied URLs carry credentials in ``user:pass@`` userinfo
    and pre-signed tokens in the query string. Every message this primitive
    itself emits, log lines and the exceptions it raises alike, passes the URL
    through here first.

    Never raises. ``urlparse`` rejects some malformed URLs with a
    ``ValueError`` that embeds the raw netloc, so a redactor that let that
    escape would emit the unredacted value as its own exception message —
    failing open on exactly the input it exists to sanitise. An
    unparseable URL redacts to :data:`~markdown_vault_mcp._url.UNPARSEABLE`
    instead (#343).
    """
    parsed = try_parse_url(url)
    if parsed is None:
        return UNPARSEABLE
    # ``safe_netloc`` rather than ``parsed.port``: the attribute is
    # computed on access and raises with the text it could not cast
    # quoted, which for ``https://alice:hunter2`` is the password. An
    # authority we cannot rebuild safely redacts to the placeholder
    # rather than to a partial value (#343).
    netloc = safe_netloc(parsed)
    if netloc is None:
        return UNPARSEABLE
    return urlunparse(parsed._replace(netloc=netloc, query="", fragment=""))


def _authority(request: httpx.Request) -> tuple[str, int]:
    """Return the ``(hostname, port)`` a request is really addressed to.

    The ``Host`` header is the authority, not ``request.url.host``: on a hop
    after the first, the URL host is the IP :class:`_GuardedTransport` pinned
    last time round, while httpx keeps the real name in ``Host`` (preserving
    ours on a same-origin hop, setting the new one on a cross-origin hop).
    """
    raw_host = request.headers.get("Host")
    # A bare authority parses as a URL, which handles IPv6 brackets and the
    # optional ``:port`` without hand-rolled string splitting.
    authority = httpx.URL(f"//{raw_host}") if raw_host else request.url
    return (
        authority.host or request.url.host,
        authority.port or _SCHEME_DEFAULT_PORTS[request.url.scheme],
    )


class _GuardedTransport(httpx.AsyncBaseTransport):
    """Resolve, validate, and pin every request httpx makes through it.

    The guard lives in a transport rather than inline in :func:`fetch_url`
    because httpx builds each redirect hop as a new request and sends it back
    through the transport. That makes "every hop is validated" structural: no
    request reaches the network without passing through here.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme not in _ALLOWED_SCHEMES:
            raise ValueError(
                f"Only http and https URLs are allowed, got {request.url.scheme!r}"
            )
        hostname, port = _authority(request)
        if not hostname:
            raise ValueError("The fetch URL has no host.")

        # Record this hop's URL under its real hostname, before the pin
        # overwrites the host with an IP. Each hop overwrites the last, so after
        # the chain settles the final request carries the URL the bytes came
        # from. Userinfo in a Location is dropped here for the same reason it is
        # dropped from the caller's URL: it is never relayed onward.
        request.extensions[_FINAL_URL_KEY] = str(
            request.url.copy_with(host=hostname, userinfo=b"")
        )

        # Resolve + validate now and pin the result into the connection: the
        # address validated here is the one actually dialled. Fails closed.
        pinned_ip = await _resolve_pinned_ip(hostname, port)

        # Swap in the validated IP but keep the real hostname in TLS SNI so
        # certificate verification still works. The ``Host`` header already
        # carries the real name (httpx built it before this rewrite, and
        # maintains it across hops), so it is deliberately left untouched.
        request.url = request.url.copy_with(host=pinned_ip)
        request.extensions["sni_hostname"] = hostname
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def fetch_url(
    url: str,
    *,
    max_bytes: int = DEFAULT_FETCH_MAX_BYTES,
    timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchResult:
    """Fetch the bytes at *url* with SSRF hardening and a streaming size cap.

    Args:
        url: An ``http``/``https`` URL. The host is resolved and rejected if any
            address is non-public; the validated IP is pinned for the
            connection (DNS-rebind safe). Redirects are followed, with every hop
            re-running that same chain (:class:`_GuardedTransport`).
        max_bytes: Positive per-fetch size cap; the download aborts the moment
            it is exceeded.
        timeout_s: Overall request timeout in seconds.
        transport: An optional ``httpx`` transport (an extension point used by
            tests to inject a ``MockTransport``); ``None`` uses real networking.
            It is wrapped in the guard, never used bare.

    Returns:
        A :class:`FetchResult` with the body, content type, and byte size.

    Note:
        Because redirects are followed, the bytes need not come from the host
        in *url*. Validating *url* before the call is no longer sufficient on
        its own — a caller enforcing a host policy must re-check
        :attr:`FetchResult.final_url` afterwards.

    Raises:
        ValueError: On a non-positive *max_bytes* or *timeout_s*, a non-http(s)
            scheme, a missing host, a blocked/non-public address, a resolution
            failure, or a body that exceeds *max_bytes*. A redirect hop that
            fails any of those checks raises the same errors as a directly
            supplied URL would.
        httpx.HTTPStatusError: On a non-2xx final response (re-raised with a
            redacted message; the type and ``request``/``response`` are kept for
            programmatic inspection). This includes a terminal 3xx httpx cannot
            follow — one with no ``Location``, such as a ``304``. Note: the
            retained ``exc.request.url`` still contains the query string, so
            callers should not log it verbatim — the redacted message is the
            safe surface.
        httpx.TooManyRedirects: When the redirect chain exceeds httpx's
            ``max_redirects``.
        httpx.TransportError: Transport-level failures propagate uncaught — e.g.
            ``httpx.TimeoutException`` when *timeout_s* expires, or a connection
            error. This primitive does not wrap them.
    """
    if max_bytes <= 0:
        raise ValueError(f"max_bytes must be positive, got {max_bytes}")
    if timeout_s <= 0:
        raise ValueError(f"timeout_s must be positive, got {timeout_s}")

    # ``ValueError`` matches this function's existing contract for a bad
    # URL, but the message is ours: ``urlparse``'s own can embed the raw
    # netloc, userinfo included (#343).
    parsed = try_parse_url(url)
    if parsed is None:
        raise ValueError("The fetch URL could not be parsed.")
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Only http and https URLs are allowed, got {parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("The fetch URL has no host.")

    # Strip any ``user:pass@`` userinfo before httpx ever sees the URL, so
    # embedded credentials are never relayed to the origin. Everything else
    # about the request — including the address validation and the pin — is the
    # transport's job, so it also covers each redirect hop.
    netloc = safe_netloc(parsed)
    if netloc is None:
        # Same contract as the scheme/host rejections above, but the
        # message is ours: ``ParseResult.port`` quotes the text it could
        # not cast, which may be a password (#343).
        raise ValueError("The fetch URL has an invalid port.")
    request_url = urlunparse(parsed._replace(netloc=netloc))

    chunks: list[bytes] = []
    downloaded = 0
    async with (
        httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            trust_env=False,
            # trust_env=False: ignore ambient HTTP(S)_PROXY / .netrc. A proxy env
            # var would divert the request through an operator-uncontrolled host,
            # bypassing the resolve-validate-pin chain entirely (an SSRF bypass);
            # .netrc could attach ambient credentials to the outbound request.
            transport=_GuardedTransport(transport or httpx.AsyncHTTPTransport()),
        ) as client,
        client.stream("GET", request_url) as response,
    ):
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Re-raise with a redacted message: httpx embeds the request URL
            # (query string included) in the default message, which carries any
            # pre-signed token. Keep the type + request/response so callers can
            # still inspect status programmatically. `from None` sets
            # __suppress_context__ — otherwise httpx's original un-redacted
            # message would still surface via __context__ (implicit chaining)
            # in a rendered traceback (redact the messages this module
            # itself raises).
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code} response from {_redact_url(url)}",
                request=exc.request,
                response=exc.response,
            ) from None
        content_type = response.headers.get("content-type")
        async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
            downloaded += len(chunk)
            if downloaded > max_bytes:
                raise ValueError(
                    f"Download from {_redact_url(url)} exceeded the size cap "
                    f"of {max_bytes} bytes."
                )
            chunks.append(chunk)

    body = b"".join(chunks)
    logger.info(
        "fetch_url_completed bytes=%d url=%s content_type=%s",
        downloaded,
        _redact_url(url),
        content_type,
    )
    return FetchResult(
        body=body,
        content_type=content_type,
        final_url=response.request.extensions.get(_FINAL_URL_KEY, request_url),
    )
