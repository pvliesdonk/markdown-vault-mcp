"""Tests for SearchManager in isolation (no Vault dependency)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.link import LinkManager
from markdown_vault_mcp.managers.search import SearchManager
from markdown_vault_mcp.scanner import scan_directory
from markdown_vault_mcp.types import (
    AttachmentInfo,
    GroupedResult,
    NoteContext,
    NoteInfo,
)
from markdown_vault_mcp.utils.fts import fts_row_to_note_info as _fts_row_to_note_info

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def search_vault(tmp_path: Path) -> Path:
    """Create a small vault suitable for search/list tests.

    Contains:
        alpha.md   (root, tags: [a, b])
        beta.md    (root, tags: [b])
        notes/gamma.md (subfolder, tags: [c])
        notes/delta.md (subfolder, no tags)
        alpha.md -> beta.md (link)
        beta.md -> alpha.md (link)
    """
    alpha = tmp_path / "alpha.md"
    alpha.write_text(
        "---\ntitle: Alpha\ntags:\n  - a\n  - b\n---\n"
        "# Alpha\n\nHello world. Link to [beta](beta.md).\n",
        encoding="utf-8",
    )
    beta = tmp_path / "beta.md"
    beta.write_text(
        "---\ntitle: Beta\ntags:\n  - b\n---\n"
        "# Beta\n\nGoodbye world. Back to [alpha](alpha.md).\n",
        encoding="utf-8",
    )
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    gamma = notes_dir / "gamma.md"
    gamma.write_text(
        "---\ntitle: Gamma\ntags:\n  - c\n---\n"
        "# Gamma\n\nUnique gamma content in notes folder.\n",
        encoding="utf-8",
    )
    delta = notes_dir / "delta.md"
    delta.write_text(
        "---\ntitle: Delta\n---\n# Delta\n\nDelta has no tags.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def search_mgr(search_vault: Path) -> SearchManager:
    """Build a SearchManager from a scanned vault."""
    fts = FTSIndex(db_path=":memory:", indexed_frontmatter_fields=["tags"])
    for note in scan_directory(search_vault):
        fts.upsert_note(note)
    fts.resolve_vault_wikilinks()
    link_mgr = LinkManager(fts=fts, source_dir=search_vault)
    return SearchManager(
        fts=fts,
        source_dir=search_vault,
        indexed_frontmatter_fields=["tags"],
        link_manager=link_mgr,
        attachment_extensions=["png"],
    )


class TestStats:
    def test_stats_reports_vault_snapshot(self, search_mgr: SearchManager) -> None:
        stats = search_mgr.stats()
        assert stats.document_count == 4  # alpha, beta, notes/gamma, notes/delta
        assert stats.indexed_frontmatter_fields == ["tags"]
        assert "png" in stats.attachment_extensions
        assert stats.semantic_search_available is False  # no provider configured
        assert stats.link_count >= 2  # alpha <-> beta
        assert stats.chunk_count >= 4


# ---------------------------------------------------------------------------
# keyword search
# ---------------------------------------------------------------------------


class TestKeywordSearch:
    def test_search_returns_results(self, search_mgr: SearchManager) -> None:
        """Keyword search returns GroupedResult objects."""
        results = search_mgr.search("world")
        assert len(results) >= 1
        assert all(isinstance(r, GroupedResult) for r in results)
        assert all(r.search_type == "keyword" for r in results)

    def test_search_respects_limit(self, search_mgr: SearchManager) -> None:
        """Keyword search respects the limit parameter."""
        results = search_mgr.search("world", limit=1)
        assert len(results) <= 1

    def test_search_folder_filter(self, search_mgr: SearchManager) -> None:
        """Keyword search respects folder filter."""
        results = search_mgr.search("content", folder="notes")
        paths = [r.path for r in results]
        assert all(p.startswith("notes/") for p in paths)

    def test_search_returns_frontmatter(self, search_mgr: SearchManager) -> None:
        """Keyword search results include frontmatter."""
        results = search_mgr.search("Hello")
        alpha_results = [r for r in results if r.path == "alpha.md"]
        assert len(alpha_results) >= 1
        assert "tags" in alpha_results[0].frontmatter

    def test_search_no_results(self, search_mgr: SearchManager) -> None:
        """Keyword search for nonexistent term returns empty list."""
        results = search_mgr.search("zzzznonexistent")
        assert results == []

    def test_hyphenated_query_finds_document_through_keyword_leg(
        self, tmp_path: Path
    ) -> None:
        """#866: a hyphenated query must flow through the SearchManager keyword
        leg (which delegates to FTSIndex.search) and return the doc."""
        vault = tmp_path / "hyphen_vault"
        vault.mkdir()
        (vault / "slug.md").write_text(
            "# Slug\n\nThe vault-mcp File-back pipeline runs nightly.\n",
            encoding="utf-8",
        )
        fts = FTSIndex(db_path=":memory:")
        for note in scan_directory(vault):
            fts.upsert_note(note)
        mgr = SearchManager(fts=fts, source_dir=vault)
        results = mgr.search("vault-mcp File-back", mode="keyword")
        assert [r.path for r in results] == ["slug.md"]


# ---------------------------------------------------------------------------
# semantic search
# ---------------------------------------------------------------------------


class TestSemanticSearch:
    def test_semantic_raises_without_provider(self, search_mgr: SearchManager) -> None:
        """Semantic search raises ValueError without embedding config."""
        with pytest.raises(ValueError, match="Semantic search requires"):
            search_mgr.search("hello", mode="semantic")

    def test_hybrid_raises_without_provider(self, search_mgr: SearchManager) -> None:
        """Hybrid search raises ValueError without embedding config."""
        with pytest.raises(ValueError, match="Semantic search requires"):
            search_mgr.search("hello", mode="hybrid")


class TestSearchRequiresEmbeddings:
    def test_semantic_search_unconfigured_raises_embeddings_not_configured(
        self, search_mgr: SearchManager
    ) -> None:
        from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

        with pytest.raises(EmbeddingsNotConfiguredError):
            search_mgr.search("alpha", mode="semantic")
        # Subclass of ValueError → historical contract preserved.
        with pytest.raises(ValueError):
            search_mgr.search("alpha", mode="hybrid")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_list_returns_note_info(self, search_mgr: SearchManager) -> None:
        """list() returns NoteInfo objects."""
        items = search_mgr.list()
        assert len(items) == 4
        assert all(isinstance(i, NoteInfo) for i in items)

    def test_list_folder_filter(self, search_mgr: SearchManager) -> None:
        """list() filters by folder."""
        items = search_mgr.list(folder="notes")
        assert len(items) == 2
        assert all(i.path.startswith("notes/") for i in items)

    def test_list_pattern_filter(self, search_mgr: SearchManager) -> None:
        """list() filters by glob pattern."""
        items = search_mgr.list(pattern="alpha*")
        assert len(items) == 1
        assert items[0].path == "alpha.md"

    def test_list_include_attachments(
        self, search_vault: Path, search_mgr: SearchManager
    ) -> None:
        """list(include_attachments=True) returns AttachmentInfo for non-.md files."""
        # Create a .png file in the vault.
        (search_vault / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        items = search_mgr.list(include_attachments=True)
        attachment_items = [i for i in items if isinstance(i, AttachmentInfo)]
        assert len(attachment_items) == 1
        assert attachment_items[0].path == "image.png"
        assert attachment_items[0].kind == "attachment"

    def test_nested_attachment_paths_are_posix_spelled(
        self, search_vault: Path, search_mgr: SearchManager
    ) -> None:
        """A nested attachment reports POSIX ``path`` and ``folder``.

        `str(Path)` yields OS separators, so this used to hand back
        ``notes\\image.png`` on Windows while the index, the exclusion
        check and a normalized ``folder`` argument all speak POSIX — the
        folder filter below then matched no attachment at all. The
        assertions are trivially true on POSIX runners (CI is
        ubuntu-only), so they pin the contract rather than reproduce the
        platform bug.
        """
        (search_vault / "notes" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        items = search_mgr.list(include_attachments=True)
        attachments = [i for i in items if isinstance(i, AttachmentInfo)]
        assert [a.path for a in attachments] == ["notes/image.png"]
        assert attachments[0].folder == "notes"

    def test_nested_attachment_survives_the_folder_filter(
        self, search_vault: Path, search_mgr: SearchManager
    ) -> None:
        """The folder filter reaches nested attachments, in every spelling."""
        (search_vault / "notes" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        for spelling in ("notes", "notes/", "/notes", "notes\\"):
            items = search_mgr.list(folder=spelling, include_attachments=True)
            attachments = [i for i in items if isinstance(i, AttachmentInfo)]
            assert [a.path for a in attachments] == ["notes/image.png"], spelling

    def test_list_attachments_respects_extension_filter(
        self, search_vault: Path, search_mgr: SearchManager
    ) -> None:
        """Attachments not in allowlist are excluded."""
        (search_vault / "doc.txt").write_text("hi", encoding="utf-8")
        items = search_mgr.list(include_attachments=True)
        attachment_items = [i for i in items if isinstance(i, AttachmentInfo)]
        # .txt is not in ["png"], so should not appear.
        assert all(a.path != "doc.txt" for a in attachment_items)

    def test_list_attachments_hidden_dirs_excluded(
        self, search_vault: Path, search_mgr: SearchManager
    ) -> None:
        """Attachments inside hidden directories are excluded."""
        hidden = search_vault / ".hidden"
        hidden.mkdir()
        (hidden / "secret.png").write_bytes(b"\x89PNG")
        items = search_mgr.list(include_attachments=True)
        attachment_paths = [i.path for i in items if isinstance(i, AttachmentInfo)]
        assert ".hidden/secret.png" not in attachment_paths

    def test_list_attachments_when_source_dir_is_symlink(self, tmp_path: Path) -> None:
        # rglob anchors paths at the unresolved source_dir; relative_to must
        # use the same unresolved path or attachments under a symlink-mounted
        # SOURCE_DIR get filtered out by the ValueError branch.
        real = tmp_path / "real"
        real.mkdir()
        (real / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        vault_link = tmp_path / "vault"
        try:
            vault_link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not supported here: {exc}")

        fts = FTSIndex(db_path=":memory:", indexed_frontmatter_fields=["tags"])
        link_mgr = LinkManager(fts=fts, source_dir=vault_link)
        mgr = SearchManager(
            fts=fts,
            source_dir=vault_link,
            indexed_frontmatter_fields=["tags"],
            link_manager=link_mgr,
            attachment_extensions=["png"],
        )

        items = mgr.list(include_attachments=True)
        attachment_paths = [i.path for i in items if isinstance(i, AttachmentInfo)]
        assert "image.png" in attachment_paths


# ---------------------------------------------------------------------------
# list_folders
# ---------------------------------------------------------------------------


class TestListFolders:
    def test_list_folders_returns_folders(self, search_mgr: SearchManager) -> None:
        """list_folders() returns folder strings."""
        folders = search_mgr.list_folders()
        assert "" in folders  # root
        assert "notes" in folders


# ---------------------------------------------------------------------------
# list_tags
# ---------------------------------------------------------------------------


class TestListTags:
    def test_list_tags_returns_values(self, search_mgr: SearchManager) -> None:
        """list_tags() returns indexed tag values."""
        tags = search_mgr.list_tags("tags")
        assert "a" in tags
        assert "b" in tags
        assert "c" in tags

    def test_list_tags_empty_for_unindexed(self, search_mgr: SearchManager) -> None:
        """list_tags() for unindexed field returns empty list."""
        assert search_mgr.list_tags("nonexistent") == []


# ---------------------------------------------------------------------------
# get_recent
# ---------------------------------------------------------------------------


class TestGetRecent:
    def test_get_recent_returns_note_info(self, search_mgr: SearchManager) -> None:
        """get_recent() returns NoteInfo objects ordered by modified_at."""
        recent = search_mgr.get_recent(limit=4)
        assert len(recent) == 4
        assert all(isinstance(r, NoteInfo) for r in recent)
        # Should be ordered by modified_at descending.
        timestamps = [r.modified_at for r in recent]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_get_recent_respects_limit(self, search_mgr: SearchManager) -> None:
        """get_recent() respects limit parameter."""
        recent = search_mgr.get_recent(limit=2)
        assert len(recent) == 2

    def test_get_recent_folder_filter(self, search_mgr: SearchManager) -> None:
        """get_recent() filters by folder."""
        recent = search_mgr.get_recent(folder="notes")
        assert all(r.path.startswith("notes/") for r in recent)


# ---------------------------------------------------------------------------
# get_similar
# ---------------------------------------------------------------------------


class TestGetSimilar:
    def test_get_similar_empty_without_embeddings(
        self, search_mgr: SearchManager
    ) -> None:
        """get_similar() returns empty list without embedding config."""
        result = search_mgr.get_similar("alpha.md")
        assert result == []

    def test_get_similar_raises_for_nonexistent(
        self, search_mgr: SearchManager
    ) -> None:
        """get_similar() raises for non-existent path."""
        with pytest.raises(ValueError, match="Document not found"):
            search_mgr.get_similar("no_such.md")

    def test_get_similar_raises_for_non_md(self, search_mgr: SearchManager) -> None:
        """get_similar() raises for non-.md path."""
        with pytest.raises(ValueError, match="Path must end with"):
            search_mgr.get_similar("image.png")


# ---------------------------------------------------------------------------
# get_context
# ---------------------------------------------------------------------------


class TestGetContext:
    def test_get_context_returns_note_context(self, search_mgr: SearchManager) -> None:
        """get_context() returns a NoteContext instance."""
        ctx = search_mgr.get_context("alpha.md")
        assert isinstance(ctx, NoteContext)

    def test_get_context_basic_fields(self, search_mgr: SearchManager) -> None:
        """get_context() populates basic fields."""
        ctx = search_mgr.get_context("alpha.md")
        assert ctx.path == "alpha.md"
        assert ctx.title == "Alpha"
        assert ctx.folder == ""
        assert "tags" in ctx.frontmatter

    def test_get_context_backlinks(self, search_mgr: SearchManager) -> None:
        """get_context() includes backlinks from linked notes."""
        ctx = search_mgr.get_context("alpha.md")
        # beta links back to alpha.
        bl_sources = [bl.source_path for bl in ctx.backlinks]
        assert "beta.md" in bl_sources

    def test_get_context_outlinks(self, search_mgr: SearchManager) -> None:
        """get_context() includes outlinks."""
        ctx = search_mgr.get_context("alpha.md")
        ol_targets = [ol.target_path for ol in ctx.outlinks]
        assert "beta.md" in ol_targets

    def test_get_context_folder_notes(self, search_mgr: SearchManager) -> None:
        """get_context() includes folder peers excluding self."""
        ctx = search_mgr.get_context("alpha.md")
        # alpha.md is at root, beta.md is peer.
        assert "alpha.md" not in ctx.folder_notes
        assert "beta.md" in ctx.folder_notes

    def test_get_context_tags(self, search_mgr: SearchManager) -> None:
        """get_context() includes indexed tags."""
        ctx = search_mgr.get_context("alpha.md")
        assert "tags" in ctx.tags
        assert "a" in ctx.tags["tags"]
        assert "b" in ctx.tags["tags"]

    def test_get_context_similar_empty_without_embeddings(
        self, search_mgr: SearchManager
    ) -> None:
        """get_context() returns empty similar list without embeddings."""
        ctx = search_mgr.get_context("alpha.md")
        assert ctx.similar == []

    def test_get_context_raises_for_nonexistent(
        self, search_mgr: SearchManager
    ) -> None:
        """get_context() raises for non-existent document."""
        with pytest.raises(ValueError, match="Document not found"):
            search_mgr.get_context("no_such.md")

    def test_get_context_raises_for_non_md(self, search_mgr: SearchManager) -> None:
        """get_context() raises for non-.md path."""
        with pytest.raises(ValueError, match="Path must end with"):
            search_mgr.get_context("image.png")

    def test_get_context_link_limit(self, search_mgr: SearchManager) -> None:
        """get_context() respects link_limit."""
        ctx = search_mgr.get_context("alpha.md", link_limit=0)
        assert ctx.backlinks == []
        assert ctx.outlinks == []


# ---------------------------------------------------------------------------
# _get_frontmatter helper
# ---------------------------------------------------------------------------


class TestGetFrontmatter:
    def test_returns_dict_for_existing_note(self, search_mgr: SearchManager) -> None:
        """_get_frontmatter returns parsed dict for existing note."""
        fm = search_mgr._get_frontmatter("alpha.md")
        assert isinstance(fm, dict)
        assert "tags" in fm

    def test_returns_empty_for_missing_note(self, search_mgr: SearchManager) -> None:
        """_get_frontmatter returns {} for missing note."""
        fm = search_mgr._get_frontmatter("nonexistent.md")
        assert fm == {}


# ---------------------------------------------------------------------------
# _fts_row_to_note_info module function
# ---------------------------------------------------------------------------


class TestFtsRowToNoteInfo:
    def test_valid_row(self) -> None:
        """_fts_row_to_note_info converts a row dict to NoteInfo."""
        row = {
            "path": "test.md",
            "title": "Test",
            "folder": "",
            "frontmatter_json": '{"tags": ["x"]}',
            "modified_at": 1234567890.0,
        }
        result = _fts_row_to_note_info(row)
        assert isinstance(result, NoteInfo)
        assert result.path == "test.md"
        assert result.title == "Test"
        assert result.frontmatter == {"tags": ["x"]}

    def test_invalid_json(self) -> None:
        """_fts_row_to_note_info with bad JSON returns empty frontmatter."""
        row = {
            "path": "bad.md",
            "title": "Bad",
            "folder": "",
            "frontmatter_json": "not-json{{{",
            "modified_at": 0.0,
        }
        result = _fts_row_to_note_info(row)
        assert result.frontmatter == {}

    def test_none_json(self) -> None:
        """_fts_row_to_note_info with None JSON returns empty frontmatter."""
        row = {
            "path": "none.md",
            "title": "None",
            "folder": "",
            "frontmatter_json": None,
            "modified_at": 0.0,
        }
        result = _fts_row_to_note_info(row)
        assert result.frontmatter == {}


# ---------------------------------------------------------------------------
# vectors property
# ---------------------------------------------------------------------------


class TestVectorsProperty:
    def test_vectors_initially_none(self, search_mgr: SearchManager) -> None:
        """vectors property is None when no embeddings configured."""
        assert search_mgr.vectors is None

    def test_vectors_setter(self, search_mgr: SearchManager) -> None:
        """vectors property can be set."""
        search_mgr.vectors = None  # type: ignore[assignment]
        assert search_mgr.vectors is None


# ---------------------------------------------------------------------------
# _is_path_excluded / _effective_attachment_extensions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_is_path_excluded_no_patterns(self, search_mgr: SearchManager) -> None:
        """_is_path_excluded returns False when no patterns configured."""
        assert not search_mgr._is_path_excluded("anything.md")

    def test_is_path_excluded_with_patterns(self, search_vault: Path) -> None:
        """_is_path_excluded returns True for matching patterns."""
        fts = FTSIndex(db_path=":memory:")
        mgr = SearchManager(
            fts=fts,
            source_dir=search_vault,
            exclude_patterns=["drafts/*"],
        )
        assert mgr._is_path_excluded("drafts/wip.md")
        assert not mgr._is_path_excluded("notes/final.md")

    def test_effective_attachment_extensions_default(self, search_vault: Path) -> None:
        """Default attachment extensions are returned when none configured."""
        fts = FTSIndex(db_path=":memory:")
        mgr = SearchManager(fts=fts, source_dir=search_vault)
        exts = mgr._effective_attachment_extensions()
        assert "png" in exts
        assert "pdf" in exts

    def test_effective_attachment_extensions_custom(
        self, search_mgr: SearchManager
    ) -> None:
        """Custom attachment extensions are returned when configured."""
        exts = search_mgr._effective_attachment_extensions()
        assert exts == frozenset(["png"])


# ---------------------------------------------------------------------------
# _validate_path
# ---------------------------------------------------------------------------


class TestValidatePath:
    def test_valid_path(self, search_mgr: SearchManager) -> None:
        """Valid .md path does not raise."""
        search_mgr._validate_path("alpha.md")  # should not raise

    def test_non_md_path(self, search_mgr: SearchManager) -> None:
        """Non-.md path raises ValueError."""
        with pytest.raises(ValueError, match="must end with"):
            search_mgr._validate_path("image.png")

    def test_traversal_path(self, search_mgr: SearchManager) -> None:
        """Path traversal raises ValueError."""
        with pytest.raises(ValueError, match="traversal"):
            search_mgr._validate_path("../../etc/passwd.md")


# ---------------------------------------------------------------------------
# keyword pipeline — chunks_per_file cap + snippet projection
# ---------------------------------------------------------------------------


def test_keyword_search_applies_chunks_per_file_cap(search_mgr: SearchManager) -> None:
    """Two chunks from the same doc cannot both occupy the top-N."""
    # The search_vault fixture has single-chunk docs, so this test asserts
    # the manager honours the chunks_per_file parameter without erroring;
    # the pathology case is exercised in tests/test_search_pipeline_integration.py.
    results = search_mgr.search("world", mode="keyword", chunks_per_file=1, limit=10)
    paths_in_top = [r.path for r in results]
    assert len(set(paths_in_top)) == len(paths_in_top)


def test_keyword_search_returns_snippet(search_mgr: SearchManager) -> None:
    """When snippet_words is set, section content is shorter than (or equal to) the full chunk."""
    long_results = search_mgr.search("world", mode="keyword", snippet_words=0, limit=10)
    short_results = search_mgr.search(
        "world", mode="keyword", snippet_words=3, limit=10
    )

    def _join_sections(groups: list) -> list[str]:
        # Flatten each GroupedResult's sections to one content string per
        # surviving file so the per-position comparison still makes sense
        # when files have multiple sections.
        return [" ".join(s.content for s in g.sections) for g in groups]

    short_contents = _join_sections(short_results)
    long_contents = _join_sections(long_results)
    assert all(
        len(s.split()) <= len(lg.split())
        for s, lg in zip(short_contents, long_contents, strict=False)
    )


@pytest.fixture()
def search_mgr_with_embeddings(search_vault: Path) -> SearchManager:
    """Build a SearchManager with a deterministic mock embedding provider."""
    from markdown_vault_mcp.vector_index import VectorIndex
    from tests.conftest import MockEmbeddingProvider

    fts = FTSIndex(db_path=":memory:", indexed_frontmatter_fields=["tags"])
    for note in scan_directory(search_vault):
        fts.upsert_note(note)
    fts.resolve_vault_wikilinks()
    provider = MockEmbeddingProvider()
    embeddings_path = search_vault / "embeddings"
    vectors = VectorIndex(provider)
    for note in scan_directory(search_vault):
        texts = [c.content for c in note.chunks]
        from pathlib import Path as _Path

        meta = [
            {
                "path": note.path,
                "title": note.title,
                "folder": (
                    ""
                    if _Path(note.path).parent.as_posix() == "."
                    else _Path(note.path).parent.as_posix()
                ),
                "heading": c.heading,
                "content": c.content,
            }
            for c in note.chunks
        ]
        if texts:
            vectors.add(texts, meta)
    vectors.save(embeddings_path)
    mgr = SearchManager(
        fts=fts,
        source_dir=search_vault,
        embeddings_path=embeddings_path,
        embedding_provider=provider,
        indexed_frontmatter_fields=["tags"],
    )
    mgr._vectors = vectors
    return mgr


def test_semantic_search_applies_chunks_per_file_cap_and_snippet(
    search_mgr_with_embeddings: SearchManager,
) -> None:
    """Semantic mode honours chunks_per_file and snippet_words."""
    results = search_mgr_with_embeddings.search(
        "world",
        mode="semantic",
        chunks_per_file=1,
        snippet_words=5,
        limit=10,
    )
    paths = [r.path for r in results]
    # File-level uniqueness is intrinsic to the grouped shape.
    assert len(set(paths)) == len(paths)
    # chunks_per_file=1 → at most one section per group → check section length.
    for r in results:
        assert len(r.sections) <= 1
        for s in r.sections:
            assert len(s.content.split()) <= 10


class TestGetSimilarFilters:
    """Folder and frontmatter filter params on get_similar (post-filtered)."""

    def test_folder_filter_restricts_results(
        self, search_mgr_with_embeddings: SearchManager
    ) -> None:
        results = search_mgr_with_embeddings.get_similar("alpha.md", folder="notes")
        paths = [r.path for r in results]
        assert paths, "expected candidates within notes/"
        assert all(p.startswith("notes/") for p in paths)

    def test_folder_filter_normalizes_trailing_slash(
        self, search_mgr_with_embeddings: SearchManager
    ) -> None:
        # "3-Resources/"-style input (natural after path joining) must not
        # silently match nothing.
        with_slash = search_mgr_with_embeddings.get_similar("alpha.md", folder="notes/")
        without = search_mgr_with_embeddings.get_similar("alpha.md", folder="notes")
        assert [r.path for r in with_slash] == [r.path for r in without]
        assert with_slash, "expected candidates within notes/"

    def test_folder_filter_is_prefix_not_substring(
        self, search_mgr_with_embeddings: SearchManager
    ) -> None:
        # "note" is a prefix of the "notes" folder *name* but not a folder —
        # exact-or-"note/" matching must exclude notes/* results.
        results = search_mgr_with_embeddings.get_similar("alpha.md", folder="note")
        assert results == []

    def test_frontmatter_filter_scalar_and_list(
        self, search_mgr_with_embeddings: SearchManager
    ) -> None:
        # tags is list-valued; membership matching applies.
        results = search_mgr_with_embeddings.get_similar(
            "alpha.md", filters={"tags": "b"}
        )
        paths = [r.path for r in results]
        assert paths == ["beta.md"]
        # title is a scalar field NOT in indexed_frontmatter_fields — the
        # post-filter matches any frontmatter key.
        results = search_mgr_with_embeddings.get_similar(
            "alpha.md", filters={"title": "Gamma"}
        )
        assert [r.path for r in results] == ["notes/gamma.md"]

    def test_combined_filters_and_empty_result(
        self, search_mgr_with_embeddings: SearchManager
    ) -> None:
        results = search_mgr_with_embeddings.get_similar(
            "alpha.md", folder="notes", filters={"tags": "c"}
        )
        assert [r.path for r in results] == ["notes/gamma.md"]
        results = search_mgr_with_embeddings.get_similar(
            "alpha.md", folder="notes", filters={"tags": "b"}
        )
        assert results == []

    def test_no_filters_unchanged_behavior(
        self, search_mgr_with_embeddings: SearchManager
    ) -> None:
        unfiltered = search_mgr_with_embeddings.get_similar("alpha.md")
        assert [r.path for r in unfiltered] == [
            r.path for r in search_mgr_with_embeddings.get_similar("alpha.md")
        ]
        assert "alpha.md" not in [r.path for r in unfiltered]


class _FixedQueryProvider:
    """Embeds every query to the same unit vector along the first axis.

    The stored document vectors are written directly onto the index, so the
    cosine of each chunk against the query is just that chunk's first
    coordinate. This gives a test exact control over chunk ranks.
    """

    provider_name = "fixed"
    model_name = "fixed-model"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def _semantic_mgr_with_ranked_chunks(base: Path) -> SearchManager:
    """SearchManager over a store where a small TARGET doc's only chunk sits
    at global cosine rank 51, behind 50 chunks from two large documents.

    Only three documents exist, so a caller asking for as few as three results
    wants TARGET among them. It is reachable only if the semantic candidate
    pool extends past rank 51.
    """
    import numpy as np

    from markdown_vault_mcp.vector_index import VectorIndex

    provider = _FixedQueryProvider()
    vi = VectorIndex(provider)
    texts: list[str] = []
    meta: list[dict[str, object]] = []
    vecs: list[np.ndarray] = []

    def unit(cos: float) -> np.ndarray:
        v = np.array([cos, np.sqrt(max(0.0, 1 - cos * cos))], dtype=np.float32)
        return v / np.linalg.norm(v)

    cos = 0.7500
    for doc in ("giantA", "giantB"):
        for j in range(25):
            texts.append(f"{doc} chunk {j}")
            meta.append(
                {
                    "path": f"{doc}.md",
                    "title": doc,
                    "folder": "",
                    "heading": f"h{j}",
                    "content": f"{doc} chunk {j}",
                }
            )
            vecs.append(unit(cos))
            cos += 0.0005
    texts.append("target unique answer")
    meta.append(
        {
            "path": "TARGET.md",
            "title": "Target",
            "folder": "",
            "heading": None,
            "content": "target unique answer",
        }
    )
    vecs.append(unit(0.7499))  # just below every giant chunk -> global rank 51

    vi.add(texts, meta)
    vi._embeddings = np.vstack(vecs).astype(np.float32)
    vi.save(base)

    fts = FTSIndex(db_path=":memory:")
    mgr = SearchManager(
        fts=fts,
        source_dir=base.parent,
        embeddings_path=base,
        embedding_provider=provider,
        indexed_frontmatter_fields=[],
    )
    mgr._vectors = vi
    return mgr


def test_semantic_recall_of_best_document_is_independent_of_limit(
    tmp_path: Path,
) -> None:
    """The true-best document is returned at a small limit, not only a large one.

    With three documents total and TARGET's only chunk at global cosine rank
    51, a floor-50 candidate pool fills entirely with the two large documents'
    chunks and drops TARGET at any small limit. Recall of a real document must
    not depend on how large a limit the caller happened to pass.
    """
    mgr = _semantic_mgr_with_ranked_chunks(tmp_path / "embeddings")
    results = mgr.search("q", mode="semantic", limit=5, chunks_per_file=2)
    paths = [r.path for r in results]
    assert "TARGET.md" in paths


def test_hybrid_recall_of_best_document_is_independent_of_limit(
    tmp_path: Path,
) -> None:
    """Hybrid's vector channel floors its candidate pool like semantic does.

    The store has an empty FTS index, so the keyword channel contributes
    nothing and TARGET (no keyword overlap) is reachable only through the
    vector channel. TARGET's only chunk sits at global cosine rank 51. If the
    vector channel inherits the keyword channel's floor-50 candidate limit, the
    pool fills with the two large documents' chunks and TARGET never enters the
    RRF merge at a small limit. The vector-side floor must be sized like the
    semantic path so recall does not depend on the caller's limit.
    """
    mgr = _semantic_mgr_with_ranked_chunks(tmp_path / "embeddings")
    results = mgr.search("q", mode="hybrid", limit=5, chunks_per_file=2)
    paths = [r.path for r in results]
    assert "TARGET.md" in paths


def test_hybrid_search_caps_per_file_after_rrf(
    search_mgr_with_embeddings: SearchManager,
) -> None:
    results = search_mgr_with_embeddings.search(
        "world",
        mode="hybrid",
        chunks_per_file=1,
        snippet_words=5,
        limit=10,
    )
    paths = [r.path for r in results]
    assert len(set(paths)) == len(paths)
    for r in results:
        assert len(r.sections) <= 1


# ---------------------------------------------------------------------------
# folder-scoped semantic search is limit-independent (#1108)
# ---------------------------------------------------------------------------


@pytest.fixture()
def buried_folder_vault(tmp_path: Path) -> Path:
    """Vault where the ``niche/`` notes are outnumbered by bulk content."""
    for i in range(30):
        (tmp_path / f"bulk{i}.md").write_text(
            f"---\ntitle: Bulk {i}\n---\n# Bulk {i}\n\nBulk body {i}.\n",
            encoding="utf-8",
        )
    (tmp_path / "niche").mkdir()
    for i in range(2):
        (tmp_path / "niche" / f"n{i}.md").write_text(
            f"---\ntitle: Niche {i}\n---\n# Niche {i}\n\nNiche body {i}.\n",
            encoding="utf-8",
        )
    return tmp_path


@pytest.fixture()
def buried_folder_mgr(
    buried_folder_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> SearchManager:
    """SearchManager over ``buried_folder_vault`` with a small candidate floor.

    The production floor is 1000 chunks; shrinking it to 3 reproduces the
    starvation a 27k-chunk vault shows at the default floor without
    building a 27k-chunk fixture.
    """
    from markdown_vault_mcp.vector_index import VectorIndex
    from tests.conftest import MockEmbeddingProvider

    monkeypatch.setattr(
        "markdown_vault_mcp.managers.search._SEMANTIC_CANDIDATE_FLOOR", 3
    )
    fts = FTSIndex(db_path=":memory:")
    provider = MockEmbeddingProvider()
    vectors = VectorIndex(provider)
    for note in scan_directory(buried_folder_vault):
        fts.upsert_note(note)
        texts = [c.content for c in note.chunks]
        folder = "niche" if note.path.startswith("niche/") else ""
        if texts:
            vectors.add(
                texts,
                [
                    {
                        "path": note.path,
                        "title": note.title,
                        "folder": folder,
                        "heading": c.heading,
                        "content": c.content,
                    }
                    for c in note.chunks
                ],
            )
    fts.resolve_vault_wikilinks()
    embeddings_path = buried_folder_vault / "embeddings"
    vectors.save(embeddings_path)
    mgr = SearchManager(
        fts=fts,
        source_dir=buried_folder_vault,
        embeddings_path=embeddings_path,
        embedding_provider=provider,
    )
    mgr._vectors = vectors
    return mgr


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_folder_scoped_semantic_search_is_limit_independent(
    buried_folder_mgr: SearchManager, mode: str
) -> None:
    """A folder-scoped search answers from the folder at any limit (#1108).

    The candidate pool used to be capped before the folder post-filter ran,
    so a folder whose chunks all ranked below the cap came back empty —
    and the caller could not tell that from a folder with no related
    content. Raising ``limit`` used to be the only way to surface them.
    """
    small = buried_folder_mgr.search("body", mode=mode, folder="niche", limit=1)
    assert small, f"expected a niche/ hit at limit=1 in mode={mode}"
    assert all(r.path.startswith("niche/") for r in small)

    large = buried_folder_mgr.search("body", mode=mode, folder="niche", limit=100)
    assert {r.path for r in large} == {"niche/n0.md", "niche/n1.md"}
    assert {r.path for r in small} <= {r.path for r in large}


def test_folder_scoped_get_similar_keeps_room_for_other_files(
    search_mgr_with_embeddings: SearchManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long in-folder document must not crowd every other file out (#1108).

    The folder predicate stops out-of-scope rows from consuming the cap, but
    not one in-scope document's own chunks: grouping keeps ``chunks_per_file``
    per path, so a pool filled by a single path collapses to a single result.
    ``get_similar`` has always widened its pool when a folder is given, and
    the fake store below returns a ranking where dropping that widening loses
    the second file entirely.
    """
    ranking = [
        {
            "path": "notes/long.md",
            "title": "Long",
            "folder": "notes",
            "heading": f"H{i}",
            "content": f"chunk {i}",
            "score": 1.0 - i / 1000,
            "start_line": i,
        }
        for i in range(120)
    ]
    ranking.append(
        {
            "path": "notes/other.md",
            "title": "Other",
            "folder": "notes",
            "heading": "Only",
            "content": "other chunk",
            "score": 0.1,
            "start_line": 0,
        }
    )

    def fake_search_by_path(_path, *, limit, predicate=None):
        eligible = [r for r in ranking if predicate is None or predicate(r)]
        return [dict(r) for r in eligible[:limit]]

    monkeypatch.setattr(
        search_mgr_with_embeddings._vectors, "search_by_path", fake_search_by_path
    )

    results = search_mgr_with_embeddings.get_similar(
        "alpha.md", folder="notes", limit=2, chunks_per_file=1
    )
    assert {r.path for r in results} == {"notes/long.md", "notes/other.md"}


def test_folder_scoped_get_similar_is_limit_independent(
    buried_folder_mgr: SearchManager,
) -> None:
    """``get_similar`` scopes inside the similarity scan too (#1108)."""
    results = buried_folder_mgr.get_similar("bulk0.md", folder="niche", limit=1)
    assert results, "expected a niche/ hit at limit=1"
    assert all(r.path.startswith("niche/") for r in results)


# ---------------------------------------------------------------------------
# folder="X/" — trailing/leading slashes fold on every surface (#1103)
# ---------------------------------------------------------------------------


NOTES_FOLDER_NOTES = {"notes/gamma.md", "notes/delta.md"}


@pytest.mark.parametrize("spelling", ["notes/", "/notes", "/notes/", "notes\\"])
@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_folder_spellings_select_the_same_notes(
    search_mgr_with_embeddings: SearchManager, mode: str, spelling: str
) -> None:
    """Every natural spelling of a folder selects the same notes (#1103).

    The SQL-backed channels compared the value as received, so a trailing
    slash matched no row and the call returned ``[]`` — indistinguishable
    from an empty folder.
    """
    canonical = search_mgr_with_embeddings.search(
        "gamma", mode=mode, folder="notes", limit=10
    )
    assert canonical, f"fixture precondition: no {mode} hits under notes/"
    results = search_mgr_with_embeddings.search(
        "gamma", mode=mode, folder=spelling, limit=10
    )
    assert {r.path for r in results} == {r.path for r in canonical}
    assert {r.path for r in results} <= NOTES_FOLDER_NOTES


@pytest.mark.parametrize("spelling", ["notes/", "/notes", "notes\\"])
def test_list_documents_folds_folder_spellings(
    search_mgr_with_embeddings: SearchManager, spelling: str
) -> None:
    """``list_documents`` folds the same spellings (#1103)."""
    listed = search_mgr_with_embeddings.list(folder=spelling)
    assert {n.path for n in listed} == NOTES_FOLDER_NOTES


@pytest.mark.parametrize("spelling", ["notes/", "/notes", "notes\\"])
def test_get_recent_folds_folder_spellings(
    search_mgr_with_embeddings: SearchManager, spelling: str
) -> None:
    """``get_recent`` folds the same spellings (#1103)."""
    recent = search_mgr_with_embeddings.get_recent(folder=spelling)
    assert {n.path for n in recent} == NOTES_FOLDER_NOTES


@pytest.mark.parametrize("spelling", ["notes/", "/notes", "notes\\"])
def test_get_similar_folds_folder_spellings(
    search_mgr_with_embeddings: SearchManager, spelling: str
) -> None:
    """``get_similar`` folds the same spellings (#1103)."""
    results = search_mgr_with_embeddings.get_similar(
        "alpha.md", folder=spelling, limit=10
    )
    assert results, f"expected results for folder={spelling!r}"
    assert {r.path for r in results} <= NOTES_FOLDER_NOTES


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_root_slash_folder_is_the_root_selector(
    search_mgr_with_embeddings: SearchManager, mode: str
) -> None:
    """``folder="/"`` folds to the root selector rather than matching nothing."""
    results = search_mgr_with_embeddings.search(
        "world", mode=mode, folder="/", limit=10
    )
    assert results, f"expected root-level results in mode={mode}"
    assert {r.path for r in results} <= ROOT_NOTES


# ---------------------------------------------------------------------------
# folder="" — root-level only, on every channel (#1106)
# ---------------------------------------------------------------------------


ROOT_NOTES = {"alpha.md", "beta.md"}


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_empty_folder_restricts_search_to_root_level(
    search_mgr_with_embeddings: SearchManager, mode: str
) -> None:
    """``folder=""`` selects root-level notes only, in every mode (#1106).

    The vector channel used to collapse ``""`` to "no restriction", so
    semantic and hybrid answered from the whole vault while keyword and
    ``list_documents`` honoured the documented root-only contract.
    """
    results = search_mgr_with_embeddings.search("world", mode=mode, folder="", limit=10)
    assert results, f"expected root-level results in mode={mode}"
    assert {r.path for r in results} <= ROOT_NOTES


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_empty_folder_is_not_the_same_as_no_folder(
    search_mgr_with_embeddings: SearchManager, mode: str
) -> None:
    """An unrestricted search reaches sub-folders that ``folder=""`` excludes."""
    unrestricted = search_mgr_with_embeddings.search(
        "world", mode=mode, folder=None, limit=10
    )
    assert any(r.path.startswith("notes/") for r in unrestricted)


def test_empty_folder_restricts_get_similar_to_root_level(
    search_mgr_with_embeddings: SearchManager,
) -> None:
    """``get_similar`` shares the root-only contract (#1106)."""
    results = search_mgr_with_embeddings.get_similar("alpha.md", folder="", limit=10)
    assert {r.path for r in results} <= ROOT_NOTES


def test_empty_folder_restricts_list_to_root_level(
    search_mgr_with_embeddings: SearchManager,
) -> None:
    """``list_documents`` already honoured the contract; keep it that way."""
    listed = search_mgr_with_embeddings.list(folder="")
    assert {n.path for n in listed} == ROOT_NOTES


def test_hybrid_folder_filter_normalizes_trailing_slash(
    search_mgr_with_embeddings: SearchManager,
) -> None:
    """Hybrid's vector channel shares semantic's folder normalization (#878).

    A natural ``"notes/"`` (trailing slash) must not silently filter the
    vector channel to nothing while ``mode="semantic"`` handles the same
    input correctly.
    """
    with_slash = search_mgr_with_embeddings.search(
        "world", mode="hybrid", folder="notes/", limit=10
    )
    assert with_slash, "expected hybrid results within notes/"
    assert all(r.path.startswith("notes/") for r in with_slash)
    # The vector channel must surface the same files the semantic mode does
    # for the identical un-normalized input.
    semantic = search_mgr_with_embeddings.search(
        "world", mode="semantic", folder="notes/", limit=10
    )
    assert {r.path for r in with_slash} == {r.path for r in semantic}


def test_hybrid_search_uses_fts_snippet_for_keyword_hits(
    search_mgr_with_embeddings: SearchManager,
) -> None:
    """Bound payload size in hybrid mode."""
    results = search_mgr_with_embeddings.search(
        "world",
        mode="hybrid",
        chunks_per_file=2,
        snippet_words=3,
        limit=10,
    )
    # Each section's content snippet is at most ~8 words (snippet_words=3 plus
    # an ellipsis word at each end).
    for r in results:
        for s in r.sections:
            assert len(s.content.split()) <= 8


def test_hybrid_search_labels_both_channel_hits_as_hybrid(
    search_mgr_with_embeddings: SearchManager,
) -> None:
    """Files containing a chunk present in both channels are labelled 'hybrid'."""
    results = search_mgr_with_embeddings.search(
        "world",
        mode="hybrid",
        chunks_per_file=10,
        snippet_words=0,
        limit=20,
    )
    # At least one result should be 'hybrid' (head chunk hit in both channels).
    assert any(r.search_type == "hybrid" for r in results), (
        f"expected at least one 'hybrid' label; got {[r.search_type for r in results]}"
    )


def test_hybrid_search_search_type_is_group_union_not_head(
    monkeypatch: pytest.MonkeyPatch,
    search_mgr_with_embeddings: SearchManager,
) -> None:
    """File-level search_type is the union over the group's sections.

    Regression for the case where the head section is single-channel but a
    lower section in the same file is in both channels.  The file as a whole
    spans both channels, so it must be labelled "hybrid" — not "keyword".
    """
    from markdown_vault_mcp.types import FTSResult

    # Stub FTS: alpha.md returns ONE keyword-only chunk at heading "Top".
    fake_fts = [
        FTSResult(
            path="alpha.md",
            title="Alpha",
            folder="",
            heading="Top",
            content="hello world",
            score=1.0,
        ),
        FTSResult(
            path="alpha.md",
            title="Alpha",
            folder="",
            heading="Shared",
            content="shared text",
            score=0.9,
        ),
    ]

    def fake_search(_query, *, limit, filters=None, folder=None, snippet_words=None):  # noqa: ARG001
        return list(fake_fts)

    monkeypatch.setattr(search_mgr_with_embeddings._fts, "search", fake_search)

    # Stub vectors: alpha.md/"Shared" appears in both channels; alpha.md/"VecOnly"
    # appears in semantic only.  The head section of the group (highest RRF score)
    # will be alpha.md/"Top" — keyword-only — yet the file must still be "hybrid".
    vectors = search_mgr_with_embeddings._load_vectors()
    fake_vec_rows = [
        {
            "path": "alpha.md",
            "title": "Alpha",
            "folder": "",
            "heading": "Shared",
            "content": "shared text",
            "score": 0.8,
            "start_line": 10,
        },
        {
            "path": "alpha.md",
            "title": "Alpha",
            "folder": "",
            "heading": "VecOnly",
            "content": "semantic-only chunk",
            "score": 0.7,
            "start_line": 20,
        },
    ]

    def fake_vec_search(_query, *, limit, predicate=None):  # noqa: ARG001
        return list(fake_vec_rows)

    monkeypatch.setattr(vectors, "search", fake_vec_search)

    results = search_mgr_with_embeddings.search(
        "world",
        mode="hybrid",
        chunks_per_file=10,
        snippet_words=0,
        limit=5,
    )
    alpha = next(r for r in results if r.path == "alpha.md")
    section_headings = [s.heading for s in alpha.sections]
    assert "Top" in section_headings, (
        f"expected 'Top' section to survive grouping; got {section_headings}"
    )
    assert "Shared" in section_headings or "VecOnly" in section_headings, (
        f"expected at least one cross-channel section to survive grouping; "
        f"got {section_headings}"
    )
    # The file as a whole spans both channels → must be "hybrid", not "keyword".
    assert alpha.search_type == "hybrid", (
        f"file-level search_type should be union over sections; got "
        f"{alpha.search_type!r} when sections include both keyword-only and "
        f"semantic-only chunks"
    )


def test_fetch_snippet_map_widens_pool_to_match_caller(
    monkeypatch: pytest.MonkeyPatch,
    search_mgr: SearchManager,
) -> None:
    """_fetch_snippet_map's second FTS query must use a candidate pool at
    least as wide as the caller's initial candidate_limit, so survivors
    ranked low in the initial pool aren't dropped from the snippet
    projection."""
    captured: list[int] = []

    real_search = search_mgr._fts.search

    def spy(*args, **kwargs):
        if "snippet_words" in kwargs and (kwargs.get("snippet_words") or 0) > 0:
            captured.append(int(kwargs.get("limit", 0)))
        return real_search(*args, **kwargs)

    monkeypatch.setattr(search_mgr._fts, "search", spy)

    # Run a keyword search with a wide candidate_limit (default formula:
    # max(limit * (chunks_per_file + 4), 50) → 60 at limit=10, chunks_per_file=2).
    search_mgr.search("world", mode="keyword", limit=10)

    assert captured, "snippet re-query did not run"
    assert captured[0] >= 60, (
        f"snippet re-query limit {captured[0]} < initial pool floor 60"
    )


def test_semantic_search_does_not_have_flush_embeddings_attr(tmp_path):
    """SearchManager no longer carries a _flush_embeddings attribute (#559).

    The dead callback parameter was removed in this PR; the writer thread
    is now the sole owner of embedding flushes. Structurally guarantee
    that the attribute is gone so a regression would surface immediately.
    """
    from markdown_vault_mcp.vault import Vault
    from tests.conftest import MockEmbeddingProvider

    col = Vault(
        source_dir=tmp_path,
        read_only=False,
        embeddings_path=tmp_path / "vec",
        embedding_provider=MockEmbeddingProvider(),
    )
    try:
        (tmp_path / "n.md").write_text("# n\n\nhello", encoding="utf-8")
        col.index.build_index()
        col.index.build_embeddings()

        # The attribute must not exist any more.
        assert not hasattr(col._search_mgr, "_flush_embeddings")

        # Semantic search still works without it.
        col.reader.search(query="hello", mode="semantic")
    finally:
        col.close()


class TestSearchLoadVectorsSelfHeal:
    """SearchManager._load_vectors must self-heal a corrupt sidecar.

    SearchManager owns the shared VectorIndex and has its own ``_load_vectors``
    that loads the sidecar from disk; if a search query is the first access
    after a crash-interrupted save, it — not IndexManager — sees the corrupt
    pair. ``VectorIndex.load`` raises ``VectorIndexCorruptError`` (a
    ``RuntimeError``) on a row-count mismatch, so this path must catch it and
    route to the injected rebuild rather than crash the search call (#734).
    """

    @staticmethod
    def _corrupt_json_drop_row(mgr: SearchManager) -> None:
        """Drop the last metadata row from the on-disk .json (count mismatch)."""
        import json

        json_path = mgr._embeddings_path.with_suffix(".json")  # type: ignore[union-attr]
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        payload["rows"] = payload["rows"][:-1]
        json_path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _arm_rebuild(mgr: SearchManager) -> None:
        """Wire a rebuild callback that repopulates the shared slot, then drop cache.

        Mirrors what the coordinator's writer-routed rebuild does — sets
        ``mgr.vectors`` to a populated index — so the self-heal path returns a
        usable index rather than tripping the rebuild-failure guard.
        """
        from markdown_vault_mcp.vector_index import VectorIndex

        assert mgr._embedding_provider is not None
        rebuilt = VectorIndex(mgr._embedding_provider)
        rebuilt.add(["alpha"], [{"path": "a.md", "title": "A", "heading": None}])
        mgr._rebuild_embeddings = lambda: setattr(mgr, "vectors", rebuilt)
        mgr._vectors = None  # force a re-read from disk

    def test_corrupt_sidecar_triggers_rebuild(
        self,
        search_mgr_with_embeddings: SearchManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A count-mismatched sidecar routes to the rebuild callback, not a crash."""
        import logging

        from markdown_vault_mcp.vector_index import VectorIndex

        mgr = search_mgr_with_embeddings
        self._corrupt_json_drop_row(mgr)
        mgr._vectors = None  # force a re-read from disk

        # Mirror what the coordinator's rebuild does: repopulate the shared slot.
        assert mgr._embedding_provider is not None
        rebuilt = VectorIndex(mgr._embedding_provider)
        rebuilt.add(["alpha"], [{"path": "a.md", "title": "A", "heading": None}])

        def rebuild() -> None:
            mgr.vectors = rebuilt

        mgr._rebuild_embeddings = rebuild

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.search"
        ):
            result = mgr._load_vectors()

        assert result is rebuilt
        assert any(
            "vector_index_corrupt_rebuilding" in r.getMessage() for r in caplog.records
        )

    def test_corrupt_sidecar_rebuild_failure_raises(
        self, search_mgr_with_embeddings: SearchManager
    ) -> None:
        """If the rebuild leaves no index, a corrupt sidecar surfaces a ValueError."""
        mgr = search_mgr_with_embeddings
        self._corrupt_json_drop_row(mgr)
        mgr._vectors = None
        mgr._rebuild_embeddings = lambda: None  # no-op leaves _vectors None

        with pytest.raises(
            ValueError, match="Failed to rebuild vector index after a corrupt sidecar"
        ):
            mgr._load_vectors()

    def test_zero_byte_npy_triggers_rebuild(
        self,
        search_mgr_with_embeddings: SearchManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A zero-byte .npy raises EOFError from numpy — must rebuild, not propagate.

        EOFError is neither a ValueError nor an OSError, so the SearchManager
        catch tuple must name it explicitly (parity with IndexManager).
        """
        import logging

        mgr = search_mgr_with_embeddings
        npy_path = mgr._embeddings_path.with_suffix(".npy")  # type: ignore[union-attr]
        npy_path.write_bytes(b"")
        self._arm_rebuild(mgr)

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.search"
        ):
            result = mgr._load_vectors()

        assert result.count >= 1
        assert any(
            "vector_index_corrupt_rebuilding" in r.getMessage() for r in caplog.records
        )

    def test_garbage_npy_triggers_rebuild(
        self,
        search_mgr_with_embeddings: SearchManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A garbage (non-numpy) .npy raises ValueError — must rebuild."""
        import logging

        mgr = search_mgr_with_embeddings
        npy_path = mgr._embeddings_path.with_suffix(".npy")  # type: ignore[union-attr]
        npy_path.write_bytes(b"this is not a numpy array at all")
        self._arm_rebuild(mgr)

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.search"
        ):
            result = mgr._load_vectors()

        assert result.count >= 1
        assert any(
            "vector_index_corrupt_rebuilding" in r.getMessage() for r in caplog.records
        )

    def test_missing_json_with_present_npy_triggers_rebuild(
        self,
        search_mgr_with_embeddings: SearchManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A missing .json while the .npy exists (incomplete pair) rebuilds."""
        import logging

        mgr = search_mgr_with_embeddings
        mgr._embeddings_path.with_suffix(".json").unlink()  # type: ignore[union-attr]
        self._arm_rebuild(mgr)

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.search"
        ):
            result = mgr._load_vectors()

        assert result.count >= 1
        assert any(
            "vector_index_corrupt_rebuilding" in r.getMessage() for r in caplog.records
        )

    def test_permission_error_propagates_without_rebuild(
        self,
        search_mgr_with_embeddings: SearchManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An environmental OSError on load must propagate, not trigger a rebuild.

        PermissionError is an OSError but not one of the corruption types, so it
        is deliberately excluded from the catch tuple. Pins that contract on the
        SearchManager path too (parity with IndexManager), so a future widening
        to OSError cannot silently slip through here.
        """
        from markdown_vault_mcp.vector_index import VectorIndex

        mgr = search_mgr_with_embeddings
        mgr._vectors = None

        def boom(*_args: object, **_kwargs: object) -> VectorIndex:
            raise PermissionError("read denied")

        monkeypatch.setattr(VectorIndex, "load", boom)
        rebuild_calls: list[bool] = []
        mgr._rebuild_embeddings = lambda: rebuild_calls.append(True)

        with pytest.raises(PermissionError, match="read denied"):
            mgr._load_vectors()
        assert rebuild_calls == []  # no rebuild attempted


class TestGetContextSurfacesInternalFailure:
    def test_get_context_warns_on_internal_get_similar_failure(
        self,
        search_mgr_with_embeddings: SearchManager,
        monkeypatch: pytest.MonkeyPatch,
        caplog,
    ) -> None:
        import logging

        def boom(*_args: object, **_kwargs: object) -> list[GroupedResult]:
            # A genuine internal failure, NOT the not-configured case.
            raise ValueError("Failed to load vector index after _load_vectors()")

        monkeypatch.setattr(search_mgr_with_embeddings, "get_similar", boom)
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.search"
        ):
            result = search_mgr_with_embeddings.get_context("alpha.md")
        # Degraded, not failed: dossier returns with an empty similar list.
        assert result.similar == []
        # And the internal failure is visible at WARNING, not swallowed at DEBUG.
        assert any(
            "get_context: get_similar failed" in r.message
            and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_get_context_unconfigured_is_quiet(
        self, search_mgr: SearchManager, caplog
    ) -> None:
        import logging

        # No embeddings configured: the guard skips get_similar; no WARNING, no raise.
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.search"
        ):
            result = search_mgr.get_context("alpha.md")
        assert result.similar == []
        assert not any("get_similar failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Folder ranking boost (end-to-end, all three modes)
# ---------------------------------------------------------------------------


class _UnitQueryProvider:
    """Embeds every text (query or chunk) to the same unit vector.

    Every stored chunk then scores an identical, positive cosine against any
    query, so the folder boost alone decides the ordering.
    """

    provider_name = "unit"
    model_name = "unit-model"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture()
def boost_vault(tmp_path: Path) -> Path:
    """Two documents with identical bodies in different folders."""
    body = "# Note\n\nshared magnetberry content for ranking.\n"
    (tmp_path / "sessions").mkdir()
    (tmp_path / "curated").mkdir()
    (tmp_path / "sessions" / "log.md").write_text(body, encoding="utf-8")
    (tmp_path / "curated" / "note.md").write_text(body, encoding="utf-8")
    return tmp_path


def _boost_mgr(boost_vault: Path, **mgr_kwargs) -> SearchManager:
    """SearchManager over the boost vault with a controlled vector store."""
    from markdown_vault_mcp.vector_index import VectorIndex

    fts = FTSIndex(db_path=":memory:")
    notes = list(scan_directory(boost_vault))
    for note in notes:
        fts.upsert_note(note)

    provider = _UnitQueryProvider()
    vectors = VectorIndex(provider)
    for note in notes:
        folder = note.path.rsplit("/", 1)[0] if "/" in note.path else ""
        vectors.add(
            [c.content for c in note.chunks],
            [
                {
                    "path": note.path,
                    "title": note.title,
                    "folder": folder,
                    "heading": c.heading,
                    "content": c.content,
                    "start_line": c.start_line,
                }
                for c in note.chunks
            ],
        )
    embeddings_path = boost_vault / "embeddings"
    vectors.save(embeddings_path)
    mgr = SearchManager(
        fts=fts,
        source_dir=boost_vault,
        embeddings_path=embeddings_path,
        embedding_provider=provider,
        **mgr_kwargs,
    )
    mgr.vectors = vectors
    return mgr


class TestFolderWeightsEndToEnd:
    """folder_weights={'sessions': 0.5} demotes the session doc in all modes."""

    def test_keyword_mode_demotes_and_scales_score(self, boost_vault: Path) -> None:
        mgr = _boost_mgr(boost_vault, folder_weights={"sessions": 0.5})
        results = mgr.search("magnetberry", mode="keyword")
        assert [r.path for r in results] == ["curated/note.md", "sessions/log.md"]
        # Identical bodies score identically pre-boost, so the demoted
        # GroupedResult.score is exactly half the curated one.
        assert results[1].score == pytest.approx(results[0].score * 0.5)
        assert results[0].score > 0

    def test_semantic_mode_demotes(self, boost_vault: Path) -> None:
        mgr = _boost_mgr(boost_vault, folder_weights={"sessions": 0.5})
        results = mgr.search("magnetberry", mode="semantic")
        assert [r.path for r in results] == ["curated/note.md", "sessions/log.md"]
        assert results[1].score == pytest.approx(results[0].score * 0.5)

    def test_hybrid_mode_demotes(self, boost_vault: Path) -> None:
        mgr = _boost_mgr(boost_vault, folder_weights={"sessions": 0.5})
        results = mgr.search("magnetberry", mode="hybrid")
        assert results[0].path == "curated/note.md"
        assert results[-1].path == "sessions/log.md"
        assert results[-1].score < results[0].score

    def test_no_weights_is_unaffected(self, boost_vault: Path) -> None:
        mgr = _boost_mgr(boost_vault)
        for mode in ("keyword", "semantic", "hybrid"):
            results = mgr.search("magnetberry", mode=mode)
            assert {r.path for r in results} == {
                "curated/note.md",
                "sessions/log.md",
            }

    def test_promoting_weight_lifts_folder(self, boost_vault: Path) -> None:
        mgr = _boost_mgr(boost_vault, folder_weights={"sessions": 3.0})
        results = mgr.search("magnetberry", mode="keyword")
        assert results[0].path == "sessions/log.md"
        assert results[0].score == pytest.approx(results[1].score * 3.0)


# ---------------------------------------------------------------------------
# blank-query guard (#1111)
# ---------------------------------------------------------------------------


class _RejectsBlankProvider:
    """Provider that rejects blank input the way a real endpoint does.

    Every OpenAI-compatible endpoint answers ``[""]`` with a hard HTTP 400
    (#1087 on the index side, #1111 on the read side). The stub raises so a
    query that reaches it fails the test loudly instead of silently
    succeeding.
    """

    dimension = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        for text in texts:
            if not text.strip():
                msg = "OpenAI API error 400: Input cannot contain empty strings"
                raise RuntimeError(msg)
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_blank_query_returns_empty_without_reaching_provider(
    search_mgr_with_embeddings: SearchManager,
    mode: str,
    query: str,
) -> None:
    """A blank query resolves to [] without a provider round-trip (#1111).

    The guard lives at the manager boundary so hybrid — which reaches the
    provider through its own vector-channel call site — takes the same path
    as semantic.
    """
    search_mgr_with_embeddings._vectors._provider = _RejectsBlankProvider()  # type: ignore[assignment]

    assert search_mgr_with_embeddings.search(query, mode=mode) == []  # type: ignore[arg-type]


def test_blank_query_still_raises_when_embeddings_unconfigured(
    search_mgr: SearchManager,
) -> None:
    """The blank-query guard does not mask the unconfigured-provider error.

    ``_require_vectors`` runs first, so a caller with no embedding provider
    learns about the missing configuration rather than getting a silent ``[]``.
    """
    from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

    with pytest.raises(EmbeddingsNotConfiguredError):
        search_mgr.search("", mode="semantic")


def test_vector_index_blank_query_returns_empty(
    search_mgr_with_embeddings: SearchManager,
) -> None:
    """VectorIndex.search backstops a direct library consumer (#1111)."""
    vectors = search_mgr_with_embeddings._vectors
    assert vectors is not None
    vectors._provider = _RejectsBlankProvider()  # type: ignore[assignment]

    assert vectors.search("   ", limit=5) == []


def _mgr(vault_dir: Path, *, default_mode: str) -> SearchManager:
    """Build a SearchManager over *vault_dir* with an explicit default mode."""
    fts = FTSIndex(db_path=":memory:", indexed_frontmatter_fields=["tags"])
    for note in scan_directory(vault_dir):
        fts.upsert_note(note)
    return SearchManager(
        fts=fts,
        source_dir=vault_dir,
        indexed_frontmatter_fields=["tags"],
        link_manager=LinkManager(fts=fts, source_dir=vault_dir),
        attachment_extensions=["png"],
        default_mode=default_mode,
    )


class TestDefaultModeResolution:
    """mode=None resolves from config; explicit modes keep their contract."""

    def test_shipped_default_is_auto(self, search_mgr: SearchManager) -> None:
        """The shipped default defers the choice to the vault's capabilities."""
        assert search_mgr._default_mode == "auto"

    def test_auto_picks_keyword_without_embeddings(
        self, search_mgr: SearchManager
    ) -> None:
        """A vault with no vector index still searches, via keyword."""
        assert search_mgr._resolve_mode(None) == "keyword"
        results = search_mgr.search("alpha")
        assert "alpha.md" in [r.path for r in results]
        assert all(r.search_type == "keyword" for r in results)

    def test_auto_picks_hybrid_with_embeddings(
        self, search_mgr_with_embeddings: SearchManager
    ) -> None:
        """A vault that built a vector index searches it without being asked.

        This is the whole point of the auto default: the operator paid for
        embeddings, so an unqualified search should use them.
        """
        mgr = search_mgr_with_embeddings
        assert mgr._default_mode == "auto"
        assert mgr._resolve_mode(None) == "hybrid"

        implicit = [r.path for r in mgr.search("alpha")]
        explicit_hybrid = [r.path for r in mgr.search("alpha", mode="hybrid")]
        explicit_keyword = [r.path for r in mgr.search("alpha", mode="keyword")]

        assert implicit == explicit_hybrid
        # Guard against a vacuous pass: if hybrid and keyword agree on this
        # fixture the comparison above proves nothing, so require a
        # difference before trusting it.
        assert explicit_hybrid != explicit_keyword

    def test_pinned_keyword_suppresses_hybrid_with_embeddings(
        self, search_mgr_with_embeddings: SearchManager
    ) -> None:
        """Pinning keyword is a real escape hatch, not a no-op.

        An operator on a metered embedding provider pins keyword to keep
        unqualified searches from embedding every query. If auto won here,
        that setting would be decorative.
        """
        mgr = search_mgr_with_embeddings
        mgr._default_mode = "keyword"
        assert mgr._resolve_mode(None) == "keyword"

        implicit = [r.path for r in mgr.search("alpha")]
        assert implicit == [r.path for r in mgr.search("alpha", mode="keyword")]
        assert implicit != [r.path for r in mgr.search("alpha", mode="hybrid")]

    def test_pinned_hybrid_degrades_without_embeddings(
        self, search_vault: Path
    ) -> None:
        """A pinned hybrid default must not break an embedding-less vault.

        Without the degrade, pinning the option on a vault that has no
        embeddings would raise on every unqualified search — turning a
        preference into an outage.
        """
        mgr = _mgr(search_vault, default_mode="hybrid")
        results = mgr.search("alpha")
        assert "alpha.md" in [r.path for r in results]
        assert all(r.search_type == "keyword" for r in results)

    def test_pinned_semantic_also_degrades(self, search_vault: Path) -> None:
        """The degrade covers semantic, not just hybrid."""
        assert _mgr(search_vault, default_mode="semantic")._resolve_mode(None) == (
            "keyword"
        )

    def test_explicit_hybrid_still_raises_without_embeddings(
        self, search_mgr: SearchManager
    ) -> None:
        """An explicit hybrid request is never silently downgraded.

        Degrading it would hand back keyword results under a semantic
        label — the caller must learn their vault cannot serve the mode.
        """
        from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

        with pytest.raises(EmbeddingsNotConfiguredError):
            search_mgr.search("alpha", mode="hybrid")

    def test_explicit_semantic_still_raises_without_embeddings(
        self, search_mgr: SearchManager
    ) -> None:
        """Same contract for an explicit semantic request."""
        from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

        with pytest.raises(EmbeddingsNotConfiguredError):
            search_mgr.search("alpha", mode="semantic")

    def test_explicit_keyword_is_honoured(self, search_mgr: SearchManager) -> None:
        """An explicit keyword request runs keyword search."""
        results = search_mgr.search("alpha", mode="keyword")
        assert "alpha.md" in [r.path for r in results]


class TestDefaultModeValidation:
    """The accepted set is the same at both boundaries (#1205).

    ``SearchConfig`` rejected an unknown ``default_mode`` while
    ``SearchManager`` — reachable through ``Vault`` without touching the
    environment — stored it unchecked. ``_resolve_mode`` returned it verbatim
    through its cast, and ``search`` dispatches keyword → semantic → else
    hybrid, so an unrecognised mode ran hybrid rather than failing.
    """

    @pytest.mark.parametrize("mode", ["auto", "keyword", "semantic", "hybrid"])
    def test_every_configurable_mode_constructs(
        self, search_vault: Path, mode: str
    ) -> None:
        assert _mgr(search_vault, default_mode=mode)._default_mode == mode

    @pytest.mark.parametrize("mode", ["fuzzy", "Hybrid", "", "auto "])
    def test_an_unknown_mode_raises_at_construction(
        self, search_vault: Path, mode: str
    ) -> None:
        """Fails where the value enters, not at the first search."""
        from markdown_vault_mcp.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="default_mode"):
            _mgr(search_vault, default_mode=mode)

    def test_the_vault_constructor_rejects_it_too(self, tmp_path: Path) -> None:
        """The path a library consumer actually takes."""
        from markdown_vault_mcp.exceptions import ConfigurationError
        from markdown_vault_mcp.vault import Vault

        source = tmp_path / "vault"
        source.mkdir()
        with pytest.raises(ConfigurationError, match="default_mode"):
            Vault(source_dir=source, default_search_mode="fuzzy")

    def test_both_boundaries_accept_the_same_set(self) -> None:
        """One constant, so the env route and the constructor cannot drift."""
        from markdown_vault_mcp.config_sections.search import _SEARCH_MODES
        from markdown_vault_mcp.types import DEFAULT_SEARCH_MODES

        assert frozenset(DEFAULT_SEARCH_MODES) == _SEARCH_MODES
