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


async def test_a_second_server_on_a_held_index_starts_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from tests.fixtures.keyword_index_holder import Holder

    (tmp_path / "vault").mkdir()
    index = tmp_path / "state" / "index.db"
    held = Holder(index)
    try:
        service = Service(
            ProjectConfig(source_dir=tmp_path / "vault", index_path=index)
        )
        with caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.domain"):
            await service.start()
        try:
            records = [
                r
                for r in caplog.records
                if r.msg.startswith("vault_unavailable reason=index_busy")
            ]
            assert [r.args[0] for r in records] == [str(index)]
            assert [r.levelno for r in records] == [logging.ERROR]
            with pytest.raises(ConfigurationError) as failure:
                get_vault_singleton()
            assert f"Keyword index at {index}" in str(failure.value)
            assert "being written by another process" in str(failure.value)
            assert service.startup_error == str(failure.value)
        finally:
            await service.stop()
    finally:
        held.stop()


async def test_the_server_opens_the_vault_once_the_index_is_released(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from markdown_vault_mcp import domain as domain_mod
    from tests.fixtures.keyword_index_holder import Holder

    monkeypatch.setattr(domain_mod, "_INDEX_BUSY_RETRY_S", 0.05)
    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "alpha.md").write_text("# Alpha\n\nDragons.\n")
    index = tmp_path / "state" / "index.db"
    held = Holder(index)
    service = Service(ProjectConfig(source_dir=tmp_path / "vault", index_path=index))
    try:
        with caplog.at_level(logging.INFO, logger="markdown_vault_mcp.domain"):
            await service.start()
            with pytest.raises(ConfigurationError, match="Keyword index at"):
                get_vault_singleton()
            held.stop()
            for _ in range(200):
                if service.startup_error is None:
                    break
                await asyncio.sleep(0.05)
        assert service.startup_error is None
        assert get_vault_singleton() is service.vault
        assert any(r.msg.startswith("vault_available") for r in caplog.records)
    finally:
        held.stop()
        await service.stop()


async def test_stop_cancels_the_wait_for_a_held_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp import domain as domain_mod
    from tests.fixtures.keyword_index_holder import Holder

    monkeypatch.setattr(domain_mod, "_INDEX_BUSY_RETRY_S", 0.05)
    (tmp_path / "vault").mkdir()
    index = tmp_path / "index.db"
    held = Holder(index)
    try:
        service = Service(
            ProjectConfig(source_dir=tmp_path / "vault", index_path=index)
        )
        await service.start()
        await service.stop()
        assert service.startup_error is not None
    finally:
        held.stop()
    # The stopped service took no share: another process may own the index.
    Holder(index).stop()
