"""Scoped text replacement previews and application."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    InvalidRequestError,
    ReadOnlyError,
)
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers import bulk_replace, document
from markdown_vault_mcp.managers.bulk_replace import (
    BulkReplacer,
    ReplaceInterruptedError,
    ReplaceRequest,
)
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.scanner import HeadingChunker


@pytest.fixture
def manager_factory() -> Iterator[Callable[..., DocumentManager]]:
    indexes: list[FTSIndex] = []

    def make(root: Path, **kwargs: Any) -> DocumentManager:
        index = FTSIndex(db_path=":memory:")
        indexes.append(index)
        kwargs.setdefault("read_only", False)
        return DocumentManager(
            index,
            root,
            write_lock=threading.RLock(),
            chunk_strategy=HeadingChunker(),
            **kwargs,
        )

    yield make
    for index in indexes:
        index.close()


def test_preview_then_apply_literal(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager]
) -> None:

    (tmp_path / "a.md").write_text("old old\n", encoding="utf-8")
    manager = manager_factory(tmp_path)
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


def test_large_reviewed_apply_does_not_regenerate_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    from dataclasses import replace

    for number in range(300):
        (tmp_path / f"{number:03}.md").write_text("old\n", encoding="utf-8")
    replacer = BulkReplacer(manager_factory(tmp_path))
    request = ReplaceRequest("old", "new", folders=("",))
    preview = replacer.replace(request)
    assert len(preview.notes) == 300
    monkeypatch.setattr(bulk_replace, "MAX_DIFF_CHARS", 1)
    result = replacer.replace(replace(request, dry_run=False, plan=preview.plan))
    assert len(result.notes) == 300
    assert all(note.diff is None for note in result.notes)
    assert all(path.read_text() == "new\n" for path in tmp_path.glob("*.md"))


@pytest.mark.parametrize(
    "scenario", ["new_match", "deleted_note", "deleted_folder", "unrelated_edit"]
)
def test_plan_staleness(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager], scenario: str
) -> None:
    from dataclasses import replace

    folder = tmp_path / "notes"
    folder.mkdir()
    note = folder / "a.md"
    note.write_text("Ab 123\n", encoding="utf-8")
    other = folder / "other.md"
    other.write_text("unrelated", encoding="utf-8")
    replacer = BulkReplacer(manager_factory(tmp_path))
    selector = (
        {"paths": ("notes/a.md",)}
        if scenario == "deleted_note"
        else {"folders": ("notes",)}
    )
    request = ReplaceRequest(
        r"(?i)(ab) (?P<num>\d+)", r"\g<num>-\1", regex=True, **selector
    )
    preview = replacer.replace(request)
    assert "+123-Ab" in preview.notes[0].diff
    if scenario == "unrelated_edit":
        other.write_text("still unrelated", encoding="utf-8")
        replacer.replace(replace(request, dry_run=False, plan=preview.plan))
        assert note.read_text() == "123-Ab\n"
        return
    if scenario == "new_match":
        (folder / "new.md").write_text("AB 234", encoding="utf-8")
    elif scenario == "deleted_note":
        note.unlink()
    else:
        note.unlink()
        other.unlink()
        folder.rmdir()
    with pytest.raises(ConcurrentModificationError):
        replacer.replace(replace(request, dry_run=False, plan=preview.plan))


def test_limits_and_literal_backslash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    from markdown_vault_mcp.managers._replace_worker import propose

    (tmp_path / "a.md").write_text("aaa", encoding="utf-8")
    manager = manager_factory(tmp_path)
    replacer = BulkReplacer(manager)
    preview = replacer.replace(ReplaceRequest("a", r"\1", paths=("a.md",)))
    assert r"\1\1\1" in preview.notes[0].diff
    monkeypatch.setattr(bulk_replace, "MAX_MATCHES", 2)
    with pytest.raises(InvalidRequestError, match="Too many matches"):
        replacer.replace(ReplaceRequest("a", folders=("",)))
    with pytest.raises(ValueError, match="Too many matches"):
        propose(
            {
                "pattern": "a",
                "replacement": "x",
                "regex": False,
                "snapshots": [("a.md", "aaa", "etag")],
                "max_matches": 2,
                "max_output": 100,
                "max_diff": 1000,
                "want_diff": True,
            }
        )


def test_folder_selection_skips_reserved_hidden_and_excluded(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager]
) -> None:

    (tmp_path / ".hidden").mkdir()
    for name in ("a.md", "index.md", "log.md", ".hidden/b.md", "excluded.md"):
        (tmp_path / name).write_text("old", encoding="utf-8")
    manager = manager_factory(
        tmp_path,
        exclude_patterns=["excluded.md"],
    )
    result = BulkReplacer(manager).replace(
        ReplaceRequest("old", "new", folders=("",), paths=("index.md", "a.md"))
    )
    assert [note.path for note in result.notes] == ["a.md", "index.md", "log.md"]
    assert result.scanned == 3


@pytest.mark.parametrize("failure", ["before", "after", "after_unreadable", "conflict"])
def test_interrupted_write_maintenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
    failure: str,
) -> None:
    from dataclasses import replace

    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text("old", encoding="utf-8")

    def fail(_paths: object) -> None:
        if failure == "after_unreadable":

            def unreadable(*_args: object, **_kwargs: object) -> None:
                raise OSError("unreadable")

            monkeypatch.setattr(manager, "read_for_replace", unreadable)
        raise OSError("index failed")

    manager = manager_factory(
        tmp_path, **({"mark_paths_dirty": fail} if failure.startswith("after") else {})
    )
    replacer = BulkReplacer(manager)
    request = ReplaceRequest("old", "new", folders=("",))
    preview = replacer.replace(request)
    real_write = manager.write
    if failure == "before":

        def fail_second(path: str, content: str, **kwargs: object) -> object:
            if path == "b.md":
                raise OSError("disk full")
            return real_write(path, content, **kwargs)

        monkeypatch.setattr(manager, "write", fail_second)
    elif failure == "conflict":

        def conflict(*_args: object, **_kwargs: object) -> None:
            (tmp_path / "a.md").write_text("external", encoding="utf-8")
            raise ConcurrentModificationError("a.md", "old", "external")

        monkeypatch.setattr(manager, "write", conflict)
    maintained: list[str] = []
    with pytest.raises(ReplaceInterruptedError) as error:
        replacer.replace(
            replace(request, dry_run=False, plan=preview.plan),
            maintain=maintained.append,
        )
    assert "may already be written: " in str(error.value)
    assert maintained == ([] if failure == "conflict" else ["a.md"])
    assert (tmp_path / "a.md").read_text() == (
        "external" if failure == "conflict" else "new"
    )
    assert (tmp_path / "b.md").read_text() == "old"
    expected = {"before": "Written: ['a.md']"}.get(failure, "Written: []")
    assert expected in str(error.value)


def test_preflight_refusals_and_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    from markdown_vault_mcp.managers._replace_worker import propose

    note = tmp_path / "a.md"
    note.write_text("old", encoding="utf-8")
    manager = manager_factory(tmp_path)
    replacer = BulkReplacer(manager)
    assert replacer.replace(ReplaceRequest("old", "old", folders=("",))).notes == []
    monkeypatch.setattr(bulk_replace, "MAX_DIFF_CHARS", 1)
    with pytest.raises(InvalidRequestError, match="Diff too large"):
        replacer.replace(ReplaceRequest("old", "new", folders=("",)))
    with pytest.raises(ValueError, match="Diff too large"):
        propose(
            {
                "pattern": "old",
                "replacement": "new",
                "regex": False,
                "snapshots": [("a.md", "old", "etag")],
                "max_matches": 100,
                "max_output": 100,
                "max_diff": 1,
                "want_diff": True,
            }
        )
    note.write_bytes(b"\xff")
    with pytest.raises(InvalidRequestError, match="UTF-8"):
        replacer.replace(ReplaceRequest("old", "new", folders=("",)))


@pytest.mark.parametrize(
    ("pattern", "text", "must_refuse"),
    [
        ("(?:(?:(?:a{1000}){1000}){1000}){1000}", "a", False),
        ("(a+)+$", "a" * 5000 + "b", True),
    ],
)
def test_hostile_regex_runs_in_bounded_process(
    tmp_path: Path, pattern: str, text: str, must_refuse: bool
) -> None:
    import json
    import os
    import signal
    import subprocess
    import sys

    note = tmp_path / "a.md"
    note.write_text(text, encoding="utf-8")
    probe = """
