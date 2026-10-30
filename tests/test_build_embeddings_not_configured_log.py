"""Building embeddings on a vault without them logs no ERROR (#1733).

The ``index`` and ``reindex`` commands and the ``build_embeddings`` tool all
call ``build_embeddings`` and handle the refusal themselves.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from pathlib import Path


def test_a_vault_without_embeddings_logs_no_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "a.md").write_text("# a\n\nhello\n")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        with (
            caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp"),
            pytest.raises(EmbeddingsNotConfiguredError),
        ):
            vault.index.build_embeddings()
    finally:
        vault.close()
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_an_async_build_without_embeddings_logs_no_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The build_embeddings tool's path: the refusal is recorded for
    # get_index_status and reported by the tool, not logged as a fault.
    (tmp_path / "a.md").write_text("# a\n\nhello\n")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        with (
            caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp"),
            pytest.raises(EmbeddingsNotConfiguredError),
        ):
            asyncio.run(_await(vault.index.build_embeddings_async()))
        status = vault.index.get_index_status()
    finally:
        vault.close()
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert "not enabled" in str(status["last_build_embeddings_error"])


async def _await(future: object) -> object:
    return await asyncio.wrap_future(future)  # type: ignore[arg-type]
