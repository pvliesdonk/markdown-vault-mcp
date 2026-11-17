"""The vault's embedding sites go through one reconciler (#1768)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.indexing import FlushDirtyEmbeddings
from markdown_vault_mcp.scanner import HeadingChunker
from markdown_vault_mcp.types import DocumentRows
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vault_store_contract import VaultStoreContract
from tests.fixtures.vectors import metadata_by_path

if TYPE_CHECKING:
    from pathlib import Path


class _Counting(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dim=32)
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed(texts)


def _vault(tmp_path: Path, provider: MockEmbeddingProvider) -> Vault:
    root = tmp_path / "vault"
    if not root.exists():
        root.mkdir()
        (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
        (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
    return Vault(
        source_dir=root,
        settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
        embedding_provider=provider,
    )


def _write(vault: Vault, name: str, text: str) -> None:
    (vault.source_dir / name).write_text(text)
    vault._index_mgr.process_dirty_paths({name})
    vault._index_mgr.flush_dirty_embeddings({name})


def test_backlink_rewrite_flush_batches_and_checkpoints(tmp_path, monkeypatch):
    provider = _Counting()
    vault = _vault(tmp_path, provider)
    try:
        for name in ("alpha.md", "beta.md"):
            (vault.source_dir / name).write_text(f"# {name}\n\nSee [[old]].\n")
        vault.index.build_index()
        vault.index.build_embeddings()
        for name in ("alpha.md", "beta.md"):
            path = vault.source_dir / name
            path.write_text(path.read_text().replace("[[old]]", "[[new]]"))
        paths = {"alpha.md", "beta.md"}
        vault._index_mgr.process_dirty_paths(paths)
        store = vault._vectors
        checkpoint = store.checkpoint
        checkpoints = []

        def record_checkpoint():
            checkpoints.append(True)
            return checkpoint()

        monkeypatch.setattr(store, "checkpoint", record_checkpoint)
        provider.calls = 0
        vault._index_mgr.flush_dirty_embeddings(paths)
        assert provider.calls == 1
        assert len(checkpoints) == 1
        for path in paths:
            assert "[[new]]" in store.rows_for(path)[0].metadata["content"]
    finally:
        vault.close()


def test_multi_chunk_flush_packs_boundaries_and_checkpoints(tmp_path, monkeypatch):
    class Capture(_Counting):
        def __init__(self):
            super().__init__()
            self.sizes = []

        def embed(self, texts):
            self.sizes.append(len(texts))
            return super().embed(texts)

    provider = Capture()
    root = tmp_path / "vault"
    root.mkdir()
    notes = {
        f"{c}.md": f"# {c}\n\nIntro [[old]] {c}.\n\n## One\n\nApple [[old]] {c}.\n\n## Two\n\nBoat [[old]] {c}.\n"
        for c in "abcd"
    }
    for name, text in notes.items():
        (root / name).write_text(text)
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=tmp_path / "index.db", read_only=False, embedding_batch_size=4
        ),
        embedding_provider=provider,
        chunk_strategy=HeadingChunker(short_doc_lines=0),
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        for name, text in notes.items():
            (root / name).write_text(text.replace("[[old]]", "[[new]]"))
        paths = set(notes)
        vault._index_mgr.process_dirty_paths(paths)
        store = vault._vectors
        checkpoint = store.checkpoint
        checkpoints = []

        def record():
            checkpoints.append(True)
            return checkpoint()

        monkeypatch.setattr(store, "checkpoint", record)
        provider.sizes.clear()
        vault._index_mgr.flush_dirty_embeddings(paths)
        assert provider.sizes == [4, 4, 4]
        assert len(checkpoints) == 1
        assert all(
            "[[new]]" in row.metadata["content"]
            for path in paths
            for row in store.rows_for(path)
        )
    finally:
        vault.close()


class TestTokens:
    def test_every_document_carries_its_content_hash(self, tmp_path):
        vault = _vault(tmp_path, MockEmbeddingProvider())
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            hashes = {r["path"]: r["content_hash"] for r in vault._fts.list_chunks()}
            assert vault._vectors.tokens() == hashes  # type: ignore[union-attr]
        finally:
            vault.close()

    def test_a_frontmatter_only_edit_costs_no_provider_call(self, tmp_path):
        provider = _Counting()
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            provider.calls = 0
            _write(
                vault,
                "alpha.md",
                "---\nmodified: today\n---\n# Alpha\n\nApples and pears.\n",
            )
            assert provider.calls == 0
        finally:
            vault.close()


class TestUpgrade:
    def test_a_token_less_store_is_adopted_on_the_first_boot_only(self, tmp_path):
        provider = _Counting()
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            store = vault._vectors
            store.publish_generation(  # type: ignore[union-attr]
                store.identity,  # type: ignore[union-attr]
                [
                    DocumentRows(p, None, store.rows_for(p))
                    for p in sorted(store.tokens())
                ],  # type: ignore[union-attr]
            )
        finally:
            vault.close()
        provider.calls = 0
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            assert vault.index.build_embeddings() == 0
            assert provider.calls == 0
            assert None not in vault._vectors.tokens().values()  # type: ignore[union-attr]
            generation = vault._vectors.generation  # type: ignore[union-attr]
            vault.index.build_embeddings()
            assert vault._vectors.generation == generation  # type: ignore[union-attr]
        finally:
            vault.close()


class TestParseErrorOnTheFlush:
    def test_a_note_edited_into_bad_frontmatter_keeps_its_vectors(self, tmp_path):
        vault = _vault(tmp_path, MockEmbeddingProvider())
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            _write(vault, "alpha.md", "---\ntitle: [unclosed\n---\nbody\n")
            # Kept until the note is fixed or deleted, out of search (#1505).
            assert sorted(metadata_by_path(vault._vectors)) == ["alpha.md", "beta.md"]  # type: ignore[arg-type]
        finally:
            vault.close()


class TestRetriesFromEverySite:
    def test_a_failed_inline_embed_is_retried_by_the_next_flush(self, tmp_path):
        class Flaky(MockEmbeddingProvider):
            fail = False

            def embed(self, texts: list[str]) -> list[list[float]]:
                if self.fail:
                    raise RuntimeError("provider down")
                return super().embed(texts)

        provider = Flaky()
        vault = _vault(tmp_path, provider)
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            provider.fail = True
            (vault.source_dir / "alpha.md").write_text("# Alpha\n\nQuinces.\n")
            vault.index.reindex()
            # A retry is not pending work: the vault stays drained (#1768).
            assert vault.index.is_drained()
            assert vault.index.get_index_status()["dirty_embeddings"] == 0
            provider.fail = False
            vault._coordinator.writer.submit(FlushDirtyEmbeddings()).result(5)
            rows = metadata_by_path(vault._vectors)["alpha.md"]  # type: ignore[arg-type]
            assert any("Quinces" in r["content"] for r in rows)
        finally:
            vault.close()


class TestPurgesUseObservedTokens:
    def _spy(self, store, monkeypatch):
        expected: list[Any] = []
        real = store.remove_document
        monkeypatch.setattr(
            store,
            "remove_document",
            lambda path, *, expected_token: (
                expected.append(expected_token)
                or real(path, expected_token=expected_token)
            ),
        )
        return expected

    def test_a_deleted_note(self, tmp_path, monkeypatch):
        vault = _vault(tmp_path, MockEmbeddingProvider())
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            store = vault._vectors
            token = store.tokens()["beta.md"]  # type: ignore[union-attr]
            expected = self._spy(store, monkeypatch)
            (vault.source_dir / "beta.md").unlink()
            vault.index.reindex()
            assert expected == [token]
            assert sorted(metadata_by_path(store)) == ["alpha.md"]  # type: ignore[arg-type]
        finally:
            vault.close()

    def test_a_newly_excluded_note(self, tmp_path, monkeypatch):
        vault = _vault(tmp_path, MockEmbeddingProvider())
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
        finally:
            vault.close()
        root = tmp_path / "vault"
        vault = Vault(
            source_dir=root,
            settings=VaultSettings(
                index_path=tmp_path / "index.db",
                read_only=False,
                exclude_patterns=["beta.md"],
            ),
            embedding_provider=MockEmbeddingProvider(),
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            store = vault._vectors
            token = store.tokens()["beta.md"]  # type: ignore[union-attr]
            expected = self._spy(store, monkeypatch)
            vault.index.reindex()
            assert expected == [token]
            assert sorted(metadata_by_path(store)) == ["alpha.md"]  # type: ignore[arg-type]
        finally:
            vault.close()


class TestNumpyVaultConformance(VaultStoreContract):
    @pytest.fixture
    def store_settings(self) -> dict[str, Any]:
        return {"embedding_store": "numpy"}