import json, os, sys, threading
from pathlib import Path
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.bulk_replace import BulkReplacer, ReplaceRequest
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.scanner import HeadingChunker
root, pattern = sys.argv[1:]
index = FTSIndex(db_path=":memory:")
manager = DocumentManager(index, Path(root), write_lock=threading.RLock(), chunk_strategy=HeadingChunker(), read_only=False)
try:
    BulkReplacer(manager).replace(ReplaceRequest(pattern, "x", paths=("a.md",), regex=True))
    result = "completed"
except InvalidRequestError:
    result = "refused"
finally:
    index.close()
try:
    os.waitpid(-1, os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise AssertionError("worker child was not reaped")
print(json.dumps(result))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", probe, str(tmp_path), pattern],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail("regex compile or match exceeded the process deadline")
    assert process.returncode == 0, stderr
    result = json.loads(stdout)
    assert result in ({"refused"} if must_refuse else {"completed", "refused"})
    assert note.read_text(encoding="utf-8") == text


def test_diff_shows_both_lines_when_note_has_no_final_newline(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager]
) -> None:

    (tmp_path / "a.md").write_text("old", encoding="utf-8")
    manager = manager_factory(tmp_path)
    diff = (
        BulkReplacer(manager)
        .replace(ReplaceRequest("old", "new", paths=("a.md",)))
        .notes[0]
        .diff
    )
    assert "-old\n" in diff
    assert "+new\n" in diff


