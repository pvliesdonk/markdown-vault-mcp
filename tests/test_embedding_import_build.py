"""A full build previewed and run as an import (#1505 child 6b)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import EmbeddingsNotPublishedError
from markdown_vault_mcp.indexing.donor import Donor
from markdown_vault_mcp.indexing.embedding_reconciler import RebuildPreview
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.types import ANY_TOKEN
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.sidecar_v5_1.notes import NOTES
from tests.fixtures.vector_store_contract import doc

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _Counting(MockEmbeddingProvider):
    """``mock/mock-dim-32``, counting embedded texts; fails while ``down``."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []
        self.down = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.down:
            raise RuntimeError("provider down")
        self.texts.extend(texts)
        return super().embed(texts)


def _vault(tmp_path: Path, store: str, provider: Any) -> Vault:
    return Vault(
        source_dir=tmp_path / "vault",
        settings=VaultSettings(
            index_path=tmp_path / "index.db", read_only=False, embedding_store=store
        ),
        embedding_provider=provider,
    )


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A numpy store holding every chunk of the notes; returns its base."""
    root = tmp_path / "vault"
    for name, text in NOTES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    vault = _vault(tmp_path, "numpy", _Counting())
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
    finally:
        vault.close()
    return tmp_path / "index-db-embeddings"


@pytest.fixture
def provider() -> _Counting:
    return _Counting()


@pytest.fixture
def destination(tmp_path: Path, source: Path, provider: _Counting) -> Iterator[Any]:
    """The SQLite vault's embeddings manager, its keyword index built."""
    del source
    vault = _vault(tmp_path, "sqlite", provider)
    try:
        vault.index.build_index()
        yield vault._index_mgr._embeddings
    finally:
        vault.close()


def _donor(manager: Any, base: Path) -> Donor:
    opened = Donor.open(
        NumpySidecarBackend(),
        base,
        manager._slot.identity,
        builder=manager._embed_builder,
    )
    assert isinstance(opened, Donor)
    return opened


def test_preview_counts_what_the_build_does(
    tmp_path: Path, source: Path, provider: _Counting
) -> None:
    (tmp_path / "vault" / "alpha.md").write_text(NOTES["alpha.md"] + "\nEdited.\n")
    vault = _vault(tmp_path, "sqlite", provider)
    try:
        vault.index.build_index()
        vault.index.reindex()  # picks the edit up; no store is open yet
        manager = vault._index_mgr._embeddings
        with _donor(manager, source) as donor:
            preview = manager.preview_import(donor)
        assert preview == RebuildPreview(chunks=3, reusable=2, to_embed=1)
        assert provider.texts == []  # a preview requests no embedding
        with _donor(manager, source) as donor:
            outcome = manager.import_from(donor)
        assert (outcome.reused, outcome.embedded, outcome.published) == (2, 1, True)
        assert len(provider.texts) == 1
    finally:
        vault.close()


def test_a_repeated_import_embeds_nothing(
    destination: Any, source: Path, provider: _Counting
) -> None:
    with _donor(destination, source) as donor:
        first = destination.import_from(donor)
    with _donor(destination, source) as donor:
        again = destination.import_from(donor)
    assert (first.embedded, again.embedded, again.reused) == (0, 0, 3)
    assert destination.import_target().count == 3  # no duplicates
    assert provider.texts == []


def test_an_import_over_a_populated_store_replaces_it(
    destination: Any, source: Path
) -> None:
    target = destination.import_target()
    # A note the vault no longer has, left by an older build.
    target.replace_document(
        doc("gone.md", [[1.0] * 32], token="t-gone"), expected_token=ANY_TOKEN
    )
    with _donor(destination, source) as donor:
        destination.import_from(donor)
    assert set(destination.import_target().tokens()) == set(NOTES)


def test_a_failed_batch_keeps_a_populated_destination(
    tmp_path: Path, source: Path, provider: _Counting
) -> None:
    vault = _vault(tmp_path, "sqlite", provider)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        manager = vault._index_mgr._embeddings
        (tmp_path / "vault" / "alpha.md").write_text(NOTES["alpha.md"] + "\nEdited.\n")
        provider.down = True  # from here on, so the edit is never embedded
        vault.index.reindex()
        before = dict(manager.import_target().tokens())
        with (
            _donor(manager, source) as donor,
            pytest.raises(EmbeddingsNotPublishedError),
        ):
            manager.import_from(donor)
        assert dict(manager.import_target().tokens()) == before
    finally:
        vault.close()


def test_preview_of_a_destination_of_another_model_counts_no_own_rows(
    tmp_path: Path, source: Path
) -> None:
    other = _vault(tmp_path, "sqlite", MockEmbeddingProvider(dim=8))
    try:
        other.index.build_index()
        other.index.build_embeddings()  # the destination holds mock-dim-8 rows
    finally:
        other.close()
    vault = _vault(tmp_path, "sqlite", _Counting())
    try:
        vault.index.build_index()
        manager = vault._index_mgr._embeddings
        with _donor(manager, source) as donor:
            assert manager.preview_import(donor) == RebuildPreview(
                chunks=3, reusable=3, to_embed=0
            )
    finally:
        vault.close()
