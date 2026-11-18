"""Embedding coverage for designated OKF indexes."""

import re
from pathlib import Path

import pytest

from markdown_vault_mcp.okf_capabilities import OkfCapabilities
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import MockEmbeddingProvider, wait_for_writer_drain
from tests.fixtures.postgres import PostgresSchema
from tests.fixtures.vectors import metadata_by_path


class CountingProvider(MockEmbeddingProvider):
    """Collect inputs sent to the deterministic mock provider."""

    def __init__(self, dim: int = 32) -> None:
        super().__init__(dim)
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed(texts)


def _capabilities(
    index: bool, folders: tuple[str, ...] = ("generated",)
) -> OkfCapabilities:
    return OkfCapabilities(False, "elicit", index, "off", folders, False)


def _persistent_vault(  # noqa: PLR0913, RUF100 - Test options vary policy, folder and backend.
    root: Path,
    tmp_path: Path,
    provider: CountingProvider,
    *,
    enabled: bool,
    folders: tuple[str, ...] = ("generated",),
    backend: str = "numpy",
) -> Vault:
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=_capabilities(enabled, folders),
            embedding_store=backend,
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


class MeaningProvider(CountingProvider):
    """Map fixture concepts and query synonyms to orthogonal vectors."""

    def __init__(self) -> None:
        super().__init__(dim=7)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        axes = (
            ("orchard", "fruit"),
            ("voyage", "journey"),
            ("canine", "dog"),
            ("astral", "stars"),
            ("luminescence", "glowing"),
            ("cobalt", "blue"),
            ("citrus", "orange"),
        )
        result = []
        for text in texts:
            vector = [
                float(any(term in text.lower() for term in terms)) for terms in axes
            ]
            result.append(vector if any(vector) else [0.5] * 7)
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
        (root / "generated/cobalt.md").write_text("---\ntitle: Cobalt\n---\n")
        (root / "generated/orbit.md").write_text(
            "---\ntitle: Orbit\ndescription: citrus\n---\n\nunrelated prose\n"
        )
    provider = MeaningProvider()
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=_capabilities(True),
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
            {"Canine", "Astral", "Concept", "Cobalt", "Orbit"}
            if description_only
            else {"Canine", "Astral"}
        )
        if description_only:
            assert "luminescence" in listing
            assert "Concept\nluminescence\n\n" in provider.texts
            assert "Cobalt\n\n" in provider.texts
            assert any(text.startswith("Orbit\ncitrus\n\n") for text in provider.texts)
        assert "generated/index.md" not in _paths(vault)
        assert not any("[Canine](" in text for text in provider.texts)
        queries = {"fruit": "authored/index.md", "journey": "generated/log.md"}
        synonyms = {
            "Canine": "dog",
            "Astral": "stars",
            "Concept": "glowing",
            "Cobalt": "blue",
            "Orbit": "orange",
        }
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


