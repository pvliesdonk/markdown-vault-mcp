"""The vault's vector slot (#1777, #1778)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
    StoreCorruptError,
    StoreIncompatibleError,
    StoreUnavailableError,
)
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
        (tmp_path / "vec.v2.json").write_text("{not json")
        slot = _slot(tmp_path / "vec")
        store = slot.get(rebuild=slot.fresh)
        assert store.count == 0

    def test_a_rebuild_that_installs_nothing_raises(self, tmp_path: Path) -> None:
        (tmp_path / "vec.v2.json").write_text("{not json")
        slot = _slot(tmp_path / "vec")
        with pytest.raises(ValueError, match="rebuild"):
            slot.get(rebuild=lambda: None)

    @pytest.mark.parametrize("fault", ["incompatible", "corrupt"])
    def test_without_a_rebuild_a_fault_is_unusable(
        self, tmp_path: Path, fault: str
    ) -> None:
        if fault == "corrupt":
            (tmp_path / "vec.v2.json").write_text("{not json")
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
        store = slot.writable(rebuild=None)  # the writer's open fixes the dimension
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
            "search_mode": None,
            "search_workspace_bytes": None,
            "stored_bytes": None,
            "resident_vector_bytes": None,
            "store_details": None,
        }
        slot.writable(rebuild=None)
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
            "search_mode": "resident",
            "search_workspace_bytes": None,
            "stored_bytes": 0,
            "resident_vector_bytes": 0,
            "store_details": None,
        }

    def test_describe_includes_store_details_only_from_a_store_that_gives_them(
        self,
    ) -> None:
        injected = NumpySidecarBackend().open(None, IDENTITY)

        class _Describing:
            def __getattr__(self, name: str) -> Any:
                return getattr(injected, name)

            def describe(self) -> dict[str, Any]:
                return {"host": "db", "schema": "vault_a"}

        slot = _slot(None, store=_Describing())
        slot.get(rebuild=None)
        assert slot.describe()["store_details"] == {"host": "db", "schema": "vault_a"}
        plain = _slot(None, store=injected)
        plain.get(rebuild=None)
        assert plain.describe()["store_details"] is None
        injected.close()

    def test_describe_raises_when_a_streamed_store_cannot_be_read(
        self, tmp_path: Path
    ) -> None:
        import sqlite3

        from markdown_vault_mcp.exceptions import StoreUnavailableError
        from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend

        slot = VectorStoreSlot(
            backend=SqliteVectorBackend(search="streamed"),
            location=tmp_path / "vec",
            provider=PROVIDER,
            text_format="v1",
        )
        store = slot.writable(rebuild=None)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)

        class _Refusing:
            def execute(self, *_args: object) -> None:
                raise sqlite3.OperationalError("disk I/O error")

        real = store._registry.conn
        store._registry.conn = _Refusing  # type: ignore[method-assign]
        with pytest.raises(StoreUnavailableError):
            slot.describe()  # the generation is a database read when streamed
        store._registry.conn = real  # type: ignore[method-assign]
        slot.close()

    def test_describe_a_store_that_reports_no_resources(self) -> None:
        injected = NumpySidecarBackend().open(None, IDENTITY)

        class _Opaque:
            """A store that does not report its resources."""

            def __getattr__(self, name: str) -> Any:
                if name == "resources":
                    raise AttributeError(name)
                return getattr(injected, name)

        slot = _slot(None, store=_Opaque())
        slot.get(rebuild=None)
        described = slot.describe()
        assert described["generation"] == 0
        assert {
            k: described[k]
            for k in (
                "search_mode",
                "search_workspace_bytes",
                "stored_bytes",
                "resident_vector_bytes",
            )
        } == dict.fromkeys(
            (
                "search_mode",
                "search_workspace_bytes",
                "stored_bytes",
                "resident_vector_bytes",
            )
        )
        injected.close()


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

        writes = slot.writable(rebuild=rebuild)
        assert rebuilt == [1]
        assert writes.identity.dimension == 4

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
            slot.writable(rebuild=None)

    def test_an_empty_injected_store_takes_the_provider_dimension(self) -> None:
        injected = NumpySidecarBackend().open(
            None, EmbeddingIdentity("mock", "mock-dim-3", "", "v1", 0)
        )
        slot = _slot(None, store=injected)
        writes = slot.writable(rebuild=None)
        assert writes is injected
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
            slot.writable(rebuild=lambda: pytest.fail("rebuilt"))
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
        (tmp_path / "vec.v2.json").write_text("{not json")
        slot = _slot(tmp_path / "vec")
        target = slot.rebuild_target()
        assert target.count == 0
        assert slot.current() is target
        # Moved aside, not rebuilt over (#1505 child 9).
        assert not (tmp_path / "vec.v2.json").exists()
        (copy,) = tmp_path.glob("vec.v2.json.corrupt-*")
        assert copy.read_text() == "{not json"

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


class TestLoaderCasesPorted:
    def test_a_rebuild_that_raises_is_logged_then_propagates(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "vec.v2.json").write_text("{not json")
        slot = _slot(tmp_path / "vec")

        def rebuild() -> None:
            raise RuntimeError("provider down")

        with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
            slot.get(rebuild=rebuild)
        assert any(
            r.msg.startswith("vector_store_rebuild_failed") for r in caplog.records
        )

    def test_an_empty_rebuild_is_accepted(self, tmp_path: Path) -> None:
        (tmp_path / "vec.v2.json").write_text("{not json")
        slot = _slot(tmp_path / "vec")
        store = slot.get(rebuild=slot.rebuild_target)
        assert store.count == 0

    def test_a_location_ending_in_npy_finds_its_store(self, tmp_path: Path) -> None:
        _persist_with(tmp_path / "vec", IDENTITY)
        slot = _slot(tmp_path / "vec.npy")
        assert slot.get(rebuild=None).count == 1


class _Recorder:
    """What a recording backend did, in order."""

    def __init__(self) -> None:
        self.events: list[str] = []


class _RecordingStore:
    """A numpy store whose close is recorded (and can be made to fail)."""

    def __init__(
        self, inner: Any, name: str, recorder: _Recorder, *, fail_close: bool = False
    ) -> None:
        self._inner, self.name, self._recorder, self._fail = (
            inner,
            name,
            recorder,
            fail_close,
        )

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._inner, attr)

    def close(self) -> None:
        self._recorder.events.append(f"close {self.name}")
        self._inner.close()
        if self._fail:
            raise OSError("No space left on device")


class _RecordingBackend:
    """Opens numpy stores, recording every open, fresh open and close."""

    name = "numpy"

    def __init__(self, *, fail_close: bool = False, fail_fresh: bool = False) -> None:
        self.recorder = _Recorder()
        self._real = NumpySidecarBackend()
        self._fail_close, self._fail_fresh = fail_close, fail_fresh
        self.opens = 0

    def open(self, location: Any, identity: Any, *, owns_files: bool = True) -> Any:
        self.opens += 1
        name = f"open{self.opens}"
        self.recorder.events.append(name)
        return _RecordingStore(
            self._real.open(location, identity, owns_files=owns_files),
            name,
            self.recorder,
            fail_close=self._fail_close,
        )

    def open_fresh(self, location: Any, identity: Any) -> Any:
        self.recorder.events.append("fresh")
        if self._fail_fresh:
            raise StoreUnavailableError("down", backend="numpy", location=str(location))
        return _RecordingStore(
            self._real.open_fresh(location, identity), "fresh", self.recorder
        )


def _recording_slot(tmp_path: Path, backend: _RecordingBackend) -> VectorStoreSlot:
    return VectorStoreSlot(
        backend=backend, location=tmp_path / "vec", provider=PROVIDER, text_format="v1"
    )


class TestTheSlotClosesWhatItStopsUsing:
    def test_reads_before_the_first_build_reuse_one_provisional_store(
        self, tmp_path: Path
    ) -> None:
        backend = _RecordingBackend()
        slot = _recording_slot(tmp_path, backend)
        first = slot.get(rebuild=None)
        assert slot.get(rebuild=None) is first  # reused, not reopened
        assert backend.opens == 1
        assert (
            slot.current() is None
        )  # still not installed: the writer learns the dimension

    def test_the_build_closes_the_provisional_store_it_replaces(
        self, tmp_path: Path
    ) -> None:
        backend = _RecordingBackend()
        slot = _recording_slot(tmp_path, backend)
        slot.get(rebuild=None)
        slot.writable(rebuild=None)  # an empty store of dimension 0 → fresh()
        assert backend.recorder.events == ["open1", "close open1", "fresh"]

    def test_closing_the_slot_closes_a_provisional_store(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        slot = _recording_slot(tmp_path, backend)
        slot.get(rebuild=None)
        slot.close()
        assert backend.recorder.events == ["open1", "close open1"]

    def test_fresh_closes_the_old_store_before_the_rebuild_persists(
        self, tmp_path: Path
    ) -> None:
        seeded = _slot(tmp_path / "vec")
        seeded.writable(rebuild=None).replace_document(
            doc("old.md", [[1, 0, 0]]), expected_token=ANY_TOKEN
        )
        seeded.close()
        backend = _RecordingBackend()
        slot = _recording_slot(tmp_path, backend)
        old = slot.writable(rebuild=None)  # sized and installed
        old.replace_document(
            doc("pending.md", [[0, 1, 0]]), expected_token=ANY_TOKEN
        )  # not yet persisted
        rebuilt = slot.fresh()
        assert backend.recorder.events == ["open1", "close open1", "fresh"]
        rebuilt.publish_generation(IDENTITY, [doc("new.md", [[0, 0, 1]])])
        rebuilt.checkpoint()
        slot.close()
        reopened = NumpySidecarBackend().open(tmp_path / "vec", IDENTITY)
        assert set(reopened.tokens()) == {"new.md"}  # the rebuild wins
        reopened.close()

    def test_a_rebuild_that_persists_nothing_keeps_the_previous_rows(
        self, tmp_path: Path
    ) -> None:
        seeded = _slot(tmp_path / "vec")
        seeded.writable(rebuild=None).replace_document(
            doc("old.md", [[1, 0, 0]]), expected_token=ANY_TOKEN
        )
        seeded.close()
        slot = _recording_slot(tmp_path, _RecordingBackend())
        slot.writable(rebuild=None).replace_document(
            doc("pending.md", [[0, 1, 0]]), expected_token=ANY_TOKEN
        )
        slot.fresh()  # the rebuild then writes nothing
        slot.close()
        reopened = NumpySidecarBackend().open(tmp_path / "vec", IDENTITY)
        assert set(reopened.tokens()) == {"old.md", "pending.md"}  # flushed, not lost
        reopened.close()

    def test_a_store_that_fails_to_close_is_logged_and_dropped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        backend = _RecordingBackend(fail_close=True)
        slot = _recording_slot(tmp_path, backend)
        slot.get(rebuild=None)
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers._vector_slot"
        ):
            store = slot.writable(rebuild=None)
        assert slot.current() is store  # installed anyway
        (event,) = [
            r for r in caplog.records if r.msg.startswith("vector_store_close_failed ")
        ]
        assert event.exc_info is not None

    def test_a_failed_fresh_open_leaves_the_slot_empty(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.exceptions import StoreUnavailableError

        backend = _RecordingBackend(fail_fresh=True)
        slot = _recording_slot(tmp_path, backend)
        slot.get(rebuild=None)
        with pytest.raises(StoreUnavailableError):
            slot.fresh()
        assert slot.current() is None
        assert slot.get(rebuild=None) is not None  # opens again
        assert backend.opens == 2

    def test_concurrent_first_reads_open_one_provisional_store(
        self, tmp_path: Path
    ) -> None:
        import threading

        backend = _RecordingBackend()
        slot = _recording_slot(tmp_path, backend)
        barrier = threading.Barrier(8)
        got: list[Any] = []

        def read() -> None:
            barrier.wait()
            got.append(slot.get(rebuild=None))

        threads = [threading.Thread(target=read) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert backend.opens == 1
        assert len({id(s) for s in got}) == 1

    def test_an_injected_store_is_never_closed_by_fresh(self) -> None:
        injected = NumpySidecarBackend().open(None, IDENTITY)
        slot = _slot(None, store=injected)
        slot.get(rebuild=None)
        assert slot.fresh() is injected
        injected.count  # noqa: B018 - still open
        injected.close()

    def test_a_schema_name_is_a_location(self) -> None:
        slot = VectorStoreSlot(
            backend=NumpySidecarBackend(),
            location="vault_a",
            provider=PROVIDER,
            text_format="v1",
        )
        assert slot.location == "vault_a"


class _SlowOpeningBackend(_RecordingBackend):
    """A recording backend whose open waits until it is let go."""

    def __init__(self) -> None:
        super().__init__()
        import threading

        self.opening, self.go = threading.Event(), threading.Event()

    def open(self, location: Any, identity: Any, *, owns_files: bool = True) -> Any:
        self.opening.set()
        self.go.wait(5)
        return super().open(location, identity, owns_files=owns_files)


class TestTheSlotUnderContention:
    def test_a_reader_waiting_on_an_open_gets_the_store_it_installed(
        self, tmp_path: Path
    ) -> None:
        import threading
        import time

        _persist_with(tmp_path / "vec", IDENTITY)  # a sized store: installed on open
        backend = _SlowOpeningBackend()
        slot = _recording_slot(tmp_path, backend)
        got: list[Any] = []
        first = threading.Thread(target=lambda: got.append(slot.get(rebuild=None)))
        first.start()
        assert backend.opening.wait(5)
        second = threading.Thread(target=lambda: got.append(slot.get(rebuild=None)))
        second.start()
        time.sleep(0.1)  # the second reader is now waiting on the slot's lock
        backend.go.set()
        first.join(5)
        second.join(5)
        assert backend.opens == 1
        assert len({id(s) for s in got}) == 1
        assert slot.current() is got[0]
        slot.close()

    def test_replace_closes_a_provisional_store(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        slot = _recording_slot(tmp_path, backend)
        slot.get(rebuild=None)
        other = NumpySidecarBackend().open(None, IDENTITY)
        slot.replace(other)
        assert backend.recorder.events == ["open1", "close open1"]
        assert slot.current() is other
        other.close()


class TestTheSlotAfterReview:
    def test_a_closed_slot_opens_nothing_more(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        slot = _recording_slot(tmp_path, backend)
        slot.get(rebuild=None)
        slot.close()
        with pytest.raises(RuntimeError, match="closed"):
            slot.get(rebuild=None)
        with pytest.raises(RuntimeError, match="closed"):
            slot.fresh()
        assert backend.opens == 1  # nothing reopened behind the closed slot

    def test_closing_the_slot_lets_go_of_the_installed_store(
        self, tmp_path: Path
    ) -> None:
        slot = _recording_slot(tmp_path, _RecordingBackend())
        slot.writable(rebuild=None)
        slot.close()
        assert slot.current() is None

    def test_a_failing_close_of_the_replaced_store_is_logged_and_dropped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        backend = _RecordingBackend(fail_close=True)
        slot = _recording_slot(tmp_path, backend)
        old = slot.writable(rebuild=None)  # installed through fresh()
        installed = slot.current()
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers._vector_slot"
        ):
            rebuilt = slot.fresh()
        assert rebuilt is not old
        assert slot.current() is rebuilt
        assert rebuilt is not installed
        assert [
            r for r in caplog.records if r.msg.startswith("vector_store_close_failed ")
        ]

    @pytest.mark.parametrize("search", ["resident", "streamed"])
    def test_a_sqlite_store_closed_before_its_fresh_reset_loses_nothing_it_should_keep(
        self, tmp_path: Path, search: str
    ) -> None:
        from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend

        def slot() -> VectorStoreSlot:
            return VectorStoreSlot(
                backend=SqliteVectorBackend(search=search),
                location=tmp_path / "vec",
                provider=PROVIDER,
                text_format="v1",
            )

        first = slot()
        first.writable(rebuild=None).replace_document(
            doc("old.md", [[1, 0, 0]]), expected_token=ANY_TOKEN
        )
        first.close()
        second = slot()
        second.writable(rebuild=None)  # the sized store, installed
        rebuilt = second.fresh()  # closes it before the fresh reset
        rebuilt.publish_generation(IDENTITY, [doc("new.md", [[0, 1, 0]])])
        second.close()
        reopened = SqliteVectorBackend(search=search).open(tmp_path / "vec", IDENTITY)
        try:
            assert set(reopened.tokens()) == {"new.md"}
        finally:
            reopened.close()


class TestCorruptRecovery:
    def test_a_stale_corrupt_failure_moves_nothing_aside(self, tmp_path: Path) -> None:
        # Another thread already moved the store aside and rebuilt it: a
        # failure seen before that must not rename the healthy store (#1505).
        _persist_with(tmp_path / "vec", IDENTITY)
        before = sorted(p.name for p in tmp_path.iterdir())
        slot = _slot(tmp_path / "vec")
        stale = StoreCorruptError("seen earlier", backend="numpy", location="vec")
        store = slot._rebuilt(stale, lambda: pytest.fail("rebuilt"))
        assert store.count == 1
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_concurrent_recoveries_move_the_store_aside_once(
        self, tmp_path: Path
    ) -> None:
        import threading

        (tmp_path / "vec.v2.json").write_text("{not json")
        (tmp_path / "vec.v2.m1.npy").write_bytes(b"x")
        slot = _slot(tmp_path / "vec")
        barrier = threading.Barrier(4)
        errors: list[BaseException] = []

        def recover() -> None:
            barrier.wait()
            try:
                slot.get(rebuild=slot.fresh)
            except BaseException as exc:  # collected for the assert below
                errors.append(exc)

        threads = [threading.Thread(target=recover) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert len(list(tmp_path.glob("vec.v2.json.corrupt-*"))) == 1
        assert not (tmp_path / "vec.v2.json").exists()  # nothing put back
