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

    def test_non_default_glob_uses_raw_glob(self, tmp_path: Path) -> None:
        """A non-default glob_pattern falls back to the raw glob, not the walker.

        The pruning walker only serves the default ``**/*.md`` pattern; any
        other pattern keeps the plain ``source_dir.glob`` so its semantics are
        unchanged. A single-level ``*.md`` must discover only the top-level
        file, not the nested one a recursive walk would find.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "top.md")
        _write_md(vault, "sub/deep.md")

        tracker = ChangeTracker(tmp_path / "state.json")
        changes = tracker.detect_changes(vault, glob_pattern="*.md")

        assert changes.added == ["top.md"]


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

        assert result == ({}, {}, {})
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
            tracker._save_state({"a.md": "abc123"}, {}, {})

        # No leftover .tmp files.
        leftover = list(tmp_path.glob("*.tmp"))
        assert leftover == [], f"Leftover tmp files: {leftover}"


class TestStateFileFormat:
    def test_state_file_format_is_versioned_maps(self, tmp_path: Path) -> None:
        """The JSON state file holds versioned indexed/skipped path→hash maps."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "check.md", "some content\n")
        expected_hash = hashlib.sha256(md.read_bytes()).hexdigest()
        skipped_hash = hashlib.sha256(b"skipped\n").hexdigest()

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)
        note = _make_note("check.md", expected_hash)
        tracker.update_state([note], skipped={"skip.md": skipped_hash})

        raw = json.loads(state_path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        assert raw["version"] == 2
        assert raw["indexed"] == {"check.md": expected_hash}
        assert raw["skipped"] == {"skip.md": skipped_hash}
        # All values should look like SHA256 hex digests (64 hex chars).
        for value in {**raw["indexed"], **raw["skipped"]}.values():
            assert isinstance(value, str)
            assert len(value) == 64
            int(value, 16)  # raises ValueError if not hex


class TestSkippedFiles:
    def _hash_of(self, path: Path) -> str:
        """Return the SHA256 hex digest of a file's raw bytes."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_unchanged_skipped_file_not_rereported(self, tmp_path: Path) -> None:
        """A recorded skipped file with unchanged content stays out of added."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "skip.md", "no frontmatter\n")

        tracker = ChangeTracker(tmp_path / "state.json")
        tracker.detect_changes(vault)
        tracker.update_state([], skipped={"skip.md": self._hash_of(md)})

        changes = tracker.detect_changes(vault)
        assert changes.added == []
        assert changes.modified == []
        assert changes.deleted == []
        assert changes.unchanged == 0
        assert changes.skipped_unchanged == 1

    def test_changed_skipped_file_reported_added(self, tmp_path: Path) -> None:
        """A skipped file whose content changes is re-reported as added."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "skip.md", "no frontmatter\n")

        tracker = ChangeTracker(tmp_path / "state.json")
        tracker.detect_changes(vault)
        tracker.update_state([], skipped={"skip.md": self._hash_of(md)})

        md.write_text("---\nname: now valid\n---\nbody\n", encoding="utf-8")

        changes = tracker.detect_changes(vault)
        assert changes.added == ["skip.md"]
        assert changes.modified == []
        assert changes.skipped_unchanged == 0

    def test_deleted_skipped_file_not_reported_deleted(self, tmp_path: Path) -> None:
        """A skipped file removed from disk is dropped silently, not deleted."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "skip.md", "no frontmatter\n")

        tracker = ChangeTracker(tmp_path / "state.json")
        tracker.detect_changes(vault)
        tracker.update_state([], skipped={"skip.md": self._hash_of(md)})

        md.unlink()

        changes = tracker.detect_changes(vault)
        assert changes.deleted == []
        assert changes.skipped_unchanged == 0

        # The dropped entry must not be carried into the next state write.
        tracker.update_state([])
        raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert raw["skipped"] == {}

    def test_skipped_carry_persists_across_update_state(self, tmp_path: Path) -> None:
        """Unchanged skipped entries survive update_state without re-passing them."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "skip.md", "no frontmatter\n")

        tracker = ChangeTracker(tmp_path / "state.json")
        tracker.detect_changes(vault)
        tracker.update_state([], skipped={"skip.md": self._hash_of(md)})

        # Subsequent scan + state write without new skips keeps the entry.
        tracker.detect_changes(vault)
        tracker.update_state([])

        raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert raw["skipped"] == {"skip.md": self._hash_of(md)}

    def test_carry_consumed_by_one_update_state(self, tmp_path: Path) -> None:
        """A second update_state without a fresh detect_changes (a full
        build_index) must not merge the previous scan's skipped carry."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "skip.md", "no frontmatter\n")

        tracker = ChangeTracker(tmp_path / "state.json")
        tracker.update_state([], skipped={"skip.md": self._hash_of(md)})
        tracker.detect_changes(vault)  # populates the carry with skip.md
        tracker.update_state([])  # consumes the carry

        md.unlink()
        # Full-build-style call: no detect_changes beforehand, and the file
        # is gone from disk — its stale entry must not reappear.
        tracker.update_state([])

        raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert raw["skipped"] == {}

    def test_indexed_path_wins_over_skipped(self, tmp_path: Path) -> None:
        """A path present in notes is dropped from the skipped map."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "doc.md", "content\n")
        digest = hashlib.sha256(md.read_bytes()).hexdigest()

        tracker = ChangeTracker(tmp_path / "state.json")
        note = _make_note("doc.md", digest)
        tracker.update_state([note], skipped={"doc.md": digest})

        raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert raw["indexed"] == {"doc.md": digest}
        assert raw["skipped"] == {}


class TestUnreadableFiles:
    def test_unreadable_file_skipped_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A file that raises OSError on read is skipped from the scan."""
        import logging
        import os
        import stat

        if os.getuid() == 0:
            pytest.skip("chmod-based permission test skipped when running as root")

        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "ok.md")
        locked = _write_md(vault, "locked.md")
        locked.chmod(0o000)

        tracker = ChangeTracker(tmp_path / "state.json")
        try:
            with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.tracker"):
                changes = tracker.detect_changes(vault)
        finally:
            locked.chmod(stat.S_IRUSR | stat.S_IWUSR)

        assert changes.added == ["ok.md"]
        assert any("Cannot read" in r.message for r in caplog.records)

    def test_file_outside_source_dir_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A discovered path resolving outside source_dir is skipped + warned."""
        import logging
        from unittest.mock import patch as mock_patch

        vault = tmp_path / "vault"
        vault.mkdir()
        outside = _write_md(tmp_path, "outside.md")

        tracker = ChangeTracker(tmp_path / "state.json")

        def fake_discover(source_dir, exclude_patterns):  # noqa: ARG001
            return iter([outside])

        with (
            mock_patch(
                "markdown_vault_mcp.tracker.iter_markdown_files",
                side_effect=fake_discover,
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.tracker"),
        ):
            changes = tracker.detect_changes(vault)

        assert changes.added == []
        assert any("File outside source_dir" in r.message for r in caplog.records)

    def test_transient_fd_exhaustion_is_retried_within_the_scan(
        self, tmp_path: Path
    ) -> None:
        """A momentary EMFILE while hashing does not drop the file for the scan.

        File-descriptor exhaustion (errno EMFILE) is transient: the very next
        open often succeeds. A single scan must retry rather than silently drop
        a brand-new file — otherwise it is reported as neither added nor
        unchanged and never reaches the index until a later scan happens to
        hash it cleanly (the file-watcher reindex symptom).
        """
        import errno
        from unittest.mock import patch as mock_patch

        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "new.md", "brand new\n")

        real = ChangeTracker._compute_hash
        calls: list[int] = []

        def flaky(self: ChangeTracker, path: Path) -> str:
            calls.append(1)
            if len(calls) == 1:
                raise OSError(errno.EMFILE, "Too many open files")
            return real(self, path)

        tracker = ChangeTracker(tmp_path / "state.json")
        with mock_patch.object(ChangeTracker, "_compute_hash", flaky):
            changes = tracker.detect_changes(vault)

        assert changes.added == ["new.md"]  # recovered on retry, not dropped
        assert len(calls) >= 2  # the first EMFILE was retried

    def test_persistent_fd_exhaustion_is_dropped_and_logged_at_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A persistent EMFILE that survives the retries is surfaced at ERROR.

        The file is still dropped for this scan (nothing can be done without a
        successful read) and retried on the next scan, but the failure must be
        visible at ERROR rather than lost among the many INFO reindex-summary
        lines a busy watcher emits.
        """
        import errno
        import logging
        from unittest.mock import patch as mock_patch

        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "stuck.md", "cannot read\n")

        def always_emfile(_self: ChangeTracker, _path: Path) -> str:
            raise OSError(errno.EMFILE, "Too many open files")

        tracker = ChangeTracker(tmp_path / "state.json")
        with (
            mock_patch.object(ChangeTracker, "_compute_hash", always_emfile),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.tracker"),
        ):
            changes = tracker.detect_changes(vault)

        assert changes.added == []  # dropped this scan; retried next scan
        assert any(
            r.levelno == logging.ERROR and "stuck.md" in r.getMessage()
            for r in caplog.records
        )

    def test_indexed_file_failing_to_hash_is_kept_not_deleted(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An already-indexed file that fails to re-hash is retained, not purged.

        Dropping it from the disk snapshot would route it into ``deleted``,
        purging a live document from FTS and its embedding on a transient (or
        even a permanent-but-still-present, e.g. EACCES) read failure (#831).
        The prior hash is carried forward so the file counts as unchanged this
        scan and is re-hashed on the next one.
        """
        import errno
        import hashlib
        import logging
        from unittest.mock import patch as mock_patch

        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "keep.md", "content\n")

        tracker = ChangeTracker(tmp_path / "state.json")
        tracker.detect_changes(vault)
        note = _make_note("keep.md", hashlib.sha256(md.read_bytes()).hexdigest())
        tracker.update_state([note])

        def eacces(_self: ChangeTracker, _path: Path) -> str:
            raise OSError(errno.EACCES, "Permission denied")

        with (
            mock_patch.object(ChangeTracker, "_compute_hash", eacces),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.tracker"),
        ):
            changes = tracker.detect_changes(vault)

        assert changes.deleted == [], "an unreadable indexed file must not be purged"
        assert "keep.md" not in changes.modified
        assert changes.unchanged == 1  # carried forward as unchanged
        assert any(
            r.levelno == logging.WARNING
            and "hash_read_failed_keeping_indexed" in r.getMessage()
            and "keep.md" in r.getMessage()
            for r in caplog.records
        ), "the carry-forward must be visible in the log for operators"

    def test_non_transient_errno_is_not_retried(self, tmp_path: Path) -> None:
        """A non-transient OSError (EACCES) fails on the first attempt, no retry.

        Guards the discriminate-then-retry contract: only EMFILE/ENFILE retry;
        if EACCES were added to the transient set, the EMFILE tests would still
        pass (just slower), so this asserts the call count directly (#836).
        """
        import errno
        from unittest.mock import patch as mock_patch

        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "new.md", "x\n")
        calls: list[int] = []

        def eacces(_self: ChangeTracker, _path: Path) -> str:
            calls.append(1)
            raise OSError(errno.EACCES, "Permission denied")

        tracker = ChangeTracker(tmp_path / "state.json")
        with mock_patch.object(ChangeTracker, "_compute_hash", eacces):
            tracker.detect_changes(vault)

        assert len(calls) == 1, "a non-transient errno must be attempted exactly once"


class TestLegacyStateFormat:
    def test_legacy_flat_state_loads_as_indexed(self, tmp_path: Path) -> None:
        """An old flat path→hash state file loads with all entries indexed."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "old.md", "legacy content\n")
        digest = hashlib.sha256(md.read_bytes()).hexdigest()

        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"old.md": digest}), encoding="utf-8")

        tracker = ChangeTracker(state_path)
        changes = tracker.detect_changes(vault)

        assert changes.added == []
        assert changes.modified == []
        assert changes.deleted == []
        assert changes.unchanged == 1
        assert changes.skipped_unchanged == 0

    def test_legacy_flat_state_detects_modification(self, tmp_path: Path) -> None:
        """Legacy state entries still participate in modification detection."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "old.md", "legacy content\n")
        digest = hashlib.sha256(md.read_bytes()).hexdigest()

        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"old.md": digest}), encoding="utf-8")

        md.write_text("changed content\n", encoding="utf-8")

        tracker = ChangeTracker(state_path)
        changes = tracker.detect_changes(vault)
        assert changes.modified == ["old.md"]

    def test_v2_state_with_non_dict_maps_resets(self, tmp_path: Path) -> None:
        """A version-2 state file with malformed maps is treated as empty."""
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "a.md")

        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps({"version": 2, "indexed": [], "skipped": {}}),
            encoding="utf-8",
        )

        tracker = ChangeTracker(state_path)
        changes = tracker.detect_changes(vault)
        assert "a.md" in changes.added


class TestExcludePatterns:
    """#257: detect_changes filters paths matching exclude_patterns."""

    def test_excluded_paths_not_reported(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "note.md")
        _write_md(vault, ".claude/test.md")

        tracker = ChangeTracker(tmp_path / "state.json")
        changes = tracker.detect_changes(vault, exclude_patterns=[".claude/**"])

        assert "note.md" in changes.added
        assert ".claude/test.md" not in changes.added

    def test_default_none_includes_all(self, tmp_path: Path) -> None:
        # Back-compat: callers that pass no exclude_patterns see every file.
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "note.md")
        _write_md(vault, ".claude/test.md")

        tracker = ChangeTracker(tmp_path / "state.json")
        changes = tracker.detect_changes(vault)

        assert ".claude/test.md" in changes.added

    def test_excluded_paths_not_hashed(self, tmp_path: Path) -> None:
        # The perf win: excluded files are skipped BEFORE hashing.
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "note.md")
        _write_md(vault, ".obsidian/cache.md")

        tracker = ChangeTracker(tmp_path / "state.json")
        hashed: list[str] = []
        original = tracker._compute_hash

        def spy(path: Path) -> str:
            hashed.append(path.name)
            return original(path)

        with patch.object(tracker, "_compute_hash", side_effect=spy):
            tracker.detect_changes(vault, exclude_patterns=[".obsidian/**"])

        assert "note.md" in hashed
        assert "cache.md" not in hashed

    def test_excluded_path_in_state_not_reported_deleted(self, tmp_path: Path) -> None:
        # A previously-indexed path that is now excluded is filtered, not
        # "deleted" — the reindex stale-purge (#255) removes it (and its
        # vectors). Reporting it as deleted would leak its embedding (#257).
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "note.md")
        _write_md(vault, ".claude/old.md")

        tracker = ChangeTracker(tmp_path / "state.json")
        tracker.update_state(
            [_make_note("note.md", "h1"), _make_note(".claude/old.md", "h2")]
        )

        changes = tracker.detect_changes(vault, exclude_patterns=[".claude/**"])
        assert ".claude/old.md" not in changes.deleted
        assert ".claude/old.md" not in changes.added
        assert ".claude/old.md" not in changes.modified


