"""Tests for one text replacement across many notes (#2031)."""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import TYPE_CHECKING

import frontmatter as fm
import pytest

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentNotFoundError,
    InvalidRequestError,
    ReadOnlyError,
    SubmodulePathError,
)
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.managers import _replace_worker as worker
from markdown_vault_mcp.managers import text_replace as tr
from markdown_vault_mcp.managers.text_replace import (
    ReplaceRequest,
    StalePlanError,
    TextReplacer,
)
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain
from tests.fixtures.git import make_superproject

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


_ROOT_INDEX = '---\nokf_version: "0.3"\ntitle: Root\n---\n# Root\n'


def _vault(root: Path, **settings: object) -> Vault:
    vault = Vault(source_dir=root, settings=VaultSettings(read_only=False, **settings))  # type: ignore[arg-type]
    vault.index.build_index()
    return vault


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "vault"
    (base / "a" / "deep").mkdir(parents=True)
    (base / "a" / "one.md").write_text(
        "---\ntitle: foo\n---\n# foo\n\nfoo bar foo\n", encoding="utf-8"
    )
    (base / "a" / "deep" / "two.md").write_text("a foo here\n", encoding="utf-8")
    (base / "b.md").write_text("no match\n", encoding="utf-8")
    return base


@pytest.fixture
def vault(root: Path) -> Iterator[Vault]:
    col = _vault(root)
    try:
        yield col
    finally:
        col.close()


def _req(**kw: object) -> ReplaceRequest:
    kw.setdefault("folders", ("",))
    return ReplaceRequest(**kw)  # type: ignore[arg-type]


class TestSpanEngines:
    def test_literal_finds_non_overlapping_occurrences(self) -> None:
        spans = worker.literal_spans(
            "aa", "b", ["aaaa", "x"], max_replacements=10, max_output_chars=10
        )
        assert spans == [[(0, 2, "b"), (2, 4, "b")], []]

    def test_regex_expands_groups(self) -> None:
        spans = worker.regex_spans(
            r"(\d+)-(\d+)",
            r"\2-\1",
            ["1-2 and 3-4"],
            max_replacements=10,
            max_output_chars=100,
        )
        assert spans == [[(0, 3, "2-1"), (8, 11, "4-3")]]

    @pytest.mark.parametrize(
        ("max_replacements", "max_output_chars", "detail"),
        [(1, 100, "more than 1 replacements"), (10, 3, "more than 3 characters")],
    )
    def test_bounds_refuse(
        self, max_replacements: int, max_output_chars: int, detail: str
    ) -> None:
        with pytest.raises(worker.ReplacementLimitError, match=detail):
            worker.literal_spans(
                "x",
                "yy",
                ["xx"],
                max_replacements=max_replacements,
                max_output_chars=max_output_chars,
            )

    @pytest.mark.parametrize(
        ("pattern", "replacement", "key"),
        [("(", "", "error"), ("a", r"\9", "error"), ("a", "bb", "limit")],
    )
    def test_run_reports_errors_and_limits(
        self, pattern: str, replacement: str, key: str
    ) -> None:
        response = worker.run(
            {
                "pattern": pattern,
                "replacement": replacement,
                "bodies": ["aaa"],
                "max_replacements": 10,
                "max_output_chars": 3,
            }
        )
        assert key in response

    def test_run_returns_spans(self) -> None:
        response = worker.run(
            {
                "pattern": "a",
                "replacement": "b",
                "bodies": ["a"],
                "max_replacements": 10,
                "max_output_chars": 10,
            }
        )
        assert response == {"spans": [[(0, 1, "b")]]}


def _change(text: str, spans: list[tuple[int, int, str]], head: str = "") -> tr._Change:
    return tr._Change(tr._Snapshot("n.md", "etag", head + text, head), tuple(spans))


