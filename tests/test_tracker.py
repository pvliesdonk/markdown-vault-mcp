"""Unit tests for ChangeTracker (tracker.py)."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.types import Chunk, ParsedNote

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_md(directory: Path, name: str, content: str = "# Hello\n") -> Path:
    """Write a markdown file and return its path.

    Args:
        directory: Directory in which to create the file.
        name: Filename (relative to *directory*).
        content: File content. Defaults to a minimal markdown heading.

    Returns:
        Absolute path of the created file.
    """
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_note(path: str, content_hash: str) -> ParsedNote:
    """Create a minimal ParsedNote with the given path and hash.

    Args:
        path: Relative document path used as the state key.
        content_hash: SHA256 hex digest string.

    Returns:
        A :class:`ParsedNote` with one placeholder chunk.
    """
    return ParsedNote(
        path=path,
        frontmatter={},
        title="Title",
        chunks=[Chunk(heading=None, heading_level=0, content="body", start_line=0)],
        content_hash=content_hash,
        modified_at=1000.0,
    )


# ===========================================================================
# Tests
# ===========================================================================


class TestFreshScan:
    def test_fresh_scan_all_added(self, tmp_path: Path) -> None:
        """On first run (no state file) every discovered file is in added."""
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "a.md")
        _write_md(vault, "b.md")
        _write_md(vault, "sub/c.md")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)
        changes = tracker.detect_changes(vault)

        assert set(changes.added) == {"a.md", "b.md", "sub/c.md"}
        assert changes.modified == []
        assert changes.deleted == []
        assert changes.unchanged == 0


class TestModifiedFile:
    def test_modified_file_detected(self, tmp_path: Path) -> None:
        """A file whose content changes between scans appears in modified."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "note.md", "original content\n")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        tracker.detect_changes(vault)
        note = _make_note("note.md", hashlib.sha256(md.read_bytes()).hexdigest())
        tracker.update_state([note])

        # Modify the file.
        md.write_text("modified content\n", encoding="utf-8")

        changes2 = tracker.detect_changes(vault)
        assert "note.md" in changes2.modified
        assert "note.md" not in changes2.added
        assert changes2.deleted == []


class TestDeletedFile:
    def test_deleted_file_detected(self, tmp_path: Path) -> None:
        """A file present in state but gone from disk appears in deleted."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "vanish.md")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        tracker.detect_changes(vault)
        note = _make_note("vanish.md", hashlib.sha256(md.read_bytes()).hexdigest())
        tracker.update_state([note])

        md.unlink()

        changes2 = tracker.detect_changes(vault)
        assert "vanish.md" in changes2.deleted
        assert "vanish.md" not in changes2.added
        assert "vanish.md" not in changes2.modified


class TestUnchangedFiles:
    def test_unchanged_files_counted(self, tmp_path: Path) -> None:
        """Files with matching hashes contribute to the unchanged count."""
        vault = tmp_path / "vault"
        vault.mkdir()
        files = ["x.md", "y.md", "z.md"]
        mds = [_write_md(vault, name) for name in files]

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)
        tracker.detect_changes(vault)

        notes = [
            _make_note(name, hashlib.sha256(md.read_bytes()).hexdigest())
            for name, md in zip(files, mds, strict=True)
        ]
        tracker.update_state(notes)

        changes2 = tracker.detect_changes(vault)
        assert changes2.added == []
        assert changes2.modified == []
        assert changes2.deleted == []
        assert changes2.unchanged == len(files)


class TestUpdateStatePersists:
    def test_update_state_persists_state_file(self, tmp_path: Path) -> None:
        """update_state() writes a file that survives between tracker instances."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "persist.md")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        note = _make_note("persist.md", hashlib.sha256(md.read_bytes()).hexdigest())
        tracker.update_state([note])

        assert state_path.exists()

        # A fresh tracker reading the same state file sees no additions.
        tracker2 = ChangeTracker(state_path)
        changes = tracker2.detect_changes(vault)
        assert changes.added == []
        assert changes.unchanged == 1