def test_read_only_and_invalid_selectors(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager]
) -> None:

    (tmp_path / "a.md").write_text("old", encoding="utf-8")
    manager = manager_factory(
        tmp_path,
        read_only=True,
        exclude_patterns=["hidden.md"],
    )
    replacer = BulkReplacer(manager)
    plan = replacer.replace(ReplaceRequest("old", "new", paths=("a.md",))).plan
    with pytest.raises(ReadOnlyError):
        replacer.replace(
            ReplaceRequest("old", "new", paths=("a.md",), dry_run=False, plan=plan)
        )
    for path in ("missing.md", "image.png", "../escape.md"):
        with pytest.raises(InvalidRequestError):
            replacer.replace(ReplaceRequest("old", paths=(path,)))


def test_folder_skips_submodule_but_explicit_refuses(
    tmp_path: Path,
    manager_factory: Callable[..., DocumentManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    from pathlib import Path

    from markdown_vault_mcp.git.submodules import SubmoduleGuard
    from tests.fixtures.git import make_superproject

    root = make_superproject(tmp_path)
    (root / "top.md").write_text("old", encoding="utf-8")
    (root / "sub" / "note.md").write_text("old", encoding="utf-8")
    manager = manager_factory(
        root,
        submodule_guard=SubmoduleGuard(root),
    )
    replacer = BulkReplacer(manager)
    result = replacer.replace(ReplaceRequest("old", "new", folders=("",)))
    assert [note.path for note in result.notes] == ["top.md"]
    with pytest.raises(InvalidRequestError, match="submodules"):
        replacer.replace(ReplaceRequest("old", "new", paths=("sub/note.md",)))
    with pytest.raises(InvalidRequestError, match="sub"):
        replacer.replace(ReplaceRequest("old", "new", folders=("sub",)))
    scandir = os.scandir

    def deny_submodule(path: Any) -> Any:
        if Path(path) == root / "sub":
            raise PermissionError(13, "unreadable", str(path))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", deny_submodule)
    assert [
        note.path
        for note in replacer.replace(ReplaceRequest("old", "new", folders=("",))).notes
    ] == ["top.md"]


@pytest.mark.parametrize("invalid", [False, True])
def test_okf_enricher_preview_and_apply(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager], invalid: bool
) -> None:
    from datetime import UTC, datetime

    import frontmatter

    from markdown_vault_mcp.okf import apply_okf_write_stamp

    note = tmp_path / "a.md"
    note.write_text(
        "---\nname: valid\nverified:\n  - by: human:a\n    at: 2026-01-01\n---\nold\n",
        encoding="utf-8",
    )

    def enrich(text: str, _operation: object) -> str:
        return apply_okf_write_stamp(
            text, actor="tool:test", now=datetime(2026, 1, 2, tzinfo=UTC)
        )

    replacer = BulkReplacer(manager_factory(tmp_path, okf_write_enrich=enrich))
    if invalid:
        with pytest.raises(InvalidRequestError, match="frontmatter"):
            replacer.replace(ReplaceRequest("name: valid", "name: [", paths=("a.md",)))
        assert note.read_text().startswith("---\nname: valid")
        return
    plan = replacer.replace(ReplaceRequest("old", "new", paths=("a.md",))).plan
    assert "verified" in frontmatter.loads(note.read_text()).metadata
    replacer.replace(
        ReplaceRequest("old", "new", paths=("a.md",), dry_run=False, plan=plan)
    )
    metadata = frontmatter.loads(note.read_text()).metadata
    assert "verified" not in metadata
    assert "generated" in metadata


def test_files_growing_after_stat_still_respect_total_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:

    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text("aaaaa", encoding="utf-8")
    manager = manager_factory(tmp_path)
    candidates = manager.replace_candidates

    def grow(
        paths: Sequence[str], folders: Sequence[str], limit: int, **kwargs: Any
    ) -> list[str]:
        result = candidates(paths, folders, limit, **kwargs)
        (tmp_path / "a.md").write_text("aaaaaa", encoding="utf-8")
        return result

    monkeypatch.setattr(manager, "replace_candidates", grow)
    monkeypatch.setattr(bulk_replace, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(InvalidRequestError, match="budget"):
        BulkReplacer(manager).replace(ReplaceRequest("a", "b", folders=("",)))


@pytest.mark.parametrize(
    "case",
    [
        ("a" * 20, "a", "bb", 5),
        ("unchanged a unchanged", "a", "b", 10),
        ("a", "a", "éé", 3),
        ("unchanged", "absent", "x", 5),
    ],
)
def test_output_size_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
    case: tuple[str, str, str, int],
) -> None:
    from markdown_vault_mcp.managers._replace_worker import propose

    text, pattern, replacement, cap = case
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", cap)
    with pytest.raises(InvalidRequestError, match="output too large"):
        BulkReplacer(manager_factory(tmp_path)).replace(
            ReplaceRequest(pattern, replacement, paths=("a.md",))
        )
    with pytest.raises(ValueError, match="output too large"):
        propose(
            {
                "pattern": pattern,
                "replacement": replacement,
                "regex": False,
                "snapshots": [("a.md", text, "etag")],
                "max_matches": 100,
                "max_output": cap,
                "max_diff": 1000,
                "want_diff": True,
            }
        )


def test_additional_limits_and_invalid_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:

    manager = manager_factory(tmp_path)
    replacer = BulkReplacer(manager)
    (tmp_path / "a.md").write_text("a" * 1000, encoding="utf-8")
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", 1000)
    with pytest.raises(InvalidRequestError, match="output too large"):
        replacer.replace(ReplaceRequest("(a+)", r"\1" * 3, paths=("a.md",), regex=True))


def test_candidate_limits_and_walk_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    import os

    (tmp_path / "a.md").write_text("12345", encoding="utf-8")
    (tmp_path / "directory.md").mkdir()
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "pipe.md")
    manager = manager_factory(
        tmp_path,
        max_note_read_bytes=4,
    )
    replacer = BulkReplacer(manager)
    with pytest.raises(InvalidRequestError, match="limit"):
        replacer.replace(ReplaceRequest("a", paths=("a.md",)))
    with pytest.raises(InvalidRequestError, match="not found"):
        replacer.replace(ReplaceRequest("a", paths=("directory.md",)))
    if hasattr(os, "mkfifo"):
        with pytest.raises(InvalidRequestError, match="not found"):
            replacer.replace(ReplaceRequest("a", paths=("pipe.md",)))
    manager._max_note_read_bytes = 0
    monkeypatch.setattr(bulk_replace, "MAX_NOTE_BYTES", 4)
    with pytest.raises(InvalidRequestError, match="limit"):
        replacer.replace(ReplaceRequest("a", paths=("a.md",)))
    monkeypatch.setattr(bulk_replace, "MAX_NOTES", 0)
    with pytest.raises(InvalidRequestError, match="Too many notes"):
        replacer.replace(ReplaceRequest("a", paths=("a.md",)))

    def failing_walk(
        _root: Path,
        _exclude: object,
        *,
        on_error: Callable[[OSError], None],
        **_kwargs: Any,
    ) -> Iterator[Path]:
        on_error(OSError("unreadable"))
        return iter(())

    monkeypatch.setattr(document, "iter_markdown_files", failing_walk)
    with pytest.raises(OSError, match="unreadable"):
        replacer.replace(ReplaceRequest("a", folders=("",)))