def test_listed_identity_rebuild_reuse_and_restore(  # noqa: PLR0915, RUF100 - One persisted lifecycle covers upgrade and reversal.
    tmp_path: Path,
) -> None:
    from markdown_vault_mcp.indexing.embedding_reconciler import stored_inputs

    backend = "numpy"

    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "authored").mkdir()
    concept = root / "generated/concept.md"
    concept.write_text(
        "---\ntitle: Concept\ndescription: first meaning\n---\n\nbody text\n"
    )
    (root / "generated/index.md").write_text("# Listing\n\nlisting text\n")
    (root / "generated/log.md").write_text("# Log\n\nlog text\n")
    (root / "authored/index.md").write_text("# Authored\n\nauthored text\n")
    (root / "authored/note.md").write_text("# Other\n\nother text\n")
    provider = CountingProvider()
    first = _persistent_vault(root, tmp_path, provider, enabled=False, backend=backend)
    try:
        first.index.build_index()
        first.index.build_embeddings()
        plain = list(provider.texts)
        assert "listing text" in " ".join(plain)
    finally:
        first.close()
    provider.texts.clear()
    second = _persistent_vault(root, tmp_path, provider, enabled=True, backend=backend)
    try:
        second.index.build_index()
        second.index.build_embeddings()
        assert second._embed_builder.format_token() == 'v1;listed;scope=["generated"]'
        assert "generated/index.md" not in _paths(second)
        assert {
            "# Log\n\nlog text",
            "# Authored\n\nauthored text",
            "# Other\n\nother text",
        } <= set(provider.texts)
        store = second._index_mgr._embeddings._loaded_store()
        assert store is not None
        rows = store.rows_for("generated/concept.md")
        assert "Concept\nfirst meaning\n\nbody text" in stored_inputs(
            second._embed_builder, rows
        )
        provider.texts.clear()
        second.index.build_embeddings()
        assert not provider.texts
        concept.write_text(
            "---\ntitle: Concept\ndescription: second meaning\n---\n\nbody text\n"
        )
        second.index.reindex()
        assert any("second meaning" in text for text in provider.texts)
    finally:
        second.close()
    provider.texts.clear()
    restored = _persistent_vault(
        root, tmp_path, provider, enabled=False, backend=backend
    )
    try:
        restored.index.build_index()
        restored.index.build_embeddings()
        assert "generated/index.md" in _paths(restored)
        assert any("listing text" in text for text in provider.texts)
        assert not any("second meaning" in text for text in provider.texts)
    finally:
        restored.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_listed_v2_preserves_configured_fields_and_keyword_provenance(
    tmp_path: Path,
    enabled: bool,
) -> None:
    from markdown_vault_mcp.indexing.embedding_reconciler import stored_inputs
    from markdown_vault_mcp.scanner import HeadingChunker

    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "generated/concept.md").write_text(
        "---\ntitle: Concept\nsummary: brief\ndescription: "
        + "description " * 20
        + "\n---\n\n## Heading\nbody\n"
    )
    (root / "generated/log.md").write_text("# Log\n\nlog body\n")
    provider = CountingProvider()
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_capabilities=_capabilities(enabled),
            searchable_frontmatter_fields=["summary"],
            max_chunk_chars=70,
        ),
        embedding_provider=provider,
        chunk_strategy=HeadingChunker(short_doc_lines=0, max_chunk_chars=70),
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        store = vault._index_mgr._embeddings._loaded_store()
        assert store is not None
        rows = store.rows_for("generated/concept.md")
        inputs = stored_inputs(vault._embed_builder, rows)
        text = next(iter(inputs))
        assert text.startswith("Concept\nHeading\nbrief")
        assert ("description" in text) is enabled
        assert len(text) <= 70
        if enabled:
            # The description is a quarter of the cap; the body keeps its room.
            assert text.endswith("\n\nbody")
            assert "brief\ndescription descr\n\n" in text
        assert "Log\nLog\n\n\nlog body" in provider.texts
        provider.texts.clear()
        vault.index.build_embeddings()
        assert not provider.texts
    finally:
        vault.close()


def test_postgres_designation_rebuild_and_reindex(
    tmp_path: Path, postgres_schema: PostgresSchema
) -> None:
    schema = postgres_schema
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "generated/index.md").write_text("# Listing\n\nlisting marker\n")
    (root / "generated/note.md").write_text("# Note\n\nconcept marker\n")
    provider = CountingProvider()

    def make(enabled: bool) -> Vault:
        return Vault(
            source_dir=root,
            settings=VaultSettings(
                read_only=False,
                okf_capabilities=_capabilities(enabled),
                embedding_store="postgres",
                embedding_postgres_dsn=schema.dsn,
                embedding_postgres_schema=schema.name,
                index_path=tmp_path / "index.db",
                state_path=tmp_path / "state.json",
            ),
            embedding_provider=provider,
        )

    old = make(False)
    try:
        old.index.build_index()
        old.index.build_embeddings()
        assert "generated/index.md" in _paths(old)
    finally:
        old.close()
    vault = make(True)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert "generated/index.md" not in _paths(vault)
        assert "generated/note.md" in _paths(vault)
    finally:
        vault.close()


