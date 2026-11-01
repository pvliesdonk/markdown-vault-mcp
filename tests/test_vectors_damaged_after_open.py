"""A vector store found damaged after it opened is moved aside, never rebuilt over (#1881).

A SQLite store's open reads its rows but not every page, and a streamed
store keeps no rows resident, so damage can first show at a later read or
not at all until a rebuild writes into it. Either way the store is
renamed to ``.corrupt-<time>`` and rebuilt from the notes, as a store that
fails its open is.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import StoreCorruptError
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

        assert rebuilt is not None and rebuilt is not store
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

        assert target is not store and target.count == 0
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
