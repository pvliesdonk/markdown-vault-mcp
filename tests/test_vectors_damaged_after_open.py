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
from typing import TYPE_CHECKING

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


def _built(tmp_path: Path) -> Vault:
    vault = _vault(tmp_path)
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
    def test_moves_a_damaged_store_aside_rebuilds_it_and_answers(
        self, tmp_path: Path
    ) -> None:
        vault = _built(tmp_path)
        try:
            _damage(tmp_path)

            results = vault.reader.search("apples", mode="semantic")

            assert {r.path for r in results} >= {"alpha.md"}
            assert len(_aside(tmp_path)) == 1
        finally:
            vault.close()

    def test_a_process_that_does_not_own_the_files_raises_and_renames_nothing(
        self, tmp_path: Path
    ) -> None:
        _built(tmp_path).close()
        reader = _vault(tmp_path, owns_index_files=False)
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


class TestVerify:
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
