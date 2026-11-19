"""Provider-boundary regressions for keyword-only frontmatter (#2311)."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.query_types import NoteQuery
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path

_TITLE = "Example Agreement"
_FIRST = "First paragraph of the agreement.\n" * 16 + "\n"
_SECOND = ("Second paragraph of the agreement.\n" * 16).rstrip("\n")
_CONTENT = (
    f"---\ntitle: {_TITLE}\ndescription: initialword\n"
    "type: contract\ntags: [renewal]\nsource: sourcesentinel\n"
    "hash: hashsentinel\nmodified: 2026-11-01\n---\n"
    f"# Terms\n{_FIRST}## Conditions\n{_SECOND}"
)
_FIELDS = ["title", "description", "source", "hash", "modified"]


class _ProviderSpy(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Capture the exact input at the document embedding boundary."""
        self.texts.extend(texts)
        return super().embed(texts)


def _settings(tmp_path: Path) -> VaultSettings:
    return VaultSettings(
        index_path=tmp_path / "fts.db",
        state_path=tmp_path / "state.json",
        embeddings_path=tmp_path / "vectors",
        indexed_frontmatter_fields=["type", "tags"],
        searchable_frontmatter_fields=_FIELDS,
    )


def _write_note(
    note: Path, *, keyword: str = "initialword", tag: str = "renewal"
) -> None:
    note.write_text(
        _CONTENT.replace("initialword", keyword).replace("[renewal]", f"[{tag}]"),
        encoding="utf-8",
    )


def _assert_metadata_hit(vault: Vault, keyword: str, tag: str) -> None:
    page = vault.reader.query_documents(
        NoteQuery(
            query=keyword, scope="metadata", filters={"type": "contract", "tags": tag}
        )
    )
    assert [item["path"] for item in page.items] == ["doc.md"]


@pytest.mark.parametrize("context", [False, True], ids=["body-only", "title-heading"])
def test_keyword_only_fields_never_reach_provider(
    tmp_path: Path, context: bool
) -> None:
    """Keyword indexing does not opt in to sending frontmatter to embeddings."""
    source = tmp_path / "vault"
    source.mkdir()
    _write_note(source / "doc.md")
    provider = _ProviderSpy()
    settings = replace(
        _settings(tmp_path), embed_searchable_fields=False, embed_context=context
    )

    with closing(
        Vault(source_dir=source, settings=settings, embedding_provider=provider)
    ) as vault:
        vault.index.build_index()
        assert vault.index.build_embeddings() == 2
        _assert_metadata_hit(vault, "initialword", "renewal")
        _assert_metadata_hit(vault, "Example", "renewal")

    expected = (
        [f"{_TITLE}\nTerms\n\n{_FIRST}", f"{_TITLE}\nConditions\n\n{_SECOND}"]
        if context
        else [_FIRST, _SECOND]
    )
    assert provider.texts == expected


def test_keyword_only_metadata_updates_reuse_vectors(tmp_path: Path) -> None:
    """Refresh and restart update metadata without recomputing unchanged inputs."""
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "doc.md"
    _write_note(note)
    settings = replace(_settings(tmp_path), embed_searchable_fields=False)
    provider = _ProviderSpy()

    with closing(
        Vault(source_dir=source, settings=settings, embedding_provider=provider)
    ) as vault:
        vault.index.build_index()
        assert vault.index.build_embeddings() == 2
        provider.texts.clear()

        _write_note(note, keyword="updatedword", tag="pending")
        vault.index.reindex()
        _assert_metadata_hit(vault, "updatedword", "pending")
        assert (
            vault.reader.count_documents_matching(NoteQuery(query="initialword")).total
            == 0
        )
        assert (
            vault.reader.count_documents_matching(
                NoteQuery(filters={"tags": "renewal"})
            ).total
            == 0
        )
        assert vault.index.build_embeddings() == 0
        assert provider.texts == []

    _write_note(note, keyword="offlineword", tag="approved")
    with closing(
        Vault(source_dir=source, settings=settings, embedding_provider=provider)
    ) as vault:
        # Even rebuilding the keyword index keeps compatible on-disk vectors.
        vault.index.build_index(force=True)
        _assert_metadata_hit(vault, "offlineword", "approved")
        assert (
            vault.reader.count_documents_matching(NoteQuery(query="updatedword")).total
            == 0
        )
        assert vault.index.build_embeddings() == 0
        assert provider.texts == []

        # The vector generation still contains both searchable chunks.
        assert vault._vectors is not None
        assert len(vault._vectors.rows_for("doc.md")) == 2


def test_legacy_searchable_fields_still_enrich_first_chunk(tmp_path: Path) -> None:
    """Existing configurations continue to use searchable-field enrichment."""
    source = tmp_path / "vault"
    source.mkdir()
    _write_note(source / "doc.md")
    provider = _ProviderSpy()
    settings = _settings(tmp_path)

    with closing(
        Vault(source_dir=source, settings=settings, embedding_provider=provider)
    ) as vault:
        vault.index.build_index()
        assert vault.index.build_embeddings() == 2

    assert provider.texts == [
        f"{_TITLE}\nTerms\n{_TITLE}\ninitialword\nsourcesentinel\n"
        f"hashsentinel\n2026-11-01\n\n{_FIRST}",
        f"{_TITLE}\nConditions\n\n{_SECOND}",
    ]