class TestSkipReasons:
    """skip_reasons map is persisted in lockstep with the skipped map (#775)."""

    def _hash_of(self, path: Path) -> str:
        from markdown_vault_mcp.hashing import compute_file_hash

        return compute_file_hash(path)

    def test_round_trips_through_state_file(self, tmp_path: Path) -> None:
        state_path = tmp_path / "state.json"
        md = tmp_path / "skip.md"
        md.write_text("bad", encoding="utf-8")
        tracker = ChangeTracker(state_path)
        tracker.update_state(
            [],
            skipped={"skip.md": self._hash_of(md)},
            skip_reasons={"skip.md": {"category": "parse_error", "detail": "boom"}},
        )
        reloaded = ChangeTracker(state_path)
        assert reloaded.skip_reasons() == {
            "skip.md": {"category": "parse_error", "detail": "boom"},
        }

    def test_missing_key_loads_as_empty(self, tmp_path: Path) -> None:
        state_path = tmp_path / "state.json"
        # A version-2 state file with no skip_reasons key (older format).
        state_path.write_text(
            '{"version": 2, "indexed": {}, "skipped": {"skip.md": "abc"}}',
            encoding="utf-8",
        )
        tracker = ChangeTracker(state_path)
        assert tracker.skip_reasons() == {}

    def test_reason_dropped_when_path_leaves_skipped_set(self, tmp_path: Path) -> None:
        # A skipped file that later indexes successfully loses its reason.
        state_path = tmp_path / "state.json"
        md = tmp_path / "skip.md"
        md.write_text("---\ntitle: x\n---\nbody", encoding="utf-8")
        tracker = ChangeTracker(state_path)
        tracker.update_state(
            [],
            skipped={"skip.md": self._hash_of(md)},
            skip_reasons={
                "skip.md": {
                    "category": "missing_frontmatter",
                    "detail": "missing: ['title']",
                }
            },
        )
        # Now the same path indexes successfully (present in notes).
        from markdown_vault_mcp.types import ParsedNote

        note = ParsedNote(
            path="skip.md",
            frontmatter={"title": "x"},
            title="x",
            chunks=[],
            content_hash=self._hash_of(md),
            modified_at=0.0,
        )
        tracker.update_state([note])
        assert tracker.skip_reasons() == {}

    def test_reason_carried_forward_for_unchanged_skip(self, tmp_path: Path) -> None:
        # An unchanged skipped file keeps its reason across a reindex cycle
        # even though update_state is not re-passed the reason.
        state_path = tmp_path / "state.json"
        md = tmp_path / "skip.md"
        md.write_text("bad yaml", encoding="utf-8")
        tracker = ChangeTracker(state_path)
        tracker.update_state(
            [],
            skipped={"skip.md": self._hash_of(md)},
            skip_reasons={"skip.md": {"category": "parse_error", "detail": "boom"}},
        )
        # A fresh scan sees the file unchanged -> carry -> next update_state
        # (with no skip_reasons passed) must preserve the reason.
        tracker.detect_changes(tmp_path)
        tracker.update_state([])
        assert tracker.skip_reasons() == {
            "skip.md": {"category": "parse_error", "detail": "boom"},
        }


