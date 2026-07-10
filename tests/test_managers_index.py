"""Tests for IndexManager in isolation (no Vault dependency)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.index import IndexManager
from markdown_vault_mcp.scanner import HeadingChunker
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.types import IndexStats, ReindexResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def index_vault(tmp_path: Path) -> Path:
    """Create a small vault suitable for index tests.

    Contains:
        alpha.md   (root, tags: [a, b])
        beta.md    (root, tags: [b])
        notes/gamma.md (subfolder, tags: [c])
        notes/delta.md (subfolder, no tags)
    """
    alpha = tmp_path / "alpha.md"
    alpha.write_text(
        "---\ntitle: Alpha\ntags:\n  - a\n  - b\n---\n# Alpha\n\nHello world.\n",
        encoding="utf-8",
    )
    beta = tmp_path / "beta.md"
    beta.write_text(
        "---\ntitle: Beta\ntags:\n  - b\n---\n# Beta\n\nGoodbye world.\n",
        encoding="utf-8",
    )
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    gamma = notes_dir / "gamma.md"
    gamma.write_text(
        "---\ntitle: Gamma\ntags:\n  - c\n---\n# Gamma\n\nUnique gamma content.\n",
        encoding="utf-8",
    )
    delta = notes_dir / "delta.md"
    delta.write_text(
        "---\ntitle: Delta\n---\n# Delta\n\nDelta content.\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_index_mgr(
    vault: Path,
    state_dir: Path,
    **overrides,
) -> tuple[IndexManager, FTSIndex, dict]:
    """Build an IndexManager with default wiring.

    Returns (index_mgr, fts, vectors_holder) so tests can inspect state.

    Note: ``write_lock`` kwarg is silently dropped if provided — the
    IndexManager no longer takes a lock (the IndexWriter thread is the
    sole mutator post #559).
    """
    fts = overrides.pop("fts", None) or FTSIndex(db_path=":memory:")
    tracker = overrides.pop("tracker", None) or ChangeTracker(
        state_dir / ".state" / "state.json"
    )
    # Accept-and-discard write_lock for callers that still pass it.
    overrides.pop("write_lock", None)
    vectors_holder: dict = {"vectors": None}
    defaults = {
        "fts": fts,
        "tracker": tracker,
        "source_dir": vault,
        "chunk_strategy": HeadingChunker(),
        "get_vectors": lambda: vectors_holder["vectors"],
        "set_vectors": lambda v: vectors_holder.__setitem__("vectors", v),
    }
    defaults.update(overrides)
    mgr = IndexManager(**defaults)
    return mgr, fts, vectors_holder


@pytest.fixture()
def index_mgr(index_vault: Path, tmp_path: Path) -> tuple[IndexManager, FTSIndex, dict]:
    """Build an IndexManager from the test vault."""
    return _make_index_mgr(index_vault, tmp_path)


# ---------------------------------------------------------------------------
# EmbeddingsNotConfiguredError
# ---------------------------------------------------------------------------


class TestEmbeddingsNotConfiguredError:
    def test_require_vectors_raises_embeddings_not_configured(
        self, tmp_path: Path
    ) -> None:
        from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError
        from markdown_vault_mcp.vault import Vault

        vault = Vault(source_dir=tmp_path)  # no embedding provider configured
        try:
            vault.index.build_index()
            with pytest.raises(EmbeddingsNotConfiguredError):
                vault.index.build_embeddings()
            # Subclass of ValueError → back-compat catchers still work.
            with pytest.raises(ValueError):
                vault.index.build_embeddings()
        finally:
            vault.close()


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------


class TestBuildIndex:
    """Tests for IndexManager.build_index()."""

    def test_returns_index_stats(self, index_mgr):
        mgr, _fts, _ = index_mgr
        result = mgr.build_index()
        assert isinstance(result, IndexStats)
        assert result.documents_indexed >= 4

    def test_document_count(self, index_mgr):
        mgr, fts, _ = index_mgr
        mgr.build_index()
        notes = fts.list_notes()
        assert len(notes) == 4

    def test_idempotent_rebuild(self, index_mgr):
        """Second build_index without force re-scans (no early exit at mgr level)."""
        mgr, _fts, _ = index_mgr
        result1 = mgr.build_index()
        result2 = mgr.build_index()
        # IndexManager always rescans; the Vault wrapper handles the no-op.
        assert result2.documents_indexed == result1.documents_indexed

    def test_force_rebuild(self, index_mgr):
        mgr, _fts, _ = index_mgr
        mgr.build_index()
        result = mgr.build_index(force=True)
        assert result.documents_indexed >= 4

    def test_respects_exclude_patterns(self, index_vault: Path, tmp_path: Path):
        mgr, fts, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            exclude_patterns=["notes/*"],
        )
        mgr.build_index()
        notes = fts.list_notes()
        paths = {n["path"] for n in notes}
        assert "alpha.md" in paths
        assert "beta.md" in paths
        assert not any(p.startswith("notes/") for p in paths)

    def test_excluded_files_invisible_for_non_prunable_pattern(
        self, index_vault: Path, tmp_path: Path
    ):
        """Excluded files under a file-shaped pattern are not skip-counted or
        recorded in skipped_state (the invisible-excluded contract, #257/#832).

        ``notes/*`` is not a directory-prunable shape, so ``iter_markdown_files``
        still descends ``notes/`` and yields its files; without the per-file
        ``is_path_excluded`` filter they leak into ``IndexStats.skipped`` and the
        tracker's skipped map, contradicting the contract and the comment.
        """
        mgr, _fts, _ = _make_index_mgr(
            index_vault, tmp_path, exclude_patterns=["notes/*"]
        )
        result = mgr.build_index()

        # Two indexable roots (alpha, beta); the two notes/ files are excluded.
        assert result.skipped == 0
        _indexed, skipped_state, _reasons = mgr._tracker._load_state()
        assert not any(p.startswith("notes/") for p in skipped_state), (
            "excluded files must not enter the tracker's skipped state"
        )

    def test_broken_symlink_md_is_skipped_not_hashed(
        self, index_vault: Path, tmp_path: Path
    ):
        """A broken symlink named ``*.md`` is discovered but skipped, not hashed.

        ``_discover_indexable_candidates`` drops non-files via ``is_file()`` so a
        dangling link does not reach ``compute_file_hash``; the build must not
        crash and the link must not be indexed.
        """
        dangling = index_vault / "dangling.md"
        try:
            dangling.symlink_to(index_vault / "does-not-exist-target.md")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not supported here: {exc}")

        mgr, fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()  # must not raise on the broken link
        paths = {n["path"] for n in fts.list_notes()}
        assert "dangling.md" not in paths

    def test_continues_on_upsert_error(self, index_vault: Path, tmp_path: Path):
        """If one document fails to upsert, others still get indexed."""
        fts = FTSIndex(db_path=":memory:")
        original_upsert = fts.upsert_note
        call_count = {"n": 0}

        def failing_upsert(note):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Simulated upsert failure")
            return original_upsert(note)

        fts.upsert_note = failing_upsert  # type: ignore[assignment]
        mgr, _, _ = _make_index_mgr(index_vault, tmp_path, fts=fts)
        result = mgr.build_index()
        # One errored, rest succeeded.
        assert result.documents_indexed == 3

    def test_updates_tracker_state(self, index_mgr):
        """build_index updates the change tracker baseline."""
        mgr, _fts, _ = index_mgr
        mgr.build_index()
        # After build_index, detect_changes should report no changes.
        changes = mgr._tracker.detect_changes(mgr._source_dir)
        assert len(changes.added) == 0
        assert len(changes.modified) == 0
        assert len(changes.deleted) == 0

    def test_resolves_wikilinks(self, index_vault: Path, tmp_path: Path):
        """build_index calls resolve_vault_wikilinks on the FTS index."""
        fts = MagicMock(spec=FTSIndex)
        fts.list_notes.return_value = []
        mgr, _, _ = _make_index_mgr(index_vault, tmp_path, fts=fts)
        mgr.build_index()
        fts.resolve_vault_wikilinks.assert_called_once()


# ---------------------------------------------------------------------------
# reindex
# ---------------------------------------------------------------------------


class TestReindex:
    """Tests for IndexManager.reindex()."""

    def test_detects_new_file(self, index_vault: Path, tmp_path: Path):
        mgr, fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()

        # Add a new file.
        new_file = index_vault / "new_note.md"
        new_file.write_text(
            "---\ntitle: New\n---\n# New\n\nNew content.\n",
            encoding="utf-8",
        )

        result = mgr.reindex()
        assert isinstance(result, ReindexResult)
        assert result.added >= 1

        paths = {n["path"] for n in fts.list_notes()}
        assert "new_note.md" in paths

    def test_detects_deleted_file(self, index_vault: Path, tmp_path: Path):
        mgr, fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()

        # Delete a file.
        (index_vault / "beta.md").unlink()

        result = mgr.reindex()
        assert result.deleted >= 1

        paths = {n["path"] for n in fts.list_notes()}
        assert "beta.md" not in paths

    def test_detects_modified_file(self, index_vault: Path, tmp_path: Path):
        mgr, _fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()

        # Modify a file.
        (index_vault / "alpha.md").write_text(
            "---\ntitle: Alpha Updated\n---\n# Alpha\n\nUpdated.\n",
            encoding="utf-8",
        )

        result = mgr.reindex()
        assert result.modified >= 1


# ---------------------------------------------------------------------------
# Skip-state memory (#665): skipped files are remembered in tracker state
# ---------------------------------------------------------------------------


class TestSkipStateMemory:
    """reindex() must not re-report/re-parse deterministically skipped files."""

    def _vault_with_skip(self, tmp_path: Path) -> Path:
        """Vault with one indexable note and one missing required frontmatter."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "good.md").write_text(
            "---\ntitle: Good\n---\n# Good\n\nbody\n", encoding="utf-8"
        )
        (vault / "skip.md").write_text("# No frontmatter\n\nbody\n", encoding="utf-8")
        return vault

    def test_build_index_records_skips_so_first_reindex_is_quiet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reindex right after a full build re-parses nothing (boot path)."""
        vault = self._vault_with_skip(tmp_path)
        mgr, _fts, _ = _make_index_mgr(vault, tmp_path, required_frontmatter=["title"])
        mgr.build_index()

        import markdown_vault_mcp.scanner as scanner_module

        parse_calls: list[str] = []
        original_parse = scanner_module.parse_note

        def counting_parse(abs_path, source_dir, chunk_strategy, **kwargs):
            parse_calls.append(str(abs_path))
            return original_parse(abs_path, source_dir, chunk_strategy, **kwargs)

        monkeypatch.setattr(scanner_module, "parse_note", counting_parse)

        result = mgr.reindex()
        assert result.added == 0
        assert result.modified == 0
        assert result.deleted == 0
        assert result.unchanged == 1
        assert result.skipped == 1
        assert parse_calls == [], f"reindex re-parsed {parse_calls}"

    def test_unchanged_skipped_file_not_relogged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The missing-frontmatter skip is logged once, not on every scan."""
        vault = self._vault_with_skip(tmp_path)
        mgr, _fts, _ = _make_index_mgr(vault, tmp_path, required_frontmatter=["title"])
        # Cold tracker state: build via reindex so the skip is logged once.
        mgr.build_index()
        mgr._tracker.reset()

        with caplog.at_level(logging.INFO, logger="markdown_vault_mcp.managers.index"):
            mgr.reindex()
        first_run_skips = [
            r for r in caplog.records if "missing frontmatter" in r.getMessage()
        ]
        assert len(first_run_skips) == 1

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="markdown_vault_mcp.managers.index"):
            result = mgr.reindex()
        second_run_skips = [
            r for r in caplog.records if "missing frontmatter" in r.getMessage()
        ]
        assert second_run_skips == []
        assert result.skipped == 1

    def test_skipped_file_gaining_frontmatter_is_indexed(self, tmp_path: Path) -> None:
        """A skipped file whose content gains valid frontmatter is indexed."""
        vault = self._vault_with_skip(tmp_path)
        mgr, fts, _ = _make_index_mgr(vault, tmp_path, required_frontmatter=["title"])
        mgr.build_index()
        assert mgr.reindex().skipped == 1

        (vault / "skip.md").write_text(
            "---\ntitle: Now Valid\n---\n# Valid\n\nbody\n", encoding="utf-8"
        )

        result = mgr.reindex()
        assert result.added == 1
        assert result.skipped == 0
        paths = {n["path"] for n in fts.list_notes()}
        assert "skip.md" in paths

    def test_excluded_file_not_hashed_or_reported(self, tmp_path: Path) -> None:
        """A file matching exclude_patterns is filtered before hashing (#257):
        it is neither indexed nor surfaced as a change, so it is not even
        skip-counted (detect_changes drops it entirely)."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "good.md").write_text("# Good\n\nbody\n", encoding="utf-8")
        mgr, fts, _ = _make_index_mgr(vault, tmp_path, exclude_patterns=["drafts/**"])
        mgr.build_index()

        drafts = vault / "drafts"
        drafts.mkdir()
        (drafts / "wip.md").write_text("# WIP\n", encoding="utf-8")

        result = mgr.reindex()
        assert result.added == 0
        assert result.skipped == 0  # excluded files are invisible, not skip-counted
        indexed = {row["path"] for row in fts.list_notes()}
        assert "drafts/wip.md" not in indexed

    def test_transient_oserror_skip_is_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An OSError during parse is NOT recorded; the file retries next scan."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "flaky.md").write_text("# Flaky\n\nbody\n", encoding="utf-8")
        mgr, fts, _ = _make_index_mgr(vault, tmp_path)

        import markdown_vault_mcp.scanner as scanner_module

        original_parse = scanner_module.parse_note

        def failing_parse(abs_path, source_dir, chunk_strategy, **kwargs):  # noqa: ARG001
            raise OSError("transient I/O error")

        monkeypatch.setattr(scanner_module, "parse_note", failing_parse)
        first = mgr.reindex()
        assert first.added == 0
        assert first.skipped == 0  # not recorded — must retry

        monkeypatch.setattr(scanner_module, "parse_note", original_parse)
        second = mgr.reindex()
        assert second.added == 1
        paths = {n["path"] for n in fts.list_notes()}
        assert "flaky.md" in paths

    def test_parse_error_skip_is_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deterministic parse error is recorded; no re-parse next scan."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "broken.md").write_text("# Broken\n\nbody\n", encoding="utf-8")
        mgr, _fts, _ = _make_index_mgr(vault, tmp_path)

        import markdown_vault_mcp.scanner as scanner_module

        parse_calls: list[str] = []

        def broken_parse(abs_path, source_dir, chunk_strategy, **kwargs):  # noqa: ARG001
            parse_calls.append(str(abs_path))
            raise ValueError("malformed note")

        monkeypatch.setattr(scanner_module, "parse_note", broken_parse)
        first = mgr.reindex()
        assert first.added == 0
        assert first.skipped == 1
        assert len(parse_calls) == 1

        second = mgr.reindex()
        assert second.skipped == 1
        assert len(parse_calls) == 1, "unchanged broken file must not re-parse"

    def test_undecodable_file_skip_is_recorded(self, tmp_path: Path) -> None:
        """A UnicodeDecodeError is deterministic: recorded, not re-parsed."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "binary.md").write_bytes(b"\xff\xfe\x00\x00 not utf-8")
        mgr, fts, _ = _make_index_mgr(vault, tmp_path)

        first = mgr.reindex()
        assert first.added == 0
        assert first.skipped == 1

        second = mgr.reindex()
        assert second.added == 0
        assert second.skipped == 1
        assert fts.list_notes() == []

    def test_record_skip_hash_oserror_is_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An OSError while hashing a deterministically-skipped file (a parse
        error) leaves it unrecorded, so it retries on the next scan."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bad.md").write_text("# Bad\n", encoding="utf-8")
        mgr, _fts, _ = _make_index_mgr(vault, tmp_path)

        import markdown_vault_mcp.managers.index as index_module
        import markdown_vault_mcp.scanner as scanner_module

        # A non-OSError parse failure is a deterministic skip recorded via
        # _record_skip_hash (the path that hashes the file).
        def failing_parse(abs_path, source_dir, chunk_strategy, **kwargs):  # noqa: ARG001
            raise ValueError("unparseable")

        monkeypatch.setattr(scanner_module, "parse_note", failing_parse)
        real_hash = index_module.compute_file_hash

        def failing_hash(path):  # noqa: ARG001
            raise OSError("unreadable")

        monkeypatch.setattr(index_module, "compute_file_hash", failing_hash)
        first = mgr.reindex()
        assert first.added == 0
        assert first.skipped == 0  # hash failed → skip not recorded

        monkeypatch.setattr(index_module, "compute_file_hash", real_hash)
        second = mgr.reindex()
        assert second.skipped == 1  # retried and recorded this time

    def test_build_index_skip_hash_oserror_is_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """build_index tolerates a hash failure while recording skips."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "good.md").write_text(
            "---\ntitle: Good\n---\n# Good\n\nbody\n", encoding="utf-8"
        )
        (vault / "skip.md").write_text("# No frontmatter\n", encoding="utf-8")
        mgr, _fts, _ = _make_index_mgr(vault, tmp_path, required_frontmatter=["title"])

        import markdown_vault_mcp.managers.index as index_module

        def failing_hash(path):  # noqa: ARG001
            raise OSError("unreadable")

        monkeypatch.setattr(index_module, "compute_file_hash", failing_hash)
        stats = mgr.build_index()
        assert stats.documents_indexed == 1
        monkeypatch.undo()

        # The skip was not recorded, so the next scan re-evaluates it.
        result = mgr.reindex()
        assert result.added == 0  # still lacks frontmatter → skipped again
        assert result.skipped == 1

    def test_build_index_ignores_directory_matching_glob(self, tmp_path: Path) -> None:
        """A directory whose name matches *.md is not recorded as a skip."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "good.md").write_text(
            "---\ntitle: Good\n---\n# Good\n\nbody\n", encoding="utf-8"
        )
        (vault / "folder.md").mkdir()
        mgr, _fts, _ = _make_index_mgr(vault, tmp_path, required_frontmatter=["title"])
        mgr.build_index()

        result = mgr.reindex()
        assert result.added == 0
        assert result.skipped == 0

    def _mgr_with_embeddings(self, vault: Path, state_dir: Path):
        """Build an IndexManager wired with a mock embedding provider."""
        from tests.conftest import MockEmbeddingProvider

        holder: dict = {"vectors": None}
        mgr, _fts, _ = _make_index_mgr(
            vault,
            state_dir,
            embeddings_path=state_dir / "embeddings",
            embedding_provider=MockEmbeddingProvider(),
            get_vectors=lambda: holder["vectors"],
            set_vectors=lambda v: holder.__setitem__("vectors", v),
        )
        return mgr, holder

    def test_empty_diff_reindex_does_not_save_vectors(
        self, index_vault: Path, tmp_path: Path
    ) -> None:
        """A reindex with no file changes must not re-serialise the vector index.

        The unconditional save churned the embeddings file on every watcher
        wake-up, feeding the reindex self-feedback loop (#720).
        """
        mgr, holder = self._mgr_with_embeddings(index_vault, tmp_path)
        mgr.build_index()
        mgr.build_embeddings()
        vectors = holder["vectors"]
        assert vectors is not None

        vectors.save = MagicMock(wraps=vectors.save)  # type: ignore[method-assign]
        result = mgr.reindex()

        assert (result.added, result.modified, result.deleted) == (0, 0, 0)
        vectors.save.assert_not_called()

    def test_real_change_reindex_saves_vectors(
        self, index_vault: Path, tmp_path: Path
    ) -> None:
        """A reindex that indexes a real change still persists the vector index."""
        mgr, holder = self._mgr_with_embeddings(index_vault, tmp_path)
        mgr.build_index()
        mgr.build_embeddings()
        vectors = holder["vectors"]
        assert vectors is not None

        vectors.save = MagicMock(wraps=vectors.save)  # type: ignore[method-assign]
        (index_vault / "alpha.md").write_text(
            "---\ntitle: Alpha\n---\n# Alpha\n\nChanged body content.\n",
            encoding="utf-8",
        )
        result = mgr.reindex()

        assert result.modified == 1
        vectors.save.assert_called_once()

    def test_stale_excluded_reindex_saves_vectors(
        self, index_vault: Path, tmp_path: Path
    ) -> None:
        """A reindex that only purges stale-excluded entries must still save.

        Purging an excluded-but-already-indexed doc calls
        ``vectors.delete_by_path`` (mutating the in-memory index) while the
        file diff stays empty (the file is still on disk, just filtered).
        Skipping the save would leave the stale vectors on disk to reload on
        the next startup (#720 follow-up).
        """
        mgr, holder = self._mgr_with_embeddings(index_vault, tmp_path)
        mgr.build_index()
        mgr.build_embeddings()
        vectors = holder["vectors"]
        assert vectors is not None

        vectors.save = MagicMock(wraps=vectors.save)  # type: ignore[method-assign]
        # Exclude a note that is already indexed; the file remains on disk so
        # the tracker reports no add/modify/delete — only the stale-excluded
        # purge mutates the vector index.
        mgr._exclude_patterns = ["alpha.md"]
        result = mgr.reindex()

        assert (result.added, result.modified, result.deleted) == (0, 0, 0)
        vectors.save.assert_called_once()

    def test_deletion_only_reindex_saves_vectors(
        self, index_vault: Path, tmp_path: Path
    ) -> None:
        """A reindex whose only change is a deleted file must still save.

        A pure deletion purges vectors via ``vectors.delete_by_path`` while
        ``indexed_added``/``indexed_modified``/``stale_excluded`` all stay 0, so
        ``changes.deleted`` is the sole term that must keep the save alive (#720).
        """
        mgr, holder = self._mgr_with_embeddings(index_vault, tmp_path)
        mgr.build_index()
        mgr.build_embeddings()
        vectors = holder["vectors"]
        assert vectors is not None

        vectors.save = MagicMock(wraps=vectors.save)  # type: ignore[method-assign]
        (index_vault / "beta.md").unlink()
        result = mgr.reindex()

        assert (result.added, result.modified) == (0, 0)
        assert result.deleted == 1
        vectors.save.assert_called_once()


# ---------------------------------------------------------------------------
# build_embeddings
# ---------------------------------------------------------------------------


class TestBuildEmbeddings:
    """Tests for IndexManager.build_embeddings()."""

    def test_returns_zero_without_provider(self, index_vault: Path, tmp_path: Path):
        """build_embeddings raises ValueError when no provider is configured."""
        mgr, _fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()
        with pytest.raises(ValueError, match="Embeddings require"):
            mgr.build_embeddings()

    def test_builds_with_provider(self, index_vault: Path, tmp_path: Path):
        """build_embeddings returns chunk count when provider is configured."""
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        embeddings_path = tmp_path / "embeddings"
        vectors_holder: dict = {"vectors": None}
        mgr, _fts, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=embeddings_path,
            embedding_provider=provider,
            get_vectors=lambda: vectors_holder["vectors"],
            set_vectors=lambda v: vectors_holder.__setitem__("vectors", v),
        )
        mgr.build_index()
        count = mgr.build_embeddings()
        assert count >= 4
        assert vectors_holder["vectors"] is not None

    def test_progress_logging_throttled_and_per_batch_at_debug(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """Per-batch embed detail logs at DEBUG; INFO carries only bounded
        decile progress lines (≤11) plus the final summary (#311)."""
        from tests.conftest import MockEmbeddingProvider

        vault = tmp_path / "vault"
        vault.mkdir()
        # One doc with 60 H2 sections -> ~60 chunks -> ~15 batches of 4.
        body = "\n".join(
            f"## Section {i}\n\nContent for section {i}.\n" for i in range(60)
        )
        (vault / "big.md").write_text(f"# Big\n\n{body}\n", encoding="utf-8")

        holder: dict = {"vectors": None}
        mgr, _fts, _ = _make_index_mgr(
            vault,
            tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=MockEmbeddingProvider(),
            get_vectors=lambda: holder["vectors"],
            set_vectors=lambda v: holder.__setitem__("vectors", v),
        )
        mgr.build_index()
        with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.managers.index"):
            total = mgr.build_embeddings()
        assert total >= 40  # many chunks => many batches

        per_batch = [r for r in caplog.records if "embedded chunks" in r.getMessage()]
        info_progress = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO
            and "build_embeddings:" in r.getMessage()
            and "%" in r.getMessage()
        ]
        # Per-batch detail is still emitted, but only at DEBUG.
        assert per_batch, "per-batch detail should still be logged (at DEBUG)"
        assert all(r.levelno == logging.DEBUG for r in per_batch)
        # INFO progress is throttled well below the per-batch count and bounded
        # by deciles.
        assert info_progress, "an INFO decile-progress line should be emitted"
        assert len(info_progress) <= 11
        assert len(info_progress) < len(per_batch)

    def test_build_embeddings_skips_failing_batch(self, tmp_path, caplog):
        import logging

        from tests.conftest import MockEmbeddingProvider

        class FlakyProvider(MockEmbeddingProvider):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def embed(self, texts):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError(
                        "Ollama API error 400: input length exceeds context"
                    )
                return super().embed(texts)

        vault = tmp_path / "vault"
        vault.mkdir()
        body = "\n".join(f"## S{i}\n\nbody {i}.\n" for i in range(20))
        (vault / "big.md").write_text(f"# Big\n\n{body}\n", encoding="utf-8")
        holder = {"vectors": None}
        embeddings_path = tmp_path / "embeddings"
        mgr, _fts, _ = _make_index_mgr(
            vault,
            tmp_path,
            embeddings_path=embeddings_path,
            embedding_provider=FlakyProvider(),
            get_vectors=lambda: holder["vectors"],
            set_vectors=lambda v: holder.__setitem__("vectors", v),
        )
        mgr.build_index()
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.index"
        ):
            count = mgr.build_embeddings()
        assert count >= 1  # build did not abort
        assert any("skip" in r.getMessage().lower() for r in caplog.records)

    def test_build_embeddings_all_batches_fail_escalates(self, tmp_path, caplog):
        """Every batch failing returns 0, saves nothing, warns loudly (#649)."""
        import logging

        from tests.conftest import MockEmbeddingProvider

        class DeadProvider(MockEmbeddingProvider):
            def embed(self, texts):  # noqa: ARG002
                raise RuntimeError("Ollama API error 500: provider down")

        vault = tmp_path / "vault"
        vault.mkdir()
        body = "\n".join(f"## S{i}\n\nbody {i}.\n" for i in range(12))
        (vault / "big.md").write_text(f"# Big\n\n{body}\n", encoding="utf-8")
        holder = {"vectors": None}
        embeddings_path = tmp_path / "embeddings"
        mgr, _fts, _ = _make_index_mgr(
            vault,
            tmp_path,
            embeddings_path=embeddings_path,
            embedding_provider=DeadProvider(),
            get_vectors=lambda: holder["vectors"],
            set_vectors=lambda v: holder.__setitem__("vectors", v),
        )
        mgr.build_index()
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.index"
        ):
            count = mgr.build_embeddings()
        assert count == 0  # nothing embedded
        assert not embeddings_path.exists()  # no vectors saved
        assert any("all_batches_failed" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# _load_vectors self-heal of a corrupt/incomplete sidecar
# ---------------------------------------------------------------------------


class TestLoadVectorsSelfHeal:
    """A corrupt/incomplete vector sidecar must rebuild instead of wedging boot.

    An interrupted save can leave a truncated or zero-byte sidecar; before the
    self-heal the corrupt file propagated out of ``_load_vectors`` and kept
    semantic search down until a manual rebuild. These tests pin the broadened
    catch — ``(json.JSONDecodeError, ValueError, EOFError, FileNotFoundError)``
    route to the same ``force=True`` rebuild — while confirming environmental
    errors (``PermissionError`` / generic ``OSError``) still propagate.
    """

    def _build_persisted_index(
        self, vault: Path, state_dir: Path
    ) -> tuple[IndexManager, dict, Path, Path]:
        """Build an index + embeddings and return (mgr, holder, npy, json).

        After this helper the sidecars exist on disk and the in-memory cache
        is cleared, so a subsequent ``_load_vectors()`` re-reads from disk.
        """
        from tests.conftest import MockEmbeddingProvider

        embeddings_path = state_dir / "embeddings"
        holder: dict = {"vectors": None}
        mgr, _fts, _ = _make_index_mgr(
            vault,
            state_dir,
            embeddings_path=embeddings_path,
            embedding_provider=MockEmbeddingProvider(),
            get_vectors=lambda: holder["vectors"],
            set_vectors=lambda v: holder.__setitem__("vectors", v),
        )
        mgr.build_index()
        mgr.build_embeddings()
        assert holder["vectors"] is not None
        # Drop the cache so _load_vectors() re-reads the sidecars from disk.
        holder["vectors"] = None

        npy_path = embeddings_path.with_suffix(".npy")
        json_path = embeddings_path.with_suffix(".json")
        assert npy_path.exists() and json_path.exists()
        return mgr, holder, npy_path, json_path

    def test_corrupt_json_triggers_rebuild(
        self, index_vault: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A truncated .json with a valid .npy rebuilds rather than propagating."""
        mgr, holder, _npy, json_path = self._build_persisted_index(
            index_vault, tmp_path
        )
        json_path.write_text('{"rows": [{"path"', encoding="utf-8")

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.index"
        ):
            result = mgr._load_vectors()

        assert result is holder["vectors"]
        assert result.count >= 4  # a usable, repopulated index
        assert any(
            "vector_index_corrupt_rebuilding" in r.getMessage() for r in caplog.records
        )

    def test_zero_byte_npy_triggers_rebuild(
        self, index_vault: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A zero-byte .npy raises EOFError from numpy — must rebuild, not propagate.

        EOFError is neither a ValueError nor an OSError, so it has to be named
        explicitly in the catch; this is the canonical interrupted-save residue.
        """
        mgr, holder, npy_path, _json = self._build_persisted_index(
            index_vault, tmp_path
        )
        npy_path.write_bytes(b"")  # truncate to zero bytes

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.index"
        ):
            result = mgr._load_vectors()

        assert result is holder["vectors"]
        assert result.count >= 4
        assert any(
            "vector_index_corrupt_rebuilding" in r.getMessage() for r in caplog.records
        )

    def test_garbage_npy_triggers_rebuild(
        self, index_vault: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A garbage (non-numpy) .npy raises ValueError — must rebuild."""
        mgr, holder, npy_path, _json = self._build_persisted_index(
            index_vault, tmp_path
        )
        npy_path.write_bytes(b"this is not a numpy array at all")

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.index"
        ):
            result = mgr._load_vectors()

        assert result is holder["vectors"]
        assert result.count >= 4
        assert any(
            "vector_index_corrupt_rebuilding" in r.getMessage() for r in caplog.records
        )

    def test_missing_json_with_present_npy_triggers_rebuild(
        self, index_vault: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A missing .json while the .npy exists (incomplete pair) rebuilds.

        With the .npy-exists guard satisfied, the FileNotFoundError raised when
        VectorIndex.load opens the absent .json can only mean an incomplete pair
        — so a rebuild is the correct response.
        """
        mgr, holder, _npy, json_path = self._build_persisted_index(
            index_vault, tmp_path
        )
        json_path.unlink()

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.index"
        ):
            result = mgr._load_vectors()

        assert result is holder["vectors"]
        assert result.count >= 4
        assert any(
            "vector_index_corrupt_rebuilding" in r.getMessage() for r in caplog.records
        )

    def test_row_count_mismatch_triggers_rebuild(
        self, index_vault: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A valid pair whose .json drops rows (count mismatch) rebuilds.

        This is the residue of a crash between the two atomic sidecar
        replaces: both files parse, but embeddings rows != metadata rows.
        VectorIndex.load raises VectorIndexCorruptError (a RuntimeError,
        caught by its explicit entry in the self-heal tuple), which routes
        to a force rebuild.
        """
        import json as _json

        mgr, holder, _npy, json_path = self._build_persisted_index(
            index_vault, tmp_path
        )
        payload = _json.loads(json_path.read_text(encoding="utf-8"))
        # Drop the last metadata row, leaving more embedding rows than metadata.
        payload["rows"] = payload["rows"][:-1]
        json_path.write_text(_json.dumps(payload), encoding="utf-8")

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.index"
        ):
            result = mgr._load_vectors()

        assert result is holder["vectors"]
        assert result.count >= 4  # repopulated, internally consistent
        assert result.count == result._embeddings.shape[0]  # parity restored
        assert any(
            "vector_index_corrupt_rebuilding" in r.getMessage() for r in caplog.records
        )

    def test_permission_error_propagates_without_rebuild(
        self, index_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An environmental OSError on load must propagate, not trigger a rebuild.

        PermissionError is an OSError but not one of the corruption types, so it
        is deliberately left out of the catch: a destructive rebuild would be
        futile against a disk/permission fault and would mask the real problem.
        """
        from markdown_vault_mcp.vector_index import VectorIndex

        mgr, _holder, _npy, _json = self._build_persisted_index(index_vault, tmp_path)

        def boom(*_args: object, **_kwargs: object) -> VectorIndex:
            raise PermissionError("read denied")

        monkeypatch.setattr(VectorIndex, "load", boom)
        rebuild_calls: list[bool] = []
        original_build = mgr.build_embeddings

        def tracking_build(*args: object, **kwargs: object) -> int:
            rebuild_calls.append(True)
            return original_build(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(mgr, "build_embeddings", tracking_build)

        with pytest.raises(PermissionError, match="read denied"):
            mgr._load_vectors()
        assert rebuild_calls == []  # no rebuild attempted

    def test_missing_index_cold_builds_without_rebuild_loop(
        self, index_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fully-missing index (.npy absent) takes the cold-build path, not rebuild.

        The .npy-exists guard must still steer a never-built vault to an empty
        VectorIndex rather than a force rebuild — verify the broadened catch did
        not disturb that branch.
        """
        from markdown_vault_mcp.vector_index import VectorIndex
        from tests.conftest import MockEmbeddingProvider

        embeddings_path = tmp_path / "embeddings"
        holder: dict = {"vectors": None}
        mgr, _fts, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=embeddings_path,
            embedding_provider=MockEmbeddingProvider(),
            get_vectors=lambda: holder["vectors"],
            set_vectors=lambda v: holder.__setitem__("vectors", v),
        )
        # No build_embeddings() call — sidecars never written.
        assert not embeddings_path.with_suffix(".npy").exists()

        rebuild_calls: list[bool] = []
        original_build = mgr.build_embeddings

        def tracking_build(*args: object, **kwargs: object) -> int:
            rebuild_calls.append(True)
            return original_build(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(mgr, "build_embeddings", tracking_build)
        result = mgr._load_vectors()

        assert isinstance(result, VectorIndex)
        assert result.count == 0  # empty cold-build index
        assert rebuild_calls == []  # cold path, not a rebuild


# ---------------------------------------------------------------------------
# embeddings_status
# ---------------------------------------------------------------------------


class TestEmbeddingsStatus:
    """Tests for IndexManager.embeddings_status()."""

    def test_not_configured(self, index_mgr):
        mgr, _, _ = index_mgr
        status = mgr.embeddings_status()
        assert status["available"] is False
        assert status["provider"] is None
        assert status["chunk_count"] == 0
        assert status["path"] is None

    def test_configured(self, index_vault: Path, tmp_path: Path):
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        embeddings_path = tmp_path / "embeddings"
        mgr, _, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=embeddings_path,
            embedding_provider=provider,
        )
        status = mgr.embeddings_status()
        assert status["available"] is True
        assert status["provider"] == "MockEmbeddingProvider"
        assert status["path"] == str(embeddings_path)

    def test_chunk_count_from_disk_sidecar_with_npy_extension(
        self, index_vault: Path, tmp_path: Path
    ):
        """The disk-read branch reports the real count for a `.npy`-suffixed base.

        With vectors unloaded, ``embeddings_status`` reads the sidecar count from
        disk. This exercises the second copy of the #819 ``with_suffix`` fix (in
        ``embeddings_status`` itself): a base carrying a ``.npy`` extension must
        resolve to the real files, not ``{base}.npy.npy`` (which would misreport
        0). Guards the previously-untested extension-carrying disk path (#836).
        """
        from markdown_vault_mcp.vector_index import VectorIndex
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        base = tmp_path / "embeddings.npy"
        vi = VectorIndex(provider)
        vi.add(
            ["hello world"],
            [
                {
                    "path": "a.md",
                    "title": "A",
                    "folder": "",
                    "heading": None,
                    "content": "hello world",
                }
            ],
        )
        vi.save(base)

        # get_vectors defaults to None (unbuilt), forcing the disk-read branch.
        mgr, _, holder = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=base,
            embedding_provider=provider,
        )
        assert holder["vectors"] is None
        status = mgr.embeddings_status()
        assert status["chunk_count"] == 1


# ---------------------------------------------------------------------------
# _is_path_excluded
# ---------------------------------------------------------------------------


class TestIsPathExcluded:
    """Tests for IndexManager._is_path_excluded()."""

    def test_no_patterns(self, index_mgr):
        mgr, _, _ = index_mgr
        assert mgr._is_path_excluded("anything.md") is False

    def test_matching_pattern(self, index_vault: Path, tmp_path: Path):
        mgr, _, _ = _make_index_mgr(index_vault, tmp_path, exclude_patterns=["notes/*"])
        assert mgr._is_path_excluded("notes/gamma.md") is True
        assert mgr._is_path_excluded("alpha.md") is False


# ---------------------------------------------------------------------------
# process_dirty_paths
# ---------------------------------------------------------------------------


class TestProcessDirtyPaths:
    """Tests for IndexManager.process_dirty_paths() (#559)."""

    def test_empty_set_is_noop(self, index_mgr):
        mgr, fts, _ = index_mgr
        mgr.build_index()
        before = fts.list_notes()
        mgr.process_dirty_paths(set())
        assert fts.list_notes() == before

    def test_upserts_modified_file(self, index_vault, tmp_path):
        mgr, fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()
        # Modify alpha.md on disk
        (index_vault / "alpha.md").write_text(
            "---\ntitle: AlphaModified\ntags:\n  - a\n  - b\n---\n# Alpha modified\n\nNew content.\n",
            encoding="utf-8",
        )
        mgr.process_dirty_paths({"alpha.md"})
        note = fts.get_note("alpha.md")
        assert note is not None
        # Title was changed via process_dirty_paths re-parse
        assert note["title"] == "AlphaModified"

    def test_deletes_missing_file(self, index_vault, tmp_path):
        mgr, fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()
        assert fts.get_note("beta.md") is not None
        (index_vault / "beta.md").unlink()
        mgr.process_dirty_paths({"beta.md"})
        assert fts.get_note("beta.md") is None

    def test_excluded_path_never_indexed_even_when_file_exists(
        self, index_vault, tmp_path
    ):
        """Excluded paths are purged, not upserted — the choke point every
        dirty-path producer flows through (write tools, future watchers)."""
        mgr, fts, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            exclude_patterns=["_conventions.md", "**/_conventions.md"],
        )
        mgr.build_index()
        (index_vault / "_conventions.md").write_text("Rules.", encoding="utf-8")
        mgr.process_dirty_paths({"_conventions.md"})
        assert fts.get_note("_conventions.md") is None

    def test_excluded_path_purges_stale_index_entry(self, index_vault, tmp_path):
        """A previously-indexed path that becomes excluded is purged on dirty."""
        mgr, fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()
        assert fts.get_note("alpha.md") is not None
        mgr2, _, _ = _make_index_mgr(
            index_vault, tmp_path, fts=fts, exclude_patterns=["alpha.md"]
        )
        mgr2.process_dirty_paths({"alpha.md"})
        assert fts.get_note("alpha.md") is None

    def test_stale_purge_loads_vectors_only_when_needed(self, index_vault, tmp_path):
        """The upgrade-scenario purge loads the vector sidecar and deletes
        the excluded doc's vectors; without stale rows the sidecar stays
        unloaded (lazy-loading regression guard)."""
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        embeddings_path = tmp_path / "embeddings"
        mgr, fts, holder = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=embeddings_path,
            embedding_provider=provider,
        )
        mgr.build_index()
        mgr.flush_dirty_embeddings({"alpha.md", "beta.md"})
        holder["vectors"].save(embeddings_path)

        # Same index, alpha.md now excluded: build purges FTS AND vectors.
        mgr2, _, holder2 = _make_index_mgr(
            index_vault,
            tmp_path,
            fts=fts,
            embeddings_path=embeddings_path,
            embedding_provider=provider,
            exclude_patterns=["alpha.md"],
        )
        mgr2.build_index()
        assert fts.get_note("alpha.md") is None
        vectors = holder2["vectors"]
        assert vectors is not None  # loaded because stale rows existed
        assert all(m["path"] != "alpha.md" for m in vectors._metadata)

        # A follow-up manager with the same exclusion but nothing stale
        # must NOT load the sidecar just because patterns are configured.
        mgr3, _, holder3 = _make_index_mgr(
            index_vault,
            tmp_path,
            fts=fts,
            embeddings_path=embeddings_path,
            embedding_provider=provider,
            exclude_patterns=["alpha.md"],
        )
        mgr3.build_index()
        assert holder3["vectors"] is None

    def test_continues_after_per_path_failure(self, index_vault, tmp_path):
        mgr, fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()
        # alpha.md still exists; bogus.md doesn't — should not raise
        mgr.process_dirty_paths({"alpha.md", "bogus.md"})
        # alpha.md still indexed; bogus.md correctly absent
        assert fts.get_note("alpha.md") is not None
        assert fts.get_note("bogus.md") is None


