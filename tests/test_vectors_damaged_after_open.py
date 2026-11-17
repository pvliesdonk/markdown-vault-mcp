"""A vector store found damaged after it opened is moved aside, never rebuilt over (#1881).

A SQLite store's open reads its rows but not every page, and a streamed
store keeps no rows resident, so damage can first show at a later read or
not at all until a rebuild writes into it. Either way the store is
renamed to ``.corrupt-<time>`` and rebuilt from the notes, as a store that
fails its open is.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import StoreCorruptError, StoreUnavailableError
from markdown_vault_mcp.indexing.index_writer import FlushDirtyEmbeddings
from markdown_vault_mcp.managers._vector_slot import VectorStoreSlot
from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
from markdown_vault_mcp.types import ANY_TOKEN, EmbeddingIdentity
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vector_store_contract import doc
from tests.fixtures.vectors import damage_sqlite_page, metadata_by_path

if TYPE_CHECKING:
    from pathlib import Path

_UNDECODABLE = "UPDATE chunks SET metadata = CAST(x'ff' AS TEXT)"


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    if not root.exists():
        root.mkdir()
        (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
        (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
    return root


def _vault(tmp_path: Path, **kw: object) -> Vault:
    return Vault(
        source_dir=_source(tmp_path),
        settings=VaultSettings(
            index_path=tmp_path / "index.db",
            read_only=False,
            embedding_store="sqlite",
            **kw,  # type: ignore[arg-type]
        ),
        embedding_provider=MockEmbeddingProvider(),
    )


def _db(tmp_path: Path) -> Path:
    return tmp_path / "index-db-embeddings.sqlite3"


def _damage(tmp_path: Path, statement: str = _UNDECODABLE) -> None:
    """Damage the store's rows from outside, as a disk or another tool would."""
    conn = sqlite3.connect(_db(tmp_path))
    try:
        conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _aside(tmp_path: Path) -> list[Path]:
    return sorted(tmp_path.glob("index-db-embeddings.sqlite3.corrupt-*"))


def _built(tmp_path: Path, **kw: object) -> Vault:
    vault = _vault(tmp_path, **kw)
    vault.index.build_index()
    vault.index.build_embeddings()
    return vault


class TestAForcedRebuild:
    def test_moves_a_store_damaged_after_it_opened_aside_first(
        self, tmp_path: Path
    ) -> None:
        vault = _built(tmp_path)
        try:
            _damage(tmp_path)

            vault.index.build_embeddings(force=True)

            (copy,) = _aside(tmp_path)
            assert copy.stat().st_size > 0
            store = vault._vectors
            assert store is not None
            assert sorted(metadata_by_path(store)) == ["alpha.md", "beta.md"]
        finally:
            vault.close()

    def test_keeps_a_healthy_store_in_place(self, tmp_path: Path) -> None:
        vault = _built(tmp_path)
        try:
            vault.index.build_embeddings(force=True)
            assert _aside(tmp_path) == []
        finally:
            vault.close()


