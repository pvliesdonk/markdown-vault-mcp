"""The vault's embedding sites go through one reconciler (#1768)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.indexing import FlushDirtyEmbeddings
from markdown_vault_mcp.types import DocumentRows
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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


class TestConflictRequeue:
    def test_a_conflicting_document_is_requeued_and_lands_on_the_next_flush(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        vault = _vault(tmp_path, MockEmbeddingProvider())
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            store = vault._vectors
            real = store.replace_document  # type: ignore[union-attr]
            seen: list[str] = []

            def once(document: DocumentRows, *, expected_token: Any) -> str:
                if document.path == "alpha.md" and not seen:
                    seen.append("alpha.md")
                    return "conflict"
                return real(document, expected_token=expected_token)

            monkeypatch.setattr(store, "replace_document", once)
            (vault.source_dir / "alpha.md").write_text("# Alpha\n\nQuinces.\n")
            vault.index.reindex()
            vault.index.wait_for_drain(timeout=10)
            rows = metadata_by_path(store)["alpha.md"]  # type: ignore[arg-type]
            assert any("Quinces" in r["content"] for r in rows)
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


class TestAnUnreadableSource:
    def test_a_forced_rebuild_over_it_keeps_the_vectors(self, tmp_path):
        # The default state file lives under source_dir; keep it outside so
        # chmod(0) on the root does not fail the tracker's write instead.
        root = tmp_path / "vault"
        root.mkdir()
        (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
        vault = Vault(
            source_dir=root,
            settings=VaultSettings(
                index_path=tmp_path / "index.db",
                state_path=tmp_path / "state.json",
                read_only=False,
            ),
            embedding_provider=MockEmbeddingProvider(),
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            before = sorted(metadata_by_path(vault._vectors))  # type: ignore[arg-type]
            root.chmod(0)
            try:
                vault.index.build_index(force=True)
                assert not vault._fts.is_build_walk_complete()
                vault.index.build_embeddings(force=True)
            finally:
                root.chmod(0o755)
            assert sorted(metadata_by_path(vault._vectors)) == before  # type: ignore[arg-type]
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