@pytest.mark.parametrize(
    ("bad_folder", "refuse"),
    [
        ("selected/blocked", True),
        ("selected/.hidden", False),
        ("selected/excluded", False),
        ("other/blocked", False),
    ],
)
def test_folder_walk_skips_ineligible_unreadable_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
    bad_folder: str,
    refuse: bool,
) -> None:
    import os
    from pathlib import Path

    (tmp_path / "selected").mkdir()
    (tmp_path / "selected" / "a.md").write_text("old", encoding="utf-8")
    blocked = tmp_path / bad_folder
    blocked.mkdir(parents=True)
    scandir = os.scandir
    visited: list[Path] = []

    def fail_scandir(path: Any) -> Any:
        visited.append(Path(path))
        if Path(path) == blocked:
            raise PermissionError(13, "unreadable", str(path))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", fail_scandir)
    manager = manager_factory(tmp_path, exclude_patterns=["selected/excluded/**"])
    request = ReplaceRequest("old", "new", folders=("selected",))
    if refuse:
        with pytest.raises(PermissionError):
            BulkReplacer(manager).replace(request)
    else:
        result = BulkReplacer(manager).replace(request)
        assert [note.path for note in result.notes] == ["selected/a.md"]
    if "excluded" in bad_folder or ".hidden" in bad_folder or "other/" in bad_folder:
        assert blocked not in visited