class TestASearch:
    # Only a streamed store reads the database at a search; a resident one
    # serves from memory and meets the damage at a full rebuild instead.

    def test_moves_a_damaged_store_aside_rebuilds_it_and_answers(
        self, tmp_path: Path
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            _damage(tmp_path)

            results = vault.reader.search("apples", mode="semantic")

            assert {r.path for r in results} >= {"alpha.md"}
            assert len(_aside(tmp_path)) == 1
        finally:
            vault.close()

    def test_get_similar_recovers_the_same_way(self, tmp_path: Path) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            _damage(tmp_path)

            similar = vault.reader.get_similar("alpha.md")

            assert [r.path for r in similar] == ["beta.md"]
            assert len(_aside(tmp_path)) == 1
        finally:
            vault.close()

    def test_a_process_that_does_not_own_the_files_raises_and_renames_nothing(
        self, tmp_path: Path
    ) -> None:
        _built(tmp_path).close()
        reader = _vault(tmp_path, owns_index_files=False, embedding_search="streamed")
        try:
            reader.index.build_index()
            reader.reader.search("apples", mode="semantic")  # opens the store
            _damage(tmp_path)
            with pytest.raises(StoreCorruptError):
                reader.reader.search("apples", mode="semantic")
        finally:
            reader.close()
        assert _aside(tmp_path) == []


class TestAWriterJob:
    @pytest.mark.parametrize(
        ("job", "physical"),
        [
            ("reindex", False),
            ("reindex", True),
            ("flush", False),
            ("converge", False),
            ("build_index", False),
        ],
    )
    def test_writer_job_recovers_damaged_store(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        job: str,
        physical: bool,
    ) -> None:
        root = _source(tmp_path)
        if physical:
            for i in range(6):
                (root / f"note-{i}.md").write_text(f"# Note {i}\n\nOriginal.\n")
        vault = _built(tmp_path, embedding_search="streamed", embedding_batch_size=1)
        slot = vault._vector_slot
        reconciler = vault._index_mgr._embeddings._reconciler
        provider = vault._embedding_provider
        assert reconciler is not None and provider is not None
        condemn, reconcile, embed = slot.condemn, reconciler.reconcile, provider.embed
        observed: list[set[str]] = []
        calls = 0
        wasted = 0

        def observe_condemn(store: Any) -> bool:
            observed.append({row.path for row in vault._fts.search("sailboats")})
            return condemn(store)

        def observe_reconcile(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            return reconcile(*args, **kwargs)

        def observe_embed(texts: list[str]) -> list[list[float]]:
            nonlocal wasted
            if slot.has_suspect():
                wasted += len(texts)
            return embed(texts)

        try:
            if physical:
                damage_sqlite_page(_db(tmp_path), "sqlite_autoindex_documents_1")
                monkeypatch.setattr(provider, "embed", observe_embed)
                for path in root.glob("*.md"):
                    path.write_text(path.read_text() + "\nFresh sailboats.\n")
            else:
                _damage(tmp_path)
                if job in ("reindex", "build_index"):
                    (root / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
                    (root / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            if job == "reindex":
                monkeypatch.setattr(slot, "condemn", observe_condemn)
                monkeypatch.setattr(reconciler, "reconcile", observe_reconcile)
                assert vault.index.reindex().modified == (8 if physical else 2)
                assert observed == [
                    {p.name for p in root.glob("*.md")} if physical else {"beta.md"}
                ]
                assert calls == 1
                assert wasted == 0
                if not physical:
                    assert {
                        r.path for r in vault.reader.search("kumquats", mode="keyword")
                    } == {"alpha.md"}
            elif job == "flush":
                writer = vault._coordinator.writer
                writer.mark_embedding_dirty(["alpha.md"])
                writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            elif job == "converge":
                assert vault.index.build_embeddings() == 2
            else:
                assert slot.mark_suspect(vault._vectors)
                vault.index.build_index(force=True)
                assert {
                    r.path for r in vault.reader.search("sailboats", mode="keyword")
                } == {"beta.md"}
            assert vault._coordinator.wait_for_drain(timeout=5)
            assert len(_aside(tmp_path)) == 1
            assert set(metadata_by_path(vault._vectors)) == {
                p.name for p in root.glob("*.md")
            }
            assert "writer_job_failed" not in caplog.text
        finally:
            vault.close()

    def test_failed_recovery_can_be_rebuilt_explicitly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _source(tmp_path)
        (root / "gamma.md").write_text("# Gamma\n\nUnchanged blueberries.\n")
        vault = _built(tmp_path, embedding_search="streamed", embedding_batch_size=1)
        provider = vault._embedding_provider
        assert provider is not None
        original = provider.embed

        def failing(_texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider unavailable")

        try:
            _damage(tmp_path)
            monkeypatch.setattr(provider, "embed", failing)
            (root / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            assert vault.index.reindex().modified == 1
            assert len(_aside(tmp_path)) == 1
            assert not metadata_by_path(vault._vectors)
            monkeypatch.setattr(provider, "embed", original)
            vault.index.build_embeddings()
            assert set(metadata_by_path(vault._vectors)) == {
                "alpha.md",
                "beta.md",
                "gamma.md",
            }
        finally:
            vault.close()

    def test_deferred_vectors_do_not_rebuild_suspect(self, tmp_path: Path) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            _damage(tmp_path)
            assert vault._vector_slot.mark_suspect(vault._vectors)
            manager = vault._index_mgr._embeddings
            with manager.vectors_deferred():
                vault.index.reindex()
                assert not _aside(tmp_path)
            vault.index.reindex()
            assert len(_aside(tmp_path)) == 1
        finally:
            vault.close()

    @pytest.mark.parametrize("job", ["reindex", "flush"])
    def test_failed_quarantine_propagates_and_later_recovers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: str
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        backend = vault._vector_slot._backend
        original = backend.quarantine
        writer = vault._coordinator.writer

        def unavailable(_location: Any) -> None:
            raise StoreUnavailableError(
                "rename unavailable", backend="sqlite", location="fixture"
            )

        try:
            _damage(tmp_path)
            monkeypatch.setattr(backend, "quarantine", unavailable)
            if job == "reindex":
                (_source(tmp_path) / "alpha.md").write_text(
                    "# Alpha\n\nFresh kumquats.\n"
                )
                with pytest.raises(StoreUnavailableError, match="rename unavailable"):
                    vault.index.reindex()
                assert {
                    r.path for r in vault.reader.search("kumquats", mode="keyword")
                } == {"alpha.md"}
            else:
                writer.mark_embedding_dirty(["alpha.md"])
                with pytest.raises(StoreUnavailableError, match="rename unavailable"):
                    writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            monkeypatch.setattr(backend, "quarantine", original)
            if job == "reindex":
                vault.index.build_embeddings()
            else:
                writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert len(_aside(tmp_path)) == 1
            assert set(metadata_by_path(vault._vectors)) == {"alpha.md", "beta.md"}
        finally:
            vault.close()

    @pytest.mark.parametrize("job", ["purge", "build"])
    def test_count_corruption_recovers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: str
    ) -> None:
        from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorStore

        _built(tmp_path, embedding_search="streamed").close()
        vault = _vault(
            tmp_path,
            embedding_search="streamed",
            owns_index_files=True,
            exclude_patterns=["alpha.md"] if job == "purge" else [],
        )
        try:
            vault.index.build_index()
            if job == "build":
                vault.reader.search("apples", mode="semantic")
            else:
                assert vault._index_mgr._embeddings._loaded_store() is None
            original = SqliteVectorStore.count
            raised = False

            def count(instance: Any) -> int:
                nonlocal raised
                if not raised:
                    raised = True
                    raise StoreCorruptError(
                        "damaged", backend="sqlite", location="fixture"
                    )
                return original.__get__(instance, SqliteVectorStore)

            monkeypatch.setattr(SqliteVectorStore, "count", property(count))
            if job == "purge":
                (_source(tmp_path) / "beta.md").write_text(
                    "# Beta\n\nFresh sailboats.\n"
                )
                vault.index.reindex()
                assert len(_aside(tmp_path)) == 1
                assert {
                    r.path for r in vault.reader.search("sailboats", mode="keyword")
                } == {"beta.md"}
                assert set(metadata_by_path(vault._vectors)) == {"beta.md"}
            else:
                assert vault.index.build_embeddings() == 2
                assert len(_aside(tmp_path)) == 1
        finally:
            vault.close()

    @pytest.mark.parametrize(
        "case", ["read_unowned", "read_injected", "purge_count", "build_count"]
    )
    def test_unrecoverable_store_read_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        if case == "read_injected":
            provider = MockEmbeddingProvider()
            base = tmp_path / "injected"
            store = SqliteVectorBackend(search="streamed").open(
                base, EmbeddingIdentity.of(provider, "v1")
            )
            vault = Vault(
                source_dir=_source(tmp_path),
                settings=VaultSettings(
                    index_path=tmp_path / "index.db", read_only=False
                ),
                embedding_provider=provider,
                vector_store=store,
            )
            vault.index.build_index()
            vault.index.build_embeddings()
            path = base.with_suffix(".sqlite3")
        else:
            _built(tmp_path).close()
            vault = _vault(
                tmp_path,
                owns_index_files=False,
                embedding_search="streamed",
                exclude_patterns=["alpha.md"] if case == "purge_count" else [],
            )
            vault.index.build_index()
            if case != "purge_count":
                vault.reader.search("apples", mode="semantic")
            store = None
            path = _db(tmp_path)
        try:
            if case.endswith("_count"):
                from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorStore

                def bad_count(_instance: Any) -> int:
                    raise StoreCorruptError(
                        "damaged", backend="sqlite", location="fixture"
                    )

                monkeypatch.setattr(SqliteVectorStore, "count", property(bad_count))
                with pytest.raises(StoreCorruptError):
                    vault.index.reindex() if case == "purge_count" else vault.index.build_embeddings()
                assert not _aside(tmp_path)
                return
            with contextlib.closing(sqlite3.connect(path)) as conn:
                conn.execute(_UNDECODABLE)
                conn.commit()
            writer = vault._coordinator.writer
            writer.mark_embedding_dirty(["alpha.md"])
            with pytest.raises(StoreCorruptError):
                writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert not list(tmp_path.glob("*.corrupt-*"))
        finally:
            vault.close()
            if store is not None:
                store.close()


PROVIDER = MockEmbeddingProvider(dim=3)
IDENTITY = EmbeddingIdentity.of(PROVIDER, "v1")


def _slot(base: Path) -> VectorStoreSlot:
    return VectorStoreSlot(
        backend=SqliteVectorBackend(),
        location=base,
        provider=PROVIDER,
        text_format="v1",
    )


def _seeded(base: Path) -> None:
    store = SqliteVectorBackend().open(base, IDENTITY)
    store.replace_document(doc("a.md", [[1.0, 0.0, 0.0]]), expected_token=ANY_TOKEN)
    store.close()


class TestCondemn:
    def test_moves_the_installed_store_aside(self, tmp_path: Path) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = _slot(base)
        store = slot.get(rebuild=None)

        assert slot.condemn(store) is True

        assert slot.current() is None
        assert len(list(tmp_path.glob("vec.sqlite3.corrupt-*"))) == 1
        slot.close()

    def test_condemning_a_suspect_store_directly_clears_the_mark(
        self, tmp_path: Path
    ) -> None:
        # A full rebuild's verify can condemn a store a search marked first.
        base = tmp_path / "vec"
        _seeded(base)
        slot = _slot(base)
        store = slot.get(rebuild=None)
        slot._suspect = store

        assert slot.condemn(store) is True

        assert slot.condemn_suspect() is False
        slot.close()

    def test_a_store_already_replaced_is_left_alone(self, tmp_path: Path) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = _slot(base)
        installed = slot.get(rebuild=None)
        stale = SqliteVectorBackend().open(base, IDENTITY)
        try:
            assert slot.condemn(stale) is False
            assert slot.current() is installed
            assert list(tmp_path.glob("vec.sqlite3.corrupt-*")) == []
        finally:
            stale.close()
            slot.close()

    def test_no_thread_reopens_the_damaged_files_before_they_move(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        backend = SqliteVectorBackend()
        slot = VectorStoreSlot(
            backend=backend, location=base, provider=PROVIDER, text_format="v1"
        )
        store = slot.get(rebuild=None)
        opened: list[Any] = []
        rival = threading.Thread(target=lambda: opened.append(slot.get(rebuild=None)))
        real = backend.quarantine

        def quarantine(location: Any, **kw: Any) -> Any:
            # Another thread asks for the store while the files still exist:
            # it must wait for the rename, not reopen them.
            rival.start()
            rival.join(timeout=0.3)
            assert rival.is_alive()
            return real(location, **kw)

        backend.quarantine = quarantine  # type: ignore[method-assign]

        assert slot.condemn(store) is True

        rival.join(timeout=5)
        assert len(list(tmp_path.glob("vec.sqlite3.corrupt-*"))) == 1
        (reopened,) = opened
        assert reopened.count == 0  # a fresh store, not the damaged files
        slot.close()

    def test_stale_suspect_does_not_block_replacement(self, tmp_path: Path) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = _slot(base)
        try:
            store = slot.get(rebuild=None)
            assert slot.mark_suspect(store)
            slot.fresh()
            assert not slot.has_suspect()
        finally:
            slot.close()

    def test_a_slot_in_memory_condemns_nothing(self) -> None:
        slot = _slot(None)  # type: ignore[arg-type]
        store = slot.writable(rebuild=None)

        assert slot.condemn(store) is False

        assert slot.current() is store
        slot.close()

    def test_a_slot_that_does_not_own_the_files_condemns_nothing(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = VectorStoreSlot(
            backend=SqliteVectorBackend(),
            location=base,
            provider=PROVIDER,
            text_format="v1",
            owns_files=False,
        )
        store = slot.get(rebuild=None)

        assert slot.condemn(store) is False

        assert slot.current() is store
        assert list(tmp_path.glob("vec.sqlite3.corrupt-*")) == []
        slot.close()


class TestRebuiltAfterRead:
    def test_condemns_on_the_rebuild_and_returns_the_rebuilt_store(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = _slot(base)
        store = slot.get(rebuild=None)

        def rebuild() -> None:
            assert slot.condemn_suspect() is True
            slot.fresh()

        rebuilt = slot.rebuilt_after_read(store, rebuild)

        assert rebuilt is not None
        assert rebuilt is not store
        assert rebuilt.count == 0
        assert len(list(tmp_path.glob("vec.sqlite3.corrupt-*"))) == 1
        slot.close()

    def test_returns_the_store_another_thread_installed(self, tmp_path: Path) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = _slot(base)
        stale = slot.get(rebuild=None)
        replacement = slot.fresh()

        assert slot.rebuilt_after_read(stale, lambda: pytest.fail("rebuilt")) is (
            replacement
        )
        slot.close()

    def test_a_slot_that_does_not_own_the_files_returns_nothing(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = VectorStoreSlot(
            backend=SqliteVectorBackend(),
            location=base,
            provider=PROVIDER,
            text_format="v1",
            owns_files=False,
        )
        store = slot.get(rebuild=None)

        assert slot.rebuilt_after_read(store, lambda: pytest.fail("rebuilt")) is None
        slot.close()

    def test_a_rebuild_that_replaces_nothing_raises(self, tmp_path: Path) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = _slot(base)
        store = slot.get(rebuild=None)

        with pytest.raises(ValueError, match="damaged"):
            slot.rebuilt_after_read(store, lambda: None)
        slot.close()


class TestRebuildTarget:
    def test_a_damaged_store_is_condemned_and_a_fresh_one_returned(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = _slot(base)
        store = slot.get(rebuild=None)
        _damage_at(base, "UPDATE chunks SET metadata = CAST(x'ff' AS TEXT)")

        target = slot.rebuild_target()

        assert target is not store
        assert target.count == 0
        assert len(list(tmp_path.glob("vec.sqlite3.corrupt-*"))) == 1
        slot.close()

    def test_a_damaged_store_replaced_meanwhile_still_gets_a_fresh_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        slot = _slot(base)
        store = slot.get(rebuild=None)
        _damage_at(base, "UPDATE chunks SET metadata = CAST(x'ff' AS TEXT)")
        monkeypatch.setattr(slot, "condemn", lambda _store: False)

        target = slot.rebuild_target()

        assert target is not store
        slot.close()


def _damage_at(base: Path, statement: str) -> None:
    conn = sqlite3.connect(base.with_suffix(".sqlite3"))
    conn.execute(statement)
    conn.commit()
    conn.close()


class TestVerify:
    def test_a_fresh_store_has_nothing_to_check(self, tmp_path: Path) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        store = SqliteVectorBackend().open_fresh(base, IDENTITY)
        try:
            store.verify()
        finally:
            store.close()

    def test_a_closed_store_refuses(self, tmp_path: Path) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        store = SqliteVectorBackend().open(base, IDENTITY)
        store.close()
        with pytest.raises(RuntimeError, match="closed"):
            store.verify()

    def test_a_healthy_store_passes(self, tmp_path: Path) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        store = SqliteVectorBackend().open(base, IDENTITY)
        try:
            store.verify()
        finally:
            store.close()

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE chunks SET metadata = CAST(x'ff' AS TEXT)",
            "UPDATE chunks SET vector = x'00000000'",
            "UPDATE documents SET token = CAST(x'ff' AS TEXT)",
        ],
        ids=["undecodable-metadata", "short-vector", "undecodable-token"],
    )
    def test_rows_damaged_after_the_open_are_corrupt(
        self, tmp_path: Path, statement: str
    ) -> None:
        base = tmp_path / "vec"
        _seeded(base)
        store = SqliteVectorBackend(search="streamed").open(base, IDENTITY)
        conn = sqlite3.connect(base.with_suffix(".sqlite3"))
        conn.execute(statement)
        conn.commit()
        conn.close()
        try:
            with pytest.raises(StoreCorruptError):
                store.verify()
        finally:
            store.close()

    def test_pages_damaged_after_the_open_are_corrupt(self, tmp_path: Path) -> None:
        base = tmp_path / "vec"
        store = SqliteVectorBackend(search="streamed").open(base, IDENTITY)
        store.publish_generation(
            IDENTITY,
            [doc(f"n{i}.md", [[1.0, 0.0, 0.0]], pad="x" * 400) for i in range(2000)],
        )
        path = base.with_suffix(".sqlite3")
        store.checkpoint()
        size = path.stat().st_size
        with path.open("r+b") as fh:
            fh.seek(size // 2)
            fh.write(os.urandom(32 * 1024))
        try:
            with pytest.raises(StoreCorruptError):
                store.verify()
        finally:
            store.close()
