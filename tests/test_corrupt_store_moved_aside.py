"""A corrupt built-in store is moved aside, then rebuilt (#1505 child 9)."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import StoreCorruptError, StoreUnavailableError
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.sidecar_v5_1.notes import NOTES as RELEASED_NOTES

FIXTURE = Path(__file__).parent / "fixtures" / "sidecar_v5_1"
NOTES = {
    "a.md": "# Alpha\n\nApples and pears.\n",
    "b.md": "# Beta\n\nBoats in the harbour.\n",
}


class _Counting(MockEmbeddingProvider):
    def __init__(self, dim: int = 32) -> None:
        super().__init__(dim)
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed(texts)


def _vault(tmp_path: Path, store: str, provider: Any = None, **settings: Any) -> Vault:
    return Vault(
        source_dir=tmp_path / "vault",
        settings=VaultSettings(
            index_path=tmp_path / "index.db",
            read_only=False,
            embedding_store=store,
            **settings,
        ),
        embedding_provider=provider or _Counting(),
    )


def _build(
    tmp_path: Path, store: str, notes: dict[str, str] = NOTES, **kw: Any
) -> None:
    root = tmp_path / "vault"
    for name, text in notes.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    vault = _vault(tmp_path, store, **kw)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
    finally:
        vault.close()


def _corrupt(folder: Path, store: str) -> bytes:
    """Damage the store's main file; return the damaged bytes."""
    if store == "numpy":
        target, damaged = folder / "index-db-embeddings.v2.json", b"{not json"
    else:
        target = folder / "index-db-embeddings.sqlite3"
        damaged = b"garbage!" * 64 + target.read_bytes()[512:]
    target.write_bytes(damaged)
    return damaged


def _aside(folder: Path) -> list[Path]:
    return sorted(folder.glob("index-db-embeddings*.corrupt-*"))


def _moved_lines(caplog: pytest.LogCaptureFixture) -> list[Any]:
    return [r for r in caplog.records if r.msg.startswith("vector_store_moved_aside")]


@pytest.mark.parametrize("store", ["numpy", "sqlite"])
def test_a_corrupt_store_is_moved_aside_and_rebuilt(
    tmp_path: Path, store: str, caplog: pytest.LogCaptureFixture
) -> None:
    _build(tmp_path, store)
    damaged = _corrupt(tmp_path, store)
    vault = _vault(tmp_path, store)
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            vault.index.build_index()
            vault.index.build_embeddings()
        hits = vault.reader.search("harbour boats", mode="semantic")
        assert hits
        assert vault._vectors is not None
        assert set(vault._vectors.tokens()) == set(NOTES)
    finally:
        vault.close()
    main = "v2.json" if store == "numpy" else "sqlite3"
    (copy,) = [p for p in _aside(tmp_path) if f".{main}.corrupt-" in p.name]
    assert copy.read_bytes() == damaged
    assert len(_moved_lines(caplog)) == 1


def test_a_corrupt_sqlite_store_is_moved_aside_with_its_wal(tmp_path: Path) -> None:
    # SQLite discards a WAL it cannot use while the open fails, so the files
    # are laid out by hand behind an open that reports them corrupt.
    from markdown_vault_mcp.managers._vector_slot import VectorStoreSlot
    from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend

    class _Corrupt(SqliteVectorBackend):
        def open(self, location: Any, identity: Any = None, **kw: Any) -> Any:
            if (tmp_path / "vec.sqlite3").exists():
                raise StoreCorruptError("damaged", backend="sqlite", location="vec")
            return super().open(location, identity, **kw)

    for name in ("vec.sqlite3", "vec.sqlite3-wal", "vec.sqlite3-shm"):
        (tmp_path / name).write_bytes(name.encode())
    slot = VectorStoreSlot(
        backend=_Corrupt(),
        location=tmp_path / "vec",
        provider=_Counting(),
        text_format="v1",
    )
    try:
        assert slot.get(rebuild=slot.fresh).count == 0
    finally:
        slot.close()
    moved = sorted(tmp_path.glob("vec*.corrupt-*"))
    assert {p.name.split(".corrupt-")[0] for p in moved} == {
        "vec.sqlite3",
        "vec.sqlite3-wal",
        "vec.sqlite3-shm",
    }
    assert len({p.name.split(".corrupt-")[1] for p in moved}) == 1  # one timestamp
    for copy in moved:  # each file kept its bytes
        assert copy.read_bytes() == copy.name.split(".corrupt-")[0].encode()


@pytest.mark.parametrize("store", ["numpy", "sqlite"])
def test_a_forced_rebuild_moves_a_corrupt_store_aside(
    tmp_path: Path, store: str
) -> None:
    _build(tmp_path, store)
    damaged = _corrupt(tmp_path, store)
    vault = _vault(tmp_path, store)
    try:
        vault.index.build_index()
        vault.index.build_embeddings(force=True)
        assert vault._vectors is not None
        assert set(vault._vectors.tokens()) == set(NOTES)
    finally:
        vault.close()
    assert damaged in [p.read_bytes() for p in _aside(tmp_path)]