@pytest.mark.parametrize(
    ("text", "pattern", "replacement"),
    [
        (
            ("z" * 49 + "\n") * 4000 + "https://old.example/item\n",
            r"https://old\.example/(\w+)",
            r"https://new.example/a/longer/path/to/the/item/\1",
        ),
        ("a" * 30000, "a+", "x" * 100),
        ("z" * 30000 + "a", r"z+(?P<n>a)", r"\g<n>" * 100),
    ],
)
def test_small_regex_replacement_not_overestimated(
    tmp_path: Path,
    manager_factory: Callable[..., DocumentManager],
    text: str,
    pattern: str,
    replacement: str,
) -> None:
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    result = BulkReplacer(manager_factory(tmp_path)).replace(
        ReplaceRequest(pattern, replacement, paths=("a.md",), regex=True)
    )
    assert result.notes[0].replacements == 1


@pytest.mark.parametrize(
    "replacement", [r"\1", r"\12", r"\012", r"\g<name>", r"\\", "\\\n"]
)
def test_regex_replacement_fragments_match_stdlib(
    tmp_path: Path,
    manager_factory: Callable[..., DocumentManager],
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    import re
    from dataclasses import replace

    from markdown_vault_mcp.managers._replace_worker import propose

    pattern = r"(?P<name>a)(b)(c)(d)(e)(f)(g)(h)(i)(j)(k)(l)"
    text = "abcdefghijkl"
    expected = re.sub(pattern, replacement, text)
    proposals = propose(
        {
            "pattern": pattern,
            "replacement": replacement,
            "regex": True,
            "snapshots": [("a.md", text, "etag")],
            "max_matches": 100,
            "max_output": len(expected.encode()),
            "max_diff": 1000,
            "want_diff": True,
        }
    )
    assert proposals[0][1] == expected
    note = tmp_path / "a.md"
    note.write_text(text, encoding="utf-8")
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", len(expected.encode()))
    replacer = BulkReplacer(manager_factory(tmp_path))
    request = ReplaceRequest(pattern, replacement, paths=("a.md",), regex=True)
    preview = replacer.replace(request)
    replacer.replace(replace(request, dry_run=False, plan=preview.plan))
    assert note.read_text(encoding="utf-8") == expected


def test_excluded_and_hidden_aliases_do_not_select_outside(
    tmp_path: Path,
    manager_factory: Callable[..., DocumentManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    (tmp_path / "scope" / ".hidden").mkdir(parents=True)
    (tmp_path / "outside.md").write_text("old", encoding="utf-8")
    external = tmp_path.parent / f"{tmp_path.name}-outside.md"
    external.write_text("old", encoding="utf-8")
    (tmp_path / "scope" / ".hidden" / "alias.md").symlink_to(tmp_path / "outside.md")
    (tmp_path / "scope" / "excluded.md").symlink_to(tmp_path / "outside.md")
    (tmp_path / "scope" / "in-vault.md").symlink_to(tmp_path / "outside.md")
    (tmp_path / "scope" / "escape.md").symlink_to(external)
    (tmp_path / "scope" / "protected.md").write_text("old", encoding="utf-8")
    (tmp_path / "scope" / "protected-alias.md").symlink_to(
        tmp_path / "scope" / "protected.md"
    )
    (tmp_path / "scope" / ".hidden" / "target.md").write_text("old", encoding="utf-8")
    (tmp_path / "scope" / "hidden-target.md").symlink_to(
        tmp_path / "scope" / ".hidden" / "target.md"
    )
    manager = manager_factory(
        tmp_path,
        exclude_patterns=["scope/excluded.md", "scope/protected.md"],
    )
    result = BulkReplacer(manager).replace(
        ReplaceRequest("old", "new", folders=("scope",))
    )
    assert result.notes == []
    with pytest.raises(InvalidRequestError):
        BulkReplacer(manager).replace(
            ReplaceRequest("old", "new", paths=("scope/excluded.md",))
        )
    eligible = tmp_path / "scope" / "eligible.md"
    eligible.write_text("old", encoding="utf-8")
    hidden_alias = tmp_path / "scope" / ".hidden" / "eligible-alias.md"
    hidden_alias.symlink_to(eligible)
    monkeypatch.setattr(
        document, "iter_markdown_files", lambda *_args, **_kwargs: iter([hidden_alias])
    )
    assert (
        BulkReplacer(manager)
        .replace(ReplaceRequest("old", "new", folders=("scope",)))
        .notes
        == []
    )


@pytest.mark.parametrize("literal_crlf", [True, False])
def test_replace_preserves_line_endings(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager], literal_crlf: bool
) -> None:
    from dataclasses import replace

    note = tmp_path / "a.md"
    note.write_bytes(b"\xef\xbb\xbfone\r\nold\r\nthree\r\n")
    replacer = BulkReplacer(manager_factory(tmp_path))
    request = ReplaceRequest(
        "old\r\n" if literal_crlf else "old",
        "new\r\n" if literal_crlf else "new",
        paths=("a.md",),
    )
    preview = replacer.replace(request)
    assert preview.replacements == 1
    for pattern in (r"^one", r"(?m)old$", "one\nold"):
        assert (
            replacer.replace(
                ReplaceRequest(pattern, "x", paths=("a.md",), regex=True)
            ).replacements
            == 0
        )
    assert (
        replacer.replace(
            ReplaceRequest("\ufeffone\r\nold", "x", paths=("a.md",))
        ).replacements
        == 1
    )
    replacer.replace(replace(request, dry_run=False, plan=preview.plan))
    assert note.read_bytes() == b"\xef\xbb\xbfone\r\nnew\r\nthree\r\n"


@pytest.mark.parametrize(
    "replace_request",
    [
        ReplaceRequest("", folders=("",)),
        ReplaceRequest("x"),
        ReplaceRequest("(", folders=("",), regex=True),
        ReplaceRequest("a", paths=("a.md",), dry_run=False),
        ReplaceRequest("x" * 4097, paths=("a.md",)),
        *(
            ReplaceRequest(pattern, replacement, paths=("a.md",), regex=True)
            for pattern, replacement in [
                ("(" * 1000 + "a" + ")" * 1000, "x"),
                ("a", r"\U00110000"),
                ("z", r"\g<missing>"),
                ("z", "\\"),
                ("\ud800", "x"),
                ("a", "\ud800"),
            ]
        ),
    ],
)
def test_invalid_regex_and_text_are_request_errors(
    tmp_path: Path,
    replace_request: ReplaceRequest,
    manager_factory: Callable[..., DocumentManager],
) -> None:

    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    manager = manager_factory(tmp_path)
    with pytest.raises(InvalidRequestError):
        BulkReplacer(manager).replace(replace_request)


def test_folder_scope_under_symlinked_vault_root(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager]
) -> None:

    root = tmp_path / "real"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.md").write_text("old", encoding="utf-8")
    alias = tmp_path / "vault"
    alias.symlink_to(root, target_is_directory=True)
    manager = manager_factory(alias)
    assert (
        BulkReplacer(manager)
        .replace(ReplaceRequest("old", "new", folders=("sub",)))
        .replacements
        == 1
    )


def test_reserved_alias_refused_with_maintenance(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager]
) -> None:

    (tmp_path / "target.md").write_text("old", encoding="utf-8")
    (tmp_path / "index.md").symlink_to(tmp_path / "target.md")
    (tmp_path / "log.md").write_text("old", encoding="utf-8")
    manager = manager_factory(tmp_path)
    with pytest.raises(InvalidRequestError, match="reserved"):
        BulkReplacer(manager).replace(
            ReplaceRequest("old", "new", paths=("index.md",)), maintain=lambda _: None
        )
    with pytest.raises(InvalidRequestError, match="reserved"):
        BulkReplacer(manager).replace(
            ReplaceRequest("old", "new", paths=("log.md",)), maintain=lambda _: None
        )
    (tmp_path / "ordinary.md").symlink_to(tmp_path / "log.md")
    with pytest.raises(InvalidRequestError, match="reserved"):
        BulkReplacer(manager).replace(
            ReplaceRequest("old", "new", paths=("ordinary.md",)),
            maintain=lambda _: None,
        )


