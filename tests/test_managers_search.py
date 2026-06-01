"""Tests for SearchManager in isolation (no Collection dependency)."""

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

    def fake_vec_search(_query, *, limit):  # noqa: ARG001
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
    from markdown_vault_mcp.collection import Collection
    from tests.conftest import MockEmbeddingProvider

    col = Collection(
        source_dir=tmp_path,
        read_only=False,
        embeddings_path=tmp_path / "vec",
        embedding_provider=MockEmbeddingProvider(),
    )
    try:
        (tmp_path / "n.md").write_text("# n\n\nhello", encoding="utf-8")
        col.build_index()
        col.build_embeddings()

        # The attribute must not exist any more.
        assert not hasattr(col._search_mgr, "_flush_embeddings")

        # Semantic search still works without it.
        col.search(query="hello", mode="semantic")
    finally:
        col.close()
