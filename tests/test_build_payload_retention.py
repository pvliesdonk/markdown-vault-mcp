"""Cold indexing releases parsed payloads without weakening rebuild controls."""

from __future__ import annotations

import gc
import logging
import sqlite3
import weakref
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers import index as index_module
from markdown_vault_mcp.managers.index import IndexManager
from markdown_vault_mcp.scanner import HeadingChunker
from markdown_vault_mcp.tracker import ChangeTracker

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from markdown_vault_mcp.types import ParsedNote


class _Payload(str):
    """Weak-referenceable text that really reaches the keyword writer."""


@pytest.fixture
def build_parts(
    tmp_path: Path,
) -> Iterator[tuple[IndexManager, FTSIndex, ChangeTracker]]:
    """Wire a real keyword writer and tracker over an explicit synthetic root."""
    root = tmp_path / "vault"
    root.mkdir()
    fts = FTSIndex(db_path=tmp_path / "index.db")
    tracker = ChangeTracker(tmp_path / "state.json")
    manager = IndexManager(
        fts=fts,
        tracker=tracker,
        source_dir=root,
        chunk_strategy=HeadingChunker(max_chunk_words=400),
    )
    try:
        yield manager, fts, tracker
    finally:
        fts.close()


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("failing", [False, True])
def test_completed_parsed_payloads_are_released_during_scan(
    tmp_path: Path,
    build_parts: tuple[IndexManager, FTSIndex, ChangeTracker],
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
    failing: bool,
) -> None:
    """Track actual chunk/frontmatter text, including failed upsert payloads."""
    # A captured exception traceback would itself keep the rejected note.
    # Failure behavior is checked separately without suppressing its logs.
    monkeypatch.setattr(logging.getLogger(index_module.__name__), "disabled", True)
    manager, fts, tracker = build_parts
    root = tmp_path / "vault"
    for number in range(8):
        (root / f"{number}.md").write_text(
            "---\nsummary: "
            + "metadata " * 4096
            + "\n---\n# Note\n\n"
            + "bodyword " * 8192,
            encoding="utf-8",
        )
    original_scan = index_module.scan_directory
    original_upsert = fts.upsert_note
    references: list[weakref.ReferenceType[_Payload]] = []
    live_after_yield: list[int] = []
    writes = 0

    def observe_scan(*args: Any, **kwargs: Any) -> Iterator[ParsedNote]:
        for note in original_scan(*args, **kwargs):
            for chunk in note.chunks:
                chunk.content = _Payload(chunk.content)
                references.append(weakref.ref(chunk.content))
            del chunk
            note.frontmatter["summary"] = _Payload(note.frontmatter["summary"])
            references.append(weakref.ref(note.frontmatter["summary"]))
            yield note
            del note
            gc.collect()
            # The consumer and original scanner may still hold the latest
            # note. Every older note's large text must already be reclaimable.
            live_after_yield.append(sum(ref() is not None for ref in references))

    def observe_upsert(note: ParsedNote) -> int:
        nonlocal writes
        assert all(isinstance(chunk.content, _Payload) for chunk in note.chunks)
        assert isinstance(note.frontmatter["summary"], _Payload)
        writes += 1
        if failing and note.path == "3.md":
            raise ValueError("synthetic note rejection")
        return original_upsert(note)

    monkeypatch.setattr(index_module, "scan_directory", observe_scan)
    monkeypatch.setattr(fts, "upsert_note", observe_upsert)
    stats = manager.build_index(force=force)
    payloads_per_note = len(references) // 8
    assert writes == 8
    assert stats.documents_indexed == 8 - failing
    assert max(live_after_yield) <= payloads_per_note
    assert len(fts.list_notes()) == 8 - failing
    expected_hash = "" if failing else fts.get_note("3.md")["content_hash"]
    assert tracker._load_state()[0]["3.md"] == expected_hash


@pytest.mark.parametrize("force", [False, True])
def test_late_unread_prefix_spares_old_rows_but_not_deleted_siblings(
    tmp_path: Path,
    build_parts: tuple[IndexManager, FTSIndex, ChangeTracker],
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
) -> None:
    """Final pruning uses complete walk evidence and preserves retry hashes."""
    manager, fts, tracker = build_parts
    root = tmp_path / "vault"
    for path in ("a.md", "fail.md", "shut/kept.md", "shutway/deleted.md"):
        target = root / path
        target.parent.mkdir(exist_ok=True)
        target.write_text("# Old\n\noldword\n", encoding="utf-8")
    (root / "shut/bad.md").write_bytes(b"\xff")
    manager.build_index()
    old_hash = fts.get_note("fail.md")["content_hash"]
    (root / "shutway/deleted.md").unlink()
    (root / "a.md").write_text("# New\n\nnewword [[kept]]\n", encoding="utf-8")
    (root / "fail.md").write_text("# Changed\n\nchangedword\n", encoding="utf-8")
    original_scan = index_module.scan_directory
    original_upsert = fts.upsert_note

    def late_unread_scan(
        *args: Any,
        on_walk_error: Callable[[OSError], None],
        **kwargs: Any,
    ) -> Iterator[ParsedNote]:
        for note in original_scan(*args, **kwargs):
            if not note.path.startswith("shut/"):
                yield note
        on_walk_error(
            PermissionError(13, "synthetic unread directory", str(root / "shut"))
        )

    def refuse_one(note: ParsedNote) -> int:
        if note.path == "fail.md":
            raise ValueError("synthetic note rejection")
        return original_upsert(note)

    monkeypatch.setattr(index_module, "scan_directory", late_unread_scan)
    monkeypatch.setattr(fts, "upsert_note", refuse_one)
    stats = manager.build_index(force=force)
    assert not stats.walk_complete
    assert stats.documents_indexed == 1
    assert fts.get_note("a.md")["title"] == "New"
    assert fts.get_note("shut/kept.md") is not None
    assert fts.get_note("shutway/deleted.md") is None
    assert [row["target_path"] for row in fts.get_outlinks("a.md")] == ["shut/kept.md"]
    failed_row = fts.get_note("fail.md")
    if force:
        assert failed_row is None
    else:
        assert failed_row is not None
        assert failed_row["content_hash"] == old_hash
    hashes, skipped, _ = tracker._load_state()
    assert hashes["fail.md"] == hashes["shut/kept.md"] == ""
    assert skipped["shut/bad.md"] == ""
    assert [row["path"] for row in fts.list_tombstones()] == ["shut/bad.md"]