@pytest.mark.parametrize("alias_kind", ["case", "hardlink", "symlink"])
def test_selection_deduplicates_physical_aliases(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager], alias_kind: str
) -> None:
    import os
    from dataclasses import replace

    note = tmp_path / "a.md"
    note.write_text("old", encoding="utf-8")
    alias = tmp_path / ("A.md" if alias_kind == "case" else "alias.md")
    if alias_kind == "case":
        if not alias.exists():
            pytest.skip("filesystem is case-sensitive")
    elif alias_kind == "hardlink":
        os.link(note, alias)
    else:
        alias.symlink_to(note)
    replacer = BulkReplacer(manager_factory(tmp_path))
    request = ReplaceRequest("old", "new", paths=("a.md", alias.name))
    if alias_kind == "hardlink":
        with pytest.raises(InvalidRequestError, match="hardlink aliases"):
            replacer.replace(request)
        assert note.read_text() == alias.read_text() == "old"
        return
    preview = replacer.replace(request)
    assert preview.scanned == 1
    assert len(preview.notes) == 1
    replacer.replace(replace(request, dry_run=False, plan=preview.plan))
    assert note.read_text() == "new"


def test_windows_crlf_write_preserves_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    from dataclasses import replace

    from markdown_vault_mcp.managers import _write_kernel

    note = tmp_path / "a.md"
    note.write_bytes(b"old\r\nsecond\r\n")
    replacer = BulkReplacer(manager_factory(tmp_path))
    request = ReplaceRequest("old", "new", paths=("a.md",))
    preview = replacer.replace(request)
    original = _write_kernel.tempfile.NamedTemporaryFile

    def windows_newlines(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("mode") == "w":
            kwargs["newline"] = "\r\n"
        return original(*args, **kwargs)

    monkeypatch.setattr(_write_kernel.tempfile, "NamedTemporaryFile", windows_newlines)
    replacer.replace(replace(request, dry_run=False, plan=preview.plan))
    assert note.read_bytes() == b"new\r\nsecond\r\n"


def test_folder_selection_does_not_walk_unrelated_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    import os
    from pathlib import Path

    for folder in ("scope", "unrelated"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "a.md").write_text("old", encoding="utf-8")
    visited: list[Path] = []
    original = os.scandir

    def record(path: Any) -> Any:
        visited.append(Path(path))
        return original(path)

    monkeypatch.setattr(os, "scandir", record)
    result = BulkReplacer(manager_factory(tmp_path)).replace(
        ReplaceRequest("old", "new", folders=("scope",))
    )
    assert [note.path for note in result.notes] == ["scope/a.md"]
    assert tmp_path / "unrelated" not in visited


def test_nested_scope_respects_vault_anchored_pruning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    import os
    from pathlib import Path

    folder = tmp_path / "selected" / "excluded"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text("old", encoding="utf-8")
    original = os.scandir
    visited: list[Path] = []

    def record(path: Any) -> Any:
        visited.append(Path(path))
        return original(path)

    monkeypatch.setattr(os, "scandir", record)
    replacer = BulkReplacer(
        manager_factory(tmp_path, exclude_patterns=["selected/excluded/**"])
    )
    with pytest.raises(InvalidRequestError, match="selected/excluded"):
        replacer.replace(ReplaceRequest("old", folders=("selected/excluded",)))
    assert folder not in visited


def test_folder_walk_prunes_directory_aliases_outside_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    import os
    from pathlib import Path

    vault = tmp_path / "vault"
    scope = vault / "scope"
    scope.mkdir(parents=True)
    (scope / "a.md").write_text("old", encoding="utf-8")
    for name, target in (("other", vault / "other"), ("outside", tmp_path / "outside")):
        target.mkdir()
        (target / "b.md").write_text("old", encoding="utf-8")
        (scope / name).symlink_to(target, target_is_directory=True)
    visited: list[Path] = []
    original = os.scandir

    def record(path: Any) -> Any:
        visited.append(Path(path))
        return original(path)

    monkeypatch.setattr(os, "scandir", record)
    result = BulkReplacer(manager_factory(vault)).replace(
        ReplaceRequest("old", "new", folders=("scope",))
    )
    assert [note.path for note in result.notes] == ["scope/a.md"]
    assert scope / "other" not in visited
    assert scope / "outside" not in visited


def test_hidden_selected_folder_is_pruned_before_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    import os
    from pathlib import Path

    folder = tmp_path / ".hidden"
    folder.mkdir()
    (folder / "a.md").write_text("old", encoding="utf-8")
    original = os.scandir
    visited: list[Path] = []

    def record(path: Any) -> Any:
        visited.append(Path(path))
        return original(path)

    monkeypatch.setattr(os, "scandir", record)
    with pytest.raises(InvalidRequestError, match=r"\.hidden"):
        BulkReplacer(manager_factory(tmp_path)).replace(
            ReplaceRequest("old", folders=(".hidden",))
        )
    assert folder not in visited


@pytest.mark.parametrize("maintenance", [False, True])
def test_folder_reserved_names_follow_maintenance_policy(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager], maintenance: bool
) -> None:
    from dataclasses import replace

    for name in ("a.md", "index.md", "log.md"):
        (tmp_path / name).write_text("old", encoding="utf-8")
    replacer = BulkReplacer(manager_factory(tmp_path))
    request = ReplaceRequest("old", "new", folders=("",))
    upkeep = (lambda _path: None) if maintenance else None
    preview = replacer.replace(request, maintain=upkeep)
    expected = ["a.md"] if maintenance else ["a.md", "index.md", "log.md"]
    assert [note.path for note in preview.notes] == expected
    replacer.replace(
        replace(request, dry_run=False, plan=preview.plan), maintain=upkeep
    )
    for name in expected:
        assert (tmp_path / name).read_text() == "new"
    if maintenance:
        assert (tmp_path / "index.md").read_text() == "old"
        assert (tmp_path / "log.md").read_text() == "old"


