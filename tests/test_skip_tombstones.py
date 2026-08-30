"""Skip tombstones (#1129): FTS absence means "not an index candidate".

Files skipped for a surfaced :data:`~markdown_vault_mcp.types.SKIP_CATEGORIES`
reason stay PRESENT in the ``documents`` table as tombstone rows — invisible
to every reader (which all go through the ``documents_live`` view), but
distinguishable from a deletion. These tests pin:

- the additive schema migration on a pre-#1129 database;
- tombstone CRUD including live ↔ tombstone transitions;
- tombstone invisibility across every direct-``documents`` reader;
- the broken-links decision (links to tombstones stay broken; wikilinks do
  not resolve to tombstones);
- the pipeline wiring (build / reindex / process_dirty_paths) plus the
  tracker-registry lockstep and tombstone garbage collection;
- the stale-row regression fix: a file that becomes unparseable stops
  serving its last-good content;
- the warm-restart gate on an all-tombstone vault;
- the embeddings gap semantics that tombstones enable.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.types import Chunk, LinkInfo, ParsedNote, SkippedFile

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _note(
    path: str,
    *,
    content: str = "some body text",
    links: list[LinkInfo] | None = None,
    content_hash: str = "hash",
    modified_at: float = 1000.0,
) -> ParsedNote:
    """Build a one-chunk ParsedNote for index fixtures."""
    return ParsedNote(
        path=path,
        frontmatter={},
        title=path.rsplit("/", 1)[-1].removesuffix(".md"),
        chunks=[Chunk(heading=None, heading_level=0, content=content, start_line=1)],
        content_hash=content_hash,
        modified_at=modified_at,
        links=links or [],
        content_chars=len(content),
    )


TOMB = "ghost/tomb.md"


@pytest.fixture()
def mixed_index() -> FTSIndex:
    """One live doc linking a second live doc AND a tombstoned path.

    ``sub/live.md`` carries a markdown link to the tombstone and a bare
    wikilink whose stem matches only the tombstone, plus a link to
    ``sub/other.md`` so live-graph queries have something to return.
    """
    idx = FTSIndex(":memory:")
    idx.upsert_note(
        _note(
            "sub/live.md",
            content="alpha searchable content",
            links=[
                LinkInfo(
                    target_path=TOMB,
                    link_text="tomb",
                    link_type="markdown",
                    raw_target="../ghost/tomb.md",
                ),
                LinkInfo(
                    target_path="tomb.md",
                    link_text="tomb",
                    link_type="wikilink",
                    raw_target="tomb",
                ),
                LinkInfo(
                    target_path="sub/other.md",
                    link_text="other",
                    link_type="markdown",
                    raw_target="other.md",
                ),
            ],
        )
    )
    idx.upsert_note(_note("sub/other.md", content="beta other content"))
    idx.upsert_tombstone(
        SkippedFile(path=TOMB, category="parse_error", detail="bad yaml"),
        content_hash="tombhash",
        modified_at=2000.0,
    )
    return idx


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


class TestMigration:
    def test_v2_database_gains_tombstone_columns_and_view(self, tmp_path: Path) -> None:
        """Opening a pre-#1129 DB adds the columns; old rows read as live."""
        db = tmp_path / "index.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                folder TEXT NOT NULL DEFAULT '',
                frontmatter_json TEXT,
                content_hash TEXT NOT NULL,
                modified_at REAL NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 1,
                content_chars INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO documents
                (path, title, folder, content_hash, modified_at)
            VALUES ('old.md', 'Old', '', 'h', 1.0);
            """
        )
        conn.commit()
        conn.close()

        idx = FTSIndex(db)
        try:
            cols = {
                r[1]
                for r in idx._conn().execute("PRAGMA table_info(documents)").fetchall()
            }
            assert {"skip_category", "skip_detail"} <= cols
            # The migrated row is live and visible through documents_live.
            assert [r["path"] for r in idx.list_notes()] == ["old.md"]
            assert idx.get_tombstone("old.md") is None
            # The view exists and tombstones written post-migration hide.
            idx.upsert_tombstone(
                SkippedFile(path="skip.md", category="parse_error", detail="x"),
                content_hash="h2",
                modified_at=2.0,
            )
            assert [r["path"] for r in idx.list_notes()] == ["old.md"]
        finally:
            idx.close()

    def test_stale_semantics_version_triggers_rebuild_with_tombstones(
        self, tmp_path: Path
    ) -> None:
        """A v2 (pre-tombstone) index cold-rebuilds once and gains tombstones."""
        from tests.test_index_coordinator import make_coordinator

        (tmp_path / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
        (tmp_path / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\nbody", encoding="utf-8"
        )
        db = tmp_path / "index.db"
        coord = make_coordinator(tmp_path, db=db)
        try:
            coord.build_index()
            fts = coord._fts
            assert fts.get_tombstone("bad.md") is not None
            # Simulate a v2 index: downgrade the recorded semantics version
            # and drop the tombstone rows that version could not have written.
            for row in fts.list_tombstones():
                fts.delete_by_path(row["path"])
            conn = fts._conn()
            with conn:
                conn.execute(
                    "UPDATE meta SET value = '2' WHERE key = 'index_semantics_version'"
                )
        finally:
            coord.close(timeout=5)

        coord2 = make_coordinator(tmp_path, db=db)
        try:
            stats = coord2.build_index()
            # A warm-restart short-circuit reports chunks_indexed == 0; the
            # provenance mismatch must instead run a real scan.
            assert stats.chunks_indexed > 0
            assert coord2._fts.get_tombstone("bad.md") is not None
            assert coord2._fts.get_chunking_meta().semantics_version >= 3
        finally:
            coord2.close(timeout=5)


# ---------------------------------------------------------------------------
# Tombstone CRUD
# ---------------------------------------------------------------------------


class TestTombstoneCrud:
    def test_upsert_get_list_roundtrip(self) -> None:
        idx = FTSIndex(":memory:")
        skip = SkippedFile(
            path="a/b.md", category="missing_frontmatter", detail="missing: ['title']"
        )
        idx.upsert_tombstone(skip, content_hash="h1", modified_at=10.0)
        tomb = idx.get_tombstone("a/b.md")
        assert tomb == {
            "path": "a/b.md",
            "skip_category": "missing_frontmatter",
            "skip_detail": "missing: ['title']",
            "content_hash": "h1",
            "modified_at": 10.0,
        }
        assert idx.list_tombstones() == [tomb]
        assert idx.get_tombstone("other.md") is None

    def test_tombstone_has_no_child_rows_or_fts_rows(self) -> None:
        idx = FTSIndex(":memory:")
        idx.upsert_tombstone(
            SkippedFile(path="t.md", category="internal_error", detail="boom"),
            content_hash="h",
            modified_at=1.0,
        )
        conn = idx._conn()
        assert conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0
        row = conn.execute(
            "SELECT title, chunk_count, content_chars FROM documents"
        ).fetchone()
        assert (row["title"], row["chunk_count"], row["content_chars"]) == ("t", 0, 0)

    def test_live_to_tombstone_and_back(self) -> None:
        idx = FTSIndex(":memory:")
        idx.upsert_note(_note("n.md", content="visible words"))
        assert idx.search("visible")
        idx.upsert_tombstone(
            SkippedFile(path="n.md", category="parse_error", detail="x"),
            content_hash="h2",
            modified_at=2.0,
        )
        # live → tombstone: the old content stops being served everywhere.
        assert idx.get_note("n.md") is None
        assert idx.search("visible") == []
        assert idx.count_documents() == 0
        # tombstone → live: a successful re-parse replaces the tombstone.
        idx.upsert_note(_note("n.md", content="repaired words"))
        assert idx.get_tombstone("n.md") is None
        assert idx.search("repaired")

    def test_delete_by_path_removes_tombstone(self) -> None:
        idx = FTSIndex(":memory:")
        idx.upsert_tombstone(
            SkippedFile(path="t.md", category="encoding_error", detail="x"),
            content_hash="h",
            modified_at=1.0,
        )
        assert idx.delete_by_path("t.md") == 1
        assert idx.get_tombstone("t.md") is None
        assert not idx.has_documents()

    def test_has_documents_counts_tombstones(self) -> None:
        idx = FTSIndex(":memory:")
        assert not idx.has_documents()
        idx.upsert_tombstone(
            SkippedFile(path="t.md", category="parse_error", detail="x"),
            content_hash="h",
            modified_at=1.0,
        )
        assert idx.has_documents()
        assert idx.count_documents() == 0


# ---------------------------------------------------------------------------
# Reader invisibility — one case per direct-``documents`` consumer
# ---------------------------------------------------------------------------


def _check_get_note(idx: FTSIndex) -> None:
    assert idx.get_note(TOMB) is None


def _check_list_notes(idx: FTSIndex) -> None:
    assert {r["path"] for r in idx.list_notes()} == {"sub/live.md", "sub/other.md"}


def _check_list_notes_folder(idx: FTSIndex) -> None:
    assert idx.list_notes(folder="ghost") == []


def _check_list_folders(idx: FTSIndex) -> None:
    assert idx.list_folders() == ["sub"]


def _check_count_documents(idx: FTSIndex) -> None:
    assert idx.count_documents() == 2


def _check_get_subtree_toc(idx: FTSIndex) -> None:
    notes, truncated = idx.get_subtree_toc("ghost")
    assert notes == [] and truncated is False


def _check_get_recent(idx: FTSIndex) -> None:
    # The tombstone carries the newest modified_at (2000.0) but never shows.
    assert {r["path"] for r in idx.get_recent(limit=10)} == {
        "sub/other.md",
        "sub/live.md",
    }


def _check_get_orphan_notes(idx: FTSIndex) -> None:
    assert TOMB not in {r["path"] for r in idx.get_orphan_notes()}


def _check_get_broken_links(idx: FTSIndex) -> None:
    broken = {
        (row["source_path"], row["target_path"]) for row in idx.get_broken_links()
    }
    assert ("sub/live.md", TOMB) in broken


def _check_count_broken_links(idx: FTSIndex) -> None:
    # The markdown link to the tombstone and the unresolved wikilink.
    assert idx.count_broken_links() == 2


def _check_count_orphans(idx: FTSIndex) -> None:
    assert idx.count_orphans() == 0


def _check_get_most_linked(idx: FTSIndex) -> None:
    assert TOMB not in {r["path"] for r in idx.get_most_linked(limit=10)}


def _check_get_connection_path(idx: FTSIndex) -> None:
    with pytest.raises(ValueError, match="not found"):
        idx.get_connection_path("sub/live.md", TOMB)


def _check_get_chunk_count(idx: FTSIndex) -> None:
    # Not-found default (1), NOT the tombstone's stored 0.
    assert idx.get_chunk_count(TOMB) == 1


def _check_get_chunk_counts(idx: FTSIndex) -> None:
    assert idx.get_chunk_counts(["sub/live.md", TOMB]) == {"sub/live.md": 1}


def _check_search(idx: FTSIndex) -> None:
    assert TOMB not in {r.path for r in idx.search("tomb")}


def _check_get_outlinks(idx: FTSIndex) -> None:
    by_target = {row["target_path"]: row for row in idx.get_outlinks("sub/live.md")}
    assert not by_target[TOMB]["target_exists"]
    assert by_target["sub/other.md"]["target_exists"]


@pytest.mark.parametrize(
    "check",
    [
        _check_get_note,
        _check_list_notes,
        _check_list_notes_folder,
        _check_list_folders,
        _check_count_documents,
        _check_get_subtree_toc,
        _check_get_recent,
        _check_get_orphan_notes,
        _check_get_broken_links,
        _check_count_broken_links,
        _check_count_orphans,
        _check_get_most_linked,
        _check_get_connection_path,
        _check_get_chunk_count,
        _check_get_chunk_counts,
        _check_search,
        _check_get_outlinks,
    ],
    ids=lambda fn: fn.__name__.removeprefix("_check_"),
)
def test_tombstone_invisible_to_reader(
    mixed_index: FTSIndex, check: Callable[[FTSIndex], None]
) -> None:
    """Every direct-``documents`` reader treats the tombstone as absent."""
    check(mixed_index)


def test_wikilink_does_not_resolve_to_tombstone(mixed_index: FTSIndex) -> None:
    """Vault-wide wikilink resolution ignores tombstones — the link stays broken."""
    mixed_index.resolve_vault_wikilinks()
    wikilink_targets = {
        row["target_path"]
        for row in mixed_index.get_outlinks("sub/live.md")
        if row["link_type"] == "wikilink"
    }
    assert wikilink_targets == {"tomb.md"}  # unresolved, not ghost/tomb.md
    broken_targets = {row["target_path"] for row in mixed_index.get_broken_links()}
    assert {"tomb.md", TOMB} <= broken_targets


# ---------------------------------------------------------------------------
# Pipeline wiring (IndexManager)
# ---------------------------------------------------------------------------


class TestPipelines:
    @staticmethod
    def _mgr(vault: Path, state_dir: Path, **overrides):
        from tests.test_managers_index import _make_index_mgr

        return _make_index_mgr(vault, state_dir, **overrides)

    def test_build_index_tombstones_surfaced_skips(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "good.md").write_text("# Good\n\nbody\n", encoding="utf-8")
        (vault / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\nbody", encoding="utf-8"
        )
        mgr, fts, _ = self._mgr(vault, tmp_path)
        mgr.build_index()
        tomb = fts.get_tombstone("bad.md")
        assert tomb is not None
        assert tomb["skip_category"] == "parse_error"
        assert [r["path"] for r in fts.list_notes()] == ["good.md"]
        # Lockstep: the tracker registry surfaces the same skip.
        assert [s.path for s in mgr.skipped_files()] == ["bad.md"]

    def test_force_rebuild_recreates_tombstones(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\nbody", encoding="utf-8"
        )
        mgr, fts, _ = self._mgr(vault, tmp_path)
        mgr.build_index()
        assert fts.get_tombstone("bad.md") is not None
        mgr.build_index(force=True)
        assert fts.get_tombstone("bad.md") is not None
        assert fts.count_documents() == 0

    def test_reindex_unparseable_stops_serving_stale_content(
        self, tmp_path: Path
    ) -> None:
        """REGRESSION (#1129 headline): reindex replaces the stale row.

        Previously a file that became unparseable KEPT its last-good FTS row,
        serving stale content from search/read indefinitely.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "note.md"
        note.write_text("# Note\n\nunique stale marker text\n", encoding="utf-8")
        mgr, fts, _ = self._mgr(vault, tmp_path)
        mgr.build_index()
        assert fts.search("stale marker")
        assert fts.get_note("note.md") is not None

        note.write_text("---\ntitle: [unclosed\n---\nbody", encoding="utf-8")
        result = mgr.reindex()
        assert result.skipped >= 1
        # The stale content is gone from every read surface...
        assert fts.search("stale marker") == []
        assert fts.get_note("note.md") is None
        # ...but the path is distinguishable from a deletion.
        tomb = fts.get_tombstone("note.md")
        assert tomb is not None and tomb["skip_category"] == "parse_error"
        # Lockstep with the get_index_status registry.
        assert [s.category for s in mgr.skipped_files()] == ["parse_error"]

    def test_reindex_gc_drops_tombstone_of_deleted_file(self, tmp_path: Path) -> None:
        """A skipped file deleted from disk loses its tombstone (registry lockstep)."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "good.md").write_text("# Good\n\nbody\n", encoding="utf-8")
        bad = vault / "bad.md"
        bad.write_text("---\ntitle: [unclosed\n---\nbody", encoding="utf-8")
        mgr, fts, _ = self._mgr(vault, tmp_path)
        mgr.build_index()
        assert fts.get_tombstone("bad.md") is not None

        bad.unlink()
        mgr.reindex()
        assert fts.get_tombstone("bad.md") is None
        assert mgr.skipped_files() == []

    def test_reindex_fixed_file_replaces_tombstone(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        bad = vault / "bad.md"
        bad.write_text("---\ntitle: [unclosed\n---\nbody", encoding="utf-8")
        mgr, fts, _ = self._mgr(vault, tmp_path)
        mgr.build_index()
        assert fts.get_tombstone("bad.md") is not None

        bad.write_text("# Fixed\n\nnow parseable\n", encoding="utf-8")
        result = mgr.reindex()
        assert result.added == 1
        assert fts.get_tombstone("bad.md") is None
        assert fts.get_note("bad.md") is not None

    def test_process_dirty_paths_parse_error_tombstones(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "note.md"
        note.write_text("# Note\n\nserved body\n", encoding="utf-8")
        mgr, fts, _ = self._mgr(vault, tmp_path)
        mgr.build_index()
        assert fts.get_note("note.md") is not None

        note.write_text("---\ntitle: [unclosed\n---\nbody", encoding="utf-8")
        mgr.process_dirty_paths({"note.md"})
        assert fts.get_note("note.md") is None
        tomb = fts.get_tombstone("note.md")
        assert tomb is not None and tomb["skip_category"] == "parse_error"

    def test_process_dirty_paths_missing_frontmatter_tombstones(
        self, tmp_path: Path
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "note.md"
        note.write_text("---\ntitle: Note\n---\n# Note\n\nbody\n", encoding="utf-8")
        mgr, fts, _ = self._mgr(vault, tmp_path, required_frontmatter=["title"])
        mgr.build_index()
        assert fts.get_note("note.md") is not None

        note.write_text("# Note\n\nbody without frontmatter\n", encoding="utf-8")
        mgr.process_dirty_paths({"note.md"})
        assert fts.get_note("note.md") is None
        tomb = fts.get_tombstone("note.md")
        assert tomb is not None
        assert tomb["skip_category"] == "missing_frontmatter"
        assert "title" in tomb["skip_detail"]

    def test_tombstone_skip_transient_read_failure_records_nothing(
        self, tmp_path: Path
    ) -> None:
        """An OSError while hashing/stat-ing tombstones nothing (retry policy)."""
        vault = tmp_path / "vault"
        vault.mkdir()
        mgr, fts, _ = self._mgr(vault, tmp_path)
        mgr._tombstone_skip(
            SkippedFile(path="gone.md", category="parse_error", detail="x"),
            vault / "gone.md",  # does not exist → OSError on hash
        )
        assert fts.get_tombstone("gone.md") is None
        assert not fts.has_documents()

    def test_process_dirty_paths_excluded_still_deletes(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note\n\nbody\n", encoding="utf-8")
        mgr, fts, _ = self._mgr(vault, tmp_path, exclude_patterns=["note.md"])
        fts.upsert_note(_note("note.md"))
        mgr.process_dirty_paths({"note.md"})
        assert fts.get_note("note.md") is None
        assert fts.get_tombstone("note.md") is None


# ---------------------------------------------------------------------------
# Warm restart (coordinator)
# ---------------------------------------------------------------------------


def test_all_tombstone_vault_warm_restarts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A vault whose only candidates are skipped short-circuits on restart."""
    from tests.test_index_coordinator import make_coordinator

    # make_coordinator seeds a.md when absent; pre-empt it with an
    # unparseable file so the vault holds tombstones only.
    (tmp_path / "a.md").write_text("---\ntitle: [unclosed\n---\nbody", encoding="utf-8")
    db = tmp_path / "index.db"
    coord = make_coordinator(tmp_path, db=db)
    try:
        coord.build_index()
        assert coord._fts.count_documents() == 0
        assert coord._fts.list_tombstones() != []
    finally:
        coord.close(timeout=5)

    coord2 = make_coordinator(tmp_path, db=db)
    try:
        with caplog.at_level(
            logging.DEBUG, logger="markdown_vault_mcp.indexing.coordinator"
        ):
            stats = coord2.build_index()
        assert stats.documents_indexed == 0
        assert any("index already populated" in r.getMessage() for r in caplog.records)
    finally:
        coord2.close(timeout=5)


# ---------------------------------------------------------------------------
# Embeddings gap semantics
# ---------------------------------------------------------------------------


def test_empty_build_not_authoritative_without_tombstone(tmp_path: Path) -> None:
    """An absentee with neither row nor tombstone vetoes the empty save."""
    from markdown_vault_mcp.vector_index import VectorIndex
    from tests.conftest import MockEmbeddingProvider
    from tests.test_managers_index import _make_index_mgr

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nbody\n", encoding="utf-8")
    embeddings_path = tmp_path / "embeddings"
    provider = MockEmbeddingProvider()
    mgr, fts, _ = _make_index_mgr(
        vault,
        tmp_path,
        embeddings_path=embeddings_path,
        embedding_provider=provider,
    )
    mgr.build_index()
    assert mgr.build_embeddings() == 1
    assert VectorIndex.load(embeddings_path, provider).count == 1

    # Build gap: the row (and any tombstone) vanish while the source stays.
    fts.delete_by_path("note.md")
    assert mgr.build_embeddings(force=True) == 0
    # The empty index must NOT reach disk — the old vector survives.
    assert VectorIndex.load(embeddings_path, provider).count == 1