class TestReset:
    def test_reset_clears_state(self, tmp_path: Path) -> None:
        """reset() removes the state file; next scan treats all files as added."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "reset.md")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        note = _make_note("reset.md", hashlib.sha256(md.read_bytes()).hexdigest())
        tracker.update_state([note])
        assert state_path.exists()

        tracker.reset()
        assert not state_path.exists()

        changes = tracker.detect_changes(vault)
        assert "reset.md" in changes.added
        assert changes.unchanged == 0


class TestStateFileParentDirs:
    def test_state_file_parent_dirs_created(self, tmp_path: Path) -> None:
        """update_state() creates missing parent directories for the state file."""
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "doc.md")

        deep_state = tmp_path / "a" / "b" / "c" / "state.json"
        assert not deep_state.parent.exists()

        tracker = ChangeTracker(deep_state)
        tracker.update_state([])

        assert deep_state.exists()


class TestResetNoStateFile:
    def test_reset_when_no_state_file_is_noop(self, tmp_path: Path) -> None:
        """reset() on a fresh tracker (no state file) does not raise."""
        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        assert not state_path.exists()
        tracker.reset()  # Must not raise.
        assert not state_path.exists()

    def test_reset_no_state_file_logs_debug(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """reset() logs a DEBUG message when the state file does not exist."""
        import logging

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.tracker"):
            tracker.reset()

        assert any(
            "does not exist" in r.message or "nothing to delete" in r.message
            for r in caplog.records
            if r.levelno == logging.DEBUG
        )


class TestMalformedStateFile:
    def test_json_array_treated_as_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A state file containing a JSON array is treated as empty; all files added."""
        import logging

        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "a.md")

        state_path = tmp_path / "state.json"
        state_path.write_text("[1, 2, 3]", encoding="utf-8")

        tracker = ChangeTracker(state_path)

        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.tracker"):
            changes = tracker.detect_changes(vault)

        # All files should be treated as added (not crash).
        assert "a.md" in changes.added
        assert any(
            "malformed" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        )

    def test_invalid_json_treated_as_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A state file with invalid JSON is treated as empty; a WARNING is logged."""
        import logging

        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "b.md")

        state_path = tmp_path / "state.json"
        state_path.write_text("not json{{{", encoding="utf-8")

        tracker = ChangeTracker(state_path)

        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.tracker"):
            changes = tracker.detect_changes(vault)

        assert "b.md" in changes.added
        assert any("Cannot read state file" in r.message for r in caplog.records)

    def test_oserror_on_state_read_treated_as_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """OSError reading the state file falls back to empty; WARNING is logged."""
        import logging
        import os
        import stat

        if os.getuid() == 0:
            pytest.skip("chmod-based permission test skipped when running as root")

        state_path = tmp_path / "state.json"
        # Write a valid state file so _state_path.exists() returns True.
        state_path.write_text("{}", encoding="utf-8")
        # Remove read permission so open() raises OSError.
        state_path.chmod(0o000)

        tracker = ChangeTracker(state_path)
        try:
            with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.tracker"):
                result = tracker._load_state()
        finally:
            # Restore permissions so pytest can clean up tmp_path.
            state_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        assert result == {}
        assert any("Cannot read state file" in r.message for r in caplog.records)


class TestSaveStateFailure:
    def test_save_state_cleans_up_tmp_file_on_failure(self, tmp_path: Path) -> None:
        """When _save_state fails mid-write, no .tmp file is left behind."""

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        with (
            patch("json.dump", side_effect=RuntimeError("disk full")),
            pytest.raises(RuntimeError, match="disk full"),
        ):
            tracker._save_state({"a.md": "abc123"})

        # No leftover .tmp files.
        leftover = list(tmp_path.glob("*.tmp"))
        assert leftover == [], f"Leftover tmp files: {leftover}"


class TestExcludePatterns:
    """Verifies that ``exclude_patterns`` filters files from the scan and
    protects stale state entries from the ``deleted`` bucket. Issue #257."""

    def test_excluded_files_not_in_added(self, tmp_path: Path) -> None:
        """Files matching an exclude pattern are not reported as added."""
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "note.md")
        _write_md(vault, ".claude/agents/knowledge-gaps.md")
        _write_md(vault, ".obsidian/workspace.md")

        tracker = ChangeTracker(tmp_path / "state.json")
        changes = tracker.detect_changes(
            vault, exclude_patterns=[".claude/**", ".obsidian/**"]
        )

        assert "note.md" in changes.added
        assert ".claude/agents/knowledge-gaps.md" not in changes.added
        assert ".obsidian/workspace.md" not in changes.added
        assert changes.modified == []
        assert changes.deleted == []

    def test_excluded_files_not_hashed(self, tmp_path: Path) -> None:
        """Excluded files are skipped during the scan and never hashed.

        Verified by patching ``_compute_hash`` and asserting it is only called
        for the non-excluded file.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "kept.md")
        _write_md(vault, "excluded/leak.md")

        tracker = ChangeTracker(tmp_path / "state.json")
        with patch.object(
            tracker, "_compute_hash", wraps=tracker._compute_hash
        ) as spy:
            tracker.detect_changes(vault, exclude_patterns=["excluded/**"])

        called_files = {call.args[0].name for call in spy.call_args_list}
        assert called_files == {"kept.md"}

    def test_excluded_files_not_in_modified(self, tmp_path: Path) -> None:
        """A previously seen file that matches a pattern is not reported as
        modified, even if its content changed."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, ".obsidian/workspace.md", "v1\n")
        old_hash = hashlib.sha256(md.read_bytes()).hexdigest()

        # Seed state with the old hash so the tracker thinks it has seen the
        # file before.
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps({".obsidian/workspace.md": old_hash}), encoding="utf-8"
        )

        # Modify file then detect changes with the exclude pattern.
        md.write_text("v2\n", encoding="utf-8")
        tracker = ChangeTracker(state_path)
        changes = tracker.detect_changes(vault, exclude_patterns=[".obsidian/**"])

        assert changes.added == []
        assert changes.modified == []
        # Stale state entry must NOT be reported as deleted either (the file
        # still exists on disk, it is just excluded).
        assert changes.deleted == []

    def test_excluded_stale_entry_not_deleted(self, tmp_path: Path) -> None:
        """Stale state entries pointing at excluded files are kept out of
        ``deleted`` even when the file is gone from disk.

        Rationale: if a file was renamed *into* an excluded subtree, the
        previous index entry should be purged by the caller via a separate
        stale-doc pass (Collection.reindex), not by ChangeTracker. Keeping
        excluded paths out of ``deleted`` makes that contract explicit.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "still.md")

        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps({
                "still.md": hashlib.sha256(
                    (vault / "still.md").read_bytes()
                ).hexdigest(),
                ".trash/old.md": "0" * 64,
            }),
            encoding="utf-8",
        )

        tracker = ChangeTracker(state_path)
        changes = tracker.detect_changes(vault, exclude_patterns=[".trash/**"])

        assert ".trash/old.md" not in changes.deleted
        assert changes.deleted == []

    def test_no_exclude_patterns_preserves_legacy_behaviour(
        self, tmp_path: Path
    ) -> None:
        """Calling detect_changes without exclude_patterns matches v1.6.0
        semantics: every markdown file under source_dir is reported."""
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "a.md")
        _write_md(vault, ".obsidian/workspace.md")

        tracker = ChangeTracker(tmp_path / "state.json")
        changes = tracker.detect_changes(vault)

        assert set(changes.added) == {"a.md", ".obsidian/workspace.md"}


class TestStateFileFormat:
    def test_state_file_format_is_path_to_hash(self, tmp_path: Path) -> None:
        """The JSON state file maps relative path strings to SHA256 hex strings."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "check.md", "some content\n")
        expected_hash = hashlib.sha256(md.read_bytes()).hexdigest()

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)
        note = _make_note("check.md", expected_hash)
        tracker.update_state([note])

        raw = json.loads(state_path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        assert "check.md" in raw
        assert raw["check.md"] == expected_hash
        # All values should look like SHA256 hex digests (64 hex chars).
        for value in raw.values():
            assert isinstance(value, str)
            assert len(value) == 64
            int(value, 16)  # raises ValueError if not hex
