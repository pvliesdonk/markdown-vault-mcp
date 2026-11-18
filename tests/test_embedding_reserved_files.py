"""Generated OKF indexes remain keyword searchable without vector requests."""

from pathlib import Path

from markdown_vault_mcp.okf_capabilities import OkfCapabilities
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path


class CountingProvider(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed(texts)


def test_generated_index_is_excluded_from_cold_build(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "authored").mkdir()
    for name, text in {
        "generated/index.md": "# Generated\n\nuniquegeneratedterm\n",
        "generated/log.md": "# Log\n\ncurated history\n",
        "generated/note.md": "# Note\n\nconcept\n",
        "authored/index.md": "# Authored\n\npersonal meaning\n",
    }.items():
        (root / name).write_text(text, encoding="utf-8")
    provider = CountingProvider()
    capabilities = OkfCapabilities(False, "elicit", True, "off", ("generated",), False)
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False, okf_mode="on", okf_capabilities=capabilities
        ),
        embedding_provider=provider,
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        store = vault._index_mgr._embeddings._loaded_store()
        assert store is not None
        assert set(metadata_by_path(store)) == {
            "generated/log.md",
            "generated/note.md",
            "authored/index.md",
        }
        assert not any("uniquegeneratedterm" in text for text in provider.texts)
        assert any(
            r.path == "generated/index.md"
            for r in vault.reader.search("uniquegeneratedterm", mode="keyword")
        )
    finally:
        vault.close()


def _capabilities(
    index: bool, folders: tuple[str, ...] = ("generated",)
) -> OkfCapabilities:
    return OkfCapabilities(False, "elicit", index, "off", folders, False)


def _persistent_vault(
    root: Path, tmp_path: Path, provider: CountingProvider, *, enabled: bool
) -> Vault:
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=_capabilities(enabled),
            index_path=tmp_path / "index.db",
            state_path=tmp_path / "state.json",
            embeddings_path=tmp_path / "vectors",
        ),
        embedding_provider=provider,
    )


def _paths(vault: Vault) -> set[str]:
    store = vault._index_mgr._embeddings._loaded_store()
    assert store is not None
    return set(metadata_by_path(store))


def test_convergence_purges_stale_and_removing_designation_restores(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "generated/index.md").write_text("# Listing\n\nindexed navigation\n")
    (root / "generated/note.md").write_text("# Note\n\nconcept\n")
    provider = CountingProvider()
    before = _persistent_vault(root, tmp_path, provider, enabled=False)
    try:
        before.index.build_index()
        before.index.build_embeddings()
        assert "generated/index.md" in _paths(before)
    finally:
        before.close()
    after = _persistent_vault(root, tmp_path, provider, enabled=True)
    try:
        after.index.build_index()
        provider.texts.clear()
        after.index.build_embeddings()
        assert "generated/index.md" not in _paths(after)
        assert not provider.texts
        after.index.build_embeddings(force=True)
        assert "generated/index.md" not in _paths(after)
    finally:
        after.close()
    restored = _persistent_vault(root, tmp_path, provider, enabled=False)
    try:
        restored.index.build_index()
        provider.texts.clear()
        restored.index.build_embeddings()
        assert "generated/index.md" in _paths(restored)
        assert any("indexed navigation" in text for text in provider.texts)
    finally:
        restored.close()


def test_write_refresh_does_not_embed_generated_index(tmp_path: Path) -> None:
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    provider = CountingProvider()
    vault = _persistent_vault(root, tmp_path, provider, enabled=True)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        provider.texts.clear()
        vault.writer.write("generated/note.md", "# Pineapple\n\nconcept content\n")
        wait_for_writer_drain(vault)
        assert (root / "generated/index.md").exists()
        assert "generated/index.md" not in _paths(vault)
        assert "generated/note.md" in _paths(vault)
        assert all(
            "Pineapple" not in text or "concept content" in text
            for text in provider.texts
        )
    finally:
        vault.close()


class MeaningProvider(MockEmbeddingProvider):
    """Map fixture concepts and their query synonyms to orthogonal vectors."""

    def __init__(self) -> None:
        super().__init__(dim=4)

    def embed(self, texts: list[str]) -> list[list[float]]:
        axes = (
            ("orchard", "fruit"),
            ("voyage", "journey"),
            ("canine", "dog"),
            ("astral", "stars"),
        )
        vectors = []
        for text in texts:
            lowered = text.lower()
            vector = [float(any(term in lowered for term in terms)) for terms in axes]
            vectors.append(vector if any(vector) else [0.5, 0.5, 0.5, 0.5])
        return vectors


def test_meaning_search_reaches_authored_content_and_listed_concepts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "authored").mkdir()
    for name, text in {
        "generated/index.md": "# Listing\n\n[[canine]] [[astral]] uniquegeneratedterm\n",
        "generated/canine.md": "# Canine\n\ncanine\n",
        "generated/astral.md": "# Astral\n\nastral\n",
        "generated/log.md": "# Log\n\nvoyage\n",
        "authored/index.md": "# Index\n\norchard\n",
    }.items():
        (root / name).write_text(text, encoding="utf-8")
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False, okf_mode="on", okf_capabilities=_capabilities(True)
        ),
        embedding_provider=MeaningProvider(),
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert "generated/index.md" not in _paths(vault)
        for query, expected in {
            "fruit": "authored/index.md",
            "journey": "generated/log.md",
            "dog": "generated/canine.md",
            "stars": "generated/astral.md",
        }.items():
            assert (
                vault.reader.search(query, mode="semantic", limit=1)[0].path == expected
            )
        for mode in ("keyword", "hybrid"):
            assert any(
                result.path == "generated/index.md"
                for result in vault.reader.search("uniquegeneratedterm", mode=mode)
            )
    finally:
        vault.close()


def test_unreadable_okf_declaration_does_not_reembed_index(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    import pytest

    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "generated/index.md").write_text("# Listing\n\ncontents\n")
    (root / "generated/note.md").write_text("# Note\n\nconcept\n")
    provider = CountingProvider()
    vault = _persistent_vault(root, tmp_path, provider, enabled=True)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        provider.texts.clear()
        monkeypatch.setattr(
            vault._okf, "state", lambda: SimpleNamespace(active=False, probe_error=True)
        )
        with pytest.raises(OSError, match="OKF scope probe failed"):
            vault.index.build_embeddings()
        assert not provider.texts
        assert "generated/index.md" not in _paths(vault)
    finally:
        vault.close()


def test_designated_index_with_no_keyword_rows_loses_stale_vectors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "generated/index.md").write_text("# Listing\n\ncontents\n")
    provider = CountingProvider()
    before = _persistent_vault(root, tmp_path, provider, enabled=False)
    try:
        before.index.build_index()
        before.index.build_embeddings()
    finally:
        before.close()
    vault = _persistent_vault(root, tmp_path, provider, enabled=True)
    try:
        vault.index.build_index()
        vault._fts.delete_by_path("generated/index.md")
        vault.index.build_embeddings()
        assert "generated/index.md" not in _paths(vault)
    finally:
        vault.close()


def test_reindex_does_not_embed_changed_designated_index(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    index = root / "generated/index.md"
    index.write_text("# Listing\n\nold listing\n")
    (root / "generated/note.md").write_text("# Note\n\nconcept\n")
    provider = CountingProvider()
    vault = _persistent_vault(root, tmp_path, provider, enabled=True)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        provider.texts.clear()
        index.write_text("# Listing\n\nchanged listing\n")
        vault.index.reindex()
        assert "generated/index.md" not in _paths(vault)
        assert not any("changed listing" in text for text in provider.texts)
    finally:
        vault.close()