class TestDiff:
    def test_lines_are_numbered_from_the_top_of_the_file(self) -> None:
        change = _change("x\nfoo\n", [(2, 5, "bar")], head="---\na: 1\n---\n")
        diff, truncated = tr._render_diff(change)
        assert not truncated
        assert "@@ -5 +5 @@\n-foo\n+bar\n" in diff

    def test_a_replaced_newline_joins_the_next_line(self) -> None:
        change = _change("a\nb\nc\n", [(1, 2, " ")])
        diff, _ = tr._render_diff(change)
        assert "@@ -1,2 +1 @@\n-a\n-b\n+a b\n" in diff

    def test_a_deleted_line_and_later_offsets(self) -> None:
        change = _change("drop\nkeep\nx\n", [(0, 5, ""), (10, 11, "y")])
        diff, _ = tr._render_diff(change)
        assert "@@ -1,2 +1 @@\n-drop\n-keep\n+keep\n" in diff
        assert "@@ -3 +2 @@\n-x\n+y\n" in diff

    def test_matches_on_one_line_share_a_hunk(self) -> None:
        change = _change("a a\n", [(0, 1, "b"), (2, 3, "b")])
        diff, _ = tr._render_diff(change)
        assert diff.count("@@") == 2
        assert "-a a\n+b b\n" in diff

    def test_an_empty_match_at_the_end(self) -> None:
        change = _change("a\n", [(2, 2, "z")])
        diff, _ = tr._render_diff(change)
        assert "@@ -1,0 +2 @@\n+z\n" in diff

    def test_a_long_diff_is_cut_at_a_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tr, "PREVIEW_DIFF_CHARS", 40)
        text = "".join(f"line{i}\n" for i in range(20))
        spans = [
            (text.index(f"line{i}\n"), text.index(f"line{i}\n") + 4, "L")
            for i in range(20)
        ]
        diff, truncated = tr._render_diff(_change(text, spans))
        assert truncated
        assert len(diff) <= 40
        assert diff.endswith("\n")


class TestSnapshot:
    @pytest.mark.parametrize(
        ("raw", "head"),
        [
            (b"---\ntitle: x\n---\nbody\n", "---\ntitle: x\n---\n"),
            (b"---\n: [unparseable\n---\nbody\n", "---\n: [unparseable\n---\n"),
            (b"---\nnever closed\nbody\n", ""),
            (b"\xef\xbb\xbf---\na: 1\n---\nb\n", "---\na: 1\n---\n"),
            (b"plain\r\nbody\r\n", ""),
        ],
    )
    def test_body_follows_the_frontmatter_block(
        self, tmp_path: Path, raw: bytes, head: str
    ) -> None:
        path = tmp_path / "n.md"
        path.write_bytes(raw)
        snapshot = tr._read_snapshot("n.md", path, tr._ReadBudget())
        assert isinstance(snapshot, tr._Snapshot)
        assert snapshot.head == head
        assert "\r" not in snapshot.text
        assert snapshot.head + snapshot.body == snapshot.text

    def test_invalid_utf8_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "n.md"
        path.write_bytes(b"\xff\xfe")
        assert tr._read_snapshot("n.md", path, tr._ReadBudget()) == tr._REASON_NOT_UTF8

    def test_a_vanished_note_is_passed_over(self, tmp_path: Path) -> None:
        assert tr._read_snapshot("n.md", tmp_path / "n.md", tr._ReadBudget()) is None


