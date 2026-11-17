"""Scoped text replacement previews and application."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.scanner import HeadingChunker


def test_preview_then_apply_literal(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("old old\n", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    preview = replacer.replace(ReplaceRequest("old", "new", folders=("",)))
    assert (tmp_path / "a.md").read_text() == "old old\n"
    assert preview.notes[0].replacements == 2
    assert "-old old" in preview.notes[0].diff
    result = replacer.replace(
        ReplaceRequest("old", "new", folders=("",), dry_run=False, plan=preview.plan)
    )
    assert result.replacements == 2
    assert (tmp_path / "a.md").read_text() == "new new\n"


def test_regex_and_stale_selection(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import ConcurrentModificationError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("Ab 123\n", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest(
        r"(?i)(ab) (?P<num>\d+)", r"\g<num>-\1", folders=("",), regex=True
    )
    preview = replacer.replace(request)
    assert "+123-Ab" in preview.notes[0].diff
    (tmp_path / "new.md").write_text("AB 234", encoding="utf-8")
    from dataclasses import replace

    with pytest.raises(ConcurrentModificationError):
        replacer.replace(replace(request, dry_run=False, plan=preview.plan))
    assert (tmp_path / "a.md").read_text() == "Ab 123\n"


def test_limits_and_literal_backslash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers import bulk_replace
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("aaa", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    preview = replacer.replace(ReplaceRequest("a", r"\1", paths=("a.md",)))
    assert r"\1\1\1" in preview.notes[0].diff
    with pytest.raises(InvalidRequestError, match="Preview first"):
        replacer.replace(ReplaceRequest("a", paths=("a.md",), dry_run=False))
    monkeypatch.setattr(bulk_replace, "MAX_MATCHES", 2)
    with pytest.raises(InvalidRequestError, match="Too many matches"):
        replacer.replace(ReplaceRequest("a", folders=("",)))


def test_deleted_explicit_note_is_stale_plan(tmp_path: Path) -> None:
    from dataclasses import replace

    from markdown_vault_mcp.exceptions import ConcurrentModificationError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    note = tmp_path / "a.md"
    note.write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("old", "new", paths=("a.md",))
    preview = replacer.replace(request)
    note.unlink()
    with pytest.raises(ConcurrentModificationError):
        replacer.replace(replace(request, dry_run=False, plan=preview.plan))


def test_folder_selection_skips_reserved_hidden_and_excluded(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / ".hidden").mkdir()
    for name in ("a.md", "index.md", "log.md", ".hidden/b.md", "excluded.md"):
        (tmp_path / name).write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        exclude_patterns=["excluded.md"],
    )
    result = BulkReplacer(manager).replace(
        ReplaceRequest("old", "new", folders=("",), paths=("index.md", "a.md"))
    )
    assert [note.path for note in result.notes] == ["a.md", "index.md"]
    assert result.scanned == 2


def test_interrupted_write_runs_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.managers.bulk_replace import (
        BulkReplacer,
        ReplaceInterruptedError,
        ReplaceRequest,
    )

    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    preview = replacer.replace(ReplaceRequest("old", "new", folders=("",)))
    real_write = manager.write

    def fail_second(path: str, content: str, **kwargs: object) -> object:
        if path == "b.md":
            raise OSError("disk full")
        return real_write(path, content, **kwargs)

    monkeypatch.setattr(manager, "write", fail_second)
    maintained: list[str] = []
    with pytest.raises(ReplaceInterruptedError, match=r"a\.md.*b\.md"):
        replacer.replace(
            ReplaceRequest(
                "old", "new", folders=("",), dry_run=False, plan=preview.plan
            ),
            maintain=maintained.append,
        )
    assert maintained == ["a.md"]
    assert (tmp_path / "a.md").read_text() == "new"
    assert (tmp_path / "b.md").read_text() == "old"


def test_preflight_refusals_and_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers import bulk_replace
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    note = tmp_path / "a.md"
    note.write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    for request in (
        ReplaceRequest("", folders=("",)),
        ReplaceRequest("x"),
        ReplaceRequest("(", folders=("",), regex=True),
    ):
        with pytest.raises(InvalidRequestError):
            replacer.replace(request)
    assert replacer.replace(ReplaceRequest("old", "old", folders=("",))).notes == []
    monkeypatch.setattr(bulk_replace, "MAX_DIFF_CHARS", 1)
    with pytest.raises(InvalidRequestError, match="Diff too large"):
        replacer.replace(ReplaceRequest("old", "new", folders=("",)))
    note.write_bytes(b"\xff")
    with pytest.raises(InvalidRequestError, match="UTF-8"):
        replacer.replace(ReplaceRequest("old", "new", folders=("",)))


def test_regex_timeout_is_bounded(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("a" * 5000 + "b", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    with pytest.raises(InvalidRequestError, match="timed out"):
        BulkReplacer(manager).replace(
            ReplaceRequest(r"(a+)+$", "x", folders=("",), regex=True)
        )


def test_caps_refuse_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers import bulk_replace
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("a" * 20, encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    monkeypatch.setattr(bulk_replace, "MAX_NOTE_BYTES", 10)
    with pytest.raises(InvalidRequestError, match="size limit"):
        replacer.replace(ReplaceRequest("a", "b", folders=("",)))
    monkeypatch.setattr(bulk_replace, "MAX_NOTE_BYTES", 100)
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", 5)
    with pytest.raises(InvalidRequestError, match="output too large"):
        replacer.replace(ReplaceRequest("a", "bb", folders=("",)))
    assert (tmp_path / "a.md").read_text() == "a" * 20


def test_diff_shows_both_lines_when_note_has_no_final_newline(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    diff = (
        BulkReplacer(manager)
        .replace(ReplaceRequest("old", "new", paths=("a.md",)))
        .notes[0]
        .diff
    )
    assert "-old\n" in diff
    assert "+new\n" in diff


def test_zero_width_and_stale_unchanged_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from markdown_vault_mcp.exceptions import (
        ConcurrentModificationError,
        InvalidRequestError,
    )
    from markdown_vault_mcp.managers import bulk_replace
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("old", encoding="utf-8")
    other = tmp_path / "b.md"
    other.write_text("untouched", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("old", "new", folders=("",))
    preview = replacer.replace(request)
    other.write_text("old changed", encoding="utf-8")
    with pytest.raises(ConcurrentModificationError):
        replacer.replace(replace(request, dry_run=False, plan=preview.plan))
    assert (tmp_path / "a.md").read_text() == "old"
    monkeypatch.setattr(bulk_replace, "MAX_MATCHES", 2)
    with pytest.raises(InvalidRequestError, match="Too many matches"):
        replacer.replace(ReplaceRequest(r"(?=.)", "x", paths=("a.md",), regex=True))


def test_read_only_and_invalid_selectors(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError, ReadOnlyError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=True,
        exclude_patterns=["hidden.md"],
    )
    replacer = BulkReplacer(manager)
    plan = replacer.replace(ReplaceRequest("old", "new", paths=("a.md",))).plan
    with pytest.raises(ReadOnlyError):
        replacer.replace(
            ReplaceRequest("old", "new", paths=("a.md",), dry_run=False, plan=plan)
        )
    for path in ("missing.md", "hidden.md", "image.png", "../escape.md"):
        with pytest.raises(InvalidRequestError):
            replacer.replace(ReplaceRequest("old", paths=(path,)))


def test_folder_skips_submodule_but_explicit_refuses(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.git.submodules import SubmoduleGuard
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest
    from tests.fixtures.git import make_superproject

    root = make_superproject(tmp_path)
    (root / "top.md").write_text("old", encoding="utf-8")
    (root / "sub" / "note.md").write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        root,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        submodule_guard=SubmoduleGuard(root),
    )
    replacer = BulkReplacer(manager)
    result = replacer.replace(ReplaceRequest("old", "new", folders=("",)))
    assert [note.path for note in result.notes] == ["top.md"]
    with pytest.raises(InvalidRequestError, match="submodules"):
        replacer.replace(ReplaceRequest("old", "new", paths=("sub/note.md",)))


def test_apply_uses_provenance_enricher(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    import frontmatter

    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest
    from markdown_vault_mcp.okf import apply_okf_write_stamp

    note = tmp_path / "a.md"
    note.write_text(
        "---\nverified:\n  - by: human:a\n    at: 2026-01-01\n---\nold\n",
        encoding="utf-8",
    )

    def enrich(text: str, _operation: object) -> str:
        return apply_okf_write_stamp(
            text, actor="tool:test", now=datetime(2026, 1, 2, tzinfo=UTC)
        )

    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        okf_write_enrich=enrich,
    )
    replacer = BulkReplacer(manager)
    plan = replacer.replace(ReplaceRequest("old", "new", paths=("a.md",))).plan
    assert "verified" in frontmatter.loads(note.read_text()).metadata
    replacer.replace(
        ReplaceRequest("old", "new", paths=("a.md",), dry_run=False, plan=plan)
    )
    metadata = frontmatter.loads(note.read_text()).metadata
    assert "verified" not in metadata
    assert "generated" in metadata


def test_deleted_selected_folder_is_stale(tmp_path: Path) -> None:
    from dataclasses import replace

    from markdown_vault_mcp.exceptions import ConcurrentModificationError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    folder = tmp_path / "notes"
    folder.mkdir()
    note = folder / "a.md"
    note.write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("old", "new", folders=("notes",))
    plan = replacer.replace(request).plan
    note.unlink()
    folder.rmdir()
    with pytest.raises(ConcurrentModificationError):
        replacer.replace(replace(request, dry_run=False, plan=plan))


def test_files_growing_after_stat_still_respect_total_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers import bulk_replace
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text("aaaaa", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    candidates = manager.replace_candidates

    def grow(
        paths: Sequence[str], folders: Sequence[str], limit: int
    ) -> list[tuple[str, int]]:
        result = candidates(paths, folders, limit)
        (tmp_path / "a.md").write_text("aaaaaa", encoding="utf-8")
        return result

    monkeypatch.setattr(manager, "replace_candidates", grow)
    monkeypatch.setattr(bulk_replace, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(InvalidRequestError, match="limit"):
        BulkReplacer(manager).replace(ReplaceRequest("a", "b", folders=("",)))


def test_output_cap_counts_unchanged_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers import bulk_replace
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("unchanged a unchanged", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", 10)
    with pytest.raises(InvalidRequestError, match="output too large"):
        BulkReplacer(manager).replace(ReplaceRequest("a", "b", folders=("",)))


def test_output_cap_counts_utf8_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers import bulk_replace
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", 3)
    with pytest.raises(InvalidRequestError, match="output too large"):
        BulkReplacer(manager).replace(ReplaceRequest("a", "éé", paths=("a.md",)))


def test_additional_limits_and_invalid_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers import bulk_replace
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("hello a", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    with pytest.raises(InvalidRequestError, match="Pattern or replacement"):
        replacer.replace(ReplaceRequest("x" * 4097, paths=("a.md",)))
    with pytest.raises(InvalidRequestError, match="Invalid replacement"):
        replacer.replace(
            ReplaceRequest("a", r"\g<missing>", paths=("a.md",), regex=True)
        )
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", 10)
    with pytest.raises(InvalidRequestError, match="output too large"):
        replacer.replace(ReplaceRequest("(a)", r"\1" * 6, paths=("a.md",), regex=True))
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", 5)
    with pytest.raises(InvalidRequestError, match="output too large"):
        replacer.replace(ReplaceRequest("absent", "x", paths=("a.md",)))
    monkeypatch.setattr(bulk_replace, "REGEX_TOTAL_SECONDS", 0.0)
    with pytest.raises(InvalidRequestError, match="timed out"):
        replacer.replace(ReplaceRequest("a", "b", paths=("a.md",)))


def test_candidate_limits_and_walk_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers import bulk_replace, document
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("12345", encoding="utf-8")
    (tmp_path / "directory.md").mkdir()
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        max_note_read_bytes=4,
    )
    replacer = BulkReplacer(manager)
    with pytest.raises(InvalidRequestError, match="read limit"):
        replacer.replace(ReplaceRequest("a", paths=("a.md",)))
    with pytest.raises(InvalidRequestError, match="not found"):
        replacer.replace(ReplaceRequest("a", paths=("directory.md",)))
    manager._max_note_read_bytes = 0
    monkeypatch.setattr(bulk_replace, "MAX_NOTES", 0)
    with pytest.raises(InvalidRequestError, match="Too many notes"):
        replacer.replace(ReplaceRequest("a", paths=("a.md",)))

    def failing_walk(
        _root: Path, *, on_error: Callable[[OSError], None]
    ) -> Iterator[Path]:
        on_error(OSError("unreadable"))
        return iter(())

    monkeypatch.setattr(document, "iter_markdown_files", failing_walk)
    with pytest.raises(OSError, match="unreadable"):
        replacer.replace(ReplaceRequest("a", folders=("",)))


def test_small_regex_match_in_large_note_is_allowed(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    note = tmp_path / "a.md"
    note.write_text(
        ("z" * 49 + "\n") * 4000 + "https://old.example/item\n", encoding="utf-8"
    )
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    result = BulkReplacer(manager).replace(
        ReplaceRequest(
            r"https://old\.example/(\w+)",
            r"https://new.example/a/longer/path/to/the/item/\1",
            paths=("a.md",),
            regex=True,
        )
    )
    assert result.notes[0].replacements == 1


def test_regex_timeout_covers_callback() -> None:
    import time

    import regex

    def slow(_match: object) -> str:
        until = time.process_time() + 0.02
        while time.process_time() < until:
            pass
        return "x"

    with pytest.raises(TimeoutError):
        regex.sub("a", slow, "aaaa", timeout=0.001)


def test_excluded_and_hidden_aliases_do_not_select_outside(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "scope" / ".hidden").mkdir(parents=True)
    (tmp_path / "outside.md").write_text("old", encoding="utf-8")
    (tmp_path / "scope" / ".hidden" / "alias.md").symlink_to(tmp_path / "outside.md")
    (tmp_path / "scope" / "excluded.md").symlink_to(tmp_path / "outside.md")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        exclude_patterns=["scope/excluded.md"],
    )
    result = BulkReplacer(manager).replace(
        ReplaceRequest("old", "new", folders=("scope",))
    )
    assert result.notes == []


def test_reserved_explicit_note_with_maintenance_refused(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("old", encoding="utf-8")
    (tmp_path / "index.md").write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    with pytest.raises(InvalidRequestError, match="reserved"):
        BulkReplacer(manager).replace(
            ReplaceRequest("old", "new", paths=("a.md", "index.md")),
            maintain=lambda _: None,
        )


def test_callback_failure_reports_and_maintains_mutated_note(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.bulk_replace import (
        BulkReplacer,
        ReplaceInterruptedError,
        ReplaceRequest,
    )

    note = tmp_path / "a.md"
    note.write_text("old", encoding="utf-8")

    def fail(_paths: object) -> None:
        raise OSError("index failed")

    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        mark_paths_dirty=fail,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("old", "new", paths=("a.md",))
    preview = replacer.replace(request)
    from dataclasses import replace

    maintained: list[str] = []
    with pytest.raises(ReplaceInterruptedError, match=r"may.*a\.md"):
        replacer.replace(
            replace(request, dry_run=False, plan=preview.plan),
            maintain=maintained.append,
        )
    assert note.read_text() == "new"
    assert maintained == ["a.md"]


def test_concurrent_write_conflict_does_not_claim_or_maintain_external_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from markdown_vault_mcp.exceptions import ConcurrentModificationError
    from markdown_vault_mcp.managers.bulk_replace import (
        BulkReplacer,
        ReplaceInterruptedError,
        ReplaceRequest,
    )

    note = tmp_path / "a.md"
    note.write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("old", "new", paths=("a.md",))
    preview = replacer.replace(request)

    def conflict(*_args: object, **_kwargs: object) -> None:
        note.write_text("external", encoding="utf-8")
        raise ConcurrentModificationError("a.md", "old", "external")

    monkeypatch.setattr(manager, "write", conflict)
    maintained: list[str] = []
    with pytest.raises(ReplaceInterruptedError, match=r"Written: \[\]"):
        replacer.replace(
            replace(request, dry_run=False, plan=preview.plan),
            maintain=maintained.append,
        )
    assert maintained == []
    assert note.read_text() == "external"


def test_literal_crlf_matches_raw_note(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    note = tmp_path / "a.md"
    note.write_bytes(b"old\r\nnext\r\n")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    result = BulkReplacer(manager).replace(
        ReplaceRequest("old\r\n", "new\n", paths=("a.md",))
    )
    assert result.replacements == 1


def test_regex_large_match_short_literal(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("a" * 30000, encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    assert (
        BulkReplacer(manager)
        .replace(ReplaceRequest("a+", "x" * 100, paths=("a.md",), regex=True))
        .replacements
        == 1
    )


def test_plan_binds_computed_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    import regex

    from markdown_vault_mcp.exceptions import ConcurrentModificationError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("a b", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("a", "x", paths=("a.md",))
    preview = replacer.replace(request)
    monkeypatch.setattr(replacer, "_compile", lambda _: regex.compile("b"))
    with pytest.raises(ConcurrentModificationError):
        replacer.replace(replace(request, dry_run=False, plan=preview.plan))


@pytest.mark.parametrize(
    ("pattern", "replacement"),
    [
        ("(" * 1000 + "a" + ")" * 1000, "x"),
        ("a", r"\U00110000"),
        ("z", r"\g<missing>"),
        ("z", "\\"),
        ("\ud800", "x"),
        ("a", "\ud800"),
    ],
)
def test_invalid_regex_and_text_are_request_errors(
    tmp_path: Path, pattern: str, replacement: str
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    with pytest.raises(InvalidRequestError):
        BulkReplacer(manager).replace(
            ReplaceRequest(pattern, replacement, paths=("a.md",), regex=True)
        )


def test_folder_scope_under_symlinked_vault_root(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    root = tmp_path / "real"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.md").write_text("old", encoding="utf-8")
    alias = tmp_path / "vault"
    alias.symlink_to(root, target_is_directory=True)
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        alias,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    assert (
        BulkReplacer(manager)
        .replace(ReplaceRequest("old", "new", folders=("sub",)))
        .replacements
        == 1
    )


def test_preview_refuses_invalid_okf_frontmatter(tmp_path: Path) -> None:
    import frontmatter

    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("---\nname: valid\n---\nbody\n", encoding="utf-8")

    def enrich(text: str, _operation: object) -> str:
        frontmatter.loads(text)
        return text

    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        okf_write_enrich=enrich,
    )
    with pytest.raises(InvalidRequestError, match="frontmatter"):
        BulkReplacer(manager).replace(
            ReplaceRequest("name: valid", "name: [", paths=("a.md",))
        )


def test_replace_preserves_bom_and_crlf(tmp_path: Path) -> None:
    from dataclasses import replace

    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    note = tmp_path / "a.md"
    note.write_bytes(b"\xef\xbb\xbfone\r\nold\r\nthree\r\n")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("old", "new", paths=("a.md",))
    preview = replacer.replace(request)
    replacer.replace(replace(request, dry_run=False, plan=preview.plan))
    assert note.read_bytes() == b"\xef\xbb\xbfone\r\nnew\r\nthree\r\n"


def test_explicit_excluded_alias_refused_and_reserved_alias_skipped(
    tmp_path: Path,
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "target.md").write_text("old", encoding="utf-8")
    (tmp_path / "excluded.md").symlink_to(tmp_path / "target.md")
    (tmp_path / "index.md").symlink_to(tmp_path / "target.md")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        exclude_patterns=["excluded.md"],
    )
    replacer = BulkReplacer(manager)
    with pytest.raises(InvalidRequestError):
        replacer.replace(ReplaceRequest("old", "new", paths=("excluded.md",)))
    result = replacer.replace(ReplaceRequest("old", "new", folders=("",)))
    assert [note.path for note in result.notes] == ["target.md"]


def test_unmatched_edits_do_not_stale_but_new_match_does(tmp_path: Path) -> None:
    from dataclasses import replace

    from markdown_vault_mcp.exceptions import ConcurrentModificationError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("old", encoding="utf-8")
    other = tmp_path / "b.md"
    other.write_text("unrelated", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("old", "new", folders=("",))
    preview = replacer.replace(request)
    other.write_text("still unrelated", encoding="utf-8")
    # No write: the same plan is available after unrelated changes.
    assert replacer.replace(request).plan == preview.plan
    other.write_text("old appeared", encoding="utf-8")
    with pytest.raises(ConcurrentModificationError):
        replacer.replace(replace(request, dry_run=False, plan=preview.plan))


def test_replacement_references_only_small_capture_of_large_match(
    tmp_path: Path,
) -> None:
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "a.md").write_text("z" * 30000 + "a", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    result = BulkReplacer(manager).replace(
        ReplaceRequest(r"z+(?P<n>a)", r"\g<n>" * 100, paths=("a.md",), regex=True)
    )
    assert result.notes[0].replacements == 1


def test_absent_path_with_maintenance_is_stale(tmp_path: Path) -> None:
    from dataclasses import replace

    from markdown_vault_mcp.exceptions import ConcurrentModificationError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    note = tmp_path / "a.md"
    note.write_text("old", encoding="utf-8")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("old", "new", paths=("a.md",))
    preview = replacer.replace(request, maintain=lambda _: None)
    note.unlink()
    with pytest.raises(ConcurrentModificationError):
        replacer.replace(
            replace(request, dry_run=False, plan=preview.plan), maintain=lambda _: None
        )


def test_reserved_alias_refused_with_maintenance(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest

    (tmp_path / "target.md").write_text("old", encoding="utf-8")
    (tmp_path / "index.md").symlink_to(tmp_path / "target.md")
    manager = DocumentManager(
        FTSIndex(db_path=":memory:"),
        tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )
    with pytest.raises(InvalidRequestError, match="reserved"):
        BulkReplacer(manager).replace(
            ReplaceRequest("old", "new", paths=("index.md",)), maintain=lambda _: None
        )
