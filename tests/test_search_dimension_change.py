"""A search after a dimension change under one model name rebuilds (#1904).

A read opens the store at its stored dimension, so only the query vector
shows that the provider now embeds at another one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import StoreIncompatibleError
from markdown_vault_mcp.managers.embeddings import EmbeddingsManager
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.interfaces import EmbeddingStore

STORES = ["numpy", "sqlite"]


def _vault(
    tmp_path: Path,
    store: str,
    dim: int,
    *,
    vector_store: EmbeddingStore | None = None,
    **kw: object,
) -> Vault:
    root = tmp_path / "vault"
    if not root.exists():
        root.mkdir()
        (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
        (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=tmp_path / "index.db",
            embeddings_path=tmp_path / "vectors",
            embedding_store=store,
            read_only=False,
            **kw,  # type: ignore[arg-type]
        ),
        embedding_provider=MockEmbeddingProvider(dim=dim, model="fixed"),
        vector_store=vector_store,
    )


def _built_at_16(tmp_path: Path, store: str) -> None:
    vault = _vault(tmp_path, store, 16)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
    finally:
        vault.close()


def _store_files(tmp_path: Path) -> dict[str, bytes]:
    # SQLite's -shm and -wal files come and go with connections; the writer
    # checkpoints on close, so they hold no rows here.
    return {
        p.name: p.read_bytes()
        for p in tmp_path.iterdir()
        if p.is_file()
        and p.name.startswith("vectors")
        and not p.name.endswith(("-shm", "-wal"))
    }


@pytest.mark.parametrize("store", STORES)
@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_a_search_rebuilds_once_at_the_new_dimension(
    tmp_path: Path,
    store: str,
    mode: Literal["semantic", "hybrid"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _built_at_16(tmp_path, store)
    rebuilds: list[None] = []
    real = EmbeddingsManager.rebuild_after_fault

    def counting(self: EmbeddingsManager) -> int:
        rebuilds.append(None)
        return real(self)

    monkeypatch.setattr(EmbeddingsManager, "rebuild_after_fault", counting)
    vault = _vault(tmp_path, store, 32)
    try:
        vault.index.build_index()
        results = vault.reader.search("Boats and harbours.", limit=1, mode=mode)
        assert results[0].path == "beta.md"
        vault.reader.search("Apples and pears.", limit=1, mode=mode)
        assert len(rebuilds) == 1
        assert vault.index.embeddings_status()["identity"]["dimension"] == 32
    finally:
        vault.close()


@pytest.mark.parametrize("store", STORES)
def test_a_read_only_reader_refuses_another_dimension(
    tmp_path: Path, store: str
) -> None:
    _built_at_16(tmp_path, store)
    before = _store_files(tmp_path)
    assert before
    reader = _vault(tmp_path, store, 32, owns_index_files=False)
    try:
        with pytest.raises(
            StoreIncompatibleError, match=r"fixed vectors of dimension 16.*dimension 32"
        ):
            reader.reader.search("Boats", limit=1, mode="semantic")
    finally:
        reader.close()
    assert _store_files(tmp_path) == before


def test_an_injected_store_of_another_dimension_is_refused(tmp_path: Path) -> None:
    """An injected store is the caller's: never rebuilt behind its back."""
    from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
    from markdown_vault_mcp.types import EmbeddingIdentity

    first = MockEmbeddingProvider(dim=16, model="fixed")
    store = NumpySidecarBackend().open(None, EmbeddingIdentity.of(first, "v1"))
    writer = _vault(tmp_path, "numpy", 16, vector_store=store)
    writer.index.build_index()
    writer.index.build_embeddings()
    writer.close()
    count = store.count
    reader = _vault(tmp_path, "numpy", 32, vector_store=store)
    try:
        reader.index.build_index()
        with pytest.raises(
            StoreIncompatibleError, match=r"fixed vectors of dimension 16.*dimension 32"
        ):
            reader.reader.search("Boats", limit=1, mode="semantic")
    finally:
        reader.close()
    assert (store.count, store.identity.dimension) == (count, 16)


def test_a_stale_search_reads_the_store_another_search_rebuilt(
    tmp_path: Path,
) -> None:
    """Two searches that read the old store rebuild it once (#1904)."""
    _built_at_16(tmp_path, "numpy")
    vault = _vault(tmp_path, "numpy", 32)
    try:
        vault.index.build_index()
        slot = vault._vector_slot
        old = slot.get(rebuild=None)
        vault.reader.search("Boats", limit=1, mode="semantic")

        def no_rebuild() -> None:
            raise AssertionError("rebuilt twice")

        assert slot.for_query(old, 32, rebuild=no_rebuild) is slot.current()
    finally:
        vault.close()


class _StaleDimension(MockEmbeddingProvider):
    """Embeds at 32 but reports 16, as a cached dimension does after a model swap."""

    @property
    def dimension(self) -> int:
        return 16


def test_a_rebuild_that_keeps_the_dimension_is_refused(tmp_path: Path) -> None:
    """A provider that still reports the stored dimension: no raw ValueError."""
    _built_at_16(tmp_path, "numpy")
    vault = Vault(
        source_dir=tmp_path / "vault",
        settings=VaultSettings(
            index_path=tmp_path / "index.db",
            embeddings_path=tmp_path / "vectors",
            embedding_store="numpy",
            read_only=False,
        ),
        embedding_provider=_StaleDimension(dim=32, model="fixed"),
    )
    try:
        vault.index.build_index()
        with pytest.raises(StoreIncompatibleError, match="restart"):
            vault.reader.search("Boats", limit=1, mode="semantic")
    finally:
        vault.close()


def test_a_failed_rebuild_is_raised_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebuild that swapped the store and then failed is not a closed store."""
    _built_at_16(tmp_path, "numpy")
    vault = _vault(tmp_path, "numpy", 32)
    calls: list[None] = []

    def swap_then_fail(_self: EmbeddingsManager) -> int:
        calls.append(None)
        vault._vector_slot.fresh()
        raise RuntimeError("provider down")

    monkeypatch.setattr(EmbeddingsManager, "rebuild_after_fault", swap_then_fail)
    try:
        vault.index.build_index()
        with pytest.raises(RuntimeError, match="provider down"):
            vault.reader.search("Boats", limit=1, mode="semantic")
        assert len(calls) == 1
    finally:
        vault.close()
