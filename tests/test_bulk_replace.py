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
        note.write_text("Ab 123\n", encoding="utf-8")
        preview = replacer.replace(request)
        other.write_text("AB 234", encoding="utf-8")
        with pytest.raises(ConcurrentModificationError):
            replacer.replace(replace(request, dry_run=False, plan=preview.plan))
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

    (tmp_path / "a.md").write_text("aaa", encoding="utf-8")
    manager = manager_factory(tmp_path)
    replacer = BulkReplacer(manager)
    preview = replacer.replace(ReplaceRequest("a", r"\1", paths=("a.md",)))
    assert r"\1\1\1" in preview.notes[0].diff
    monkeypatch.setattr(bulk_replace, "MAX_MATCHES", 2)
    with pytest.raises(InvalidRequestError, match="Too many matches"):
        replacer.replace(ReplaceRequest("a", folders=("",)))


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
    assert [note.path for note in result.notes] == ["a.md", "index.md"]
    assert result.scanned == 2


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

    note = tmp_path / "a.md"
    note.write_text("old", encoding="utf-8")
    manager = manager_factory(tmp_path)
    replacer = BulkReplacer(manager)
    assert replacer.replace(ReplaceRequest("old", "old", folders=("",))).notes == []
    monkeypatch.setattr(bulk_replace, "MAX_DIFF_CHARS", 1)
    with pytest.raises(InvalidRequestError, match="Diff too large"):
        replacer.replace(ReplaceRequest("old", "new", folders=("",)))
    note.write_bytes(b"\xff")
    with pytest.raises(InvalidRequestError, match="UTF-8"):
        replacer.replace(ReplaceRequest("old", "new", folders=("",)))


def test_regex_timeout_is_bounded(
    tmp_path: Path,
    manager_factory: Callable[..., DocumentManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    (tmp_path / "a.md").write_text("a" * 5000 + "b", encoding="utf-8")
    manager = manager_factory(tmp_path)
    with pytest.raises(InvalidRequestError, match="timed out"):
        BulkReplacer(manager).replace(
            ReplaceRequest(r"(a+)+$", "x", folders=("",), regex=True)
        )
    monkeypatch.setattr(bulk_replace, "REGEX_TOTAL_SECONDS", 0.0)
    with pytest.raises(InvalidRequestError, match="timed out"):
        BulkReplacer(manager).replace(ReplaceRequest("a", "b", paths=("a.md",)))


def test_note_size_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:

    (tmp_path / "a.md").write_text("a" * 20, encoding="utf-8")
    manager = manager_factory(tmp_path)
    replacer = BulkReplacer(manager)
    monkeypatch.setattr(bulk_replace, "MAX_NOTE_BYTES", 10)
    with pytest.raises(InvalidRequestError, match="size limit"):
        replacer.replace(ReplaceRequest("a", "b", folders=("",)))


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
    tmp_path: Path, manager_factory: Callable[..., DocumentManager]
) -> None:
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
        paths: Sequence[str], folders: Sequence[str], limit: int
    ) -> list[tuple[str, int]]:
        result = candidates(paths, folders, limit)
        (tmp_path / "a.md").write_text("aaaaaa", encoding="utf-8")
        return result

    monkeypatch.setattr(manager, "replace_candidates", grow)
    monkeypatch.setattr(bulk_replace, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(InvalidRequestError, match="limit"):
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
    text, pattern, replacement, cap = case
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", cap)
    with pytest.raises(InvalidRequestError, match="output too large"):
        BulkReplacer(manager_factory(tmp_path)).replace(
            ReplaceRequest(pattern, replacement, paths=("a.md",))
        )


def test_additional_limits_and_invalid_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:

    (tmp_path / "a.md").write_text("hello a", encoding="utf-8")
    manager = manager_factory(tmp_path)
    replacer = BulkReplacer(manager)
    with pytest.raises(InvalidRequestError, match="Invalid replacement"):
        replacer.replace(
            ReplaceRequest("a", r"\g<missing>", paths=("a.md",), regex=True)
        )
    (tmp_path / "a.md").write_text("a" * 1000, encoding="utf-8")
    monkeypatch.setattr(bulk_replace, "MAX_OUTPUT_BYTES", 1000)
    with pytest.raises(InvalidRequestError, match="output too large"):
        replacer.replace(ReplaceRequest("(a+)", r"\1" * 3, paths=("a.md",), regex=True))

    # The expansion must be rejected before allocating it, not only after subn.
    import regex

    class MatchProxy:
        def __init__(self, match: Any) -> None:
            self.match = match

        def span(self, group: int) -> tuple[int, int]:
            return self.match.span(group)  # type: ignore[no-any-return]

        def expand(self, _replacement: str) -> str:
            pytest.fail("oversized expansion was allocated")

    class PatternProxy:
        def subn(self, callback: Any, text: str, *, timeout: float) -> tuple[str, int]:
            return regex.compile("(a+)").subn(
                lambda match: callback(MatchProxy(match)), text, timeout=timeout
            )  # type: ignore[no-any-return]

    with monkeypatch.context() as patch:
        patch.setattr(replacer, "_compile", lambda _: PatternProxy())
        patch.setattr(replacer, "_replacement_template", lambda *_: [1, 1, 1])
        with pytest.raises(InvalidRequestError, match="output too large"):
            replacer.replace(
                ReplaceRequest("(a+)", r"\1" * 3, paths=("a.md",), regex=True)
            )


def test_candidate_limits_and_walk_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: Callable[..., DocumentManager],
) -> None:

    (tmp_path / "a.md").write_text("12345", encoding="utf-8")
    (tmp_path / "directory.md").mkdir()
    manager = manager_factory(
        tmp_path,
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
            ReplaceRequest("old", "new", paths=(f"../{external.name}",))
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


def test_explicit_excluded_alias_refused(
    tmp_path: Path,
    manager_factory: Callable[..., DocumentManager],
) -> None:

    (tmp_path / "target.md").write_text("old", encoding="utf-8")
    (tmp_path / "excluded.md").symlink_to(tmp_path / "target.md")
    manager = manager_factory(
        tmp_path,
        exclude_patterns=["excluded.md"],
    )
    replacer = BulkReplacer(manager)
    with pytest.raises(InvalidRequestError):
        replacer.replace(ReplaceRequest("old", "new", paths=("excluded.md",)))


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