def test_database_error_propagates_without_advancing_tracker(
    tmp_path: Path,
    build_parts: tuple[IndexManager, FTSIndex, ChangeTracker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database-level failures propagate without advancing tracker state."""
    manager, fts, _ = build_parts
    (tmp_path / "vault/a.md").write_text("# A\n\nbody\n", encoding="utf-8")

    def locked(_note: ParsedNote) -> int:
        raise sqlite3.OperationalError("synthetic locked writer")

    monkeypatch.setattr(fts, "upsert_note", locked)
    with pytest.raises(sqlite3.OperationalError, match="synthetic locked writer"):
        manager.build_index()
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("excluded", [False, True])
def test_rebuild_does_not_reload_corpus_frontmatter(
    tmp_path: Path,
    build_parts: tuple[IndexManager, FTSIndex, ChangeTracker],
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
    excluded: bool,
) -> None:
    """Pruning populated indexes must not materialize their metadata payloads."""
    manager, fts, _ = build_parts
    root = tmp_path / "vault"
    (root / "excluded").mkdir()
    for path in ("keep.md", "excluded/old.md"):
        (root / path).write_text(
            "---\nsummary: " + "metadata " * 8192 + "\n---\n# Note\n\nbodyword\n",
            encoding="utf-8",
        )
    manager.build_index()
    if excluded:
        monkeypatch.setattr(manager, "_exclude_patterns", ["excluded/**"])
    original_list = fts.list_notes

    def reject_bulk_payloads(*, folder: str | None = None) -> list[dict[str, Any]]:
        rows = original_list(folder=folder)
        assert not any(row["frontmatter_json"] for row in rows), (
            "cold build reloaded corpus frontmatter"
        )
        return rows

    monkeypatch.setattr(fts, "list_notes", reject_bulk_payloads)
    stats = manager.build_index(force=force)
    expected = {"keep.md"} if excluded else {"keep.md", "excluded/old.md"}
    assert stats.documents_indexed == len(expected)
    assert {row["path"] for row in original_list()} == expected


def test_projected_state_rows_preserve_values_and_public_metadata(
    tmp_path: Path,
    build_parts: tuple[IndexManager, FTSIndex, ChangeTracker],
) -> None:
    """The optional projection is sorted, live-only and excludes payload fields."""
    manager, fts, _ = build_parts
    root = tmp_path / "vault"
    for path in ("z.md", "a.md"):
        (root / path).write_text(
            f"---\nsummary: metadata\n---\n# {path}\n\nbodyword\n", encoding="utf-8"
        )
    (root / "bad.md").write_bytes(b"\xff")
    manager.build_index()
    reader = getattr(fts, "list_note_states", None)
    assert callable(reader), "projected state reader is unavailable"
    full_rows = fts.list_notes()
    keys = ("path", "title", "content_hash", "modified_at")
    assert reader() == [{key: row[key] for key in keys} for row in full_rows]
    assert [row["path"] for row in full_rows] == ["a.md", "z.md"]
    assert all(row["frontmatter_json"] for row in full_rows)
    assert [row["path"] for row in fts.list_tombstones()] == ["bad.md"]


@pytest.mark.parametrize("force", [False, True])
def test_indexes_without_optional_projection_remain_compatible(
    tmp_path: Path,
    build_parts: tuple[IndexManager, FTSIndex, ChangeTracker],
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
) -> None:
    """The eager fallback prunes stale rows while retaining surviving state."""
    manager, fts, tracker = build_parts
    root = tmp_path / "vault"
    (root / "a.md").write_text("# A\n\nbodyword\n", encoding="utf-8")
    (root / "deleted.md").write_text("# Deleted\n\nstaleword\n", encoding="utf-8")
    assert manager.build_index().documents_indexed == 2
    old_hash = fts.get_note("a.md")["content_hash"]
    (root / "deleted.md").unlink()
    monkeypatch.delattr(FTSIndex, "list_note_states")
    assert manager.build_index(force=force).documents_indexed == 1
    assert fts.get_note("deleted.md") is None
    assert [row["path"] for row in fts.list_notes()] == ["a.md"]
    row = fts.get_note("a.md")
    assert row is not None
    assert row["title"] == "A"
    assert row["content_hash"] == old_hash
    assert tracker._load_state()[0] == {"a.md": row["content_hash"]}
