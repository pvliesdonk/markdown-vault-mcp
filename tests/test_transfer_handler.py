"""The ``/transfer/{token}`` handler over ASGI (#2319).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_transfer_routes.py``
(this repository's ``test_transfer_routes.py`` covers the server wiring).

The handler drives the capability-link state machine over HTTP: GET downloads
via ``sink.read``, POST/PUT upload via ``sink.write``, grace-settles the token
on success (``store.complete``) and releases it on any failure so a transient
failure does not spend the link. Tests run the handler as a real Starlette route
hit over ASGI (httpx), so routing, path params, streaming, and the response
shape are all exercised.

Failure modes pinned here:

- **Download**: serves the sink's bytes + media type + RFC 6266 filename, then
  grace-settles — a second GET within the grace window still succeeds
  (re-serves); only after the window lapses is it 404.
- **Upload**: reads a size-capped body, commits via ``sink.write``, returns the
  sink's payload as JSON, then grace-settles (a retry within grace re-commits).
- **Release-on-failure**: a sink read/write error releases the token (the link
  survives and is usable again) and surfaces as 500.
- **Over-cap upload**: rejected 413, and the link is released (survives).
- **Bad token**: unknown / expired / consumed / wrong-kind → 404; a concurrent
  in-flight claim → 409.
- **Content-Disposition**: non-ASCII filename gets an RFC 5987 ``filename*``;
  a CR/LF-injecting filename is sanitised (no header injection).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient
from key_value.aio.stores.memory import MemoryStore
from starlette.applications import Starlette
from starlette.routing import Route

from markdown_vault_mcp._transfer_contract import (
    TransferReadResult,
    TransferResourceGoneError,
    TransferSinkError,
    TransferUnavailableError,
)
from markdown_vault_mcp._transfer_routes import make_transfer_handler
from markdown_vault_mcp._transfer_store import TransferStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping


class _FakeSink:
    """An in-memory sink recording writes and serving canned reads."""

    def __init__(self) -> None:
        self.reads: dict[str, tuple[bytes, str, str]] = {}
        self.writes: dict[str, bytes] = {}
        self.fail_read = False
        self.fail_write = False
        # When set, read/write raise this instead — used to exercise a sink
        # signalling a specific status (TransferSinkError) as well as an
        # unexpected error. Cleared (None) to simulate the sink recovering.
        self.read_raises: BaseException | None = None
        self.write_raises: BaseException | None = None

    async def read(self, handle: str) -> TransferReadResult:
        if self.read_raises is not None:
            raise self.read_raises
        if self.fail_read:
            raise RuntimeError("sink read boom")
        return TransferReadResult(*self.reads[handle])

    async def write(self, handle: str, body: bytes) -> Mapping[str, Any]:
        if self.write_raises is not None:
            raise self.write_raises
        if self.fail_write:
            raise RuntimeError("sink write boom")
        self.writes[handle] = body
        return {"handle": handle, "bytes": len(body)}


def _make_store(
    *, lease_seconds: float = 300.0, grace_seconds: float = 300.0
) -> TransferStore:
    return TransferStore(
        MemoryStore(), lease_seconds=lease_seconds, grace_seconds=grace_seconds
    )


@asynccontextmanager
async def _client(
    store: TransferStore, sink: _FakeSink, *, max_upload_bytes: int = 1024
) -> AsyncIterator[AsyncClient]:
    handler = make_transfer_handler(store, sink, max_upload_bytes=max_upload_bytes)
    app = Starlette(
        routes=[
            Route(
                "/transfer/{token}",
                handler,
                # DELETE is registered only so a request with an unserved method
                # reaches the handler's own 405 branch here. In production the
                # route-registration layer (§11 issue #5) must likewise route all
                # methods to the handler for its 405 + Connection: close to apply
                # — otherwise Starlette's built-in 405 (no Connection: close)
                # answers unregistered methods. See make_transfer_handler's Note.
                methods=["GET", "POST", "PUT", "DELETE"],
            )
        ]
    )
    # raise_app_exceptions=False so a re-raised handler error is observed as the
    # server's generic 500 response (as a real client would see it), not
    # propagated into the test.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


async def _mint_download(
    store: TransferStore, *, handle: str = "h1", ttl: float = 300.0
) -> str:
    return await store.mint(
        kind="download", sink_handle=handle, caps={}, ttl_seconds=ttl
    )


async def _mint_upload(
    store: TransferStore, *, handle: str = "u1", ttl: float = 300.0
) -> str:
    return await store.mint(kind="upload", sink_handle=handle, caps={}, ttl_seconds=ttl)


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #


async def test_download_serves_bytes_then_grace_settles() -> None:
    store = _make_store(grace_seconds=300.0)
    sink = _FakeSink()
    sink.reads["h1"] = (b"hello world", "text/plain", "greeting.txt")
    token = await _mint_download(store)
    async with _client(store, sink) as client:
        resp = await client.get(f"/transfer/{token}")
        assert resp.status_code == 200
        assert resp.content == b"hello world"
        assert resp.headers["content-type"].startswith("text/plain")
        assert "greeting.txt" in resp.headers["content-disposition"]
        # Grace-settle, not burn: a served-but-stalled download can retry within
        # the grace window (re-serves), rather than being stranded by a 404.
        again = await client.get(f"/transfer/{token}")
        assert again.status_code == 200
        assert again.content == b"hello world"


async def test_download_link_expires_after_grace() -> None:
    # Once the grace window lapses, the settled link is gone (404) — grace is a
    # short retry window, not an unbounded reuse.
    store = _make_store(grace_seconds=0.05)
    sink = _FakeSink()
    sink.reads["h1"] = (b"data", "text/plain", "f.txt")
    token = await _mint_download(store)
    async with _client(store, sink) as client:
        assert (await client.get(f"/transfer/{token}")).status_code == 200
        await asyncio.sleep(0.1)  # grace window lapses
        assert (await client.get(f"/transfer/{token}")).status_code == 404


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #


async def test_upload_commits_via_sink_then_grace_settles() -> None:
    store = _make_store(grace_seconds=300.0)
    sink = _FakeSink()
    token = await _mint_upload(store)
    async with _client(store, sink) as client:
        resp = await client.post(f"/transfer/{token}", content=b"payload-bytes")
        assert resp.status_code == 200
        assert resp.json() == {"handle": "u1", "bytes": len(b"payload-bytes")}
        assert sink.writes["u1"] == b"payload-bytes"
        # Grace-settle: a client that missed the ack can retry within grace
        # (re-commits, last-writer-wins), rather than getting a 404.
        again = await client.post(f"/transfer/{token}", content=b"retry")
        assert again.status_code == 200
        assert sink.writes["u1"] == b"retry"


async def test_upload_accepts_put_as_well_as_post() -> None:
    store = _make_store()
    sink = _FakeSink()
    token = await _mint_upload(store)
    async with _client(store, sink) as client:
        resp = await client.put(f"/transfer/{token}", content=b"via-put")
        assert resp.status_code == 200
        assert sink.writes["u1"] == b"via-put"


# --------------------------------------------------------------------------- #
# release-on-failure
# --------------------------------------------------------------------------- #


async def test_sink_read_failure_releases_the_link() -> None:
    store = _make_store()
    sink = _FakeSink()
    sink.reads["h1"] = (b"data", "application/octet-stream", "f.bin")
    token = await _mint_download(store)
    async with _client(store, sink) as client:
        sink.fail_read = True
        resp = await client.get(f"/transfer/{token}")
        assert resp.status_code == 500
        # The link survived — a retry succeeds.
        sink.fail_read = False
        retry = await client.get(f"/transfer/{token}")
        assert retry.status_code == 200
        assert retry.content == b"data"


async def test_sink_write_failure_releases_the_link() -> None:
    store = _make_store()
    sink = _FakeSink()
    token = await _mint_upload(store)
    async with _client(store, sink) as client:
        sink.fail_write = True
        resp = await client.post(f"/transfer/{token}", content=b"body")
        assert resp.status_code == 500
        sink.fail_write = False
        retry = await client.post(f"/transfer/{token}", content=b"body")
        assert retry.status_code == 200
        assert sink.writes["u1"] == b"body"


async def test_release_failure_does_not_mask_the_original_error(
    monkeypatch, caplog
) -> None:
    # If store.release itself fails on the failure path, _release_quietly logs
    # and swallows it so the sink's original error still surfaces (500) — the
    # release failure must not mask it. (The token then lease-reclaims.)
    store = _make_store()
    sink = _FakeSink()
    sink.reads["h1"] = (b"x", "text/plain", "f")
    token = await _mint_download(store)

    async def _boom(*_a, **_k):
        raise RuntimeError("kv release unavailable")

    monkeypatch.setattr(store, "release", _boom)
    async with _client(store, sink) as client:
        sink.fail_read = True
        with caplog.at_level("WARNING"):
            resp = await client.get(f"/transfer/{token}")
        assert resp.status_code == 500  # the sink error propagated, not masked
        assert any("transfer_release_failed" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# sink-signalled status (fastmcp-pvl-core#233)
# --------------------------------------------------------------------------- #

# A representative spread: the two named subclasses, and the base used directly
# for a status with no named class.
_SIGNALS = [
    (TransferResourceGoneError(), 410),
    (TransferUnavailableError(), 503),
    (TransferSinkError(403), 403),
    (TransferSinkError(404), 404),
    (TransferSinkError(502), 502),
]


@pytest.mark.parametrize(("exc", "status"), _SIGNALS)
async def test_download_sink_signal_maps_status_and_releases(
    exc: TransferSinkError, status: int
) -> None:
    store = _make_store()
    sink = _FakeSink()
    sink.reads["h1"] = (b"DATA", "text/plain", "f.txt")
    token = await _mint_download(store, handle="h1")
    async with _client(store, sink) as client:
        sink.read_raises = exc
        resp = await client.get(f"/transfer/{token}")
        assert resp.status_code == status
        # Released, not spent: a retry once the sink recovers serves 200.
        sink.read_raises = None
        retry = await client.get(f"/transfer/{token}")
        assert retry.status_code == 200
        assert retry.content == b"DATA"


@pytest.mark.parametrize(("exc", "status"), _SIGNALS)
async def test_upload_sink_signal_maps_status_and_releases(
    exc: TransferSinkError, status: int
) -> None:
    store = _make_store()
    sink = _FakeSink()
    token = await _mint_upload(store, handle="h1")
    async with _client(store, sink) as client:
        sink.write_raises = exc
        resp = await client.put(f"/transfer/{token}", content=b"BODY")
        assert resp.status_code == status
        # The upload body was fully read before write, so nothing is undrained →
        # the connection is not force-closed (unlike the 413 over-cap path).
        assert resp.headers.get("connection") != "close"
        # Released: a retry once the sink recovers stores the body (200).
        sink.write_raises = None
        retry = await client.put(f"/transfer/{token}", content=b"BODY")
        assert retry.status_code == 200
        assert sink.writes["h1"] == b"BODY"


# --------------------------------------------------------------------------- #
# size cap
# --------------------------------------------------------------------------- #


async def test_over_cap_upload_is_rejected_and_releases_the_link() -> None:
    store = _make_store()
    sink = _FakeSink()
    token = await _mint_upload(store)
    async with _client(store, sink, max_upload_bytes=4) as client:
        resp = await client.post(f"/transfer/{token}", content=b"toolong")
        assert resp.status_code == 413
        # The oversize body was abandoned unread → the connection must close so
        # the undrained bytes don't desync a keep-alive socket.
        assert resp.headers.get("connection") == "close"
        assert "u1" not in sink.writes  # never committed
        # Released — a within-cap retry on the same link succeeds.
        retry = await client.post(f"/transfer/{token}", content=b"ok")
        assert retry.status_code == 200
        assert sink.writes["u1"] == b"ok"


async def test_over_cap_across_many_under_cap_chunks_is_rejected() -> None:
    # The cap is enforced by accumulating across streamed chunks. A body split
    # into many individually-under-cap chunks whose SUM exceeds the cap must
    # still be rejected — otherwise a chunked upload bypasses the memory bound.
    store = _make_store()
    sink = _FakeSink()
    token = await _mint_upload(store)

    async def _chunks() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"xx"  # 10 x 2 = 20 bytes total, each chunk ≤ the 4-byte cap

    async with _client(store, sink, max_upload_bytes=4) as client:
        resp = await client.post(f"/transfer/{token}", content=_chunks())
        assert resp.status_code == 413
        assert resp.headers.get("connection") == "close"
        assert "u1" not in sink.writes


async def test_upload_exactly_at_cap_succeeds() -> None:
    store = _make_store()
    sink = _FakeSink()
    token = await _mint_upload(store)
    async with _client(store, sink, max_upload_bytes=4) as client:
        resp = await client.post(f"/transfer/{token}", content=b"abcd")
        assert resp.status_code == 200
        assert sink.writes["u1"] == b"abcd"


# --------------------------------------------------------------------------- #
# bad token
# --------------------------------------------------------------------------- #


async def test_unknown_token_is_404() -> None:
    store = _make_store()
    sink = _FakeSink()
    async with _client(store, sink) as client:
        resp = await client.get("/transfer/no-such-token")
        assert resp.status_code == 404


async def test_expired_token_is_404() -> None:
    store = _make_store()
    sink = _FakeSink()
    sink.reads["h1"] = (b"x", "text/plain", "f")
    token = await _mint_download(store, ttl=0.05)
    await asyncio.sleep(0.1)
    async with _client(store, sink) as client:
        resp = await client.get(f"/transfer/{token}")
        assert resp.status_code == 404


async def test_wrong_kind_is_404() -> None:
    store = _make_store()
    sink = _FakeSink()
    sink.reads["h1"] = (b"x", "text/plain", "f")
    download_token = await _mint_download(store)
    upload_token = await _mint_upload(store)
    async with _client(store, sink) as client:
        # POST to a download token, GET an upload token — both wrong-kind → 404.
        post = await client.post(f"/transfer/{download_token}", content=b"x")
        assert post.status_code == 404
        # An upload rejected at claim time leaves its body unread → close.
        assert post.headers.get("connection") == "close"
        assert (await client.get(f"/transfer/{upload_token}")).status_code == 404


async def test_in_flight_token_is_409() -> None:
    store = _make_store(lease_seconds=300.0)
    sink = _FakeSink()
    sink.reads["h1"] = (b"x", "text/plain", "f")
    token = await _mint_download(store)
    # Hold a live reservation out-of-band; the handler's claim must conflict.
    await store.claim(token, "download")
    async with _client(store, sink) as client:
        resp = await client.get(f"/transfer/{token}")
        assert resp.status_code == 409


async def test_unsupported_method_with_body_is_405_and_closes_connection() -> None:
    store = _make_store()
    sink = _FakeSink()
    token = await _mint_download(store)
    async with _client(store, sink) as client:
        # A DELETE carrying a body the handler never reads → 405 + close (so the
        # undrained body can't desync a keep-alive socket) + Allow (RFC 7231).
        # This exercises the handler's own 405 branch; whether every unsupported
        # method reaches it in production is the route layer's job (#218).
        resp = await client.request("DELETE", f"/transfer/{token}", content=b"body")
        assert resp.status_code == 405
        assert resp.headers.get("connection") == "close"
        assert resp.headers.get("allow") == "GET, POST, PUT"


# --------------------------------------------------------------------------- #
# Content-Disposition (RFC 6266)
# --------------------------------------------------------------------------- #


async def test_non_ascii_filename_uses_rfc5987_encoding() -> None:
    store = _make_store()
    sink = _FakeSink()
    sink.reads["h1"] = (b"x", "application/pdf", "résumé.pdf")
    token = await _mint_download(store)
    async with _client(store, sink) as client:
        resp = await client.get(f"/transfer/{token}")
        cd = resp.headers["content-disposition"]
        assert cd.startswith("attachment;")
        # RFC 5987 percent-encoded UTF-8 for the non-ASCII name.
        assert "filename*=UTF-8''r%C3%A9sum%C3%A9.pdf" in cd


async def test_filename_with_crlf_cannot_inject_headers() -> None:
    store = _make_store()
    sink = _FakeSink()
    sink.reads["h1"] = (b"x", "text/plain", "a\r\nX-Injected: evil\r\n.txt")
    token = await _mint_download(store)
    async with _client(store, sink) as client:
        resp = await client.get(f"/transfer/{token}")
        assert resp.status_code == 200
        assert "x-injected" not in {k.lower() for k in resp.headers}
        cd = resp.headers["content-disposition"]
        assert "\r" not in cd and "\n" not in cd


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_upload_cap_rejected(bad: int) -> None:
    store = _make_store()
    sink = _FakeSink()
    with pytest.raises(ValueError):
        make_transfer_handler(store, sink, max_upload_bytes=bad)