class TestSkipReasonsSanitization:
    """Malformed persisted skip_reasons entries are dropped, never raise (#775)."""

    def test_non_dict_value_is_dropped(self, tmp_path: Path, caplog) -> None:
        import logging

        state_path = tmp_path / "state.json"
        # A version-2 state file whose skip_reasons value is a bare string,
        # e.g. from format-version skew or a manual edit.
        state_path.write_text(
            '{"version": 2, "indexed": {}, "skipped": {"bad.md": "abc"}, '
            '"skip_reasons": {"bad.md": "not-a-dict"}}',
            encoding="utf-8",
        )
        tracker = ChangeTracker(state_path)
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.tracker"):
            result = tracker.skip_reasons()
        assert result == {}
        assert any("skip_reason_malformed" in r.message for r in caplog.records)

    def test_dict_missing_detail_key_is_dropped(self, tmp_path: Path) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(
            '{"version": 2, "indexed": {}, "skipped": {"bad.md": "abc"}, '
            '"skip_reasons": {"bad.md": {"category": "parse_error"}}}',
            encoding="utf-8",
        )
        tracker = ChangeTracker(state_path)
        assert tracker.skip_reasons() == {}

    def test_wellformed_entry_survives_sanitization(self, tmp_path: Path) -> None:
        state_path = tmp_path / "state.json"
        state_path.write_text(
            '{"version": 2, "indexed": {}, "skipped": {"bad.md": "abc"}, '
            '"skip_reasons": {"bad.md": {"category": "parse_error", "detail": "boom"}}}',
            encoding="utf-8",
        )
        tracker = ChangeTracker(state_path)
        assert tracker.skip_reasons() == {
            "bad.md": {"category": "parse_error", "detail": "boom"},
        }
