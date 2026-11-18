"""Embedding coverage for designated OKF indexes."""

import os
import re
from pathlib import Path

import pytest

from markdown_vault_mcp.okf_capabilities import OkfCapabilities
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain
from tests.fixtures.embedding_providers import CountingProvider
from tests.fixtures.vectors import metadata_by_path


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


class MeaningProvider(CountingProvider):
    """Map fixture concepts and query synonyms to orthogonal vectors."""

    def __init__(self) -> None:
        super().__init__(dim=7)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.record_texts(texts)
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


@pytest.mark.parametrize("route", ["reindex", "write", "cold_reindex", "no_policy"])
def test_restart_vector_policy_and_lazy_reindex(
    tmp_path: Path, route: str, monkeypatch
) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "authored").mkdir()
    (root / "generated/index.md").write_text("# Listing\n\nold navigation\n")
    (root / "generated/log.md").write_text("# Log\n\ncurated history\n")
    (root / "generated/concept.md").write_text(
        "---\ntitle: Concept\ndescription: distinct scope\n---\n\nconcept body\n"
    )
    (root / "authored/index.md").write_text("# Authored\n\nhand written\n")
    provider = CountingProvider()
    first = _persistent_vault(root, tmp_path, provider, enabled=route != "no_policy")
    try:
        first.index.build_index()
        if route != "no_policy":
            reconciler = first._index_mgr._embeddings._reconciler
            assert reconciler is not None
            reconciler._skip_embedding = lambda _path: False
        first.index.build_embeddings()
        assert "generated/index.md" in _paths(first)
        initial_token = first._embed_builder.format_token()
    finally:
        first.close()
    vault = _persistent_vault(root, tmp_path, provider, enabled=route != "no_policy")
    try:
        vault.index.build_index()
        assert vault._embed_builder.format_token() == initial_token
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


@pytest.mark.parametrize("backend", ["numpy", "sqlite"])
def test_listed_identity_rebuild_reuse_and_restore(  # noqa: PLR0915, RUF100 - One persisted lifecycle covers upgrade and reversal.
    tmp_path: Path, backend: str
) -> None:
    from markdown_vault_mcp.indexing.embedding_reconciler import stored_inputs

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
        assert first._embed_builder.format_token() == "v1"
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
        assert restored._embed_builder.format_token() == "v1"
        assert "generated/index.md" in _paths(restored)
        assert any("listing text" in text for text in provider.texts)
        assert not any("second meaning" in text for text in provider.texts)
    finally:
        restored.close()


@pytest.mark.parametrize("backend", ["numpy", "sqlite"])
def test_failed_listed_identity_rebuild_preserves_and_retries(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import EmbeddingsRebuildFailedError
    from tests.fixtures.vector_store_files import stored_vector_files

    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "generated/note.md").write_text("# Concept\n\nbody\n")
    provider = CountingProvider()
    old = _persistent_vault(root, tmp_path, provider, enabled=False, backend=backend)
    try:
        old.index.build_index()
        old.index.build_embeddings()
    finally:
        old.close()
    before = stored_vector_files(tmp_path)
    vault = _persistent_vault(root, tmp_path, provider, enabled=True, backend=backend)
    try:
        vault.index.build_index()
        embed = provider.embed

        def fail(_texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider unavailable")

        monkeypatch.setattr(provider, "embed", fail)
        with pytest.raises(EmbeddingsRebuildFailedError):
            vault.index.build_embeddings()
        assert stored_vector_files(tmp_path) == before
        monkeypatch.setattr(provider, "embed", embed)
        vault.index.build_embeddings()
        assert "generated/note.md" in _paths(vault)
        assert vault._embed_builder.format_token() == 'v1;listed;scope=["generated"]'
    finally:
        vault.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_listing_marker_control(tmp_path: Path, enabled: bool) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "generated/index.md").write_text(
        "# Generated\n\n- [Pineapple](/generated/note.md)\n"
    )
    provider = CountingProvider()
    vault = _persistent_vault(root, tmp_path, provider, enabled=enabled)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert any("[Pineapple](" in text for text in provider.texts) is (not enabled)
    finally:
        vault.close()


