"""The vault's vector slot and the temporary legacy write adapter (#1777)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
    StoreCorruptError,
    StoreIncompatibleError,
    StoreUnavailableError,
)
from markdown_vault_mcp.interfaces import VectorStore
from markdown_vault_mcp.managers._legacy_vector_writes import LegacyVectorWrites
from markdown_vault_mcp.managers._vector_slot import VectorStoreSlot
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.types import ANY_TOKEN, EmbeddingIdentity
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vector_store_contract import doc

if TYPE_CHECKING:
    from pathlib import Path

PROVIDER = MockEmbeddingProvider(dim=3)
IDENTITY = EmbeddingIdentity.of(PROVIDER, "v1")


def _slot(location: Path | None, **kwargs: Any) -> VectorStoreSlot:
    return VectorStoreSlot(
        backend=NumpySidecarBackend(),
        location=location,
        provider=PROVIDER,
        text_format="v1",
        **kwargs,
    )


def _persist_with(location: Path, identity: EmbeddingIdentity) -> None:
    store = NumpySidecarBackend().open(location, identity)
    store.replace_document(
        doc("a.md", [[1.0] * identity.dimension]), expected_token=ANY_TOKEN
    )
    store.close()


class TestOpening:
    def test_opens_once_and_installs(self, tmp_path: Path) -> None:
        _persist_with(tmp_path / "vec", IDENTITY)
        slot = _slot(tmp_path / "vec")
        store = slot.get(rebuild=None)
        assert slot.get(rebuild=None) is store
        assert slot.current() is store
        assert store.count == 1

    def test_an_owned_incompatible_store_rebuilds(self, tmp_path: Path) -> None:
        _persist_with(
            tmp_path / "vec", EmbeddingIdentity("mock", "old-model", "", "v1", 3)
        )
        slot = _slot(tmp_path / "vec")
        rebuilt: list[int] = []

        def rebuild() -> None:
            rebuilt.append(1)
            slot.fresh()

        store = slot.get(rebuild=rebuild)
        assert (rebuilt, store.count, store.identity) == ([1], 0, IDENTITY)
        assert slot.get(rebuild=rebuild) is store
        assert rebuilt == [1]

    def test_an_owned_corrupt_store_rebuilds(self, tmp_path: Path) -> None:
        (tmp_path / "vec.json").write_text("{not json")
        slot = _slot(tmp_path / "vec")
        store = slot.get(rebuild=slot.fresh)
        assert store.count == 0

    def test_a_rebuild_that_installs_nothing_raises(self, tmp_path: Path) -> None:
        (tmp_path / "vec.json").write_text("{not json")
        slot = _slot(tmp_path / "vec")
        with pytest.raises(ValueError, match="rebuild"):
            slot.get(rebuild=lambda: None)

    @pytest.mark.parametrize("fault", ["incompatible", "corrupt"])
    def test_without_a_rebuild_a_fault_is_unusable(
        self, tmp_path: Path, fault: str
    ) -> None:
        if fault == "corrupt":
            (tmp_path / "vec.json").write_text("{not json")
        else:
            _persist_with(
                tmp_path / "vec", EmbeddingIdentity("mock", "old", "", "v1", 3)
            )
        before = sorted(p.name for p in tmp_path.iterdir())
        slot = _slot(tmp_path / "vec", owns_files=False)
        expected = StoreCorruptError if fault == "corrupt" else StoreIncompatibleError
        with pytest.raises(expected):
            slot.get(rebuild=None)
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_unavailable_propagates_and_never_rebuilds(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        slot = _slot(blocker / "vec")
        rebuilt: list[int] = []
        with pytest.raises(StoreUnavailableError):
            slot.get(rebuild=lambda: rebuilt.append(1))
        assert rebuilt == []


class TestInjectedStore:
    def test_an_injected_store_is_used_never_rebuilt_never_closed(self) -> None:
        injected = NumpySidecarBackend().open(None, IDENTITY)
        injected.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        slot = _slot(None, store=injected)
        assert slot.get(rebuild=lambda: pytest.fail("rebuilt")) is injected
        slot.close()
        assert injected.count == 1  # still open

    def test_a_forced_rebuild_empties_an_injected_store_first(self) -> None:
        injected = NumpySidecarBackend().open(None, IDENTITY)
        injected.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        slot = _slot(None, store=injected)
        assert slot.fresh() is injected
        assert (injected.count, injected.generation) == (0, 1)


class TestLifecycleAndDescription:
    def test_close_closes_what_the_slot_opened(self, tmp_path: Path) -> None:
        slot = _slot(tmp_path / "vec")
        store = slot.legacy_get(
            rebuild=None
        ).store  # the writer's open fixes the dimension
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        slot.close()
        with pytest.raises(RuntimeError, match="closed"):
            store.count  # noqa: B018
        assert NumpySidecarBackend().open(tmp_path / "vec", IDENTITY).count == 1

    def test_describe(self, tmp_path: Path) -> None:
        slot = _slot(tmp_path / "vec")
        assert slot.describe() == {
            "backend": "numpy",
            "identity": None,
            "generation": None,
        }
        slot.legacy_get(rebuild=None)
        assert slot.describe() == {
            "backend": "numpy",
            "identity": {
                "provider": "mock",
                "model": "mock-dim-3",
                "variant": "",
                "text_format": "v1",
                "dimension": 3,
            },
            "generation": 0,
        }


class TestLegacyVectorWrites:
    def _writes(self) -> LegacyVectorWrites:
        return LegacyVectorWrites(NumpySidecarBackend().open(None, IDENTITY), PROVIDER)

    def test_satisfies_the_released_protocol(self) -> None:
        assert isinstance(self._writes(), VectorStore)

    def test_add_vectors_appends_per_document_and_chunks_group(self) -> None:
        writes = self._writes()
        writes.add_vectors([[1, 0, 0]], [{"path": "a.md", "content": "a0"}])
        writes.add_vectors(
            [[0, 1, 0], [0, 0, 1]],
            [{"path": "a.md", "content": "a1"}, {"path": "b.md", "content": "b0"}],
        )
        grouped = writes.chunks_by_path()
        assert [m["content"] for m in grouped["a.md"]] == ["a0", "a1"]
        assert [m["content"] for m in grouped["b.md"]] == ["b0"]
        assert writes.count == 3

    def test_a_wrong_dimension_in_a_batch_changes_nothing(self) -> None:
        writes = self._writes()
        writes.add_vectors([[1, 0, 0]], [{"path": "a.md"}])
        batch = [[0, 1, 0], [1, 0]]
        metas = [{"path": "b.md"}, {"path": "c.md"}]
        with pytest.raises(ValueError, match="dimension"):
            writes.add_vectors(batch, metas)
        assert set(writes.chunks_by_path()) == {"a.md"}

    def test_add_embeds_with_the_provider(self) -> None:
        writes = self._writes()
        assert writes.add(["hello"], [{"path": "a.md"}]) == 1
        assert writes.count == 1

    def test_delete_by_path_counts_and_removes(self) -> None:
        writes = self._writes()
        writes.add_vectors([[1, 0, 0], [0, 1, 0]], [{"path": "a.md"}, {"path": "a.md"}])
        assert writes.delete_by_path("a.md") == 2
        assert writes.delete_by_path("a.md") == 0
        assert writes.count == 0

    def test_save_checkpoints(self, tmp_path: Path) -> None:
        store = NumpySidecarBackend().open(tmp_path / "vec", IDENTITY)
        writes = LegacyVectorWrites(store, PROVIDER)
        writes.add_vectors([[1, 0, 0]], [{"path": "a.md"}])
        writes.save(tmp_path / "ignored")
        assert (
            NumpySidecarBackend()
            .open(tmp_path / "vec", IDENTITY, owns_files=False)
            .count
            == 1
        )

    def test_search_embeds_the_query(self) -> None:
        writes = self._writes()
        writes.add(["hello"], [{"path": "a.md"}])
        assert [r["path"] for r in writes.search("hello", limit=1)] == ["a.md"]
        assert writes.search("   ", limit=1) == []

    def test_the_slot_caches_one_adapter_per_store(self, tmp_path: Path) -> None:
        slot = _slot(tmp_path / "vec")
        assert slot.legacy() is None
        first = slot.legacy_get(rebuild=None)
        assert slot.legacy() is first
        slot.replace_any(first)
        assert slot.legacy() is first
        fresh = slot.legacy_fresh()
        assert fresh is not first
        assert slot.legacy() is fresh


class TestDimensionOnTheWritePath:
    class _SameNameProvider(MockEmbeddingProvider):
        @property
        def model_name(self) -> str:
            return "same-name"

    def test_reads_open_without_asking_the_provider_for_its_dimension(
        self, tmp_path: Path
    ) -> None:
        stored = EmbeddingIdentity("mock", "same-name", "", "v1", 3)
        _persist_with(tmp_path / "vec", stored)

        class Down(self._SameNameProvider):
            @property
            def dimension(self) -> int:
                raise ConnectionError("down")

        slot = VectorStoreSlot(
            backend=NumpySidecarBackend(),
            location=tmp_path / "vec",
            provider=Down(dim=3),
            text_format="v1",
        )
        store = slot.get(rebuild=None)
        assert (store.count, store.identity.dimension) == (1, 3)

    def test_a_dimension_change_under_the_same_model_rebuilds_before_writing(
        self, tmp_path: Path
    ) -> None:
        _persist_with(
            tmp_path / "vec", EmbeddingIdentity("mock", "same-name", "", "v1", 3)
        )
        slot = VectorStoreSlot(
            backend=NumpySidecarBackend(),
            location=tmp_path / "vec",
            provider=self._SameNameProvider(dim=4),
            text_format="v1",
        )
        rebuilt: list[int] = []

        def rebuild() -> None:
            rebuilt.append(1)
            slot.fresh()

        writes = slot.legacy_get(rebuild=rebuild)
        assert rebuilt == [1]
        assert writes.store.identity.dimension == 4

    def test_without_ownership_a_dimension_change_is_unusable_for_writes(
        self, tmp_path: Path
    ) -> None:
        _persist_with(
            tmp_path / "vec", EmbeddingIdentity("mock", "same-name", "", "v1", 3)
        )
        slot = VectorStoreSlot(
            backend=NumpySidecarBackend(),
            location=tmp_path / "vec",
            provider=self._SameNameProvider(dim=4),
            text_format="v1",
            owns_files=False,
        )
        with pytest.raises(StoreIncompatibleError, match="dimension"):
            slot.legacy_get(rebuild=None)

    def test_an_empty_injected_store_takes_the_provider_dimension(self) -> None:
        injected = NumpySidecarBackend().open(
            None, EmbeddingIdentity("mock", "mock-dim-3", "", "v1", 0)
        )
        slot = _slot(None, store=injected)
        writes = slot.legacy_get(rebuild=None)
        assert writes.store is injected
        assert injected.identity.dimension == 3

    def test_a_populated_injected_store_of_another_dimension_is_refused(
        self,
    ) -> None:
        stored = EmbeddingIdentity("mock", "same-name", "", "v1", 3)
        injected = NumpySidecarBackend().open(None, stored)
        injected.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        slot = VectorStoreSlot(
            backend=NumpySidecarBackend(),
            location=None,
            provider=self._SameNameProvider(dim=4),
            text_format="v1",
            store=injected,
        )
        with pytest.raises(StoreIncompatibleError, match="dimension"):
            slot.legacy_get(rebuild=lambda: pytest.fail("rebuilt"))
        assert injected.count == 1


class TestWritingThroughTheSlot:
    def test_writable_returns_the_installed_store(self, tmp_path: Path) -> None:
        slot = _slot(tmp_path / "vec")
        store = slot.writable(rebuild=None)
        assert slot.current() is store
        assert store.identity == IDENTITY

    def test_ownership_is_visible(self) -> None:
        assert _slot(None).owns_files
        assert not _slot(None, owns_files=False).owns_files

    def test_an_open_owned_store_is_its_own_rebuild_target(
        self, tmp_path: Path
    ) -> None:
        _persist_with(tmp_path / "vec", IDENTITY)
        slot = _slot(tmp_path / "vec")
        store = slot.get(rebuild=None)
        assert slot.rebuild_target() is store
        assert store.count == 1

    def test_an_unopened_owned_store_is_opened_as_the_rebuild_target(
        self, tmp_path: Path
    ) -> None:
        _persist_with(tmp_path / "vec", IDENTITY)
        slot = _slot(tmp_path / "vec")
        target = slot.rebuild_target()
        assert target.count == 1
        assert slot.current() is target

    def test_a_store_that_will_not_open_rebuilds_into_a_fresh_one(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "vec.json").write_text("{not json")
        slot = _slot(tmp_path / "vec")
        target = slot.rebuild_target()
        assert target.count == 0
        assert slot.current() is target
        assert (tmp_path / "vec.json").read_text() == "{not json"

    def test_a_process_that_does_not_own_the_files_may_not_rebuild(
        self, tmp_path: Path
    ) -> None:
        _persist_with(tmp_path / "vec", IDENTITY)
        before = sorted(p.name for p in tmp_path.iterdir())
        slot = _slot(tmp_path / "vec", owns_files=False)
        with pytest.raises(ReadOnlyError):
            slot.rebuild_target()
        with pytest.raises(ReadOnlyError):
            slot.fresh()
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_writable_current_skips_a_store_of_another_model(self) -> None:
        other = NumpySidecarBackend().open(
            None, EmbeddingIdentity("mock", "other-model", "", "v1", 3)
        )
        assert _slot(None, store=other).writable_current() is None
        same = NumpySidecarBackend().open(None, IDENTITY)
        assert _slot(None, store=same).writable_current() is same
        assert _slot(None).writable_current() is None

    def test_a_dimension_rebuild_does_not_publish_into_the_old_store(
        self, tmp_path: Path
    ) -> None:
        _persist_with(
            tmp_path / "vec", EmbeddingIdentity("mock", "same-name", "", "v1", 3)
        )

        class SameName(MockEmbeddingProvider):
            @property
            def model_name(self) -> str:
                return "same-name"

        slot = VectorStoreSlot(
            backend=NumpySidecarBackend(),
            location=tmp_path / "vec",
            provider=SameName(dim=4),
            text_format="v1",
        )
        old = slot.get(rebuild=None)
        targets: list[object] = []
        slot.writable(rebuild=lambda: targets.append(slot.rebuild_target()))
        assert targets
        assert targets[0] is not old

    def test_an_injected_store_of_another_model_is_refused_for_writing(self) -> None:
        other = EmbeddingIdentity("mock", "other-model", "", "v1", 3)
        injected = NumpySidecarBackend().open(None, other)
        injected.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        slot = _slot(None, store=injected)
        with pytest.raises(StoreIncompatibleError, match="other-model"):
            slot.writable(rebuild=lambda: pytest.fail("rebuilt"))
        assert (injected.count, injected.identity) == (1, other)

    def test_an_empty_injected_store_of_another_model_takes_the_provider_identity(
        self,
    ) -> None:
        injected = NumpySidecarBackend().open(
            None, EmbeddingIdentity("mock", "other-model", "", "v1", 3)
        )
        slot = _slot(None, store=injected)
        assert slot.writable(rebuild=None) is injected
        assert injected.identity == IDENTITY

    def test_an_injected_store_of_another_model_is_emptied_before_a_rebuild(
        self,
    ) -> None:
        injected = NumpySidecarBackend().open(
            None, EmbeddingIdentity("mock", "other-model", "", "v1", 3)
        )
        injected.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        slot = _slot(None, store=injected)
        assert slot.rebuild_target() is injected
        assert (injected.count, injected.identity) == (0, IDENTITY)

    def test_an_injected_rebuild_target_keeps_its_rows(self) -> None:
        injected = NumpySidecarBackend().open(None, IDENTITY)
        injected.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        slot = _slot(None, store=injected)
        assert slot.rebuild_target() is injected
        assert (injected.count, injected.generation) == (1, 0)
