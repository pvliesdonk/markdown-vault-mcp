"""The connection registry's settings (#1497)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._fts_connection import SqliteConnectionRegistry

if TYPE_CHECKING:
    from pathlib import Path


def test_the_defaults_keep_the_keyword_index_settings(tmp_path: Path) -> None:
    registry = SqliteConnectionRegistry(tmp_path / "a.db")
    try:
        conn = registry.conn()
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert conn.isolation_level == ""
    finally:
        registry.close()


def test_full_sync_and_autocommit(tmp_path: Path) -> None:
    registry = SqliteConnectionRegistry(
        tmp_path / "a.db", synchronous="FULL", autocommit=True
    )
    try:
        conn = registry.conn()
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
        assert conn.isolation_level is None
    finally:
        registry.close()


def test_an_unknown_sync_level_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="synchronous"):
        SqliteConnectionRegistry(tmp_path / "a.db", synchronous="LOUD")


def test_the_memory_prefix_names_the_shared_database() -> None:
    registry = SqliteConnectionRegistry(":memory:", memory_prefix="vectors")
    assert registry.connect_uri.startswith("file:vectors_")
    registry.close()
