"""Tests for :mod:`markdown_vault_mcp._server_deps`.

Covers the module-level Collection singleton accessors used by HTTP
route handlers that run outside FastMCP's ``Depends(get_collection)``
injection (e.g. the GitHub webhook route handler).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._server_deps import (
    get_collection_singleton,
    set_collection_singleton,
)
from markdown_vault_mcp.collection import Collection

if TYPE_CHECKING:
    from pathlib import Path


class TestCollectionSingleton:
    """Module-level Collection accessor for handlers outside FastMCP DI."""

    def test_get_raises_when_unset(self) -> None:
        """After clearing the singleton, the getter raises RuntimeError."""
        import markdown_vault_mcp._server_deps as _deps_module

        saved = _deps_module._collection_singleton
        try:
            set_collection_singleton(None)
            with pytest.raises(RuntimeError, match="Collection not initialised"):
                get_collection_singleton()
        finally:
            _deps_module._collection_singleton = saved

    def test_set_then_get_roundtrips(self, tmp_path: Path) -> None:
        """Setting then getting returns the same Collection instance."""
        import markdown_vault_mcp._server_deps as _deps_module

        saved = _deps_module._collection_singleton
        try:
            col = Collection(source_dir=tmp_path)
            set_collection_singleton(col)
            assert get_collection_singleton() is col
        finally:
            _deps_module._collection_singleton = saved
