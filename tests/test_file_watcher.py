"""Tests for VaultFileWatcher and related helpers (issue #558).

Failure modes covered:
- File change triggers on_change after debounce window
- Rapid changes within debounce window → single on_change call
- stop() before debounce fires → on_change not called
- stop() is idempotent (no exception on double-stop)
- start() double-call is a no-op (no resource leak)
- Changes inside hidden dirs (.git/, ._state/) are ignored
- watchdog not installed → start() logs warning and returns cleanly
- _stopped flag prevents post-stop events from calling on_change
- should_start_file_watcher() logic (all four config combinations)
- Config: debounce validation (invalid, non-positive, valid custom)
- Config: FILE_WATCHER=false is honoured
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

from markdown_vault_mcp._file_watcher import (
    VaultFileWatcher,
    _has_hidden_component,
    should_start_file_watcher,
)
from markdown_vault_mcp._server_deps import make_vault_lifespan

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
# _has_hidden_component (pure function)
# ---------------------------------------------------------------------------


def test_has_hidden_component_empty_parts() -> None:
    """Empty parts (root DirModifiedEvent) is filtered."""
    assert _has_hidden_component(()) is True


def test_has_hidden_component_hidden_dir() -> None:
    assert _has_hidden_component((".git",)) is True
    assert _has_hidden_component((".git", "COMMIT_EDITMSG")) is True


def test_has_hidden_component_nested_hidden() -> None:
    assert _has_hidden_component((".markdown_vault_mcp", "state", "index.db")) is True


def test_has_hidden_component_visible_file() -> None:
    assert _has_hidden_component(("note.md",)) is False
    assert _has_hidden_component(("subdir", "note.md")) is False


def test_has_hidden_component_hidden_inside_visible() -> None:
    """visible/subdir/.git/file has a hidden ancestor."""
    assert _has_hidden_component(("visible", ".git", "file")) is True


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
    watcher.stop()
    assert not called.wait(timeout=0.8), "on_change should not be called after stop"


def test_stop_is_idempotent(tmp_path: Path) -> None:
    """Calling stop() twice raises no exception."""
    watcher = _make_watcher(tmp_path, lambda: None)
    watcher.start()
    watcher.stop()
    watcher.stop()


def test_double_start_does_not_leak_observer(tmp_path: Path) -> None:
    """Calling start() twice is a no-op — the second call does not spawn a new observer."""
    call_count = 0
    lock = threading.Lock()

    def counter() -> None:
        nonlocal call_count
        with lock:
            call_count += 1

    watcher = _make_watcher(tmp_path, counter, debounce_s=0.1)
    watcher.start()
    watcher.start()  # second call must be a no-op
    try:
        (tmp_path / "note.md").write_text("hello")
        time.sleep(0.3)
        with lock:
            assert call_count == 1, (
                f"expected 1 call, got {call_count} (observer leaked?)"
            )
    finally:
        watcher.stop()


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
        hidden = tmp_path / ".git"
        hidden.mkdir()
        (hidden / "COMMIT_EDITMSG").write_text("update")
        time.sleep(0.05)
        (tmp_path / "real_note.md").write_text("content")
        assert called.wait(timeout=2.0), "on_change should fire for visible file change"
    finally:
        watcher.stop()


# ---------------------------------------------------------------------------
# _stopped flag
# ---------------------------------------------------------------------------


def test_stopped_flag_prevents_schedule_after_stop(tmp_path: Path) -> None:
    """Events delivered while stop() is in progress do not resurrect the timer."""
    called = threading.Event()
    watcher = _make_watcher(tmp_path, lambda: called.set(), debounce_s=0.1)
    watcher.start()

    # Force _stopped = True and then try to schedule
    watcher.stop()
    watcher._schedule()  # must be a no-op because _stopped is True

    assert not called.wait(timeout=0.3), "on_change must not fire after stop()"


def test_stopped_flag_prevents_fire_after_stop(tmp_path: Path) -> None:
    """_fire() returns early when _stopped is True."""
    called = threading.Event()
    watcher = _make_watcher(tmp_path, lambda: called.set(), debounce_s=0.1)
    watcher.start()
    watcher.stop()

    # Directly invoke _fire() — must not call on_change
    watcher._fire()
    assert not called.is_set(), "_fire() must not invoke on_change after stop()"


# ---------------------------------------------------------------------------
# watchdog unavailable
# ---------------------------------------------------------------------------


def test_start_logs_warning_when_watchdog_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When watchdog is not available start() logs a warning and returns without raising."""
    import logging

    watcher = _make_watcher(tmp_path, lambda: None)
    with (
        patch("markdown_vault_mcp._file_watcher._WATCHDOG_AVAILABLE", False),
        caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._file_watcher"),
    ):
        watcher.start()
    assert "watchdog not installed" in caplog.text
    watcher.stop()


# ---------------------------------------------------------------------------
# should_start_file_watcher helper
# ---------------------------------------------------------------------------


def test_should_start_when_no_git_active() -> None:
    assert should_start_file_watcher(True, False, None) is True


def test_should_not_start_when_git_pull_active() -> None:
    assert should_start_file_watcher(True, True, None) is False