class TestPreview:
    def test_counts_and_diffs_without_writing(self, vault: Vault, root: Path) -> None:
        before = (root / "a" / "one.md").read_bytes()
        preview = vault.writer.preview_replace(_req(pattern="foo", replacement="baz"))
        assert [(n.path, n.replacements) for n in preview.notes] == [
            ("a/deep/two.md", 1),
            ("a/one.md", 3),
        ]
        assert preview.replacements == 4
        assert preview.scanned == 3
        assert preview.notes[1].diff is not None
        assert "+# baz" in preview.notes[1].diff
        assert (root / "a" / "one.md").read_bytes() == before

    def test_frontmatter_is_never_matched(self, vault: Vault) -> None:
        preview = vault.writer.preview_replace(
            _req(pattern="title", paths=("a/one.md",), folders=())
        )
        assert preview.notes == []

    def test_diffs_stop_after_the_first_notes(
        self, vault: Vault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tr, "PREVIEW_DIFF_NOTES", 1)
        preview = vault.writer.preview_replace(_req(pattern="foo"))
        assert preview.notes[0].diff is not None
        assert preview.notes[1].diff is None

    def test_regex_with_groups(self, vault: Vault) -> None:
        preview = vault.writer.preview_replace(
            _req(pattern=r"(\w+) bar", replacement=r"[\1]", regex=True)
        )
        assert [n.path for n in preview.notes] == ["a/one.md"]
        assert "+[foo] foo" in (preview.notes[0].diff or "")

    def test_invalid_regex(self, vault: Vault) -> None:
        request = _req(pattern="(", regex=True)
        with pytest.raises(InvalidRequestError, match="not a valid regular expression"):
            vault.writer.preview_replace(request)

    def test_regex_limit(self, vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tr, "MAX_REPLACEMENTS", 1)
        request = _req(pattern="foo", regex=True)
        with pytest.raises(InvalidRequestError, match="more than 1 replacements"):
            vault.writer.preview_replace(request)

    def test_literal_limit(self, vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tr, "MAX_REPLACEMENTS", 1)
        request = _req(pattern="foo")
        with pytest.raises(InvalidRequestError, match="more than 1 replacements"):
            vault.writer.preview_replace(request)

    def test_regex_timeout(self, vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
        def slow(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="worker", timeout=3.0)

        monkeypatch.setattr(tr.subprocess, "run", slow)
        request = _req(pattern="foo", regex=True)
        with pytest.raises(InvalidRequestError, match="ran longer than 3 seconds"):
            vault.writer.preview_replace(request)

    def test_catastrophic_backtracking_is_killed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tr, "REGEX_TIMEOUT_SECONDS", 0.5)
        (tmp_path / "n.md").write_text("a" * 40 + "!\n", encoding="utf-8")
        col = _vault(tmp_path)
        try:
            request = _req(pattern=r"(a+)+$", regex=True)
            with pytest.raises(InvalidRequestError, match="ran longer"):
                col.writer.preview_replace(request)
        finally:
            col.close()

    def test_reserved_files_are_ordinary_notes(self, root: Path) -> None:
        (root / "index.md").write_text("# Root\n\nfoo listing\n", encoding="utf-8")
        col = _vault(root)
        try:
            preview = col.writer.preview_replace(_req(pattern="foo"))
            assert "index.md" in [n.path for n in preview.notes]
            assert preview.refused == []
        finally:
            col.close()


class TestDigest:
    def test_the_same_notes_give_the_same_plan(self, vault: Vault) -> None:
        request = _req(pattern="foo", replacement="baz")
        first = vault.writer.preview_replace(request).plan
        assert vault.writer.preview_replace(request).plan == first
        assert len(first) == 64

    def test_another_selection_with_the_same_changes_gives_the_same_plan(
        self, vault: Vault
    ) -> None:
        walked = vault.writer.preview_replace(_req(pattern="foo", folders=("a",)))
        named = vault.writer.preview_replace(
            _req(pattern="foo", paths=("a/one.md", "a/deep/two.md"), folders=())
        )
        assert walked.plan == named.plan

    def test_a_changed_note_changes_the_plan(self, vault: Vault, root: Path) -> None:
        request = _req(pattern="foo", replacement="baz")
        before = vault.writer.preview_replace(request).plan
        (root / "a" / "deep" / "two.md").write_text("foo changed\n", encoding="utf-8")
        assert vault.writer.preview_replace(request).plan != before

    @pytest.mark.parametrize(
        "other",
        [
            {"pattern": "foo", "replacement": "qux"},
            {"pattern": "foo", "replacement": "baz", "regex": True},
        ],
    )
    def test_another_replacement_or_mode_changes_the_plan(
        self, vault: Vault, other: dict[str, object]
    ) -> None:
        plan = vault.writer.preview_replace(_req(pattern="foo", replacement="baz")).plan
        assert vault.writer.preview_replace(_req(**other)).plan != plan


