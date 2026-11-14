"""Dirty refreshes keep surfaced skips accurate without replacing scan state."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from markdown_vault_mcp.managers import index as index_module
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.test_managers_index import _make_index_mgr

if TYPE_CHECKING:
    from os import stat_result


@settings(max_examples=40, deadline=None)
@example(["missing", "rename", "delete"])
@example(["valid", "missing", "parse", "valid"])
@given(
    st.lists(
        st.sampled_from(["missing", "parse", "valid", "delete", "rename"]),
        min_size=1,
        max_size=12,
    )
)
def test_dirty_skip_transitions_match_disk(operations: list[str]) -> None:
    """Every successful refresh reports current skips, retaining unrelated paths."""
    with TemporaryDirectory() as root:
        base = Path(root)
        vault = base / "vault"
        vault.mkdir()
        (vault / "good.md").write_text("---\ntitle: Good\n---\nbody\n")
        (vault / "other.md").write_text("untitled unrelated note\n")
        current = "note.md"
        (vault / current).write_text("untitled note\n")
        tracker = ChangeTracker(base / "state.json")
        mgr, fts, _ = _make_index_mgr(
            vault, base, tracker=tracker, required_frontmatter=["title"]
        )
        mgr.build_index()
        unrelated_hash = tracker._load_state()[0]["good.md"]
        category: str | None = "missing_frontmatter"
        for operation in operations:
            dirty = {current}
            if operation == "rename":
                destination = "moved/note.md" if current == "note.md" else "note.md"
                (vault / destination).parent.mkdir(exist_ok=True)
                if (vault / current).exists():
                    (vault / current).rename(vault / destination)
                dirty.add(destination)
                current = destination
            elif operation == "delete":
                (vault / current).unlink(missing_ok=True)
                category = None
            else:
                content = {
                    "missing": "untitled changed note\n",
                    "parse": "---\ntitle: [unclosed\n---\nbody\n",
                    "valid": "---\ntitle: Repaired\n---\nbody\n",
                }[operation]
                (vault / current).write_text(content)
                category = {
                    "missing": "missing_frontmatter",
                    "parse": "parse_error",
                    "valid": None,
                }[operation]
            mgr.process_dirty_paths(dirty)
            expected = {"other.md": "missing_frontmatter"}
            if category is not None:
                expected[current] = category
            assert {
                skip.path: skip.category for skip in mgr.skipped_files()
            } == expected
            assert {
                row["path"]: row["skip_category"] for row in fts.list_tombstones()
            } == expected
            for row in fts.list_tombstones():
                reason = tracker.skip_reasons()[row["path"]]
                assert reason == {
                    "category": row["skip_category"],
                    "detail": row["skip_detail"],
                }
                assert tracker._load_state()[1][row["path"]] == row["content_hash"]
            assert tracker._load_state()[0]["good.md"] == unrelated_hash
            assert (
                ChangeTracker(base / "state.json").skip_reasons()
                == tracker.skip_reasons()
            )
        before = mgr.skipped_files()
        mgr.reindex()
        assert mgr.skipped_files() == before
        fts.close()


@pytest.mark.parametrize(
    "failure",
    [
        OSError("retry"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
        ValueError("chunker"),
        RuntimeError("bug"),
    ],
)
def test_failed_dirty_parse_preserves_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """Retryable and unexpected failures never certify a changed skip state."""
    (tmp_path / "note.md").write_text("untitled\n")
    mgr, fts, _ = _make_index_mgr(tmp_path, tmp_path, required_frontmatter=["title"])
    mgr.build_index()
    before = mgr.skipped_files()
    refreshed: list[str] = []

    def fail(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(index_module, "parse_note", fail)
    with pytest.raises(type(failure)):
        mgr.process_dirty_paths({"note.md"}, on_refreshed=refreshed.append)
    assert mgr.skipped_files() == before
    assert refreshed == []
    fts.close()


def test_append_created_skip_is_reported_after_refresh(tmp_path: Path) -> None:
    """The issue's append caller sees its skip after waiting for the native writer."""
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, required_frontmatter=["title"]),
    )
    try:
        vault.index.build_index()
        result = vault.writer.append(
            "new.md", "See [[target]].\n", create_if_missing=True
        )
        assert result.created is True
        assert vault.index.wait_for_drain(timeout=5)
        status = vault.index.get_index_status()
        assert status["dirty_paths"] == 0
        assert status["skipped_files"] == [
            {
                "path": "new.md",
                "category": "missing_frontmatter",
                "detail": "missing: ['title']",
            }
        ]
        assert (tmp_path / "new.md").read_text() == "See [[target]].\n"
    finally:
        vault.close()