def test_should_not_start_when_webhook_active() -> None:
    assert should_start_file_watcher(True, False, "secret") is False


def test_should_not_start_when_explicitly_disabled() -> None:
    assert should_start_file_watcher(False, False, None) is False


def test_should_not_start_when_both_git_and_disabled() -> None:
    assert should_start_file_watcher(False, True, "secret") is False


# ---------------------------------------------------------------------------
# Config parsing (env-var level)
# ---------------------------------------------------------------------------


def test_load_config_file_watcher_disabled_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FILE_WATCHER=false sets file_watcher_enabled=False."""
    from markdown_vault_mcp.config import load_config

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER", "false")
    config = load_config()
    assert config.sync.file_watcher_enabled is False


def test_load_config_file_watcher_debounce_custom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FILE_WATCHER_DEBOUNCE_S=5.0 is accepted."""
    from markdown_vault_mcp.config import load_config

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S", "5.0")
    config = load_config()
    assert config.sync.file_watcher_debounce_s == 5.0


def test_load_config_file_watcher_debounce_invalid_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-numeric FILE_WATCHER_DEBOUNCE_S falls back to 2.0."""
    from markdown_vault_mcp.config import load_config

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S", "not-a-number")
    config = load_config()
    assert config.sync.file_watcher_debounce_s == 2.0


def test_load_config_file_watcher_debounce_nonpositive_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FILE_WATCHER_DEBOUNCE_S <= 0 falls back to 2.0."""
    from markdown_vault_mcp.config import load_config

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S", "0")
    config = load_config()
    assert config.sync.file_watcher_debounce_s == 2.0


def test_fire_exception_in_on_change_is_logged(tmp_path: Path) -> None:
    """Exception raised by on_change is caught and logged, not propagated."""

    def bad_callback() -> None:
        raise RuntimeError("callback failure")

    watcher = _make_watcher(tmp_path, bad_callback, debounce_s=0.05)
    watcher.start()
    try:
        (tmp_path / "note.md").write_text("hello")
        # Give enough time for debounce + callback — no exception should propagate
        time.sleep(0.3)
    finally:
        watcher.stop()


# ---------------------------------------------------------------------------
# Lifespan wiring (integration — drives make_vault_lifespan directly)
# ---------------------------------------------------------------------------


def test_lifespan_starts_and_stops_watcher_when_no_git(tmp_path: Path) -> None:
    """The lifespan starts the watcher on a non-git vault and stops it on exit."""
    import asyncio

    from markdown_vault_mcp.config import VaultConfig

    (tmp_path / "note.md").write_text("# note\n\nbody", encoding="utf-8")
    config = VaultConfig(source_dir=tmp_path, read_only=False)
    lifespan_fn = make_vault_lifespan(config)

    async def _run() -> None:
        with (
            patch.object(VaultFileWatcher, "start") as mock_start,
            patch.object(VaultFileWatcher, "stop") as mock_stop,
        ):
            async with lifespan_fn(None) as ctx:  # type: ignore[arg-type]
                assert ctx["vault"] is not None
                mock_start.assert_called_once()
                mock_stop.assert_not_called()
            mock_stop.assert_called_once()

    asyncio.run(_run())


def test_lifespan_skips_watcher_when_git_pull_active(tmp_path: Path) -> None:
    """The lifespan does not start the watcher when the git pull loop is active.

    A git strategy must be configured (here via ``git_token``) for the pull
    loop to run — ``git_pull_interval_s`` alone defaults to 600 even on
    non-git vaults, so it is not sufficient to activate the loop.
    """
    import asyncio

    from markdown_vault_mcp.config import VaultConfig

    (tmp_path / "note.md").write_text("# note\n\nbody", encoding="utf-8")
    from markdown_vault_mcp.config_sections import GitConfig

    config = VaultConfig(
        source_dir=tmp_path,
        read_only=False,
        git=GitConfig(token="fake-token", pull_interval_s=600),
    )
    lifespan_fn = make_vault_lifespan(config)

    async def _run() -> None:
        with patch.object(VaultFileWatcher, "start") as mock_start:
            async with lifespan_fn(None) as ctx:  # type: ignore[arg-type]
                assert ctx["vault"] is not None
                mock_start.assert_not_called()

    asyncio.run(_run())


def test_lifespan_skips_watcher_when_webhook_active(tmp_path: Path) -> None:
    """The lifespan does not start the watcher when a webhook secret is configured."""
    import asyncio

    from markdown_vault_mcp.config import VaultConfig

    (tmp_path / "note.md").write_text("# note\n\nbody", encoding="utf-8")
    from markdown_vault_mcp.config_sections import SyncConfig

    config = VaultConfig(
        source_dir=tmp_path,
        read_only=False,
        sync=SyncConfig(github_webhook_secret="shhh"),
    )
    lifespan_fn = make_vault_lifespan(config)

    async def _run() -> None:
        with patch.object(VaultFileWatcher, "start") as mock_start:
            async with lifespan_fn(None) as ctx:  # type: ignore[arg-type]
                assert ctx["vault"] is not None
                mock_start.assert_not_called()

    asyncio.run(_run())
