"""Test-only server construction compatible with async test bodies."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from markdown_vault_mcp.server import make_server as _make_server

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from markdown_vault_mcp.config import ProjectConfig


def make_server(
    *,
    transport: str = "stdio",
    config: ProjectConfig | None = None,
) -> FastMCP:
    """Construct a server outside any active event loop.

    pvl-core finalizes instructions synchronously. Most integration tests are
    async and configure their server immediately before opening a client, so
    construction moves to a short-lived worker only when the test loop is
    already running.

    Args:
        transport: Transport whose conditional components should be wired.
        config: Optional pre-loaded project configuration.

    Returns:
        A fully configured FastMCP server.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _make_server(transport=transport, config=config)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(
            _make_server,
            transport=transport,
            config=config,
        ).result()