def test_dirty_skip_persists_before_graph_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later graph failure retains accurate completed per-path status."""
    (tmp_path / "note.md").write_text("untitled\n")
    mgr, fts, _ = _make_index_mgr(tmp_path, tmp_path, required_frontmatter=["title"])

    def fail() -> None:
        raise sqlite3.OperationalError("graph unavailable")

    monkeypatch.setattr(fts, "resolve_vault_wikilinks", fail)
    with pytest.raises(sqlite3.OperationalError, match="graph unavailable"):
        mgr.process_dirty_paths({"note.md"})
    assert [skip.path for skip in mgr.skipped_files()] == ["note.md"]
    fts.close()


def test_failed_dirty_tombstone_does_not_record_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a committed tombstone gains a surfaced skip."""
    (tmp_path / "note.md").write_text("untitled\n")
    mgr, fts, _ = _make_index_mgr(tmp_path, tmp_path, required_frontmatter=["title"])

    def fail(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("write unavailable")

    monkeypatch.setattr(fts, "upsert_tombstone", fail)
    with pytest.raises(sqlite3.OperationalError, match="write unavailable"):
        mgr.process_dirty_paths({"note.md"})
    assert mgr.skipped_files() == []
    fts.close()


@pytest.mark.parametrize("stage", ["hash", "stat", "state"])
def test_failed_dirty_skip_recording_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """Hash/stat/state failures retain the dirty snapshot for another attempt."""
    path = tmp_path / "note.md"
    path.write_text("---\ntitle: [unclosed\n---\nbody\n")
    tracker = ChangeTracker(tmp_path / "state.json")
    mgr, fts, _ = _make_index_mgr(tmp_path, tmp_path, tracker=tracker)
    refreshed: list[str] = []

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic recording failure")

    if stage == "hash":
        monkeypatch.setattr(index_module, "compute_file_hash", fail)
    elif stage == "stat":
        original_stat = Path.stat

        def stat(item: Path, *args: object, **kwargs: object) -> stat_result:
            if item == path:
                raise OSError("synthetic recording failure")
            return original_stat(item, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", stat)
    else:
        monkeypatch.setattr(tracker, "_save_state", fail)
    with pytest.raises(OSError, match="synthetic recording failure"):
        mgr.process_dirty_paths({"note.md"}, on_refreshed=refreshed.append)
    assert mgr.skipped_files() == []
    assert refreshed == []
    fts.close()


def test_excluding_dirty_skip_clears_status(tmp_path: Path) -> None:
    """An existing skipped path leaves status when its refresh applies exclusion."""
    (tmp_path / "note.md").write_text("untitled\n")
    tracker = ChangeTracker(tmp_path / "state.json")
    mgr, fts, _ = _make_index_mgr(
        tmp_path, tmp_path, tracker=tracker, required_frontmatter=["title"]
    )
    mgr.build_index()
    excluded, _, _ = _make_index_mgr(
        tmp_path, tmp_path, fts=fts, tracker=tracker, exclude_patterns=["note.md"]
    )
    excluded.process_dirty_paths({"note.md"})
    assert excluded.skipped_files() == []
    assert fts.get_tombstone("note.md") is None
    fts.close()
