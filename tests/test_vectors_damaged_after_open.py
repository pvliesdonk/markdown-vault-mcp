"""A vector store found damaged after it opened is moved aside, never rebuilt over (#1881).

A SQLite store's open reads its rows but not every page, and a streamed
store keeps no rows resident, so damage can first show at a later read or
not at all until a rebuild writes into it. Either way the store is
renamed to ``.corrupt-<time>`` and rebuilt from the notes, as a store that
fails its open is.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import ReadOnlyError, StoreCorruptError
from markdown_vault_mcp.indexing.index_writer import FlushDirtyEmbeddings
from markdown_vault_mcp.managers._vector_slot import VectorStoreSlot
from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
from markdown_vault_mcp.types import ANY_TOKEN, EmbeddingIdentity
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vector_store_contract import doc
from tests.fixtures.vectors import metadata_by_path

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
    def test_condemn_waits_for_keyword_updates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        slot = vault._vector_slot
        original = slot.condemn
        observed: list[set[str]] = []

        def condemn(store: Any) -> bool:
            observed.append({row.path for row in vault._fts.search("sailboats")})
            return original(store)

        try:
            _damage(tmp_path)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            monkeypatch.setattr(slot, "condemn", condemn)
            assert vault.index.reindex().modified == 2
            assert observed == [{"beta.md"}]
        finally:
            vault.close()

    def test_recovers_physical_damage_found_by_write(self, tmp_path: Path) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            conn = sqlite3.connect(_db(tmp_path))
            page = conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name = "
                "'sqlite_autoindex_documents_1'"
            ).fetchone()[0]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            path = _db(tmp_path)
            data = bytearray(path.read_bytes())
            size = int.from_bytes(data[16:18], "big")
            data[(page - 1) * size : page * size] = b"\xff" * size
            data[24:28] = (int.from_bytes(data[24:28], "big") + 1).to_bytes(4, "big")
            path.write_bytes(data)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            vault.index.reindex()
            assert vault._coordinator.wait_for_drain(timeout=5)
            assert len(_aside(tmp_path)) == 1
        finally:
            vault.close()

    @pytest.mark.parametrize("operation", ["remove", "generation"])
    def test_physical_damage_is_classified_on_write(
        self, tmp_path: Path, operation: str
    ) -> None:
        vault = _built(tmp_path)
        store = vault._vectors
        assert store is not None
        try:
            conn = sqlite3.connect(_db(tmp_path))
            page = conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name = 'documents'"
            ).fetchone()[0]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            path = _db(tmp_path)
            data = bytearray(path.read_bytes())
            size = int.from_bytes(data[16:18], "big")
            data[(page - 1) * size : page * size] = b"\xff" * size
            data[24:28] = (int.from_bytes(data[24:28], "big") + 1).to_bytes(4, "big")
            path.write_bytes(data)
            with pytest.raises(StoreCorruptError):
                if operation == "remove":
                    store.remove_document("alpha.md", expected_token=ANY_TOKEN)
                else:
                    store.publish_generation(
                        store.identity,
                        [doc("alpha.md", [[1] * store.identity.dimension])],
                    )
        finally:
            vault.close()

    def test_skips_later_reconciliation_after_suspect_mark(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        reconciler = vault._index_mgr._embeddings._reconciler
        assert reconciler is not None
        original = reconciler.reconcile
        calls = 0

        def reconcile(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        try:
            _damage(tmp_path)
            monkeypatch.setattr(reconciler, "reconcile", reconcile)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            assert vault.index.reindex().modified == 2
            assert calls == 1
            assert len(_aside(tmp_path)) == 1
        finally:
            vault.close()

    def test_recovery_retries_after_provider_outage(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        provider = vault._embedding_provider
        assert provider is not None
        original = provider.embed
        calls = 0

        def unavailable(_texts: list[str]) -> list[list[float]]:
            nonlocal calls
            calls += 1
            raise RuntimeError("provider unavailable")

        try:
            _damage(tmp_path)
            monkeypatch.setattr(provider, "embed", unavailable)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            vault.index.reindex()
            assert vault._coordinator.wait_for_drain(timeout=5)
            assert calls == 1
            assert vault._vector_slot.get(rebuild=None).count == 0
            assert "build_embeddings_all_batches_failed" in caplog.text
            assert {
                r.path for r in vault.reader.search("kumquats", mode="keyword")
            } == {"alpha.md"}
            assert len(_aside(tmp_path)) == 1
            monkeypatch.setattr(provider, "embed", original)
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            vault.index.reindex()
            assert {
                r.path for r in vault.reader.search("sailboats", mode="semantic")
            } >= {"beta.md"}
        finally:
            vault.close()

    def test_outage_retries_unchanged_notes_after_later_update(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (_source(tmp_path) / "gamma.md").write_text(
            "# Gamma\n\nUnchanged blueberries.\n"
        )
        vault = _built(tmp_path, embedding_search="streamed")
        provider = vault._embedding_provider
        assert provider is not None
        original = provider.embed

        def offline(_texts: list[str]) -> list[list[float]]:
            raise RuntimeError("offline")

        try:
            _damage(tmp_path)
            monkeypatch.setattr(provider, "embed", offline)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            vault.index.reindex()
            assert vault._coordinator.wait_for_drain(timeout=5)
            assert len(_aside(tmp_path)) == 1
            monkeypatch.setattr(provider, "embed", original)
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            vault.index.reindex()
            vault._coordinator.writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert set(metadata_by_path(vault._vectors)) == {
                "alpha.md",
                "beta.md",
                "gamma.md",
            }
        finally:
            vault.close()

    @pytest.mark.parametrize("failure", ["outage", "wrong_dimension"])
    def test_failed_recovery_is_retried_by_later_reindex(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        root = _source(tmp_path)
        (root / "gamma.md").write_text("# Gamma\n\nUnchanged blueberries.\n")
        vault = _built(tmp_path, embedding_search="streamed")
        provider = vault._embedding_provider
        assert provider is not None
        original = provider.embed

        def bad_vectors(texts: list[str]) -> list[list[float]]:
            if failure == "outage":
                raise RuntimeError("provider unavailable")
            return [[0.1] * (provider.dimension + 1) for _ in texts]

        try:
            _damage(tmp_path)
            monkeypatch.setattr(provider, "embed", bad_vectors)
            (root / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            assert vault.index.reindex().modified == 1
            assert len(_aside(tmp_path)) == 1
            assert vault._vectors.count == 0
            monkeypatch.setattr(provider, "embed", original)
            (root / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            assert vault.index.reindex().modified == 1
            assert set(metadata_by_path(vault._vectors)) == {
                "alpha.md",
                "beta.md",
                "gamma.md",
            }
        finally:
            vault.close()

    def test_successful_recovery_does_not_reconcile_unmodified_notes_on_next_flush(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            _damage(tmp_path)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            vault.index.reindex()
            assert len(_aside(tmp_path)) == 1
            reconciler = vault._index_mgr._embeddings._reconciler
            assert reconciler is not None
            original = reconciler.reconcile
            seen: list[set[str] | None] = []

            def observe(store: Any, paths: Any = None, **kwargs: Any) -> Any:
                seen.append(set(paths) if paths is not None else None)
                return original(store, paths, **kwargs)

            monkeypatch.setattr(reconciler, "reconcile", observe)
            writer = vault._coordinator.writer
            writer.mark_embedding_dirty(["alpha.md"])
            writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert seen == [{"alpha.md"}]
        finally:
            vault.close()

    @pytest.mark.parametrize("outage_during_rebuild", [False, True])
    def test_empty_store_publication_recovers_physical_damage(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        outage_during_rebuild: bool,
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        root = _source(tmp_path)
        provider = vault._embedding_provider
        assert provider is not None
        original = provider.embed

        def offline(_texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider unavailable")

        try:
            (root / "keep.txt").write_text("keep root nonempty")
            (root / "alpha.md").unlink()
            (root / "beta.md").unlink()
            vault.index.reindex()
            store = vault._vectors
            assert store is not None and store.count == 0
            monkeypatch.setattr(provider, "embed", offline)
            (root / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            (root / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            vault.index.reindex()
            assert store.count == 0
            monkeypatch.setattr(provider, "embed", original)
            conn = sqlite3.connect(_db(tmp_path))
            page = conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name = 'documents'"
            ).fetchone()[0]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            path = _db(tmp_path)
            data = bytearray(path.read_bytes())
            size = int.from_bytes(data[16:18], "big")
            data[(page - 1) * size : page * size] = b"\xff" * size
            data[24:28] = (int.from_bytes(data[24:28], "big") + 1).to_bytes(4, "big")
            path.write_bytes(data)
            if outage_during_rebuild:
                monkeypatch.setattr(provider, "embed", offline)
                assert vault.index.build_embeddings() == 0
                assert vault._index_mgr._embeddings._recovery_owed
                monkeypatch.setattr(provider, "embed", original)
                (root / "beta.md").write_text("# Beta\n\nEven fresher sailboats.\n")
                vault.index.reindex()
                assert not vault._index_mgr._embeddings._recovery_owed
            else:
                assert vault.index.build_embeddings() == 2
            assert len(_aside(tmp_path)) == 1
            assert set(metadata_by_path(vault._vectors)) == {"alpha.md", "beta.md"}
        finally:
            vault.close()

    def test_empty_injected_store_publication_corruption_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend

        provider = MockEmbeddingProvider()
        store = NumpySidecarBackend().open(None, EmbeddingIdentity.of(provider, "v1"))
        vault = Vault(
            source_dir=_source(tmp_path),
            settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
            embedding_provider=provider,
            vector_store=store,
        )

        def corrupt(*_args: Any, **_kwargs: Any) -> None:
            raise StoreCorruptError("damaged", backend="test", location="injected")

        try:
            vault.index.build_index()
            monkeypatch.setattr(store, "publish_generation", corrupt)
            with pytest.raises(StoreCorruptError):
                vault.index.build_embeddings()
            assert _aside(tmp_path) == []
        finally:
            vault.close()
            store.close()

    def test_empty_unowned_store_cannot_be_rebuilt(self, tmp_path: Path) -> None:
        built = _built(tmp_path, embedding_search="streamed")
        store = built._vectors
        assert store is not None
        store.remove_document("alpha.md", expected_token=ANY_TOKEN)
        store.remove_document("beta.md", expected_token=ANY_TOKEN)
        built.close()
        vault = _vault(tmp_path, owns_index_files=False, embedding_search="streamed")
        try:
            vault.index.build_index()
            vault.reader.search("apples", mode="semantic")
            conn = sqlite3.connect(_db(tmp_path))
            page = conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name = 'documents'"
            ).fetchone()[0]
            conn.close()
            path = _db(tmp_path)
            data = bytearray(path.read_bytes())
            size = int.from_bytes(data[16:18], "big")
            data[(page - 1) * size : page * size] = b"\xff" * size
            data[24:28] = (int.from_bytes(data[24:28], "big") + 1).to_bytes(4, "big")
            path.write_bytes(data)
            with pytest.raises(ReadOnlyError):
                vault.index.build_embeddings()
            assert _aside(tmp_path) == []
        finally:
            vault.close()

    def test_deferred_vectors_do_not_rebuild_owed_recovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        provider = vault._embedding_provider
        assert provider is not None
        original = provider.embed
        calls = 0

        def offline(_texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider unavailable")

        def observe(texts: list[str]) -> list[list[float]]:
            nonlocal calls
            calls += 1
            return original(texts)

        try:
            _damage(tmp_path)
            monkeypatch.setattr(provider, "embed", offline)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            vault.index.reindex()
            manager = vault._index_mgr._embeddings
            assert manager._recovery_owed
            monkeypatch.setattr(provider, "embed", observe)
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            with manager.vectors_deferred():
                vault.index.reindex()
                assert calls == 0
                assert manager._recovery_owed
            vault.index.reindex()
            assert calls > 0
            assert not manager._recovery_owed
        finally:
            vault.close()

    def test_partial_recovery_converges_on_flush_without_reembedding_saved_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (_source(tmp_path) / "gamma.md").write_text("# Gamma\n\nBlueberries.\n")
        vault = _built(tmp_path, embedding_search="streamed", embedding_batch_size=1)
        provider = vault._embedding_provider
        assert provider is not None
        original = provider.embed
        calls = 0
        retried: list[str] = []

        def partial(texts: list[str]) -> list[list[float]]:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("second batch unavailable")
            return original(texts)

        def observe(texts: list[str]) -> list[list[float]]:
            retried.extend(texts)
            return original(texts)

        try:
            _damage(tmp_path)
            monkeypatch.setattr(provider, "embed", partial)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            vault.index.reindex()
            assert vault._index_mgr._embeddings._recovery_owed
            assert len(metadata_by_path(vault._vectors)) == 2
            monkeypatch.setattr(provider, "embed", observe)
            writer = vault._coordinator.writer
            writer.mark_embedding_dirty(["alpha.md"])
            writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert set(metadata_by_path(vault._vectors)) == {
                "alpha.md",
                "beta.md",
                "gamma.md",
            }
            assert len(retried) == 1
            assert not vault._index_mgr._embeddings._recovery_owed
        finally:
            vault.close()

    def test_conflict_keeps_recovery_owed_until_complete_convergence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.indexing.embedding_reconciler import ReconcileResult

        vault = _built(tmp_path, embedding_search="streamed")
        manager = vault._index_mgr._embeddings
        reconciler = manager._reconciler
        store = vault._vectors
        assert reconciler is not None and store is not None
        original = reconciler.reconcile
        try:
            manager._recovery_owed = True
            monkeypatch.setattr(
                reconciler,
                "reconcile",
                lambda *_args, **_kw: ReconcileResult(conflicts={"alpha.md"}),
            )
            manager._converge_embeddings(store)
            assert manager._recovery_owed
            monkeypatch.setattr(reconciler, "reconcile", original)
            manager._converge_embeddings(store)
            assert not manager._recovery_owed
        finally:
            vault.close()

    def test_authoritative_empty_publication_clears_recovery_owed(
        self, tmp_path: Path
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        root = _source(tmp_path)
        try:
            (root / "keep.txt").write_text("keep root nonempty")
            (root / "alpha.md").unlink()
            (root / "beta.md").unlink()
            vault.index.reindex()
            manager = vault._index_mgr._embeddings
            manager._recovery_owed = True
            assert vault.index.build_embeddings() == 0
            assert not manager._recovery_owed
        finally:
            vault.close()

    def test_failed_quarantine_keyword_job_warns_and_later_retries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        backend = vault._vector_slot._backend
        original = backend.quarantine
        attempts = 0
        provider = vault._embedding_provider
        assert provider is not None
        original_embed = provider.embed
        provider_calls = 0

        def offline(_texts: list[str]) -> list[list[float]]:
            nonlocal provider_calls
            provider_calls += 1
            raise RuntimeError("provider unavailable")

        def unavailable(_location: Any) -> None:
            nonlocal attempts
            attempts += 1
            raise OSError("rename unavailable")

        try:
            _damage(tmp_path)
            monkeypatch.setattr(backend, "quarantine", unavailable)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            assert vault.index.reindex().modified == 1
            assert attempts == 1
            assert vault._index_mgr._embeddings._recovery_owed
            assert "vector_store_recovery_failed" in caplog.text
            assert {
                r.path for r in vault.reader.search("kumquats", mode="keyword")
            } == {"alpha.md"}
            monkeypatch.setattr(backend, "quarantine", original)
            monkeypatch.setattr(provider, "embed", offline)
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            vault.index.reindex()
            assert len(_aside(tmp_path)) == 1
            assert provider_calls == 1
            assert vault._index_mgr._embeddings._recovery_owed
            monkeypatch.setattr(provider, "embed", original_embed)
            (_source(tmp_path) / "alpha.md").write_text(
                "# Alpha\n\nEven fresher kumquats.\n"
            )
            vault.index.reindex()
            assert set(metadata_by_path(vault._vectors)) == {"alpha.md", "beta.md"}
            assert not vault._index_mgr._embeddings._recovery_owed
        finally:
            vault.close()

    def test_failed_quarantine_flush_raises_and_restores_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        backend = vault._vector_slot._backend
        original = backend.quarantine
        writer = vault._coordinator.writer

        def unavailable(_location: Any) -> None:
            raise OSError("rename unavailable")

        try:
            _damage(tmp_path)
            monkeypatch.setattr(backend, "quarantine", unavailable)
            writer.mark_embedding_dirty(["alpha.md"])
            with pytest.raises(OSError, match="rename unavailable"):
                writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert vault._index_mgr._embeddings._recovery_owed
            monkeypatch.setattr(backend, "quarantine", original)
            writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert len(_aside(tmp_path)) == 1
            assert set(metadata_by_path(vault._vectors)) == {"alpha.md", "beta.md"}
            assert not vault._index_mgr._embeddings._recovery_owed
        finally:
            vault.close()

    @pytest.mark.parametrize("owned", [True, False])
    def test_lazy_purge_count_corruption(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned: bool
    ) -> None:
        from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorStore

        _built(tmp_path, embedding_search="streamed").close()
        vault = _vault(
            tmp_path,
            embedding_search="streamed",
            owns_index_files=owned,
            exclude_patterns=["alpha.md"],
        )
        try:
            vault.index.build_index()
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
            if not owned:
                with pytest.raises(StoreCorruptError):
                    vault.index.reindex()
                assert not _aside(tmp_path)
                return
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            vault.index.reindex()
            assert len(_aside(tmp_path)) == 1
            assert {
                r.path for r in vault.reader.search("sailboats", mode="keyword")
            } == {"beta.md"}
            assert set(metadata_by_path(vault._vectors)) == {"beta.md"}
        finally:
            vault.close()

    def test_build_index_finishes_a_suspect_recovery(self, tmp_path: Path) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            store = vault._vectors
            assert store is not None
            _damage(tmp_path)
            assert vault._vector_slot.mark_suspect(store)
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            vault.index.build_index(force=True)
            assert len(_aside(tmp_path)) == 1
            assert {
                r.path for r in vault.reader.search("sailboats", mode="keyword")
            } == {"beta.md"}
            assert set(metadata_by_path(vault._vectors)) == {"alpha.md", "beta.md"}
        finally:
            vault.close()

    def test_injected_corrupt_writes_keep_keyword_updates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend

        provider = MockEmbeddingProvider()
        store = NumpySidecarBackend().open(None, EmbeddingIdentity.of(provider, "v1"))
        vault = Vault(
            source_dir=_source(tmp_path),
            settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
            embedding_provider=provider,
            vector_store=store,
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()

            def corrupt(*_args: Any, **_kwargs: Any) -> None:
                raise StoreCorruptError("damaged", backend="test", location="injected")

            monkeypatch.setattr(store, "replace_document", corrupt)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")
            assert vault.index.reindex().modified == 2
            assert {
                r.path for r in vault.reader.search("sailboats", mode="keyword")
            } == {"beta.md"}
            assert _aside(tmp_path) == []
        finally:
            vault.close()
            store.close()

    def test_reindex_recovers_and_indexes_both_edits(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            _damage(tmp_path)
            (_source(tmp_path) / "alpha.md").write_text("# Alpha\n\nFresh kumquats.\n")
            (_source(tmp_path) / "beta.md").write_text("# Beta\n\nFresh sailboats.\n")

            result = vault.index.reindex()
            assert vault._coordinator.wait_for_drain(timeout=5)

            assert result.modified == 2
            assert len(_aside(tmp_path)) == 1
            assert {
                r.path for r in vault.reader.search("kumquats", mode="keyword")
            } == {"alpha.md"}
            assert {
                r.path for r in vault.reader.search("sailboats", mode="keyword")
            } == {"beta.md"}
            assert {
                r.path for r in vault.reader.search("kumquats", mode="semantic")
            } >= {"alpha.md"}
            assert "writer_job_failed" not in caplog.text
        finally:
            vault.close()

    def test_flush_recovers(self, tmp_path: Path) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            _damage(tmp_path)
            writer = vault._coordinator.writer
            writer.mark_embedding_dirty(["alpha.md"])
            writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert vault._coordinator.wait_for_drain(timeout=5)
            assert len(_aside(tmp_path)) == 1
            assert sorted(metadata_by_path(vault._vectors)) == ["alpha.md", "beta.md"]
        finally:
            vault.close()

    def test_convergence_recovers(self, tmp_path: Path) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            _damage(tmp_path, "UPDATE documents SET token = CAST(x'ff' AS TEXT)")
            assert vault.index.build_embeddings() == 2
            assert len(_aside(tmp_path)) == 1
        finally:
            vault.close()

    def test_convergence_recovers_corrupt_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _built(tmp_path, embedding_search="streamed")
        try:
            store = vault._vectors
            assert store is not None
            cls = type(store)
            original = cls.count

            def count(instance: Any) -> int:
                if instance is store:
                    raise StoreCorruptError(
                        "damaged", backend="sqlite", location="test"
                    )
                return original.__get__(instance, cls)

            monkeypatch.setattr(cls, "count", property(count))
            assert vault.index.build_embeddings() == 2
            assert len(_aside(tmp_path)) == 1
        finally:
            vault.close()

    def test_unowned_corrupt_count_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _built(tmp_path).close()
        vault = _vault(tmp_path, owns_index_files=False, embedding_search="streamed")
        try:
            vault.index.build_index()
            vault.reader.search("apples", mode="semantic")
            store = vault._vectors
            assert store is not None

            def count(_instance: Any) -> int:
                raise StoreCorruptError("damaged", backend="sqlite", location="test")

            monkeypatch.setattr(type(store), "count", property(count))
            with pytest.raises(StoreCorruptError):
                vault.index.build_embeddings()
            assert _aside(tmp_path) == []
        finally:
            vault.close()

    def test_unowed_fault_rebuild_does_not_reclassify_corrupt_open(
        self, tmp_path: Path
    ) -> None:
        _built(tmp_path, embedding_search="streamed").close()
        _damage(tmp_path)
        vault = _vault(tmp_path, embedding_search="streamed")
        try:
            with pytest.raises(StoreCorruptError):
                vault._index_mgr._embeddings.rebuild_after_fault()
            assert not _aside(tmp_path)
        finally:
            vault.close()

    def test_unowned_store_raises(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from markdown_vault_mcp.indexing import index_writer

        logged = threading.Event()
        original_log = index_writer._log_job_failure

        def log_failure(kind: str, exc: Exception) -> None:
            original_log(kind, exc)
            logged.set()

        monkeypatch.setattr(index_writer, "_log_job_failure", log_failure)
        _built(tmp_path).close()
        vault = _vault(tmp_path, owns_index_files=False, embedding_search="streamed")
        try:
            vault.index.build_index()
            vault.reader.search("apples", mode="semantic")
            _damage(tmp_path)
            writer = vault._coordinator.writer
            writer.mark_embedding_dirty(["alpha.md"])
            with pytest.raises(StoreCorruptError):
                writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert logged.wait(timeout=5)
            assert _aside(tmp_path) == []
            assert any(
                record.levelno == logging.ERROR
                and "writer_job_failed" in record.message
                and record.exc_info is not None
                for record in caplog.records
            )
        finally:
            vault.close()

    def test_injected_store_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = MockEmbeddingProvider()
        from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend

        store = NumpySidecarBackend().open(None, EmbeddingIdentity.of(provider, "v1"))
        vault = Vault(
            source_dir=_source(tmp_path),
            settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
            embedding_provider=provider,
            vector_store=store,
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()

            def corrupt() -> dict[str, str]:
                raise StoreCorruptError("damaged", backend="test", location="injected")

            monkeypatch.setattr(store, "tokens", corrupt)
            writer = vault._coordinator.writer
            writer.mark_embedding_dirty(["alpha.md"])
            with pytest.raises(StoreCorruptError):
                writer.submit(FlushDirtyEmbeddings()).result(timeout=5)
            assert _aside(tmp_path) == []
        finally:
            vault.close()
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
            replacement = slot.fresh()
            assert not slot.has_suspect()
            replacement.replace_document(
                doc("new.md", [[1, 0, 0]]), expected_token=None
            )
            assert replacement.tokens() == {"new.md": "t1"}
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