@pytest.mark.parametrize("folder", ["excluded", ".hidden"])
def test_explicit_ineligible_folder_refuses(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager], folder: str
) -> None:
    (tmp_path / folder).mkdir()
    (tmp_path / folder / "note.md").write_text("old", encoding="utf-8")
    replacer = BulkReplacer(manager_factory(tmp_path, exclude_patterns=["excluded/**"]))
    with pytest.raises(InvalidRequestError, match=folder.replace(".", r"\.")):
        replacer.replace(ReplaceRequest("old", "new", folders=(folder,)))


def test_oversized_note_refusal_identifies_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    (tmp_path / "large.md").write_text("old note", encoding="utf-8")
    monkeypatch.setattr(bulk_replace, "MAX_NOTE_BYTES", 4)
    with pytest.raises(InvalidRequestError, match=r"large\.md"):
        BulkReplacer(manager_factory(tmp_path)).replace(
            ReplaceRequest("old", "new", paths=("large.md",))
        )


def test_output_limit_refusal_does_not_blame_pattern(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    (tmp_path / "a.md").write_text("aaa", encoding="utf-8")
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", 3)
    with pytest.raises(InvalidRequestError) as exc:
        BulkReplacer(manager_factory(tmp_path)).replace(
            ReplaceRequest("a", "bb", paths=("a.md",))
        )
    assert "output too large" in str(exc.value).lower()
    assert "invalid pattern" not in str(exc.value).lower()


def test_partial_file_glob_does_not_prune_folder(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager]
) -> None:
    folder = tmp_path / "scope"
    folder.mkdir()
    for name in ("a.md", "p.md"):
        (folder / name).write_text("old", encoding="utf-8")
    replacer = BulkReplacer(manager_factory(tmp_path, exclude_patterns=["scope/p*.md"]))
    result = replacer.replace(ReplaceRequest("old", "new", folders=("scope",)))
    assert [note.path for note in result.notes] == ["scope/a.md"]