# ---------------------------------------------------------------------------
# flush_dirty_embeddings with explicit snapshot
# ---------------------------------------------------------------------------


class TestFlushDirtyEmbeddingsWithSnapshot:
    """Tests for the new explicit-snapshot path on flush_dirty_embeddings (#559)."""

    def test_explicit_snapshot_embeds_paths(self, index_vault, tmp_path):
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        mgr, _, vectors_holder = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=provider,
        )
        mgr.build_index()
        # Explicit snapshot drives the embedding.
        mgr.flush_dirty_embeddings({"alpha.md"})
        vectors = vectors_holder["vectors"]
        assert vectors is not None
        # Alpha's chunks now have vectors associated with them.
        # (Not asserting count — depends on chunker config — but presence.)

    def test_explicit_empty_set_is_noop(self, index_vault, tmp_path):
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        mgr, _, vectors_holder = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=provider,
        )
        mgr.build_index()
        mgr.flush_dirty_embeddings(set())
        # No-op: nothing should change
        assert vectors_holder["vectors"] is None  # _load_vectors was not called

    def test_excluded_path_gets_no_vectors_and_purges_stale_ones(
        self, index_vault, tmp_path
    ):
        """Excluded paths are treated as deletions in the vector flush."""
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        mgr, _, vectors_holder = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=provider,
            exclude_patterns=["_conventions.md", "**/_conventions.md"],
        )
        mgr.build_index()
        (index_vault / "_conventions.md").write_text("Rules.", encoding="utf-8")
        mgr.flush_dirty_embeddings({"_conventions.md", "alpha.md"})
        vectors = vectors_holder["vectors"]
        assert vectors is not None
        paths_with_vectors = {m["path"] for m in vectors._metadata}
        assert "alpha.md" in paths_with_vectors
        assert "_conventions.md" not in paths_with_vectors

    def test_parse_failure_preserves_existing_vectors(
        self, index_vault, tmp_path, monkeypatch
    ):
        """A parse failure for one path must NOT delete its existing vectors (#559)."""
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        embeddings_path = tmp_path / "embeddings"
        mgr, _, vectors_holder = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=embeddings_path,
            embedding_provider=provider,
        )
        mgr.build_index()
        # Seed vectors for alpha.md via a successful flush first.
        mgr.flush_dirty_embeddings({"alpha.md"})
        vectors = vectors_holder["vectors"]
        assert vectors is not None
        alpha_before = [m for m in vectors._metadata if m["path"] == "alpha.md"]
        assert alpha_before, "fixture should produce alpha.md vectors on first flush"

        # Now monkeypatch parse_note to raise for any path so the next flush
        # encounters a parse failure for alpha.md.
        from markdown_vault_mcp.managers import index as _idx_mod

        def _boom(*_a, **_kw):
            msg = "synthetic parse failure"
            raise OSError(msg)

        monkeypatch.setattr(_idx_mod, "parse_note", _boom)
        mgr.flush_dirty_embeddings({"alpha.md"})

        # Vectors for alpha.md must still be present.
        alpha_after = [m for m in vectors._metadata if m["path"] == "alpha.md"]
        assert alpha_after == alpha_before, (
            "parse failure must not delete existing vectors for the path"
        )


