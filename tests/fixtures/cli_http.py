"""Real loopback MCP transport fixture; no external vault or credentials."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
import uvicorn

from tests.fixtures.logging import preserve_logging_topology

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
    # The server's own logging setup must not outlive it (#2276).
    with preserve_logging_topology(), socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            # log_config=None, as run_http pins: uvicorn's default dictConfig
            # would leave uvicorn.access non-propagating for later tests.
            uvicorn.Config(app, log_config=None, log_level="critical", lifespan="on")
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
    for name in ("SERVER_URL", "CLIENT_CONFIG", "CLIENT_TOKEN"):
        monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{name}", raising=False)
    monkeypatch.setenv(
        "XDG_CONFIG_HOME", str(tmp_path_factory.getbasetemp() / "client-config")
    )