class TestRequestBounds:
    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"pattern": ""}, "pattern is empty"),
            ({"pattern": "x" * 4097}, "pattern is longer"),
            ({"pattern": "x", "replacement": "y" * 16385}, "replacement is longer"),
            ({"pattern": "x", "folders": ()}, "no notes selected"),
            ({"pattern": "x", "paths": ("b.txt",), "folders": ()}, r"end with '\.md'"),
            ({"pattern": "x", "paths": ("../out.md",), "folders": ()}, "traversal"),
        ],
    )
    def test_malformed_requests(
        self, vault: Vault, kwargs: dict[str, object], match: str
    ) -> None:
        request = _req(**kwargs)
        with pytest.raises(InvalidRequestError, match=match):
            vault.writer.preview_replace(request)

    def test_missing_note(self, vault: Vault) -> None:
        request = _req(pattern="x", paths=("nope.md",), folders=())
        with pytest.raises(DocumentNotFoundError, match=r"nope\.md"):
            vault.writer.preview_replace(request)

    def test_missing_folder(self, vault: Vault) -> None:
        request = _req(pattern="x", folders=("nope",))
        with pytest.raises(DocumentNotFoundError, match="Folder not found"):
            vault.writer.preview_replace(request)

    @pytest.mark.parametrize(
        ("name", "value", "match"),
        [
            ("MAX_SCANNED_NOTES", 2, "more than 2 notes"),
            ("MAX_NOTE_BYTES", 10, "larger than 10 bytes"),
            ("MAX_TOTAL_BYTES", 20, "more than 20 bytes"),
            ("MAX_CHANGED_NOTES", 1, "more than 1;"),
        ],
    )
    def test_bounds_refuse_rather_than_truncate(
        self,
        vault: Vault,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: int,
        match: str,
    ) -> None:
        monkeypatch.setattr(tr, name, value)
        request = _req(pattern="foo")
        with pytest.raises(InvalidRequestError, match=match):
            vault.writer.preview_replace(request)