# ---------------------------------------------------------------------------
# start_line propagation into vector metadata (#469)
# ---------------------------------------------------------------------------


def test_start_line_propagated_to_vector_metadata(tmp_path):
    """Each vector row carries start_line for stable section ordering (#469)."""
    from markdown_vault_mcp.vault import Vault
    from tests.conftest import MockEmbeddingProvider

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text(
        "# A\n\n"
        + ("first section body.\n" * 12)
        + "\n## B\n\n"
        + ("second.\n" * 12)
        + "\n## C\n\nthird.\n"
    )

    col = Vault(
        source_dir=vault,
        embedding_provider=MockEmbeddingProvider(),
        embeddings_path=tmp_path / "vectors",
    )
    col.index.build_index()
    col.index.build_embeddings()

    # Inspect the underlying VectorIndex metadata directly.
    assert col._vectors is not None
    metas = col._vectors._metadata
    assert metas, "vector index should have rows for the test note"
    assert all("start_line" in m for m in metas), (
        f"every metadata row must carry start_line; got keys {set(metas[0])}"
    )
    # Lines are monotonically non-decreasing within the document.
    starts = [m["start_line"] for m in metas if m["path"] == "note.md"]
    assert starts == sorted(starts), (
        f"start_line should be non-decreasing, got {starts}"
    )
    assert len(starts) >= 2, (
        f"fixture should produce multiple chunks; got starts={starts}.  "
        "If HeadingChunker.short_doc_lines changed, pad the fixture."
    )


