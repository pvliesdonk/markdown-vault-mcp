"""Tests for :mod:`markdown_vault_mcp._server_deps`.

Covers the module-level Collection singleton accessors used by HTTP
route handlers that run outside FastMCP's ``Depends(get_collection)``
injection (e.g. the pvl-core file-exchange upload receiver), and the
``make_collection_lifespan`` factory's startup wiring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp._server_deps import (
    get_collection_singleton,
    make_collection_lifespan,
    set_collection_singleton,
)
from markdown_vault_mcp.collection import Collection, IndexStats

if TYPE_CHECKING:
    from pathlib import Path


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
    """``make_collection_lifespan`` wires startup without blocking on indexing."""

    @pytest.mark.asyncio
    async def test_lifespan_does_not_block_on_initial_indexing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Lifespan must yield within a bounded time even if build_index is slow."""
        instances: list[FakeCollection] = []

        class FakeCollection:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.scheduled = 0
                self.build_index_calls = 0
                self.embeddings_calls = 0
                self.initialize_async_calls = 0
                self.start_calls = 0
                self.close_calls = 0
                instances.append(self)

            def sync_from_remote_before_index(self) -> None:
                pass

            def initialize_async(self) -> None:
                self.initialize_async_calls += 1

            def schedule_background_reindex(self) -> None:
                self.scheduled += 1

            def build_index(self) -> IndexStats:  # would be slow if called
                self.build_index_calls += 1
                return IndexStats(documents_indexed=0, chunks_indexed=0, skipped=0)

            def build_embeddings(self) -> int:
                self.embeddings_calls += 1
                return 0

            def start(self) -> None:
                self.start_calls += 1

            def close(self) -> None:
                self.close_calls += 1

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
            assert instances[0].scheduled == 1
            assert instances[0].build_index_calls == 0  # must NOT block on build_index
            assert instances[0].embeddings_calls == 0  # must NOT block on embeddings
            assert instances[0].initialize_async_calls == 1
            assert instances[0].start_calls == 1
        finally:
            await agen.aclose()
            # Singleton was cleared on shutdown.
            assert _deps_module._collection_singleton is None
            assert instances[0].close_calls == 1

    @pytest.mark.asyncio
    async def test_lifespan_without_embedding_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Without an embedding provider, lifespan still schedules background reindex.

        The background thread is responsible for honouring the absence of an
        embedding provider; the lifespan unconditionally schedules.
        """
        instances: list[FakeCollection] = []

        class FakeCollection:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.scheduled = 0
                self.build_index_calls = 0
                self.embeddings_calls = 0
                self.initialize_async_calls = 0
                self.start_calls = 0
                self.close_calls = 0
                instances.append(self)

            def sync_from_remote_before_index(self) -> None:
                pass

            def initialize_async(self) -> None:
                self.initialize_async_calls += 1

            def schedule_background_reindex(self) -> None:
                self.scheduled += 1

            def build_index(self) -> IndexStats:
                self.build_index_calls += 1
                return IndexStats(documents_indexed=0, chunks_indexed=0, skipped=0)

            def build_embeddings(self) -> int:
                self.embeddings_calls += 1
                return 0

            def start(self) -> None:
                self.start_calls += 1

            def close(self) -> None:
                self.close_calls += 1

        class FakeConfig:
            source_dir = tmp_path

            def to_collection_kwargs(self) -> dict[str, Any]:
                return {
                    "source_dir": tmp_path,
                    "embedding_provider": None,
                }

        import markdown_vault_mcp._server_deps as _deps_module

        monkeypatch.setattr(_deps_module, "Collection", FakeCollection)
        lifespan = make_collection_lifespan(FakeConfig())
        agen = lifespan._fn(None)
        try:
            ctx = await agen.__anext__()
            assert ctx["collection"] is instances[0]
            assert instances[0].scheduled == 1
            assert instances[0].build_index_calls == 0
            assert instances[0].embeddings_calls == 0
            assert instances[0].initialize_async_calls == 1
            assert instances[0].start_calls == 1
        finally:
            await agen.aclose()
