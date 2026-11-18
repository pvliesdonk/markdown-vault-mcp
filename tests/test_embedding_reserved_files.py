"""Embedding coverage for designated OKF indexes."""

import re
from pathlib import Path

import pytest

from markdown_vault_mcp.okf_capabilities import OkfCapabilities
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import MockEmbeddingProvider, wait_for_writer_drain
from tests.fixtures.embedding_providers import CountingProvider
from tests.fixtures.vectors import metadata_by_path


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


def test_convergence_purges_stale_and_restores_after_designation_removed(
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
        after.index.build_embeddings(force=True)
        assert not any("indexed navigation" in text for text in provider.texts)
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


def test_externally_changed_designated_index_is_not_embedded(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    provider = CountingProvider()
    vault = _persistent_vault(root, tmp_path, provider, enabled=True)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        provider.texts.clear()
        (root / "generated/index.md").write_text("# Listing\n\nchanged listing\n")
        vault.index.reindex()
        assert "generated/index.md" not in _paths(vault)
        assert not any("changed listing" in text for text in provider.texts)
    finally:
        vault.close()


class MeaningProvider(MockEmbeddingProvider):
    """Map fixture concepts and query synonyms to orthogonal vectors."""

    def __init__(self) -> None:
        super().__init__(dim=5)
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        axes = (
            ("orchard", "fruit"),
            ("voyage", "journey"),
            ("canine", "dog"),
            ("astral", "stars"),
            ("luminescence", "glowing"),
        )
        result = []
        for text in texts:
            vector = [
                float(any(term in text.lower() for term in terms)) for terms in axes
            ]
            result.append(vector if any(vector) else [0.5] * 5)
        return result


@pytest.mark.parametrize("description_only", [False, True])
def test_meaning_search_reaches_authored_content_and_generated_listing_concepts(
    tmp_path: Path,
    description_only: bool,
) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "authored").mkdir()
    (root / "authored/index.md").write_text("# Index\n\norchard\n")
    (root / "generated/log.md").write_text("# Log\n\nvoyage\n")
    if description_only:
        (root / "generated/concept.md").write_text(
            "---\ntitle: Concept\ndescription: luminescence\n---\n"
        )
    provider = MeaningProvider()
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=_capabilities(True),
            searchable_frontmatter_fields=["description"] if description_only else None,
        ),
        embedding_provider=provider,
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        for title in ("Canine", "Astral"):
            vault.writer.write(
                f"generated/{title.lower()}.md", f"# {title}\n\n{title.lower()}\n"
            )
        wait_for_writer_drain(vault)
        listing = (root / "generated/index.md").read_text()
        titles = re.findall(r"^- \[(.*?)\]\(", listing, re.MULTILINE)
        assert set(titles) == (
            {"Canine", "Astral", "Concept"}
            if description_only
            else {"Canine", "Astral"}
        )
        if description_only:
            assert "luminescence" in listing
        assert "generated/index.md" not in _paths(vault)
        assert not any("[Canine](" in text for text in provider.texts)
        queries = {"fruit": "authored/index.md", "journey": "generated/log.md"}
        synonyms = {"Canine": "dog", "Astral": "stars", "Concept": "glowing"}
        for title in titles:
            queries[synonyms[title]] = f"generated/{title.lower()}.md"
        for query, expected in queries.items():
            assert (
                vault.reader.search(query, mode="semantic", limit=1)[0].path == expected
            )
        for mode in ("keyword", "hybrid"):
            assert any(
                result.path == "generated/index.md"
                for result in vault.reader.search("canine", mode=mode)
            )
    finally:
        vault.close()


@pytest.mark.parametrize("mode", ["read_only", "legacy", "no_declaration"])
def test_designation_is_authority_without_runtime_detection(
    tmp_path: Path, mode: str
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text("# Listing\n\nroot navigation\n")
    (root / "log.md").write_text("# Log\n\ncurated history\n")
    provider = CountingProvider()
    settings = VaultSettings(
        read_only=mode == "read_only",
        okf_mode="off" if mode == "no_declaration" else "auto",
        okf_write=mode == "legacy",
        okf_capabilities=None if mode == "legacy" else _capabilities(True, (".",)),
    )
    vault = Vault(source_dir=root, settings=settings, embedding_provider=provider)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert "index.md" not in _paths(vault)
        assert "log.md" in _paths(vault)
        assert not any("root navigation" in text for text in provider.texts)
    finally:
        vault.close()


@pytest.mark.parametrize("route", ["reindex", "write", "cold_reindex", "no_policy"])
def test_restart_vector_policy_and_lazy_reindex(
    tmp_path: Path, route: str, monkeypatch
) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "authored").mkdir()
    (root / "generated/index.md").write_text("# Listing\n\nold navigation\n")
    (root / "generated/log.md").write_text("# Log\n\ncurated history\n")
    (root / "authored/index.md").write_text("# Authored\n\nhand written\n")
    provider = CountingProvider()
    first = _persistent_vault(root, tmp_path, provider, enabled=False)
    try:
        first.index.build_index()
        first.index.build_embeddings()
        assert "generated/index.md" in _paths(first)
    finally:
        first.close()
    vault = _persistent_vault(root, tmp_path, provider, enabled=route != "no_policy")
    try:
        vault.index.build_index()
        if route == "cold_reindex":
            assert vault._vector_slot.current() is None
            vault.index.reindex()
            assert vault._vector_slot.current() is None
            return
        vault.reader.search("navigation", mode="semantic")
        provider.texts.clear()
        if route == "no_policy":
            store = vault._vector_slot.current()
            assert store is not None

            def unexpected_tokens():
                raise AssertionError("unchanged reindex scanned vector tokens")

            monkeypatch.setattr(store, "tokens", unexpected_tokens)
            vault.index.reindex()
            return
        if route == "write":
            vault.writer.write("authored/note.md", "# New\n\nnew concept\n")
            wait_for_writer_drain(vault)
            assert any("new concept" in text for text in provider.texts)
        else:
            vault.index.reindex()
        assert "generated/index.md" not in _paths(vault)
        assert {"generated/log.md", "authored/index.md"} <= _paths(vault)
    finally:
        vault.close()
