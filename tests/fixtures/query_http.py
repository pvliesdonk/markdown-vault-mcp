"""Real loopback MCP transport, independent of CLI contribution fixtures."""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import uvicorn

from tests.fixtures.logging import preserve_logging_topology

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp import FastMCP


@asynccontextmanager
async def live_mcp(server: FastMCP) -> AsyncIterator[str]:
    """Serve one MCP app on a reserved loopback socket with bounded cleanup.

    Args:
        server: Configured server whose lifespan owns its vault.

    Yields:
        Complete streamable HTTP endpoint URL.
    """
    # The server's own logging setup must not outlive it (#2276).
    with preserve_logging_topology(), socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        http = uvicorn.Server(
            # log_config=None, as run_http pins: uvicorn's default dictConfig
            # would leave uvicorn.access non-propagating for later tests.
            uvicorn.Config(
                server.http_app(),
                log_config=None,
                log_level="critical",
                timeout_graceful_shutdown=2,
            )
        )
        task = asyncio.create_task(http.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not http.started:
                    if task.done():
                        await task
                        raise RuntimeError("MCP HTTP server stopped during startup")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            http.should_exit = True
            await asyncio.wait_for(task, timeout=5)
