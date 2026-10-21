"""Real loopback MCP transport fixture; no external vault or credentials."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
import uvicorn

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def serve_http(app: Any) -> Iterator[str]:
    """Serve an ASGI app on an OS-selected loopback port, then stop it.

    Args:
        app: ASGI application, including its lifecycle.

    Yields:
        Loopback HTTP origin.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="critical", lifespan="on")
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 10
        try:
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Synthetic MCP server failed to start")
                time.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Synthetic MCP server did not shut down"


@pytest.fixture(autouse=True)
def cli_client_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Prevent test commands from discovering a developer's hosted profile."""
    monkeypatch.setenv(
        "XDG_CONFIG_HOME", str(tmp_path_factory.getbasetemp() / "client-config")
    )
