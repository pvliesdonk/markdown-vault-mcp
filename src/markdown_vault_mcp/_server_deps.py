"""Service lifespan + dependency injection for Markdown Vault MCP."""

from __future__ import annotations

import logging
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, TypedDict

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from markdown_vault_mcp.domain import Service

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from markdown_vault_mcp.config import ProjectConfig

logger = logging.getLogger(__name__)

# The ``ProjectConfig`` each server was built from, keyed by the server so
# two servers in one process (a test suite builds several) never share or
# overwrite state.  ``make_server`` binds it before any ``register_*`` runs.
_CONFIGS: weakref.WeakKeyDictionary[FastMCP, ProjectConfig] = (
    weakref.WeakKeyDictionary()
)


def bind_config(mcp: FastMCP, config: ProjectConfig) -> None:
    """Record *config* as the configuration *mcp* was built from.

    ``make_server`` calls this once, right after constructing the server and
    before ``register_tools`` / ``register_resources`` / ``register_prompts``
    / ``register_apps`` run, so every registrar reaches the same resolved
    configuration through :func:`config_for` — a caller-supplied ``config``
    included, not only the environment (#534).
    """
    _CONFIGS[mcp] = config


def config_for(mcp: FastMCP) -> ProjectConfig:
    """The configuration *mcp* was built from, for registration-time wiring.

    Use inside a ``register_*`` function when a subsystem must exist while
    the tools are registered — a jobs backend, an upstream client, a
    parameter default baked from configuration.  Reading the environment
    there instead silently disagrees with a ``config`` passed to
    ``make_server``.

    Raises:
        RuntimeError: If *mcp* was not built through ``make_server`` and
            :func:`bind_config` was never called — a bare ``FastMCP()`` in a
            test, for instance.  Falling back to the environment here would
            recreate the divergence this accessor exists to close.
    """
    config = _CONFIGS.get(mcp)
    if config is None:
        msg = (
            "No ProjectConfig is bound to this server — build it with "
            "make_server(), or call bind_config(mcp, config) before registering"
        )
        raise RuntimeError(msg)
    return config


def get_config(ctx: Context = CurrentContext()) -> ProjectConfig:
    """Resolve the server's configuration from the request context.

    Use as a ``Depends`` default in tool/resource/prompt handlers, next to
    :func:`get_service`; it returns the same object :func:`config_for`
    returns at registration time.
    """
    return config_for(ctx.fastmcp)


class LifespanState(TypedDict):
    """Shape of the lifespan context yielded to request handlers."""

    service: Service


@asynccontextmanager
async def server_lifespan(_mcp: object) -> AsyncIterator[dict[str, Any]]:
    """Start the service on startup; stop it on shutdown."""
    service = Service()
    await service.start()
    logger.info("service_started")
    try:
        yield {"service": service}
    finally:
        await service.stop()
        logger.info("service_stopped")


def get_service(ctx: Context = CurrentContext()) -> Service:
    """Resolve the running :class:`Service` from the request context.

    Use as a ``Depends`` default in tool/resource/prompt handlers.

    Raises:
        RuntimeError: If the server lifespan has not run.
    """
    service: Service | None = ctx.lifespan_context.get("service")
    if service is None:
        msg = "Service not initialised — server lifespan has not run"
        raise RuntimeError(msg)
    return service