class TestSelection:
    def test_dot_and_excluded_paths_are_left_out_of_folders(self, root: Path) -> None:
        (root / ".hidden").mkdir()
        (root / ".hidden" / "n.md").write_text("foo\n", encoding="utf-8")
        (root / ".dot.md").write_text("foo\n", encoding="utf-8")
        (root / "skip").mkdir()
        (root / "skip" / "n.md").write_text("foo\n", encoding="utf-8")
        col = _vault(root, exclude_patterns=["skip/**"])
        try:
            walked = col.writer.preview_replace(_req(pattern="foo"))
            assert {n.path for n in walked.notes} == {"a/one.md", "a/deep/two.md"}
            named = col.writer.preview_replace(
                _req(pattern="foo", paths=(".hidden/n.md", "skip/n.md"), folders=())
            )
            assert {n.path for n in named.notes} == {".hidden/n.md", "skip/n.md"}
        finally:
            col.close()

    @pytest.mark.parametrize("folder", ["a/deep", "a/../a/deep/", "./a/deep"])
    def test_a_folder_walk_covers_only_that_folder(
        self, vault: Vault, folder: str
    ) -> None:
        preview = vault.writer.preview_replace(_req(pattern="foo", folders=(folder,)))
        assert [n.path for n in preview.notes] == ["a/deep/two.md"]

    def test_a_folder_outside_the_vault_is_refused(self, vault: Vault) -> None:
        request = _req(pattern="foo", folders=("../",))
        with pytest.raises(InvalidRequestError, match="traversal"):
            vault.writer.preview_replace(request)

    def test_one_file_reached_twice_is_covered_once(
        self, vault: Vault, root: Path
    ) -> None:
        (root / "alias.md").symlink_to(root / "a" / "deep" / "two.md")
        (root.parent / "elsewhere.md").write_text("foo\n", encoding="utf-8")
        (root / "out.md").symlink_to(root.parent / "elsewhere.md")
        preview = vault.writer.preview_replace(
            _req(pattern="foo", paths=("a/deep/two.md",), folders=("",))
        )
        assert [n.path for n in preview.notes] == ["a/deep/two.md", "a/one.md"]

    def test_not_utf8_is_refused(self, vault: Vault, root: Path) -> None:
        (root / "bin.md").write_bytes(b"\xff foo")
        preview = vault.writer.preview_replace(_req(pattern="foo"))
        assert [(r.path, r.reason) for r in preview.refused] == [
            ("bin.md", "not UTF-8 text")
        ]

    def test_an_unreadable_folder_is_logged_and_passed_over(
        self,
        vault: Vault,
        root: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def walk(
            _base: object, onerror: object, followlinks: bool
        ) -> Iterator[tuple[str, list[str], list[str]]]:
            del followlinks
            onerror(PermissionError(13, "denied", str(root / "a")))  # type: ignore[operator]
            yield str(root), [], ["b.md"]

        monkeypatch.setattr("markdown_vault_mcp.utils.fs.os.walk", walk)
        with caplog.at_level(logging.WARNING):
            preview = vault.writer.preview_replace(_req(pattern="match"))
        assert [n.path for n in preview.notes] == ["b.md"]
        assert any(
            r.msg.startswith("markdown_walk_dir_unreadable") for r in caplog.records
        )

    def test_nested_repository_is_refused(self, tmp_path: Path) -> None:
        source = make_superproject(tmp_path, note="# Upstream foo\n")
        (source / "top.md").write_text("foo\n", encoding="utf-8")
        col = Vault(
            source_dir=source,
            settings=VaultSettings(read_only=False),
            git_strategy=GitWriteStrategy(
                enable_pull=False, enable_push=False, git_lfs=False, repo_path=source
            ),
        )
        col.index.build_index()
        try:
            preview = col.writer.preview_replace(_req(pattern="foo"))
            assert [n.path for n in preview.notes] == ["top.md"]
            assert [(r.path, r.reason) for r in preview.refused] == [
                ("sub/note.md", tr._REASON_NESTED)
            ]
            result = col.writer.apply_replace(_req(pattern="foo"), preview.plan)
            assert [(n.path, n.status) for n in result.notes] == [
                ("sub/note.md", "refused"),
                ("top.md", "applied"),
            ]
            assert "foo" in (source / "sub" / "note.md").read_text(encoding="utf-8")
        finally:
            col.close()


class TestApply:
    def test_writes_every_planned_note(self, vault: Vault, root: Path) -> None:
        request = _req(pattern="foo", replacement="baz")
        preview = vault.writer.preview_replace(request)
        result = vault.writer.apply_replace(request, preview.plan)
        assert (result.applied, result.skipped, result.refused) == (2, 0, 0)
        assert result.plan == preview.plan
        text = (root / "a" / "one.md").read_text(encoding="utf-8")
        assert text == "---\ntitle: foo\n---\n# baz\n\nbaz bar baz\n"
        assert vault.writer.preview_replace(request).notes == []

    def test_a_note_changed_since_the_preview_refuses_the_apply(
        self, vault: Vault, root: Path
    ) -> None:
        request = _req(pattern="foo", replacement="baz")
        preview = vault.writer.preview_replace(request)
        (root / "a" / "deep" / "two.md").write_text("foo changed\n", encoding="utf-8")
        with pytest.raises(StalePlanError, match="Preview the same request again"):
            vault.writer.apply_replace(request, preview.plan)
        assert "foo" in (root / "a" / "one.md").read_text(encoding="utf-8")

    def test_a_plan_for_another_request_is_refused(self, vault: Vault) -> None:
        plan = vault.writer.preview_replace(_req(pattern="foo", replacement="baz")).plan
        other = _req(pattern="foo", replacement="qux")
        with pytest.raises(StalePlanError):
            vault.writer.apply_replace(other, plan)

    def test_a_plan_survives_a_new_replacer(self, vault: Vault) -> None:
        request = _req(pattern="foo", replacement="baz")
        plan = vault.writer.preview_replace(request).plan
        assert TextReplacer(vault._doc_mgr).apply(request, plan).applied == 2

    def test_read_only_vault(self, root: Path) -> None:
        col = Vault(source_dir=root, settings=VaultSettings(read_only=True))
        try:
            request = _req(pattern="foo")
            with pytest.raises(ReadOnlyError):
                col.writer.apply_replace(request, "x" * 64)
        finally:
            col.close()

    def test_refused_notes_are_reported_and_left_alone(
        self, vault: Vault, root: Path
    ) -> None:
        (root / "bin.md").write_bytes(b"\xff foo")
        request = _req(pattern="foo")
        result = vault.writer.apply_replace(
            request, vault.writer.preview_replace(request).plan
        )
        assert ("bin.md", "refused", "not UTF-8 text") in [
            (n.path, n.status, n.reason) for n in result.notes
        ]
        assert (root / "bin.md").read_bytes() == b"\xff foo"

    @pytest.mark.parametrize(
        ("error", "status", "reason"),
        [
            (ConcurrentModificationError("n", "a", "b"), "skipped", "changed since"),
            (DocumentNotFoundError("gone"), "skipped", "changed since"),
            (SubmodulePathError("nested"), "refused", "nested repository"),
        ],
    )
    def test_a_write_refusal_is_a_note_outcome(
        self,
        vault: Vault,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        status: str,
        reason: str,
    ) -> None:
        request = _req(pattern="foo", paths=("a/one.md",), folders=())
        preview = vault.writer.preview_replace(request)

        def refuse(_path: str, _content: str, *, if_match: str) -> None:
            del if_match
            raise error

        monkeypatch.setattr(vault._doc_mgr, "rewrite", refuse)
        result = vault.writer.apply_replace(request, preview.plan)
        assert [n.status for n in result.notes] == [status]
        assert reason in (result.notes[0].reason or "")

    def test_a_note_changed_after_the_recompute_is_skipped(
        self, vault: Vault, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request = _req(pattern="foo", replacement="baz")
        preview = vault.writer.preview_replace(request)
        real = vault._doc_mgr.rewrite
        two = root / "a" / "deep" / "two.md"

        def race(path: str, content: str, *, if_match: str) -> None:
            if path == "a/deep/two.md":
                two.write_text("foo raced\n", encoding="utf-8")
            real(path, content, if_match=if_match)

        monkeypatch.setattr(vault._doc_mgr, "rewrite", race)
        result = vault.writer.apply_replace(request, preview.plan)
        assert [(n.path, n.status) for n in result.notes] == [
            ("a/deep/two.md", "skipped"),
            ("a/one.md", "applied"),
        ]
        assert two.read_text(encoding="utf-8") == "foo raced\n"

    def test_upkeep_runs_after_each_write_outside_the_lock(self, vault: Vault) -> None:
        lock = vault._doc_mgr._file_write_lock
        seen: list[tuple[str, bool]] = []

        def lock_is_free() -> bool:
            free: list[bool] = []

            def probe() -> None:
                got = lock.acquire(blocking=False)
                free.append(got)
                if got:
                    lock.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join()
            return free[0]

        replacer = TextReplacer(
            vault._doc_mgr, after_write=lambda p: seen.append((p, lock_is_free()))
        )
        request = _req(pattern="foo")
        replacer.apply(request, replacer.preview(request).plan)
        assert seen == [("a/deep/two.md", True), ("a/one.md", True)]

    def test_a_fault_stops_the_apply(
        self, vault: Vault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        written: list[str] = []
        replacer = TextReplacer(vault._doc_mgr, after_write=written.append)
        real = vault._doc_mgr.rewrite

        def fail_second(path: str, content: str, *, if_match: str) -> None:
            if path == "a/one.md":
                raise OSError("disk full")
            real(path, content, if_match=if_match)

        monkeypatch.setattr(vault._doc_mgr, "rewrite", fail_second)
        request = _req(pattern="foo")
        plan = replacer.preview(request).plan
        with pytest.raises(OSError, match="disk full"):
            replacer.apply(request, plan)
        assert written == ["a/deep/two.md"]

    def test_rewrite_needs_an_existing_note(self, vault: Vault) -> None:
        with pytest.raises(DocumentNotFoundError):
            vault._doc_mgr.rewrite("nope.md", "x", if_match="e")


class TestOkf:
    @pytest.fixture
    def okf_root(self, tmp_path: Path) -> Path:
        base = tmp_path / "okf"
        (base / "a" / "x").mkdir(parents=True)
        (base / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        for rel in ("a/x/one.md", "a/two.md"):
            (base / rel).write_text(
                "---\ntype: guide\ntitle: T\n---\nfoo body\n", encoding="utf-8"
            )
        return base

    def test_each_written_note_gets_edit_upkeep_and_stamping(
        self, okf_root: Path
    ) -> None:
        col = _vault(okf_root, okf_mode="on", okf_write=True)
        try:
            request = _req(pattern="foo", replacement="bar")
            preview = col.writer.preview_replace(request)
            result = col.writer.apply_replace(request, preview.plan)
            assert result.applied == 2
            wait_for_writer_drain(col)
            for folder, note in (("a/x", "one.md"), ("a", "two.md")):
                log = (okf_root / folder / "log.md").read_text(encoding="utf-8")
                assert log.count("**Update**") == 1
                assert note in log
            post = fm.loads(
                (okf_root / "a" / "x" / "one.md").read_text(encoding="utf-8")
            )
            assert post.content == "bar body"
            assert "at" in post.metadata["generated"]
        finally:
            col.close()
