"""Service.start on a deployment whose vault directory does not exist.

The server starts (its tool listing and instructions stay reachable, which
the template's model-facing test relies on), builds no vault, and every
accessor fails with the variable to set.
"""

from __future__ import annotations

import logging
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


async def test_missing_directory_starts_without_a_vault(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service = Service(ProjectConfig(source_dir=tmp_path / "absent"))
    with caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.domain"):
        await service.start()
    try:
        records = [
            r for r in caplog.records if r.msg.startswith("vault_directory_missing")
        ]
        assert [r.args for r in records] == [(tmp_path / "absent",)]
        assert service.startup_error is not None
        with pytest.raises(ConfigurationError, match="MARKDOWN_VAULT_MCP_SOURCE_DIR"):
            _ = service.vault
    finally:
        await service.stop()


async def test_existing_directory_builds_the_vault(tmp_path: Path) -> None:
    service = Service(ProjectConfig(source_dir=tmp_path))
    await service.start()
    try:
        assert service.startup_error is None
        assert service.vault.source_dir == tmp_path
    finally:
        await service.stop()
