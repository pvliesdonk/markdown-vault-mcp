"""Service.start over a vault directory that does not exist fails fast.

The failure is a ConfigurationError naming the variable, raised before any
index job or file watcher runs (a wrong path used to surface as a raw
FileNotFoundError from the watcher, #1590).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.domain import Service, set_vault_singleton
from markdown_vault_mcp.exceptions import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _no_singleton() -> None:
    set_vault_singleton(None)


async def test_missing_directory_fails_fast(tmp_path: Path) -> None:
    service = Service(ProjectConfig(source_dir=tmp_path / "absent"))
    with pytest.raises(ConfigurationError, match="MARKDOWN_VAULT_MCP_SOURCE_DIR"):
        await service.start()
    await service.stop()  # nothing was started; stop is a no-op


async def test_existing_directory_starts(tmp_path: Path) -> None:
    service = Service(ProjectConfig(source_dir=tmp_path))
    await service.start()
    try:
        assert service.vault.source_dir == tmp_path
    finally:
        await service.stop()
