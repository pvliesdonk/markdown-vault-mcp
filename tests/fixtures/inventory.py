"""Synthetic served vault without index construction for inventory wire tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
from tests.server_factory import make_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest
    from fastmcp import FastMCP


def inventory_server(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    conventions_file: str | None = "_conventions.md",
) -> FastMCP:
    """Bind one synthetic root to a real server registry with a minimal lifespan.

    Args:
        root: Synthetic directory created by the test.
        monkeypatch: Test-scoped patch manager.
        conventions_file: Explicit served conventions policy.

    Returns:
        Server exercising real registration, dependencies and visibility.
    """
    config = ProjectConfig(
        source_dir=root,
        read_only=True,
        exclude=["excluded/**"],
        conventions_file=conventions_file,
        attachment_extensions=["pdf"],
    )
    settings = VaultSettings.from_project_config(config)
    service = SimpleNamespace(
        vault=SimpleNamespace(
            source_dir=root, exclude_patterns=settings.effective_exclude_patterns()
        ),
        config=config,
    )

    @asynccontextmanager
    async def lifespan(_mcp: object) -> AsyncIterator[dict[str, Any]]:
        yield {"service": service}

    monkeypatch.setattr("markdown_vault_mcp.server.server_lifespan", lifespan)
    return make_server(config=config)
