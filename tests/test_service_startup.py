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
from markdown_vault_mcp.domain import (
    Service,
    get_vault_singleton,
    set_vault_singleton,
)
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


def test_vault_before_start_raises_runtime_error(tmp_path: Path) -> None:
    service = Service(ProjectConfig(source_dir=tmp_path))
    with pytest.raises(RuntimeError, match="call start"):
        _ = service.vault


async def test_missing_directory_marks_the_singleton_unavailable(
    tmp_path: Path,
) -> None:
    service = Service(ProjectConfig(source_dir=tmp_path / "absent"))
    await service.start()
    try:
        with pytest.raises(ConfigurationError, match="MARKDOWN_VAULT_MCP_SOURCE_DIR"):
            get_vault_singleton()
    finally:
        await service.stop()
    with pytest.raises(RuntimeError):
        get_vault_singleton()


async def test_stop_closes_the_collaborators_it_built_after_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service.stop() closes what to_vault_instances built, once, after the vault (#1765)."""
    import dataclasses

    from markdown_vault_mcp import domain as domain_mod

    class ClosingSpy:
        def __init__(self, inner: object) -> None:
            self._inner = inner
            self.closed = 0

        def close(self) -> None:
            self.closed += 1
            self._inner.close()  # type: ignore[attr-defined]

        def __call__(self, *args: object, **kwargs: object) -> object:
            # on_write is called on writes; dunder lookup bypasses __getattr__.
            return self._inner(*args, **kwargs)  # type: ignore[operator]

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    spies: list[ClosingSpy] = []
    real = domain_mod.to_vault_instances

    def spied(config: ProjectConfig) -> object:
        instances = real(config)
        spy = ClosingSpy(instances.git_strategy)
        spies.append(spy)
        return dataclasses.replace(instances, git_strategy=spy, on_write=spy)  # type: ignore[arg-type]

    monkeypatch.setattr(domain_mod, "to_vault_instances", spied)

    service = Service(ProjectConfig(source_dir=tmp_path))
    await service.start()
    await service.stop()

    assert [spy.closed for spy in spies] == [1]


async def test_stop_without_a_vault_has_nothing_to_close(tmp_path: Path) -> None:
    """A start() that built no vault leaves stop() with no collaborators to close (#1765)."""
    service = Service(ProjectConfig(source_dir=tmp_path / "missing"))
    await service.start()
    assert service.startup_error is not None

    await service.stop()  # must not raise