@pytest.mark.parametrize("words", [30, 70])
def test_listed_input_keeps_metadata_within_cap_and_other_chunks_unchanged(
    tmp_path: Path, words: int
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text("# Index\n\n- [Concept](/concept.md) - x\n")
    body = "\n\n".join(f"paragraph {i} " + "word " * words for i in range(6))
    (root / "concept.md").write_text(
        f"---\ntitle: Concept\ndescription: {'glow ' * 250}\n---\n\n{body}\n"
    )

    def texts(*, listed: bool, provider: CountingProvider) -> list[str]:
        vault = Vault(
            source_dir=root,
            settings=VaultSettings(
                okf_mode="on",
                okf_capabilities=_capabilities(listed, (".",)),
                max_chunk_chars=300,
                chunk_overlap_words=10,
            ),
            embedding_provider=provider,
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            if provider.texts:
                vault.index.build_embeddings()
                count = len(provider.texts)
                vault.index.build_embeddings()
                assert len(provider.texts) == count
        finally:
            vault.close()
        return provider.texts

    listed = texts(listed=True, provider=CountingProvider())
    plain = [
        text
        for text in texts(listed=False, provider=CountingProvider())
        if "/concept.md" not in text
    ]
    assert len(listed) == len(plain)
    head, _, body = listed[0].partition("\n\n")
    assert head.startswith("Concept\nglow glow")
    assert len(head) <= 75
    assert plain[0].startswith(body)
    for text, bare in zip(listed, plain, strict=True):
        assert len(text) <= max(300, len(bare))
        assert text == bare or text is listed[0]


def test_full_chunk_preserves_generated_description_and_body_meaning(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    provider = MeaningProvider()
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=_capabilities(True, (".",)),
            max_chunk_chars=300,
        ),
        embedding_provider=provider,
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        body = "\n\n".join(f"paragraph {i} orchard " + "word " * 70 for i in range(6))
        vault.writer.write(
            "concept.md",
            f"---\ntitle: Concept\ndescription: luminescence\n---\n\n{body}\n",
        )
        wait_for_writer_drain(vault)
        assert "luminescence" in (root / "index.md").read_text()
        assert "index.md" not in _paths(vault)
        assert any("orchard" in text for text in provider.texts)
        assert any("luminescence" in text for text in provider.texts)
        for query in ("fruit", "glowing"):
            assert vault.reader.search(query, mode="semantic", limit=1)[0].path == (
                "concept.md"
            )
    finally:
        vault.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_full_chunk_preserves_configured_summary(tmp_path: Path, enabled: bool) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    body = "\n\n".join(f"paragraph {i} orchard " + "word " * 70 for i in range(6))
    (root / "concept.md").write_text(
        f"---\ntitle: Concept\nsummary: luminescence\n---\n\n{body}\n"
    )
    provider = MeaningProvider()
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            okf_mode="on",
            okf_capabilities=_capabilities(enabled, (".",)),
            max_chunk_chars=300,
            searchable_frontmatter_fields=["summary"],
        ),
        embedding_provider=provider,
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert any("orchard" in text for text in provider.texts)
        assert any("luminescence" in text for text in provider.texts)
        for query in ("fruit", "glowing"):
            assert vault.reader.search(query, mode="semantic", limit=1)[0].path == (
                "concept.md"
            )
    finally:
        vault.close()


def test_full_first_chunk_input_is_reused_across_restart_and_incremental_edit(
    tmp_path: Path,
) -> None:
    from markdown_vault_mcp.indexing.embedding_reconciler import stored_inputs

    root = tmp_path / "vault"
    root.mkdir()
    body = "\n\n".join(f"paragraph {i} orchard " + "word " * 70 for i in range(6))
    concept = root / "concept.md"

    def write(description: str) -> None:
        concept.write_text(
            f"---\ntitle: Concept\ndescription: {description}\n---\n\n{body}\n"
        )

    def run(provider: CountingProvider, *, reindex: bool = False) -> list[str]:
        vault = Vault(
            source_dir=root,
            settings=VaultSettings(
                okf_mode="on",
                okf_capabilities=_capabilities(True, (".",)),
                max_chunk_chars=300,
                index_path=tmp_path / "index.db",
                state_path=tmp_path / "state.json",
                embeddings_path=tmp_path / "vectors",
            ),
            embedding_provider=provider,
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
            if reindex:
                vault.index.reindex()
            store = vault._index_mgr._embeddings._loaded_store()
            assert store is not None
            return list(
                stored_inputs(vault._embed_builder, store.rows_for("concept.md"))
            )
        finally:
            vault.close()

    write("luminescence")
    first = CountingProvider()
    stored = run(first)
    assert stored[0] in first.texts
    assert stored[0].startswith("Concept\nluminescence\n\n")
    assert len(stored[0]) <= 300
    restart = CountingProvider()
    assert run(restart) == stored
    assert not restart.texts
    write("cobalt")
    edited = CountingProvider()
    after = run(edited, reindex=True)
    assert after[0].startswith("Concept\ncobalt\n\n")
    assert edited.texts == [after[0]]
    assert after[1:] == stored[1:]
