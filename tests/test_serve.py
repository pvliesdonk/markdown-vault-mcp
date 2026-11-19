"""The ``run_http`` seam (#2335).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_serve.py``. Its two cases
that configure logging and read what reaches root
(``test_end_to_end_access_log_policy``,
``test_uvicorn_never_installs_its_own_handlers``) test the logging setup's
handling of uvicorn and move with that area (#2276).
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time

import pytest

from markdown_vault_mcp._pvl_core import ServerConfig
from markdown_vault_mcp._serve import _build_uvicorn_config, _run_server, run_http


async def _app(scope, receive, send):
    """A minimal ASGI app: 200 on /health and /mcp, 404 elsewhere."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    status = 200 if scope["path"] in ("/health", "/mcp") else 404
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _running_server():
    """Run a real ``uvicorn.Server`` for ``_app`` on a free port, in a thread.

    Used by the e2e test below that only needs a running server, not the
    ``run_http`` composition itself: starts the server on a background
    thread, polls ``server.started`` against a 10s deadline before
    yielding ``(server, port)``, and guarantees shutdown — ``should_exit``
    then a 10s-timeout ``thread.join`` — in a ``finally``, so a test body
    expresses only what it asserts, not the start/stop mechanics.
    """
    import uvicorn

    port = _free_port()
    built = _build_uvicorn_config(_app, host="127.0.0.1", port=port, shutdown_grace_s=1)
    server = uvicorn.Server(built)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "server did not start within 10s"
        yield server, port
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@contextlib.contextmanager
def _running_server_via_run_http(monkeypatch):
    """Same shape as ``_running_server``, but drives ``run_http`` itself.

    ``_running_server`` calls ``_build_uvicorn_config`` +
    ``uvicorn.Server(...).run()`` directly — the same composition
    ``run_http`` performs internally, but not ``run_http`` itself, which is
    exactly why a bug in ``run_http``'s own epilogue (see the
    ``_run_server`` tests) had no e2e coverage. This drives ``run_http`` on
    a background thread instead, capturing the ``uvicorn.Server`` instance
    it builds internally — by patching the class it calls, not a copy of
    it — so the test body can still poll ``.started`` and drive shutdown
    via ``.should_exit`` the same way ``_running_server`` does.
    """
    import uvicorn

    port = _free_port()
    captured: dict[str, uvicorn.Server] = {}
    real_server_cls = uvicorn.Server

    def _capturing(config):
        server = real_server_cls(config)
        captured["server"] = server
        return server

    monkeypatch.setattr(uvicorn, "Server", _capturing)
    thread = threading.Thread(
        target=run_http,
        kwargs={
            "app": _app,
            "config": ServerConfig(host="127.0.0.1", port=port, shutdown_grace_s=1),
        },
        daemon=True,
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        server = None
        while time.monotonic() < deadline:
            server = captured.get("server")
            if server is not None and server.started:
                break
            time.sleep(0.05)
        assert server is not None and server.started, "server did not start within 10s"
        yield server, port
    finally:
        server = captured.get("server")
        if server is not None:
            server.should_exit = True
        thread.join(timeout=10)


def test_pins_the_fixed_settings():
    built = _build_uvicorn_config(_app, host="127.0.0.1", port=8000, shutdown_grace_s=3)
    assert built.log_config is None
    assert built.lifespan == "on"
    assert built.timeout_graceful_shutdown == 3


def test_takes_host_and_port_from_config(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "markdown_vault_mcp._serve._run_server",
        lambda built: captured.update(built=built),
    )
    run_http(_app, config=ServerConfig(host="0.0.0.0", port=9001, shutdown_grace_s=7))  # noqa: S104, RUF100 - Asserted host value; S104 is gate-only.
    built = captured["built"]
    assert (built.host, built.port) == ("0.0.0.0", 9001)  # noqa: S104, RUF100 - Asserted host value; S104 is gate-only.
    assert built.timeout_graceful_shutdown == 7


def test_explicit_host_and_port_override_config(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "markdown_vault_mcp._serve._run_server",
        lambda built: captured.update(built=built),
    )
    run_http(
        _app,
        config=ServerConfig(host="127.0.0.1", port=8000),
        host="0.0.0.0",  # noqa: S104, RUF100 - Asserted host value; S104 is gate-only.
        port=9999,
    )
    built = captured["built"]
    assert (built.host, built.port) == ("0.0.0.0", 9999)  # noqa: S104, RUF100 - Asserted host value; S104 is gate-only.


def test_zero_port_is_an_override_not_a_fallback(monkeypatch):
    """0 is a real request (bind any free port), not "unset"."""
    captured = {}
    monkeypatch.setattr(
        "markdown_vault_mcp._serve._run_server",
        lambda built: captured.update(built=built),
    )
    run_http(_app, config=ServerConfig(port=8000), port=0)
    assert captured["built"].port == 0


class TestRunServerEpilogue:
    """``_run_server`` mirrors the epilogue ``uvicorn.run(...)`` wraps
    ``server.run()`` in — without it, a failed startup exits 0 instead of
    uvicorn's own ``STARTUP_FAILURE``, and a mid-run Ctrl-C propagates as an
    uncaught ``KeyboardInterrupt`` instead of exiting silently."""

    class _FakeServer:
        def __init__(self, *, started: bool, raise_keyboard_interrupt: bool = False):
            self.started = started
            self._raise_keyboard_interrupt = raise_keyboard_interrupt
            self.run_called = False

        def run(self):
            self.run_called = True
            if self._raise_keyboard_interrupt:
                raise KeyboardInterrupt

    def _patch_server(self, monkeypatch, fake):
        import uvicorn

        monkeypatch.setattr(uvicorn, "Server", lambda _config: fake)

    def _built(self):
        """The config these tests run; its contents are asserted elsewhere."""
        return _build_uvicorn_config(
            _app, host="127.0.0.1", port=8000, shutdown_grace_s=1
        )

    def _expect_startup_failure(self, fake):
        """Assert `_run_server` exits 3 — uvicorn's STARTUP_FAILURE — after running."""
        with pytest.raises(SystemExit) as exc_info:
            _run_server(self._built())
        assert fake.run_called
        assert exc_info.value.code == 3

    def test_exits_with_startup_failure_when_server_never_started(self, monkeypatch):
        fake = self._FakeServer(started=False)
        self._patch_server(monkeypatch, fake)

        self._expect_startup_failure(fake)

    def test_keyboard_interrupt_after_startup_exits_silently(self, monkeypatch):
        """Ctrl-C after the server is up: swallowed, no SystemExit, no traceback."""
        fake = self._FakeServer(started=True, raise_keyboard_interrupt=True)
        self._patch_server(monkeypatch, fake)

        _run_server(self._built())  # must not raise

        assert fake.run_called

    def test_keyboard_interrupt_before_startup_still_signals_failure(self, monkeypatch):
        """Ctrl-C before the server ever started: still a startup failure."""
        fake = self._FakeServer(started=False, raise_keyboard_interrupt=True)
        self._patch_server(monkeypatch, fake)

        self._expect_startup_failure(fake)