def test_index_manager_no_longer_schedules_timer():
    """IndexManager no longer creates a threading.Timer on dirty mark (#559)."""
    import inspect

    from markdown_vault_mcp.managers.index import IndexManager

    source = inspect.getsource(IndexManager)
    assert "_schedule_embedding_flush" not in source
    assert "_embedding_flush_timer" not in source
    assert "update_vector_index" not in source
    # mark_dirty / remove_from_dirty also gone
    assert "def mark_dirty" not in source
    assert "def remove_from_dirty" not in source


class TestBuildIndexSkipReasons:
    """build_index records surfaced skips into tracker skip_reasons (#775)."""

    def test_invalid_yaml_file_appears_in_skip_reasons(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        (tmp_path / "good.md").write_text("---\ntitle: ok\n---\nbody", encoding="utf-8")
        (tmp_path / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\nbody", encoding="utf-8"
        )
        vault = Vault(source_dir=tmp_path)
        try:
            vault.index.build_index()
            reasons = vault.index.skipped_files()
        finally:
            vault.close()
        by_path = {sf.path: sf for sf in reasons}
        assert "bad.md" in by_path
        assert by_path["bad.md"].category == "parse_error"
        assert "good.md" not in by_path

    def test_missing_frontmatter_appears_in_skip_reasons(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        (tmp_path / "good.md").write_text("---\ntitle: ok\n---\nbody", encoding="utf-8")
        (tmp_path / "nomatter.md").write_text("no frontmatter", encoding="utf-8")
        vault = Vault(source_dir=tmp_path, required_frontmatter=["title"])
        try:
            vault.index.build_index()
            by_path = {sf.path: sf for sf in vault.index.skipped_files()}
        finally:
            vault.close()
        assert by_path["nomatter.md"].category == "missing_frontmatter"
        assert "title" in by_path["nomatter.md"].detail

    def test_encoding_error_appears_in_skip_reasons(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        (tmp_path / "good.md").write_text("---\ntitle: ok\n---\nbody", encoding="utf-8")
        (tmp_path / "latin.md").write_bytes(b"\xff\xfe not utf-8")
        vault = Vault(source_dir=tmp_path)
        try:
            vault.index.build_index()
            by_path = {sf.path: sf for sf in vault.index.skipped_files()}
        finally:
            vault.close()
        assert by_path["latin.md"].category == "encoding_error"

    def test_pass2_hash_failure_on_surfaced_skip_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        import logging

        from markdown_vault_mcp.vault import Vault

        (tmp_path / "good.md").write_text("---\ntitle: ok\n---\nbody", encoding="utf-8")
        (tmp_path / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\n", encoding="utf-8"
        )
        import markdown_vault_mcp.managers.index as index_module

        def failing_hash(_path):
            raise OSError("vanished")

        # Only non-indexed files (bad.md) are hashed in build_index's second
        # pass; good.md is indexed and skipped there. So this forces the
        # OSError only for the already-surfaced skip.
        monkeypatch.setattr(index_module, "compute_file_hash", failing_hash)
        vault = Vault(source_dir=tmp_path)
        try:
            with caplog.at_level(
                logging.WARNING, logger="markdown_vault_mcp.managers.index"
            ):
                vault.index.build_index()
        finally:
            vault.close()
        assert any(
            "build_index_surfaced_skip_dropped" in r.message and "bad.md" in r.message
            for r in caplog.records
        )


class TestReindexSkipReasons:
    """reindex records surfaced skips into tracker skip_reasons (#775)."""

    def _build(self, tmp_path: Path):
        from markdown_vault_mcp.vault import Vault

        vault = Vault(source_dir=tmp_path)
        vault.index.build_index()
        return vault

    def test_invalid_yaml_recorded_as_parse_error(self, tmp_path: Path) -> None:
        # Regression guard for the reindex yaml.YAMLError split (#802): without
        # the dedicated yaml.YAMLError branch (ahead of the generic Exception
        # branch, which records internal_error), a malformed-YAML file would be
        # mislabelled internal_error. Do not remove as "redundant".
        (tmp_path / "seed.md").write_text("---\na: 1\n---\nx", encoding="utf-8")
        vault = self._build(tmp_path)
        try:
            (tmp_path / "bad.md").write_text(
                "---\ntitle: [unclosed\n---\n", encoding="utf-8"
            )
            vault.index.reindex()
            by_path = {sf.path: sf for sf in vault.index.skipped_files()}
        finally:
            vault.close()
        assert by_path["bad.md"].category == "parse_error"

    def test_unexpected_exception_recorded_as_internal_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "seed.md").write_text("---\na: 1\n---\nx", encoding="utf-8")
        vault = self._build(tmp_path)
        try:
            (tmp_path / "boom.md").write_text(
                "---\ntitle: ok\n---\nbody", encoding="utf-8"
            )
            import markdown_vault_mcp.scanner as scanner_module

            real_parse = scanner_module.parse_note

            def maybe_explode(abs_path, source_dir, chunk_strategy, **kwargs):
                if abs_path.name == "boom.md":
                    raise RuntimeError("chunker exploded")
                return real_parse(abs_path, source_dir, chunk_strategy, **kwargs)

            monkeypatch.setattr(scanner_module, "parse_note", maybe_explode)
            vault.index.reindex()
            by_path = {sf.path: sf for sf in vault.index.skipped_files()}
        finally:
            vault.close()
        assert by_path["boom.md"].category == "internal_error"
        assert "chunker exploded" in by_path["boom.md"].detail

    def test_missing_field_recorded_as_missing_frontmatter(
        self, tmp_path: Path
    ) -> None:
        from markdown_vault_mcp.vault import Vault

        (tmp_path / "seed.md").write_text("---\ntitle: ok\n---\nx", encoding="utf-8")
        vault = Vault(source_dir=tmp_path, required_frontmatter=["title"])
        try:
            vault.index.build_index()
            (tmp_path / "nomatter.md").write_text("no fm", encoding="utf-8")
            vault.index.reindex()
            by_path = {sf.path: sf for sf in vault.index.skipped_files()}
        finally:
            vault.close()
        assert by_path["nomatter.md"].category == "missing_frontmatter"
        assert "title" in by_path["nomatter.md"].detail

    def test_non_utf8_recorded_as_encoding_error(self, tmp_path: Path) -> None:
        (tmp_path / "seed.md").write_text("---\na: 1\n---\nx", encoding="utf-8")
        vault = self._build(tmp_path)
        try:
            (tmp_path / "latin.md").write_bytes(b"\xff\xfe bad bytes")
            vault.index.reindex()
            by_path = {sf.path: sf for sf in vault.index.skipped_files()}
        finally:
            vault.close()
        assert by_path["latin.md"].category == "encoding_error"

    def test_excluded_file_not_recorded(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        (tmp_path / "seed.md").write_text("---\na: 1\n---\nx", encoding="utf-8")
        (tmp_path / "excluded").mkdir()
        vault = Vault(source_dir=tmp_path, exclude_patterns=["excluded/**"])
        try:
            vault.index.build_index()
            (tmp_path / "excluded" / "bad.md").write_text(
                "---\ntitle: [unclosed\n---\n", encoding="utf-8"
            )
            vault.index.reindex()
            paths = {sf.path for sf in vault.index.skipped_files()}
        finally:
            vault.close()
        assert "excluded/bad.md" not in paths

    def test_fixed_file_removed_from_skip_reasons(self, tmp_path: Path) -> None:
        (tmp_path / "seed.md").write_text("---\na: 1\n---\nx", encoding="utf-8")
        bad = tmp_path / "bad.md"
        bad.write_text("---\ntitle: [unclosed\n---\n", encoding="utf-8")
        vault = self._build(tmp_path)
        try:
            vault.index.reindex()
            assert "bad.md" in {sf.path for sf in vault.index.skipped_files()}
            # Fix the YAML; the next reindex indexes it and clears the reason.
            bad.write_text("---\ntitle: fixed\n---\nbody", encoding="utf-8")
            vault.index.reindex()
            assert "bad.md" not in {sf.path for sf in vault.index.skipped_files()}
        finally:
            vault.close()

    def test_reason_carried_forward_through_unchanged_reindex(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "seed.md").write_text("---\na: 1\n---\nx", encoding="utf-8")
        (tmp_path / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\n", encoding="utf-8"
        )
        vault = self._build(tmp_path)
        try:
            vault.index.reindex()
            first = {sf.path: sf for sf in vault.index.skipped_files()}
            assert first["bad.md"].category == "parse_error"
            # Second reindex with bad.md unchanged: it lands in
            # skipped_unchanged and is never re-parsed, so the reason must
            # survive via the tracker's carry-forward wired through reindex.
            vault.index.reindex()
            second = {sf.path: sf for sf in vault.index.skipped_files()}
            assert second["bad.md"].category == "parse_error"
            assert second["bad.md"].detail == first["bad.md"].detail
        finally:
            vault.close()


# ---------------------------------------------------------------------------
# Context-enriched embedding text (curated ranking)
# ---------------------------------------------------------------------------


class _CapturingProvider:
    """Deterministic provider that records every embedded text."""

    provider_name = "capturing"
    model_name = "capturing-model"

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[1.0, 0.0] for _ in texts]


def _enriched_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "doc.md").write_text(
        "---\ntitle: Enriched Doc\nsummary: A crisp summary.\n---\n"
        "# Enriched Doc\n\nplain chunk body\n",
        encoding="utf-8",
    )
    return vault


def _enriched_mgr(
    vault: Path, tmp_path: Path, provider: _CapturingProvider
) -> tuple[IndexManager, FTSIndex, dict]:
    from markdown_vault_mcp.embed_text import EmbedTextBuilder

    builder = EmbedTextBuilder(embed_context=True, searchable_fields=("summary",))
    return _make_index_mgr(
        vault,
        tmp_path,
        fts=FTSIndex(db_path=":memory:", searchable_frontmatter_fields=["summary"]),
        embeddings_path=tmp_path / "embeddings",
        embedding_provider=provider,
        embed_text_builder=builder,
    )


class TestEmbedTextEnrichment:
    def test_cold_build_embeds_enriched_text_and_stores_preamble(
        self, tmp_path: Path
    ) -> None:
        provider = _CapturingProvider()
        vault = _enriched_vault(tmp_path)
        mgr, _fts, holder = _enriched_mgr(vault, tmp_path, provider)
        mgr.build_index()
        assert mgr.build_embeddings() == 1

        # Short doc -> one heading-less chunk whose content is the whole
        # body; v2 prefixes the title line and the first-chunk preamble.
        assert provider.texts == [
            "Enriched Doc\nA crisp summary.\n\n# Enriched Doc\n\nplain chunk body"
        ]
        rows = holder["vectors"].chunks_by_path()["doc.md"]
        assert rows[0]["preamble"] == "A crisp summary."
        # Displayed content stays the raw chunk text.
        assert rows[0]["content"] == "# Enriched Doc\n\nplain chunk body"

    def test_hot_reindex_embeds_enriched_text(self, tmp_path: Path) -> None:
        provider = _CapturingProvider()
        vault = _enriched_vault(tmp_path)
        mgr, _fts, holder = _enriched_mgr(vault, tmp_path, provider)
        mgr.build_index()
        mgr.build_embeddings()
        provider.texts.clear()

        (vault / "doc.md").write_text(
            "---\ntitle: Enriched Doc\nsummary: A crisp summary.\n---\n"
            "# Enriched Doc\n\nedited chunk body\n",
            encoding="utf-8",
        )
        result = mgr.reindex()
        assert result.modified == 1
        assert provider.texts == [
            "Enriched Doc\nA crisp summary.\n\n# Enriched Doc\n\nedited chunk body"
        ]
        rows = holder["vectors"].chunks_by_path()["doc.md"]
        assert rows[0]["preamble"] == "A crisp summary."

    def test_flush_dirty_embeds_enriched_text(self, tmp_path: Path) -> None:
        provider = _CapturingProvider()
        vault = _enriched_vault(tmp_path)
        mgr, _fts, holder = _enriched_mgr(vault, tmp_path, provider)
        mgr.build_index()
        mgr.build_embeddings()
        provider.texts.clear()

        mgr.flush_dirty_embeddings({"doc.md"})
        assert provider.texts == [
            "Enriched Doc\nA crisp summary.\n\n# Enriched Doc\n\nplain chunk body"
        ]
        rows = holder["vectors"].chunks_by_path()["doc.md"]
        assert rows[0]["preamble"] == "A crisp summary."

    def test_default_builder_embeds_raw_content(self, tmp_path: Path) -> None:
        """v1 (default) stays a byte-exact no-op on embedding input."""
        provider = _CapturingProvider()
        vault = _enriched_vault(tmp_path)
        mgr, _fts, _holder = _make_index_mgr(
            vault,
            tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=provider,
        )
        mgr.build_index()
        mgr.build_embeddings()
        assert provider.texts == ["# Enriched Doc\n\nplain chunk body"]
