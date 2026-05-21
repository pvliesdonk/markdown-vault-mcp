"""Tests for :mod:`markdown_vault_mcp._server_deps`.

Covers the module-level Collection singleton accessors used by HTTP
route handlers that run outside FastMCP's ``Depends(get_collection)``
injection (e.g. the pvl-core file-exchange upload receiver).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._server_deps import (
    get_collection_singleton,
    make_collection_lifespan,
    set_collection_singleton,
)
from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.types import IndexStats

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


class TestCollectionSingleton:
    """Module-level Collection accessor mirrors :mod:`artifacts`'s pattern."""

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


class TestCollectionLifespan:
    """Server startup keeps MCP handshake work bounded."""

    @pytest.mark.asyncio
    async def test_startup_skips_embedding_build_when_sidecar_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Configured embeddings do not trigger first corpus build at startup."""
        instances: list[FakeCollection] = []

        class FakeCollection:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.embeddings_built = 0
                instances.append(self)

            def sync_from_remote_before_index(self) -> None:
                pass

            def build_index(self) -> IndexStats:
                return IndexStats(documents_indexed=1, chunks_indexed=1, skipped=0)

            def has_embedding_index_sidecar(self) -> bool:
                return False

            def build_embeddings(self) -> int:
                self.embeddings_built += 1
                return 1

            def start(self) -> None:
                pass

            def close(self) -> None:
                pass

        class FakeConfig:
            source_dir = tmp_path

            def to_collection_kwargs(self) -> dict[str, Any]:
                return {
                    "source_dir": tmp_path,
                    "embedding_provider": object(),
                    "embeddings_path": tmp_path / "embeddings",
                }

        import markdown_vault_mcp._server_deps as _deps_module

        monkeypatch.setattr(_deps_module, "Collection", FakeCollection)
        lifespan = make_collection_lifespan(FakeConfig())
        agen = lifespan._fn(None)
        try:
            await agen.__anext__()
            assert instances[0].embeddings_built == 0
        finally:
            await agen.aclose()

    @pytest.mark.asyncio
    async def test_startup_skips_embedding_sidecar_probe_without_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A provider without embeddings_path disables semantic startup work."""
        instances: list[FakeCollection] = []

        class FakeCollection:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.sidecar_checked = 0
                self.embeddings_built = 0
                instances.append(self)

            def sync_from_remote_before_index(self) -> None:
                pass

            def build_index(self) -> IndexStats:
                return IndexStats(documents_indexed=1, chunks_indexed=1, skipped=0)

            def has_embedding_index_sidecar(self) -> bool:
                self.sidecar_checked += 1
                return False

            def build_embeddings(self) -> int:
                self.embeddings_built += 1
                return 1

            def start(self) -> None:
                pass

            def close(self) -> None:
                pass

        class FakeConfig:
            source_dir = tmp_path

            def to_collection_kwargs(self) -> dict[str, Any]:
                return {
                    "source_dir": tmp_path,
                    "embedding_provider": object(),
                    "embeddings_path": None,
                }

        import markdown_vault_mcp._server_deps as _deps_module

        monkeypatch.setattr(_deps_module, "Collection", FakeCollection)
        lifespan = make_collection_lifespan(FakeConfig())
        agen = lifespan._fn(None)
        try:
            await agen.__anext__()
            assert instances[0].sidecar_checked == 0
            assert instances[0].embeddings_built == 0
        finally:
            await agen.aclose()

    @pytest.mark.asyncio
    async def test_startup_builds_embeddings_when_sidecar_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Existing vectors are loaded/refreshed at startup."""
        instances: list[FakeCollection] = []

        class FakeCollection:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.embeddings_built = 0
                instances.append(self)

            def sync_from_remote_before_index(self) -> None:
                pass

            def build_index(self) -> IndexStats:
                return IndexStats(documents_indexed=1, chunks_indexed=1, skipped=0)

            def has_embedding_index_sidecar(self) -> bool:
                return True

            def build_embeddings(self) -> int:
                self.embeddings_built += 1
                return 1

            def start(self) -> None:
                pass

            def close(self) -> None:
                pass

        class FakeConfig:
            source_dir = tmp_path

            def to_collection_kwargs(self) -> dict[str, Any]:
                return {
                    "source_dir": tmp_path,
                    "embedding_provider": object(),
                    "embeddings_path": tmp_path / "embeddings",
                }

        import markdown_vault_mcp._server_deps as _deps_module

        monkeypatch.setattr(_deps_module, "Collection", FakeCollection)
        lifespan = make_collection_lifespan(FakeConfig())
        agen = lifespan._fn(None)
        try:
            await agen.__anext__()
            assert instances[0].embeddings_built == 1
        finally:
            await agen.aclose()
