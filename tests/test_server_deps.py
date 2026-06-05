"""Tests for :mod:`markdown_vault_mcp._server_deps`.

Covers the module-level Vault singleton accessors used by HTTP
route handlers that run outside FastMCP's ``Depends(get_vault)``
injection (e.g. the GitHub webhook route handler).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._server_deps import (
    get_vault_singleton,
    set_vault_singleton,
)
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from pathlib import Path


class TestVaultSingleton:
    """Module-level Vault accessor for handlers outside FastMCP DI."""

    def test_get_raises_when_unset(self) -> None:
        """After clearing the singleton, the getter raises RuntimeError."""
        import markdown_vault_mcp._server_deps as _deps_module

        saved = _deps_module._vault_singleton
        try:
            set_vault_singleton(None)
            with pytest.raises(RuntimeError, match="Vault not initialised"):
                get_vault_singleton()
        finally:
            _deps_module._vault_singleton = saved

    def test_set_then_get_roundtrips(self, tmp_path: Path) -> None:
        """Setting then getting returns the same Vault instance."""
        import markdown_vault_mcp._server_deps as _deps_module

        saved = _deps_module._vault_singleton
        try:
            col = Vault(source_dir=tmp_path)
            set_vault_singleton(col)
            assert get_vault_singleton() is col
        finally:
            _deps_module._vault_singleton = saved