@pytest.mark.parametrize("failure", ["timeout", "crash", "bad_json", "launch"])
def test_worker_failures_distinguish_timeout_and_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
    failure: str,
) -> None:
    import subprocess
    from types import SimpleNamespace

    (tmp_path / "a.md").write_text("old", encoding="utf-8")

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        if failure == "timeout":
            raise subprocess.TimeoutExpired("worker", 3)
        if failure == "crash":
            raise subprocess.CalledProcessError(1, "worker")
        if failure == "launch":
            raise OSError("unavailable")
        return SimpleNamespace(stdout="not json")

    monkeypatch.setattr(bulk_replace.subprocess, "run", fail)
    error = InvalidRequestError if failure == "timeout" else RuntimeError
    with pytest.raises(
        error, match="timed out" if failure == "timeout" else "worker failed"
    ):
        BulkReplacer(manager_factory(tmp_path)).replace(
            ReplaceRequest("old", paths=("a.md",))
        )


def test_aggregate_budget_refusal_names_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text("12345", encoding="utf-8")
    monkeypatch.setattr(bulk_replace, "MAX_TOTAL_BYTES", 7)
    with pytest.raises(InvalidRequestError, match=r"b\.md.*aggregate"):
        BulkReplacer(manager_factory(tmp_path, max_note_read_bytes=0)).replace(
            ReplaceRequest("1", "2", folders=("",))
        )


def test_missing_explicit_folder_names_path(
    tmp_path: Path, manager_factory: Callable[..., DocumentManager]
) -> None:
    with pytest.raises(InvalidRequestError, match="missing"):
        BulkReplacer(manager_factory(tmp_path)).replace(
            ReplaceRequest("old", folders=("missing",))
        )
