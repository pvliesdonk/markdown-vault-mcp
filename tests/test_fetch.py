"""The SSRF-hardened ``fetch_url`` (#2282).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_transfer_fetch.py``, with the
redactor cases from its ``tests/test_url_redaction.py``.

The primitive is split into two testable planes:

- **Pure SSRF validation** — ``_ip_is_blocked`` and ``_resolve_pinned_ip``
  (with DNS resolution injected via ``_resolve_addresses``). No httpx needed;
  this is where the security guarantee lives.
- **HTTP mechanics** — ``fetch_url`` end-to-end, driven through an injected
  ``httpx.MockTransport`` so we can assert on the exact request dialled
  (pinned IP, Host header, no relayed credentials) and drive the streaming size
  cap. Redirects are followed, so these also assert that *every* hop is
  re-validated and re-pinned rather than only the first.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
import pytest

from markdown_vault_mcp._fetch import (
    FetchResult,
    _ip_is_blocked,
    _redact_url,
    _resolve_pinned_ip,
    fetch_url,
)
from markdown_vault_mcp._url import safe_netloc

PUBLIC_V4 = "93.184.216.34"  # example.com
PUBLIC_V4_B = "93.184.215.14"  # a second public literal, for redirect hops
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


# --------------------------------------------------------------------------- #
# _ip_is_blocked — the non-public-range predicate (v4 + v6)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # private
        "192.168.1.1",  # private
        "172.16.0.1",  # private
        "169.254.169.254",  # link-local (cloud metadata)
        "0.0.0.0",  # noqa: S104, RUF100 - Unspecified, to block; S104 is gate-only.
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
        "::1",  # v6 loopback
        "fe80::1",  # v6 link-local
        "fc00::1",  # v6 unique-local (private)
        "ff02::1",  # v6 multicast
        # IPv4-mapped IPv6 — must be blocked via the embedded IPv4. Locks the
        # guarantee verified across Python 3.10-3.13 (is_private/is_link_local
        # delegate to the mapped IPv4); guards against a future refactor that
        # would open an SSRF bypass to cloud metadata.
        "::ffff:169.254.169.254",  # mapped link-local (cloud metadata)
        "::ffff:127.0.0.1",  # mapped loopback
        "::ffff:10.0.0.1",  # mapped private
        # Shared address space that a plain is_private/is_loopback deny-list
        # silently allows — the permit-list (not is_global) closes these.
        "100.64.0.1",  # CGNAT / shared address space (RFC 6598)
        "::ffff:100.64.0.1",  # mapped CGNAT
        "198.18.0.1",  # benchmarking (RFC 2544)
        # NAT64 well-known prefix (RFC 6052) embedding a non-public IPv4 —
        # stdlib does not decode this for is_global, so it must be blocked via
        # explicit embedded-IPv4 validation (else a NAT64 translator reaches
        # the private/metadata target).
        "64:ff9b::169.254.169.254",  # NAT64 → cloud metadata
        "64:ff9b::10.0.0.1",  # NAT64 → private
        "64:ff9b::127.0.0.1",  # NAT64 → loopback
    ],
)
def test_ip_is_blocked_true_for_non_public(ip: str) -> None:
    assert _ip_is_blocked(ip) is True


def test_ip_is_blocked_allows_nat64_public_embedded() -> None:
    # A NAT64 address embedding a *public* IPv4 must stay allowed (a NAT64
    # translator legitimately routes it to the public host) — the recursion
    # validates the embedded IPv4, it does not blanket-block the prefix.
    assert _ip_is_blocked(f"64:ff9b::{PUBLIC_V4}") is False


@pytest.mark.parametrize("ip", [PUBLIC_V4, "8.8.8.8", PUBLIC_V6])
def test_ip_is_blocked_false_for_public(ip: str) -> None:
    assert _ip_is_blocked(ip) is False


# --------------------------------------------------------------------------- #
# _resolve_pinned_ip — blocked hosts, IP literals, DNS validation, fail-closed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "host", ["localhost", "localhost.localdomain", "metadata.google.internal"]
)
async def test_resolve_pinned_ip_rejects_blocked_hostnames(host: str) -> None:
    with pytest.raises(ValueError):
        await _resolve_pinned_ip(host, 80)


async def test_resolve_pinned_ip_public_literal_skips_dns(monkeypatch) -> None:
    # An IP literal must be validated directly with no DNS lookup.
    def _boom(*_a, **_k):
        raise AssertionError("DNS must not be consulted for an IP literal")

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _boom)
    assert await _resolve_pinned_ip(PUBLIC_V4, 443) == PUBLIC_V4


async def test_resolve_pinned_ip_private_literal_rejected() -> None:
    with pytest.raises(ValueError):
        await _resolve_pinned_ip("127.0.0.1", 80)


async def test_resolve_pinned_ip_domain_to_public_returns_ip(monkeypatch) -> None:
    async def _fake(_host, _port):
        return [PUBLIC_V4]

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    assert await _resolve_pinned_ip("example.com", 443) == PUBLIC_V4


async def test_resolve_pinned_ip_domain_to_private_rejected(monkeypatch) -> None:
    # Fail closed: even one non-public resolved address rejects the whole host.
    async def _fake(_host, _port):
        return [PUBLIC_V4, "10.0.0.5"]

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    with pytest.raises(ValueError):
        await _resolve_pinned_ip("rebind.example", 443)


async def test_resolve_pinned_ip_dns_error_fails_closed(monkeypatch) -> None:
    async def _fake(_host, _port):
        raise OSError("temporary DNS failure")

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    with pytest.raises(ValueError):
        await _resolve_pinned_ip("example.com", 443)


async def test_resolve_pinned_ip_empty_resolution_rejected(monkeypatch) -> None:
    async def _fake(_host, _port):
        return []

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    with pytest.raises(ValueError):
        await _resolve_pinned_ip("example.com", 443)


# --------------------------------------------------------------------------- #
# _redact_url — strip userinfo + query from any emitted URL (#122: logs AND exc)
# --------------------------------------------------------------------------- #


def test_redact_url_strips_userinfo_and_query() -> None:
    # Exact match, not substring checks: userinfo (user:s3cr3t), query
    # (token=abc), and fragment are stripped; scheme, host, port, and path are
    # kept. (Exact equality also avoids the `host in url` substring pattern that
    # CodeQL flags as incomplete-URL-sanitization — and is a stronger assertion.)
    redacted = _redact_url("https://user:s3cr3t@example.com:8443/path?token=abc#f")
    assert redacted == "https://example.com:8443/path"


# --------------------------------------------------------------------------- #
# fetch_url — scheme / host guards
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url", ["ftp://example.com/x", "file:///etc/passwd", "gopher://h"]
)
async def test_fetch_url_rejects_non_http_scheme(url: str) -> None:
    with pytest.raises(ValueError):
        await fetch_url(url)


async def test_fetch_url_rejects_missing_host() -> None:
    with pytest.raises(ValueError):
        await fetch_url("http:///no-host")


# --------------------------------------------------------------------------- #
# fetch_url — HTTP mechanics via injected MockTransport
# --------------------------------------------------------------------------- #


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _exception_chain_text(exc: BaseException) -> str:
    """Join the ``str()`` of an exception and its ``__cause__``/``__context__``
    chain — the text that surfaces when the exception is logged/rendered."""
    parts: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(str(cur))
        cur = cur.__cause__ or (None if cur.__suppress_context__ else cur.__context__)
    return " ".join(parts)


async def test_fetch_url_happy_path_returns_result() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, content=b"hello", headers={"content-type": "text/plain"}
        )

    result = await fetch_url(f"http://{PUBLIC_V4}/x", transport=_transport(handler))

    assert isinstance(result, FetchResult)
    assert result.body == b"hello"
    assert result.size == 5
    assert result.content_type == "text/plain"
    assert len(seen) == 1


async def test_fetch_url_aborts_over_size_cap() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100)

    # Credential-bearing URL so the assertion also guards message redaction on
    # this emit-path (a _redact_url(url) → url regression would leak here).
    url = f"http://user:pass@{PUBLIC_V4}/big?token=s3cr3t"
    with pytest.raises(ValueError) as excinfo:
        await fetch_url(url, max_bytes=10, transport=_transport(handler))
    msg = str(excinfo.value)
    assert "s3cr3t" not in msg
    assert "pass" not in msg


async def test_fetch_url_final_url_is_the_request_url_when_nothing_redirects() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    result = await fetch_url(f"http://{PUBLIC_V4}/x?q=1", transport=_transport(handler))
    assert result.final_url == f"http://{PUBLIC_V4}/x?q=1"


async def test_fetch_url_final_url_drops_userinfo() -> None:
    # Credentials are never relayed, and never handed back in final_url either.
    # The query string *is* kept: a redirect target's query is often the
    # load-bearing part (pre-signed links).
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    result = await fetch_url(
        f"http://user:pass@{PUBLIC_V4}/x?token=s3cr3t", transport=_transport(handler)
    )
    assert result.final_url == f"http://{PUBLIC_V4}/x?token=s3cr3t"


async def test_fetch_url_refuses_redirect_to_private_target() -> None:
    # The guard runs per hop, so a Location pointing at an internal address is
    # refused exactly as a directly-supplied one would be — and the target is
    # never dialled.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://10.0.0.1/internal"})

    url = f"http://user:pass@{PUBLIC_V4}/r?token=s3cr3t"
    with pytest.raises(ValueError) as excinfo:
        await fetch_url(url, transport=_transport(handler))
    assert len(seen) == 1
    assert "10.0.0.1" not in str(seen[0].url)
    # ...and the refusal message must be redacted on this emit-path.
    msg = str(excinfo.value)
    assert "s3cr3t" not in msg
    assert "pass" not in msg


async def test_fetch_url_refuses_redirect_to_disallowed_scheme() -> None:
    # The scheme allowlist is re-checked per hop: a redirect cannot escape
    # http(s) into file:// or any other scheme.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    with pytest.raises(ValueError, match="http and https"):
        await fetch_url(f"http://{PUBLIC_V4}/x", transport=_transport(handler))


async def test_fetch_url_follows_redirect_to_public_target(monkeypatch) -> None:
    # The ordinary case the refuse-all policy made unreachable: a redirect to a
    # different public host. Both hops resolve, validate, and pin — each dial
    # goes to the pinned IP while carrying the real name in Host and SNI.
    async def _fake(host, _port):
        return {"start.example": [PUBLIC_V4], "end.example": [PUBLIC_V4_B]}[host]

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                302, headers={"location": "https://end.example/final"}
            )
        return httpx.Response(200, content=b"arrived")

    result = await fetch_url("https://start.example/go", transport=_transport(handler))
    assert result.body == b"arrived"
    # final_url reports where the bytes came from, under the real hostname —
    # not the pinned IP the request was dialled at.
    assert result.final_url == "https://end.example/final"
    assert [str(r.url.host) for r in seen] == [PUBLIC_V4, PUBLIC_V4_B]
    assert [r.headers["host"] for r in seen] == ["start.example", "end.example"]
    assert [r.extensions["sni_hostname"] for r in seen] == [
        "start.example",
        "end.example",
    ]


async def test_fetch_url_relative_redirect_keeps_the_real_hostname(
    monkeypatch,
) -> None:
    # Regression guard for the Host-header-is-the-authority rule: httpx joins a
    # relative Location against the *pinned* URL, so hop 2's `url.host` is an IP
    # literal. Reading the name from `url.host` would send the IP as SNI and
    # break TLS on any relative redirect.
    async def _fake(_host, _port):
        return [PUBLIC_V4]

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "/next"})
        return httpx.Response(200, content=b"ok")

    result = await fetch_url("https://vhost.example/go", transport=_transport(handler))
    assert result.final_url == "https://vhost.example/next"
    assert str(seen[1].url) == f"https://{PUBLIC_V4}/next"
    assert seen[1].headers["host"] == "vhost.example"
    assert seen[1].extensions["sni_hostname"] == "vhost.example"


@pytest.mark.parametrize("code", [300, 302, 304, 307, 308])
async def test_fetch_url_raises_on_terminal_3xx(code: int) -> None:
    # A 3xx httpx cannot follow — no Location header (e.g. a 304, or a bare 302)
    # — is not a redirect at all. It surfaces as a non-2xx status error, with
    # no second dial.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(code)  # no Location header

    with pytest.raises(httpx.HTTPStatusError, match=f"HTTP {code}"):
        await fetch_url(f"http://{PUBLIC_V4}/x", transport=_transport(handler))
    assert len(seen) == 1


async def test_fetch_url_keeps_explicit_port_zero() -> None:
    # An explicit port 0 must be dialled as 0, not silently coerced to the
    # scheme default (80). Uses `is not None`, not truthiness, in the port logic.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    await fetch_url(f"http://{PUBLIC_V4}:0/x", transport=_transport(handler))
    assert seen[0].url.port == 0
    assert seen[0].headers["host"] == f"{PUBLIC_V4}:0"


async def test_fetch_url_strips_embedded_credentials_from_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    await fetch_url(f"http://user:pass@{PUBLIC_V4}/x", transport=_transport(handler))
    req = seen[0]
    assert req.url.username == ""
    assert req.url.password == ""
    assert "pass" not in req.headers.get("authorization", "")


async def test_fetch_url_pins_ip_and_keeps_host_header(monkeypatch) -> None:
    async def _fake(_host, _port):
        return [PUBLIC_V4]

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    await fetch_url("http://example.com/x", transport=_transport(handler))
    req = seen[0]
    assert req.url.host == PUBLIC_V4  # dialled the pinned IP
    assert req.headers["host"] == "example.com"  # vhost preserved
    # SNI carries the real hostname (drives TLS cert verification), not the
    # pinned IP — a regression here would silently break HTTPS validation.
    assert req.extensions["sni_hostname"] == "example.com"


async def test_fetch_url_raises_on_http_error_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_url(f"http://{PUBLIC_V4}/x", transport=_transport(handler))


async def test_fetch_url_own_log_is_redacted(caplog) -> None:
    # fetch_url controls only *its own* emit-paths. httpx's internal request
    # logger also emits the URL (with query) — that is the operator's logging
    # config concern (pvl-core ships SecretMaskFilter), not something a
    # primitive should globally reconfigure. So we scope this to the fetch
    # module's own logger records.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    with caplog.at_level(logging.INFO, logger="markdown_vault_mcp._fetch"):
        await fetch_url(
            f"http://{PUBLIC_V4}/x?token=s3cr3t", transport=_transport(handler)
        )
    own = [
        r.getMessage() for r in caplog.records if r.name == "markdown_vault_mcp._fetch"
    ]
    assert own, "expected fetch_url to emit its own log line"
    assert "s3cr3t" not in " ".join(own)


async def test_fetch_url_http_error_message_is_redacted() -> None:
    # The HTTPStatusError fetch_url raises is its own declared emit-path; its
    # message must not carry the query token (httpx's default message would).
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"nope")

    url = f"http://{PUBLIC_V4}/x?token=s3cr3t"
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await fetch_url(url, transport=_transport(handler))
    assert "s3cr3t" not in str(excinfo.value)
    # The token must also be absent from the whole exception *message chain* —
    # a `raise ... from exc` would re-attach httpx's original un-redacted
    # message via __cause__, which surfaces when the exception is rendered. We
    # check the chained messages (the real logged surface), not the traceback
    # frames (those legitimately show the caller's own source).
    assert "s3cr3t" not in _exception_chain_text(excinfo.value)
    assert excinfo.value.response.status_code == 404  # type preserved + usable


async def test_fetch_url_pins_ipv6_and_brackets_literal(monkeypatch) -> None:
    async def _fake(_host, _port):
        return [PUBLIC_V6]

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    await fetch_url("http://example.com/x", transport=_transport(handler))
    req = seen[0]
    assert req.url.host == PUBLIC_V6  # dialled the pinned v6 literal
    assert req.headers["host"] == "example.com"  # vhost preserved


async def test_fetch_url_preserves_non_default_port(monkeypatch) -> None:
    async def _fake(_host, _port):
        return [PUBLIC_V4]

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    await fetch_url("http://example.com:8080/x", transport=_transport(handler))
    req = seen[0]
    assert req.url.host == PUBLIC_V4
    assert req.url.port == 8080  # non-default port dialled on the pinned IP
    assert req.headers["host"] == "example.com:8080"  # port carried in Host


async def test_fetch_url_size_cap_boundary() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 10)

    # Exactly at the cap succeeds (predicate is strict `>`), one over raises.
    ok = await fetch_url(
        f"http://{PUBLIC_V4}/x", max_bytes=10, transport=_transport(handler)
    )
    assert ok.size == 10

    def over(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 11)

    with pytest.raises(ValueError):
        await fetch_url(
            f"http://{PUBLIC_V4}/x", max_bytes=10, transport=_transport(over)
        )


@pytest.mark.parametrize("bad", [0, -1])
async def test_fetch_url_rejects_non_positive_max_bytes(bad: int) -> None:
    with pytest.raises(ValueError):
        await fetch_url(f"http://{PUBLIC_V4}/x", max_bytes=bad)


@pytest.mark.parametrize("bad", [0, -1.0])
async def test_fetch_url_rejects_non_positive_timeout(bad: float) -> None:
    with pytest.raises(ValueError):
        await fetch_url(f"http://{PUBLIC_V4}/x", timeout_s=bad)


async def test_fetch_url_omits_explicit_default_port_from_host_header(
    monkeypatch,
) -> None:
    async def _fake(_host, _port):
        return [PUBLIC_V4]

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    # An explicit default port (80 for http) must be dialled but omitted from
    # the Host header (RFC 7230 §5.4), so strict origin-matching vhosts accept.
    await fetch_url("http://example.com:80/x", transport=_transport(handler))
    req = seen[0]
    assert req.url.port in (None, 80)
    assert req.headers["host"] == "example.com"


async def test_fetch_url_propagates_transport_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    # Transport-level failures are documented as propagating uncaught.
    with pytest.raises(httpx.TransportError):
        await fetch_url(f"http://{PUBLIC_V4}/x", transport=_transport(handler))


async def test_fetch_url_client_disables_ambient_env(monkeypatch) -> None:
    # trust_env=False is an SSRF control: an ambient HTTP(S)_PROXY / .netrc must
    # not divert the request past the pinned IP. Its effect is unobservable
    # through the injected MockTransport (which bypasses env-proxy mounting), so
    # the only regression guard is asserting the AsyncClient constructor kwargs.
    captured: dict[str, object] = {}
    real_client = httpx.AsyncClient

    def _spy(*args, **kwargs):
        captured.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _spy)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    await fetch_url(f"http://{PUBLIC_V4}/x", transport=_transport(handler))
    assert captured.get("trust_env") is False
    assert captured.get("follow_redirects") is True


async def test_fetch_url_wraps_idna_failure_as_value_error() -> None:
    # An over-long (>63 char) hostname label fails IDNA encoding in getaddrinfo,
    # raising UnicodeError (a ValueError subclass, NOT OSError). It must still
    # funnel into the redacted "Could not resolve host" ValueError and fail
    # closed — no client is constructed. Uses real resolution (no transport).
    bad = "a" * 64 + ".example.com"
    with pytest.raises(ValueError, match="Could not resolve host"):
        await fetch_url(f"http://{bad}/x")


async def test_fetch_url_content_type_none_when_header_absent() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")  # no content-type header

    result = await fetch_url(f"http://{PUBLIC_V4}/x", transport=_transport(handler))
    assert result.content_type is None


async def test_fetch_url_size_cap_across_multiple_chunks() -> None:
    # A body larger than the 64 KiB read chunk exercises the *cumulative*
    # accumulation (downloaded += len(chunk)); a body of ~200 KiB against a
    # 100 KiB cap must abort, catching a `+=` → `=` regression that a single
    # sub-chunk body would not.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 200_000)

    with pytest.raises(ValueError):
        await fetch_url(
            f"http://{PUBLIC_V4}/big",
            max_bytes=100_000,
            transport=_transport(handler),
        )


async def test_fetch_url_reassembles_multi_chunk_body_under_cap() -> None:
    # A >64 KiB body under the cap must be reassembled completely and in order
    # — guards a truncation/ordering regression in b"".join(chunks) (the worst
    # failure mode: wrong bytes, no error).
    payload = bytes(range(256)) * 1000  # 256 KiB, distinctive/order-sensitive

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    result = await fetch_url(
        f"http://{PUBLIC_V4}/x", max_bytes=1_000_000, transport=_transport(handler)
    )
    assert result.body == payload
    assert result.size == len(payload)


async def test_fetch_url_brackets_ipv6_literal_host_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    await fetch_url(f"http://[{PUBLIC_V6}]/x", transport=_transport(handler))
    # A v6-literal URL must produce a bracketed Host header, not the raw
    # colon-bearing address (which is malformed per RFC 7230).
    assert seen[0].headers["host"] == f"[{PUBLIC_V6}]"


async def test_fetch_url_ipv6_with_non_default_port(monkeypatch) -> None:
    async def _fake(_host, _port):
        return [PUBLIC_V6]

    monkeypatch.setattr("markdown_vault_mcp._fetch._resolve_addresses", _fake)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    await fetch_url("http://example.com:8080/x", transport=_transport(handler))
    req = seen[0]
    assert req.url.host == PUBLIC_V6  # bracketed v6 literal dialled
    assert req.url.port == 8080
    assert req.headers["host"] == "example.com:8080"


# NFKC-normalising host, so ``_checknetloc`` raises with the netloc in it.
_LEAKY_HTTPS = "https://alice:hunter2@h\u2100st/x"
_SECRETS = ("hunter2", "alice")


class TestRedactorNeverFailsOpen:
    """A sanitiser that raises on malformed input defeats itself.

    ``_redact_url`` is the single choke point every message in the fetch
    passes through. Raising there would not merely skip the redaction: it
    would emit the unredacted netloc as the exception message.
    """

    @pytest.mark.parametrize(
        "url",
        [
            _LEAKY_HTTPS,
            # The port slot is the second way in: ``ParseResult.port`` raises
            # quoting the text it cannot cast, and a truncated authority puts
            # the password there.
            "https://alice:hunter2",
            "https://alice:hunter2@host:notaport/p?token=SECRETTOKEN",
        ],
    )
    def test_redact_url_returns_a_placeholder_instead_of_raising(
        self, url: str
    ) -> None:
        redacted = _redact_url(url)
        for secret in (*_SECRETS, "SECRETTOKEN"):
            assert secret not in redacted

    def test_valid_urls_still_redact_rather_than_becoming_placeholders(self) -> None:
        assert (
            _redact_url("https://alice:hunter2@h.example:8443/x?token=T")
            == "https://h.example:8443/x"
        )
        assert _redact_url("https://[::1]:8443/x") == "https://[::1]:8443/x"


class TestSafeNetloc:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://alice:hunter2@h.example:8443/x", "h.example:8443"),
            ("https://h.example/x", "h.example"),
            ("https://[::1]:8443/x", "[::1]:8443"),
            # The port will not cast, and the text it would quote is a password.
            ("https://alice:hunter2", None),
            # No host at all.
            ("file:///etc/passwd", None),
        ],
    )
    def test_authority_without_userinfo(self, url: str, expected: str | None) -> None:
        assert safe_netloc(urlparse(url)) == expected