@pytest.mark.parametrize("store", ["numpy", "sqlite"])
def test_a_released_sidecar_donates_to_the_corrupt_rebuild(
    tmp_path: Path, store: str
) -> None:
    for suffix in (".json", ".npy"):
        shutil.copy(
            FIXTURE / f"index-db-embeddings{suffix}",
            tmp_path / f"index-db-embeddings{suffix}",
        )
    _build(tmp_path, store, RELEASED_NOTES)
    _corrupt(tmp_path, store)
    provider = _Counting()
    vault = _vault(tmp_path, store, provider)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert vault._vectors is not None
        assert vault._vectors.count > 0
    finally:
        vault.close()
    assert provider.texts == []
    assert _aside(tmp_path)
    for suffix in (".json", ".npy"):  # the released files are left alone
        assert (tmp_path / f"index-db-embeddings{suffix}").read_bytes() == (
            FIXTURE / f"index-db-embeddings{suffix}"
        ).read_bytes()


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions"
)
@pytest.mark.parametrize("store", ["numpy", "sqlite"])
def test_a_store_that_cannot_be_moved_is_unavailable(
    tmp_path: Path, store: str
) -> None:
    folder = tmp_path / "vectors"
    folder.mkdir()
    base = folder / "index-db-embeddings"
    _build(tmp_path, store, embeddings_path=base)
    damaged = _corrupt(folder, store)
    before = sorted(p.name for p in folder.iterdir())
    folder.chmod(0o555)
    vault = _vault(tmp_path, store, embeddings_path=base)
    try:
        vault.index.build_index()
        with pytest.raises(StoreUnavailableError):
            vault.index.build_embeddings()
    finally:
        vault.close()
        folder.chmod(0o755)
    assert sorted(p.name for p in folder.iterdir()) == before
    main = "v2.json" if store == "numpy" else "sqlite3"
    assert (folder / f"index-db-embeddings.{main}").read_bytes() == damaged


@pytest.mark.parametrize("store", ["numpy", "sqlite"])
def test_a_reader_never_moves_a_store_aside(tmp_path: Path, store: str) -> None:
    _build(tmp_path, store)
    _corrupt(tmp_path, store)
    before = sorted(tmp_path.glob("index-db-embeddings*"))
    vault = _vault(tmp_path, store, owns_index_files=False)
    try:
        with pytest.raises(StoreCorruptError):
            vault.reader.search("harbour boats", mode="semantic")
    finally:
        vault.close()
    assert _aside(tmp_path) == []
    assert sorted(tmp_path.glob("index-db-embeddings*")) == before


@pytest.mark.parametrize("store", ["numpy", "sqlite"])
def test_another_identity_still_rebuilds_from_scratch(
    tmp_path: Path, store: str
) -> None:
    _build(tmp_path, store)
    provider = _Counting(dim=16)  # another dimension: another identity
    vault = _vault(tmp_path, store, provider)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
    finally:
        vault.close()
    assert len(provider.texts) >= len(NOTES)
    assert _aside(tmp_path) == []


class _SameName(_Counting):
    """One model name at any dimension, as an endpoint swap can produce."""

    @property
    def model_name(self) -> str:
        return "same-name"


@pytest.mark.parametrize("store", ["numpy", "sqlite"])
def test_a_dimension_change_under_the_same_model_rebuilds(
    tmp_path: Path, store: str
) -> None:
    # The store opens at its stored dimension, so only writing finds it is
    # another identity; the rebuild must not take that path again (#1862 review).
    _build(tmp_path, store, provider=_SameName(dim=16))
    provider = _SameName(dim=32)
    vault = _vault(tmp_path, store, provider)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert vault.index.embeddings_status()["identity"]["dimension"] == 32
    finally:
        vault.close()
    assert len(provider.texts) >= len(NOTES)
    assert _aside(tmp_path) == []


@pytest.mark.parametrize("store", ["numpy", "sqlite"])
def test_a_search_first_corrupt_rebuild_uses_the_released_sidecar(
    tmp_path: Path, store: str
) -> None:
    for suffix in (".json", ".npy"):
        shutil.copy(
            FIXTURE / f"index-db-embeddings{suffix}",
            tmp_path / f"index-db-embeddings{suffix}",
        )
    _build(tmp_path, store, RELEASED_NOTES)
    _corrupt(tmp_path, store)
    provider = _Counting()
    vault = _vault(tmp_path, store, provider)
    try:
        vault.index.build_index()
        assert vault.reader.search("tides", mode="semantic")  # recovers the store
    finally:
        vault.close()
    assert [t for t in provider.texts if t != "tides"] == []  # only the query
    assert _aside(tmp_path)


def test_a_damaged_sqlite_store_of_another_model_is_moved_aside_not_deleted(
    tmp_path: Path,
) -> None:
    # The open reads its identity before it meets the damage, so it is
    # refused as another model and rebuilt through a fresh open (#1862 review).
    _build(tmp_path, "sqlite")
    target = tmp_path / "index-db-embeddings.sqlite3"
    data = bytearray(target.read_bytes())
    middle = len(data) // 2
    data[middle : middle + 4096] = os.urandom(4096)
    target.write_bytes(bytes(data))
    damaged = target.read_bytes()
    vault = _vault(tmp_path, "sqlite", _Counting(dim=16))  # another identity
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
    finally:
        vault.close()
    copies = [p for p in _aside(tmp_path) if ".sqlite3.corrupt-" in p.name]
    assert [p.read_bytes() for p in copies] == [damaged]
