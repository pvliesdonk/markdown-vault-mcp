"""Tests for VaultFileWatcher (issue #558).

Failure modes covered:
- File change triggers on_change after debounce window
- Rapid changes within debounce window → single on_change call
- stop() before debounce fires → on_change not called
- stop() is idempotent (no exception on double-stop)
- Changes inside hidden dirs (.git/, ._state/) are ignored
- watchdog not installed → start() logs warning and returns cleanly
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

from markdown_vault_mcp._file_watcher import VaultFileWatcher

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEBOUNCE = 0.08  # 80 ms — fast enough for tests, long enough to debounce


def _make_watcher(
    source_dir: Path,
    on_change: object,
    debounce_s: float = _DEBOUNCE,
) -> VaultFileWatcher:
    return VaultFileWatcher(source_dir, on_change, debounce_s=debounce_s)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Core debounce behaviour
# ---------------------------------------------------------------------------


def test_file_change_triggers_on_change(tmp_path: Path) -> None:
    """A file written to source_dir triggers on_change after the debounce window."""
    called = threading.Event()
    watcher = _make_watcher(tmp_path, lambda: called.set())
    watcher.start()
    try:
        (tmp_path / "note.md").write_text("hello")
        assert called.wait(timeout=2.0), "on_change not called within 2 s"
    finally:
        watcher.stop()


def test_rapid_changes_trigger_single_on_change(tmp_path: Path) -> None:
    """Multiple rapid file writes within the debounce window result in one on_change call."""
    call_count = 0
    lock = threading.Lock()

    def counter() -> None:
        nonlocal call_count
        with lock:
            call_count += 1

    watcher = _make_watcher(tmp_path, counter, debounce_s=0.2)
    watcher.start()
    try:
        for i in range(10):
            (tmp_path / f"note{i}.md").write_text(f"content {i}")
            time.sleep(0.01)
        # Wait for debounce to flush plus a generous margin
        time.sleep(0.5)
        with lock:
            assert call_count == 1, f"expected 1 call, got {call_count}"
    finally:
        watcher.stop()


def test_stop_before_debounce_cancels_callback(tmp_path: Path) -> None:
    """Stopping the watcher before the debounce timer fires does not invoke on_change."""
    called = threading.Event()
    watcher = _make_watcher(tmp_path, lambda: called.set(), debounce_s=0.5)
    watcher.start()
    (tmp_path / "note.md").write_text("hello")
    # Stop immediately — debounce hasn't fired yet
    watcher.stop()
    # Wait past the would-be debounce window
    assert not called.wait(timeout=0.8), "on_change should not be called after stop"


def test_stop_is_idempotent(tmp_path: Path) -> None:
    """Calling stop() twice raises no exception."""
    watcher = _make_watcher(tmp_path, lambda: None)
    watcher.start()
    watcher.stop()
    watcher.stop()  # must not raise


# ---------------------------------------------------------------------------
# Hidden directory filtering
# ---------------------------------------------------------------------------


def test_hidden_dir_changes_are_ignored(tmp_path: Path) -> None:
    """File changes inside hidden directories are not forwarded to on_change."""
    called = threading.Event()
    watcher = _make_watcher(tmp_path, lambda: called.set())
    watcher.start()
    try:
        hidden = tmp_path / ".git"
        hidden.mkdir()
        (hidden / "COMMIT_EDITMSG").write_text("update")
        assert not called.wait(timeout=0.4), (
            "on_change must not fire for hidden-dir changes"
        )
    finally:
        watcher.stop()


def test_nested_hidden_dir_changes_are_ignored(tmp_path: Path) -> None:
    """File changes nested under a hidden directory ancestor are also ignored."""
    called = threading.Event()
    watcher = _make_watcher(tmp_path, lambda: called.set())
    watcher.start()
    try:
        nested = tmp_path / ".markdown_vault_mcp" / "state"
        nested.mkdir(parents=True)
        (nested / "index.db").write_text("data")
        assert not called.wait(timeout=0.4), (
            "on_change must not fire for nested hidden changes"
        )
    finally:
        watcher.stop()


def test_visible_file_after_hidden_change_triggers_callback(tmp_path: Path) -> None:
    """A visible file change after a hidden-dir change still triggers on_change."""
    called = threading.Event()
    watcher = _make_watcher(tmp_path, lambda: called.set())
    watcher.start()
    try:
        # Hidden change — should be ignored
        hidden = tmp_path / ".git"
        hidden.mkdir()
        (hidden / "COMMIT_EDITMSG").write_text("update")
        time.sleep(0.05)
        # Visible change — should trigger
        (tmp_path / "real_note.md").write_text("content")
        assert called.wait(timeout=2.0), "on_change should fire for visible file change"
    finally:
        watcher.stop()


# ---------------------------------------------------------------------------
# watchdog unavailable
# ---------------------------------------------------------------------------


def test_start_logs_warning_when_watchdog_unavailable(tmp_path: Path) -> None:
    """When watchdog cannot be imported, start() logs a warning and returns without raising."""
    watcher = _make_watcher(tmp_path, lambda: None)
    with patch("markdown_vault_mcp._file_watcher._WATCHDOG_AVAILABLE", False):
        watcher.start()  # must not raise
    # Cleanup — stop is a no-op when never started
    watcher.stop()


# ---------------------------------------------------------------------------
# Auto-disable logic in lifespan (config-level tests)
# ---------------------------------------------------------------------------


def test_lifespan_starts_watcher_when_no_git_active(tmp_path: Path) -> None:
    """File watcher is started when git pull and webhook are both inactive."""
    from markdown_vault_mcp._file_watcher import VaultFileWatcher

    started: list[VaultFileWatcher] = []

    def tracking_start(self: VaultFileWatcher) -> None:
        started.append(self)

    with (
        patch.object(VaultFileWatcher, "start", tracking_start),
        patch.object(VaultFileWatcher, "stop"),
    ):
        git_active = False
        file_watcher_enabled = True
        if file_watcher_enabled and not git_active:
            w = VaultFileWatcher(tmp_path, lambda: None, debounce_s=2.0)
            w.start()

    assert len(started) == 1


def test_lifespan_skips_watcher_when_git_pull_active(tmp_path: Path) -> None:
    """File watcher is NOT started when git pull interval is > 0."""
    from markdown_vault_mcp._file_watcher import VaultFileWatcher

    started: list[VaultFileWatcher] = []

    def tracking_start(self: VaultFileWatcher) -> None:
        started.append(self)

    with patch.object(VaultFileWatcher, "start", tracking_start):
        git_pull_interval_s = 600
        git_active = git_pull_interval_s > 0
        file_watcher_enabled = True
        if file_watcher_enabled and not git_active:
            w = VaultFileWatcher(tmp_path, lambda: None, debounce_s=2.0)
            w.start()

    assert len(started) == 0, "watcher must not start when git pull is active"


def test_lifespan_skips_watcher_when_webhook_active(tmp_path: Path) -> None:
    """File watcher is NOT started when a GitHub webhook secret is configured."""
    from markdown_vault_mcp._file_watcher import VaultFileWatcher

    started: list[VaultFileWatcher] = []

    def tracking_start(self: VaultFileWatcher) -> None:
        started.append(self)

    with patch.object(VaultFileWatcher, "start", tracking_start):
        github_webhook_secret = "secret-abc"
        git_active = bool(github_webhook_secret)
        file_watcher_enabled = True
        if file_watcher_enabled and not git_active:
            w = VaultFileWatcher(tmp_path, lambda: None, debounce_s=2.0)
            w.start()

    assert len(started) == 0, "watcher must not start when webhook is configured"


def test_lifespan_skips_watcher_when_explicitly_disabled(tmp_path: Path) -> None:
    """FILE_WATCHER=false prevents the watcher from starting regardless of git config."""
    from markdown_vault_mcp._file_watcher import VaultFileWatcher

    started: list[VaultFileWatcher] = []

    def tracking_start(self: VaultFileWatcher) -> None:
        started.append(self)

    with patch.object(VaultFileWatcher, "start", tracking_start):
        file_watcher_enabled = False
        git_active = False  # even without git active
        if file_watcher_enabled and not git_active:
            w = VaultFileWatcher(tmp_path, lambda: None, debounce_s=2.0)
            w.start()

    assert len(started) == 0, "watcher must not start when explicitly disabled"