def test_listed_v2_preserves_configured_fields_and_keyword_provenance(
    tmp_path: Path,
) -> None:
    from markdown_vault_mcp.indexing.embedding_reconciler import stored_inputs

    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "generated/concept.md").write_text(
        "---\ntitle: Concept\nsummary: brief\ndescription: description value\n---\n\nbody\n"
    )
    (root / "generated/log.md").write_text("# Log\n\nlog body\n")
    provider = CountingProvider()
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_capabilities=_capabilities(True),
            searchable_frontmatter_fields=["summary"],
        ),
        embedding_provider=provider,
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert (
            vault._embed_builder.format_token()
            == 'v2;fields=summary;listed;scope=["generated"]'
        )
        assert vault._index_mgr.chunking_meta.searchable_fields == "summary"
        store = vault._index_mgr._embeddings._loaded_store()
        assert store is not None
        rows = store.rows_for("generated/concept.md")
        inputs = stored_inputs(vault._embed_builder, rows)
        assert "Concept\nbrief\ndescription value\n\nbody" in inputs
        assert "Log\n\n# Log\n\nlog body" in provider.texts
        provider.texts.clear()
        vault.index.build_embeddings()
        assert not provider.texts
    finally:
        vault.close()


@pytest.mark.parametrize("route", ["write", "reindex"])
def test_widened_folder_reaches_unchanged_empty_concept_before_embedding_build(
    tmp_path: Path, route: str
) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "other").mkdir()
    (root / "generated/index.md").write_text(
        "# Listing\n\n- [Concept](/generated/concept.md) - luminescence\n"
    )
    (root / "generated/concept.md").write_text(
        "---\ntitle: Concept\ndescription: luminescence\n---\n"
    )
    provider = MeaningProvider()

    def make(folders: tuple[str, ...]) -> Vault:
        return Vault(
            source_dir=root,
            settings=VaultSettings(
                read_only=False,
                okf_capabilities=_capabilities(True, folders),
                index_path=tmp_path / "index.db",
                state_path=tmp_path / "state.json",
                embeddings_path=tmp_path / "vectors",
            ),
            embedding_provider=provider,
        )

    old = make((".",))
    try:
        old.index.build_index()
        old.index.build_embeddings()
    finally:
        old.close()
    vault = make(("generated",))
    try:
        vault.index.build_index()
        provider.texts.clear()
        if route == "write":
            vault.writer.write("other/note.md", "# Other\n\nunrelated\n")
            wait_for_writer_drain(vault)
            assert "generated/concept.md" in _paths(vault)
            assert "generated/index.md" not in _paths(vault)
        else:
            vault.index.reindex()
            assert vault._index_mgr._embeddings._loaded_store() is None
            assert not provider.texts
        assert (
            vault.reader.search("glowing", mode="semantic", limit=1)[0].path
            == "generated/concept.md"
        )
        assert "generated/index.md" not in _paths(vault)
    finally:
        vault.close()


@pytest.mark.parametrize("backend", ["numpy", "sqlite"])
def test_legacy_alias_upgrades_v1_store(tmp_path: Path, backend: str) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text("# Listing\n\nlegacy listing\n")
    (root / "concept.md").write_text(
        "---\ntitle: Concept\ndescription: legacy description\n---\n"
    )
    provider = CountingProvider()
    old = _persistent_vault(root, tmp_path, provider, enabled=False, backend=backend)
    try:
        old.index.build_index()
        old.index.build_embeddings()
        assert "index.md" in _paths(old)
    finally:
        old.close()
    provider.texts.clear()
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_write=True,
            embedding_store=backend,
            index_path=tmp_path / "index.db",
            state_path=tmp_path / "state.json",
            embeddings_path=tmp_path / "vectors",
        ),
        embedding_provider=provider,
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert "index.md" not in _paths(vault)
        assert "concept.md" in _paths(vault)
        assert any("legacy description" in text for text in provider.texts)
        assert vault._embed_builder.format_token().startswith("v1;listed;scope=")
    finally:
        vault.close()


def test_equivalent_folder_scope_does_not_rebuild_store(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "generated").mkdir(parents=True)
    (root / "generated/note.md").write_text("# Note\n\nbody\n")
    provider = CountingProvider()
    first = _persistent_vault(root, tmp_path, provider, enabled=True)
    try:
        first.index.build_index()
        first.index.build_embeddings()
    finally:
        first.close()
    provider.texts.clear()
    reopened = _persistent_vault(
        root, tmp_path, provider, enabled=True, folders=("./generated/", "generated")
    )
    try:
        reopened.index.build_index()
        reopened.index.build_embeddings()
        assert not provider.texts
        assert "generated/note.md" in _paths(reopened)
    finally:
        reopened.close()


def test_postgres_designation_rebuild_and_reindex(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    if not os.environ.get("MVM_TEST_POSTGRES_DSN") and not os.environ.get("CI"):
        pytest.skip("Postgres service not supplied; CI exercises its existing fixture")
    schema = request.getfixturevalue("postgres_schema")
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
        (root / "generated/index.md").write_text("# Listing\n\nchanged marker\n")
        provider.texts.clear()
        vault.index.reindex()
        assert "generated/index.md" not in _paths(vault)
        assert not any("changed marker" in text for text in provider.texts)
    finally:
        vault.close()
