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
from pathlib import Path
from unittest.mock import patch

import pytest
from watchdog.events import (
    DirModifiedEvent,
    FileClosedEvent,
    FileClosedNoWriteEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
)

from markdown_vault_mcp._file_watcher import (
    VaultFileWatcher,
    _derive_watch_roots,
    _has_hidden_component,
    _is_hidden_relative_to_root,
    _VaultEventHandler,
    _WatchRoot,
    should_start_file_watcher,
)
from markdown_vault_mcp._server_deps import make_vault_lifespan

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
# Event-type filtering (#720 — read events must not re-trigger reindex)
# ---------------------------------------------------------------------------


def _record_handler(source_dir: Path) -> tuple[_VaultEventHandler, list[int]]:
    """Return a handler whose schedule appends to a call-log list."""
    calls: list[int] = []
    handler = _VaultEventHandler(lambda: calls.append(1), source_dir)
    return handler, calls


def test_read_only_events_do_not_schedule(tmp_path: Path) -> None:
    """OPEN / CLOSE_NOWRITE / CLOSE events from the reindex walk are ignored.

    These are exactly the inotify events the read-only vault walk inside
    ``reindex()`` emits; forwarding them would re-arm the watcher and spin
    the reindex self-feedback loop (#720).
    """
    handler, calls = _record_handler(tmp_path)
    visible = str(tmp_path / "note.md")
    for event in (
        FileOpenedEvent(visible),
        FileClosedNoWriteEvent(visible),
        FileClosedEvent(visible),
    ):
        handler.on_any_event(event)
    assert calls == [], "read-only events must not schedule a reindex"


def test_mutating_events_schedule(tmp_path: Path) -> None:
    """A real create/modify/move/delete on a visible path triggers a reindex."""
    visible = str(tmp_path / "note.md")
    for event in (
        FileCreatedEvent(visible),
        FileModifiedEvent(visible),
        FileDeletedEvent(visible),
        FileMovedEvent(visible, str(tmp_path / "renamed.md")),
        DirModifiedEvent(str(tmp_path / "subdir")),
    ):
        handler, calls = _record_handler(tmp_path)
        handler.on_any_event(event)
        assert calls == [1], f"{type(event).__name__} should schedule a reindex"


def test_mutating_event_in_hidden_dir_still_ignored(tmp_path: Path) -> None:
    """The hidden-path filter still applies to mutating events."""
    handler, calls = _record_handler(tmp_path)
    handler.on_any_event(FileModifiedEvent(str(tmp_path / ".git" / "HEAD")))
    assert calls == [], "mutations inside hidden dirs must not schedule a reindex"


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


def test_start_skips_root_whose_schedule_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A single watch root that fails to schedule is logged and skipped.

    A symlink-farm child whose recursive schedule raises must not abort
    ``start()``; the remaining roots, notably the ``source_dir`` floor, are
    still scheduled. Simulated by a fake observer whose first ``schedule`` call
    raises and whose later calls succeed.
    """
    import logging

    (tmp_path / "childA").mkdir()
    (tmp_path / "childB").mkdir()

    class _FlakyObserver:
        def __init__(self) -> None:
            self.scheduled: list[str] = []
            self._calls = 0

        def schedule(self, _handler: object, path: str, **_kwargs: object) -> None:
            self._calls += 1
            if self._calls == 1:
                raise OSError("cannot watch this root")
            self.scheduled.append(path)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            pass

    fake = _FlakyObserver()
    watcher = _make_watcher(tmp_path, lambda: None)
    with (
        patch("markdown_vault_mcp._file_watcher.Observer", lambda: fake),
        caplog.at_level(logging.WARNING, logger="markdown_vault_mcp"),
    ):
        watcher.start()  # must not raise despite the first schedule failing
        watcher.stop()

    assert any("could not schedule watch" in r.getMessage() for r in caplog.records)
    # The floor + the two children is three roots; one raised, so two scheduled.
    assert len(fake.scheduled) == 2


def test_start_logs_error_when_all_roots_fail_to_schedule(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A watcher that ends up watching nothing is surfaced at ERROR (#835).

    If every derived root fails to schedule, the observer runs but watches
    nothing, so no edit ever triggers a reindex. That degraded state must be
    visible above the per-root WARNINGs and the INFO summary — distinct from the
    legitimate "no roots at all" case.
    """
    import logging

    (tmp_path / "childA").mkdir()

    class _AllFailObserver:
        def schedule(self, _handler: object, _path: str, **_kwargs: object) -> None:
            raise OSError("cannot watch anything")

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            pass

    fake = _AllFailObserver()
    watcher = _make_watcher(tmp_path, lambda: None)
    with (
        patch("markdown_vault_mcp._file_watcher.Observer", lambda: fake),
        caplog.at_level(logging.ERROR, logger="markdown_vault_mcp._file_watcher"),
    ):
        watcher.start()  # must not raise
        watcher.stop()

    assert any(
        r.levelno == logging.ERROR
        and "live" in r.getMessage()
        and "change detection is disabled" in r.getMessage()
        for r in caplog.records
    ), "a fully-blind watcher must be surfaced at ERROR"


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


def test_from_env_file_watcher_disabled_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FILE_WATCHER=false sets file_watcher_enabled=False."""
    from markdown_vault_mcp.config import ProjectConfig

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER", "false")
    config = ProjectConfig.from_env()
    assert config.sync.file_watcher_enabled is False


def test_from_env_root_floor_disabled_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FILE_WATCHER_ROOT_FLOOR=false sets file_watcher_root_floor=False."""
    from markdown_vault_mcp.config import ProjectConfig

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_ROOT_FLOOR", "false")
    config = ProjectConfig.from_env()
    assert config.sync.file_watcher_root_floor is False


def test_from_env_root_floor_defaults_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unset FILE_WATCHER_ROOT_FLOOR defaults to True."""
    from markdown_vault_mcp.config import ProjectConfig

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_ROOT_FLOOR", raising=False)
    config = ProjectConfig.from_env()
    assert config.sync.file_watcher_root_floor is True


def test_from_env_file_watcher_debounce_custom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FILE_WATCHER_DEBOUNCE_S=5.0 is accepted."""
    from markdown_vault_mcp.config import ProjectConfig

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S", "5.0")
    config = ProjectConfig.from_env()
    assert config.sync.file_watcher_debounce_s == 5.0


def test_from_env_file_watcher_debounce_invalid_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-numeric FILE_WATCHER_DEBOUNCE_S raises (no warn-and-default; #638)."""
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import ConfigurationError

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S", "not-a-number")
    with pytest.raises(ConfigurationError):
        ProjectConfig.from_env()


def test_from_env_file_watcher_debounce_nonpositive_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FILE_WATCHER_DEBOUNCE_S <= 0 raises (must be > 0; #638)."""
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import ConfigurationError

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S", "0")
    with pytest.raises(ConfigurationError, match="file_watcher_debounce_s"):
        ProjectConfig.from_env()


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

    from markdown_vault_mcp.config import ProjectConfig

    (tmp_path / "note.md").write_text("# note\n\nbody", encoding="utf-8")
    config = ProjectConfig(source_dir=tmp_path, read_only=False)
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


def test_lifespan_passes_configured_internal_dirs_to_watcher(tmp_path: Path) -> None:
    """Explicit state/index/embeddings paths flow into the watcher's internal_dirs.

    Covers the non-``None`` branches of the ``internal_dirs`` construction in
    ``make_vault_lifespan`` (custom ``state_path`` fallback plus the
    ``index_path``/``embeddings_path`` parent-append loop, #830): each configured
    directory, and ``.git``, must be protected from watching so the watcher can
    never observe the writes reindex makes into them.
    """
    import asyncio

    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.config_sections import IndexingConfig

    (tmp_path / "note.md").write_text("# note\n\nbody", encoding="utf-8")
    state_dir = tmp_path / "state_area"
    index_dir = tmp_path / "index_area"
    emb_dir = tmp_path / "emb_area"
    for d in (state_dir, index_dir, emb_dir):
        d.mkdir()

    config = ProjectConfig(
        source_dir=tmp_path,
        read_only=False,
        indexing=IndexingConfig(
            state_path=state_dir / "state.json",
            index_path=index_dir / "index.db",
            embeddings_path=emb_dir / "embeddings",
        ),
    )
    lifespan_fn = make_vault_lifespan(config)

    captured: dict[str, object] = {}
    real_init = VaultFileWatcher.__init__

    def spy_init(self: VaultFileWatcher, *args: object, **kwargs: object) -> None:
        captured["internal_dirs"] = kwargs.get("internal_dirs")
        real_init(self, *args, **kwargs)  # type: ignore[arg-type]

    async def _run() -> None:
        with (
            patch.object(VaultFileWatcher, "__init__", spy_init),
            patch.object(VaultFileWatcher, "start"),
            patch.object(VaultFileWatcher, "stop"),
        ):
            async with lifespan_fn(None):  # type: ignore[arg-type]
                pass

    asyncio.run(_run())

    internal = set(captured["internal_dirs"])  # type: ignore[arg-type]
    assert state_dir in internal
    assert index_dir in internal
    assert emb_dir in internal
    assert tmp_path / ".git" in internal


def test_lifespan_skips_watcher_when_git_pull_active(tmp_path: Path) -> None:
    """The lifespan does not start the watcher when the git pull loop is active.

    A git strategy must be configured (here via ``git_token``) for the pull
    loop to run — ``git_pull_interval_s`` alone defaults to 600 even on
    non-git vaults, so it is not sufficient to activate the loop.
    """
    import asyncio

    from markdown_vault_mcp.config import ProjectConfig

    (tmp_path / "note.md").write_text("# note\n\nbody", encoding="utf-8")
    from markdown_vault_mcp.config_sections import GitConfig

    config = ProjectConfig(
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


# ---------------------------------------------------------------------------
# _derive_watch_roots (scoped watch-root computation)
# ---------------------------------------------------------------------------


def _root_names(roots: list[_WatchRoot]) -> set[str]:
    """Return the basenames of the recursive roots in *roots*."""
    return {root.path.name for root in roots if root.recursive}


def test_derive_watch_roots_prunes_anchored_top_level_dir(tmp_path: Path) -> None:
    """An anchored ``PREFIX/**`` exclude prunes that top-level dir's watch."""
    (tmp_path / "go").mkdir()
    (tmp_path / "notes").mkdir()
    roots = _derive_watch_roots(tmp_path, ["go/**"])
    names = _root_names(roots)
    assert "go" not in names, "an anchored-excluded top-level dir must not be watched"
    assert "notes" in names, "a non-excluded top-level dir must be watched recursively"


def test_derive_watch_roots_keeps_top_level_anydepth_name(tmp_path: Path) -> None:
    """A ``**/NAME/**`` exclude does NOT prune a *top-level* dir named NAME.

    ``_should_prune_dir`` (the reused source of truth) only prunes ``NAME`` when
    it is a non-leading component, because ``**/node_modules/**`` does not match
    root-level ``node_modules/file.md``. A top-level ``node_modules`` therefore
    still gets a recursive watch; locking this in guards the shared semantics.
    """
    (tmp_path / "node_modules").mkdir()
    roots = _derive_watch_roots(tmp_path, ["**/node_modules/**"])
    assert "node_modules" in _root_names(roots)


def test_derive_watch_roots_keeps_dot_root_with_scoped_exclude(tmp_path: Path) -> None:
    """A dot-root like ``.claude`` is watched recursively when only a subtree is
    excluded (``.claude/plugins/**`` prunes ``.claude/plugins`` but not
    ``.claude`` itself)."""
    (tmp_path / ".claude").mkdir()
    roots = _derive_watch_roots(tmp_path, [".claude/plugins/**"])
    assert ".claude" in _root_names(roots)


def test_derive_watch_roots_always_appends_non_recursive_source_dir(
    tmp_path: Path,
) -> None:
    """The non-recursive ``source_dir`` root is always present and last."""
    (tmp_path / "notes").mkdir()
    roots = _derive_watch_roots(tmp_path, ["go/**"])
    assert roots[-1] == _WatchRoot(tmp_path, recursive=False)
    assert sum(1 for r in roots if not r.recursive) == 1


def test_derive_watch_roots_skips_plain_files(tmp_path: Path) -> None:
    """A plain (non-directory) child does not become a watch root."""
    (tmp_path / "top.md").write_text("x")
    (tmp_path / "notes").mkdir()
    roots = _derive_watch_roots(tmp_path, None)
    assert all(r.path.name != "top.md" for r in roots)
    assert _root_names(roots) == {"notes"}


def test_derive_watch_roots_falls_back_on_enumeration_error(tmp_path: Path) -> None:
    """An unreadable/vanished source_dir yields just the non-recursive root."""
    missing = tmp_path / "gone"
    roots = _derive_watch_roots(missing, None)
    assert roots == [_WatchRoot(missing, recursive=False)]


def test_derive_watch_roots_skips_internal_state_and_git_dirs(tmp_path: Path) -> None:
    """The vault's own state dir and ``.git`` are never watch roots (#830).

    With no ``exclude_patterns`` the scoped-watch change would otherwise promote
    every top-level dir — including the default state dir ``.markdown_vault_mcp``
    (which ``reindex`` writes ``state.json`` into on every run) and ``.git`` — to
    a recursive watch root, closing a self-feedback reindex loop. A deliberately
    watched *user* dot-root like ``.claude`` must still be watched; only the
    vault's internal write targets are skipped.
    """
    (tmp_path / ".markdown_vault_mcp").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude").mkdir()
    roots = _derive_watch_roots(
        tmp_path,
        None,
        internal_dirs=[tmp_path / ".markdown_vault_mcp", tmp_path / ".git"],
    )
    names = _root_names(roots)
    assert ".markdown_vault_mcp" not in names, "state dir must not be a watch root"
    assert ".git" not in names, ".git must not be a watch root"
    assert ".claude" in names, "a user dot-root must still be watched"


def test_derive_watch_roots_skips_top_level_dir_containing_internal_dir(
    tmp_path: Path,
) -> None:
    """A top-level dir that *contains* an internal write dir is not watched.

    Guards against reintroducing the loop when the state dir is configured
    beneath a top-level directory: watching that directory recursively would
    still deliver the state-file writes. The whole subtree is skipped so the
    loop can never form (a limitation documented for nested state paths).
    """
    state_dir = tmp_path / "data" / "state"
    state_dir.mkdir(parents=True)
    roots = _derive_watch_roots(tmp_path, None, internal_dirs=[state_dir])
    assert "data" not in _root_names(roots)


def test_start_does_not_schedule_watch_on_internal_state_dir(tmp_path: Path) -> None:
    """``start()`` forwards ``internal_dirs`` so the state dir is never scheduled.

    Exercises the wiring, not just ``_derive_watch_roots``: a pre-existing state
    dir (present before the watcher starts, as at lifespan startup) must not get
    a recursive watch, while a sibling content dir must (#830).
    """
    (tmp_path / ".markdown_vault_mcp").mkdir()
    (tmp_path / "notes").mkdir()

    class _RecordingObserver:
        def __init__(self) -> None:
            self.scheduled: list[str] = []

        def schedule(self, _handler: object, path: str, **_kwargs: object) -> None:
            self.scheduled.append(path)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            pass

    fake = _RecordingObserver()
    watcher = VaultFileWatcher(
        tmp_path,
        lambda: None,  # type: ignore[arg-type]
        internal_dirs=[tmp_path / ".markdown_vault_mcp"],
    )
    with patch("markdown_vault_mcp._file_watcher.Observer", lambda: fake):
        watcher.start()
        watcher.stop()

    assert str(tmp_path / ".markdown_vault_mcp") not in fake.scheduled
    assert str(tmp_path / "notes") in fake.scheduled


def test_derive_watch_roots_root_floor_false_omits_non_recursive_root(
    tmp_path: Path,
) -> None:
    """root_floor=False drops the non-recursive floor but keeps recursive roots.

    The recursive roots derived from subdirs are identical to the root_floor=True
    result; only the non-recursive ``source_dir`` floor is removed.
    """
    (tmp_path / "notes").mkdir()
    with_floor = _derive_watch_roots(tmp_path, None)
    without_floor = _derive_watch_roots(tmp_path, None, root_floor=False)
    assert all(r.recursive for r in without_floor), "no non-recursive root expected"
    assert _WatchRoot(tmp_path, recursive=False) not in without_floor
    recursive_with = [r for r in with_floor if r.recursive]
    assert without_floor == recursive_with, "recursive roots must be unchanged"


def test_derive_watch_roots_root_floor_false_fallback_is_empty(tmp_path: Path) -> None:
    """root_floor=False on a vanished source_dir yields [] (no floor re-added)."""
    missing = tmp_path / "gone"
    assert _derive_watch_roots(missing, None, root_floor=False) == []


# ---------------------------------------------------------------------------
# _is_hidden_relative_to_root (hiddenness relative to the delivering root)
# ---------------------------------------------------------------------------


def test_is_hidden_relative_to_root_dot_root_delivers_descendant() -> None:
    """A non-dot descendant of a watched dot-root is delivered (not hidden)."""
    assert (
        _is_hidden_relative_to_root(
            "/root/.claude/projects/x/memory/n.md", Path("/root/.claude")
        )
        is False
    )


def test_is_hidden_relative_to_root_dot_root_drops_git_child() -> None:
    """A ``.git`` child below a watched dot-root is still ignored."""
    assert (
        _is_hidden_relative_to_root("/root/.claude/.git/HEAD", Path("/root/.claude"))
        is True
    )


def test_is_hidden_relative_to_root_empty_remainder_is_dropped() -> None:
    """The watched dir itself (empty remainder → DirModified) is ignored."""
    assert _is_hidden_relative_to_root("/root/.claude", Path("/root/.claude")) is True


def test_is_hidden_relative_to_root_outside_root_is_dropped() -> None:
    """A path not under the watch root is ignored (delivered by another root)."""
    assert _is_hidden_relative_to_root("/other/note.md", Path("/root/.claude")) is True


# ---------------------------------------------------------------------------
# Handler bound to a dot-root (the #558 delivery fix)
# ---------------------------------------------------------------------------


def test_handler_on_dot_root_delivers_non_dot_descendant(tmp_path: Path) -> None:
    """A handler scheduled on a dot-root schedules for a non-dot descendant."""
    dot_root = tmp_path / ".claude"
    calls: list[int] = []
    handler = _VaultEventHandler(lambda: calls.append(1), dot_root)
    handler.on_any_event(
        FileModifiedEvent(str(dot_root / "projects" / "x" / "memory" / "n.md"))
    )
    assert calls == [1], "dot-root descendant must schedule a reindex"


def test_handler_on_dot_root_drops_git_child(tmp_path: Path) -> None:
    """A handler on a dot-root does NOT schedule for a ``.git`` child under it."""
    dot_root = tmp_path / ".claude"
    calls: list[int] = []
    handler = _VaultEventHandler(lambda: calls.append(1), dot_root)
    handler.on_any_event(FileModifiedEvent(str(dot_root / ".git" / "HEAD")))
    assert calls == [], ".git below a watched dot-root must not schedule"


# ---------------------------------------------------------------------------
# Preserved source_dir-rooted behavior (old single-watch code path)
# ---------------------------------------------------------------------------


def test_handler_on_source_dir_drops_git_head(tmp_path: Path) -> None:
    """A handler bound to source_dir still drops ``.git/HEAD``."""
    handler, calls = _record_handler(tmp_path)
    handler.on_any_event(FileModifiedEvent(str(tmp_path / ".git" / "HEAD")))
    assert calls == []


def test_handler_on_source_dir_drops_root_dirmodified(tmp_path: Path) -> None:
    """A handler bound to source_dir still drops the root DirModified (empty
    remainder)."""
    handler, calls = _record_handler(tmp_path)
    handler.on_any_event(DirModifiedEvent(str(tmp_path)))
    assert calls == []


def test_handler_on_source_dir_delivers_root_note(tmp_path: Path) -> None:
    """A handler bound to source_dir still delivers a root-level ``note.md``."""
    handler, calls = _record_handler(tmp_path)
    handler.on_any_event(FileModifiedEvent(str(tmp_path / "note.md")))
    assert calls == [1]


# ---------------------------------------------------------------------------
# End-to-end: watched subdir created before start fires on_change
# ---------------------------------------------------------------------------


def test_file_in_watched_subdir_created_before_start_triggers(tmp_path: Path) -> None:
    """A file written into a subdir that existed before start() fires on_change.

    The subdir gets its own recursive watch root at start(), so mutations deep
    inside it are delivered (the core #558 scoped-watch behavior).
    """
    subdir = tmp_path / "notes"
    subdir.mkdir()
    called = threading.Event()
    watcher = _make_watcher(tmp_path, lambda: called.set())
    watcher.start()
    try:
        (subdir / "deep.md").write_text("content")
        assert called.wait(timeout=2.0), "on_change should fire for watched-subdir file"
    finally:
        watcher.stop()


# ---------------------------------------------------------------------------
# Farm layout: all-symlink children do not crash start() (#508)
# ---------------------------------------------------------------------------


def test_symlink_farm_layout_does_not_crash(tmp_path: Path) -> None:
    """A source_dir whose children are symlinks to real dirs starts cleanly.

    Whether watchdog's FSEvents observer follows the links to their targets is
    platform-dependent; the contract here is only that start() does not raise
    and the non-recursive source_dir floor watch is always scheduled.
    """
    targets = tmp_path / "targets"
    targets.mkdir()
    farm = tmp_path / "farm"
    farm.mkdir()
    for name in ("alpha", "beta", "gamma"):
        real = targets / name
        real.mkdir()
        (real / "note.md").write_text("x")
        (farm / name).symlink_to(real, target_is_directory=True)

    # is_dir() follows the links, so each becomes a candidate recursive root.
    roots = _derive_watch_roots(farm, None)
    assert _WatchRoot(farm, recursive=False) in roots, "floor watch must be present"

    watcher = _make_watcher(farm, lambda: None)
    watcher.start()  # must not raise even if a symlinked-child schedule fails
    watcher.stop()


def test_lifespan_skips_watcher_when_webhook_active(tmp_path: Path) -> None:
    """The lifespan does not start the watcher when a webhook secret is configured."""
    import asyncio

    from markdown_vault_mcp.config import ProjectConfig

    (tmp_path / "note.md").write_text("# note\n\nbody", encoding="utf-8")
    from markdown_vault_mcp.config_sections import SyncConfig

    config = ProjectConfig(
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


# ---------------------------------------------------------------------------
# root_floor knob: start() log line observability
# ---------------------------------------------------------------------------


def test_start_log_line_reports_floor_on_by_default(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The default start() INFO line reports floor=on and lists the /(root) floor."""
    import logging

    (tmp_path / "notes").mkdir()
    watcher = _make_watcher(tmp_path, lambda: None)
    with caplog.at_level(logging.INFO, logger="markdown_vault_mcp._file_watcher"):
        watcher.start()
    watcher.stop()
    assert "floor=on" in caplog.text
    assert "/(root)" in caplog.text


def test_start_log_line_reports_floor_off_when_disabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """With root_floor=False the INFO line reports floor=off and no /(root) root."""
    import logging

    (tmp_path / "notes").mkdir()
    watcher = VaultFileWatcher(
        tmp_path, lambda: None, debounce_s=_DEBOUNCE, root_floor=False
    )  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO, logger="markdown_vault_mcp._file_watcher"):
        watcher.start()
    watcher.stop()
    assert "floor=off" in caplog.text
    assert "/(root)" not in caplog.text


# ---------------------------------------------------------------------------
# root_floor knob: end-to-end delivery behavior
# ---------------------------------------------------------------------------


def test_root_floor_false_still_delivers_watched_subdir_file(tmp_path: Path) -> None:
    """With root_floor=False a file in a pre-existing subdir still fires on_change.

    The subdir keeps its own recursive watch; only the source_dir floor is gone.
    """
    subdir = tmp_path / "notes"
    subdir.mkdir()
    called = threading.Event()
    watcher = VaultFileWatcher(
        tmp_path, lambda: called.set(), debounce_s=_DEBOUNCE, root_floor=False
    )  # type: ignore[arg-type]
    watcher.start()
    try:
        (subdir / "note.md").write_text("content")
        assert called.wait(timeout=2.0), "on_change should fire for watched-subdir file"
    finally:
        watcher.stop()


def test_root_floor_false_ignores_root_level_file(tmp_path: Path) -> None:
    """With root_floor=False a root-level note.md does NOT fire on_change.

    No watch is rooted at source_dir, so a direct-child write is not delivered
    (root-level files rely on scans under this knob).
    """
    (tmp_path / "notes").mkdir()
    called = threading.Event()
    watcher = VaultFileWatcher(
        tmp_path, lambda: called.set(), debounce_s=_DEBOUNCE, root_floor=False
    )  # type: ignore[arg-type]
    watcher.start()
    try:
        (tmp_path / "note.md").write_text("content")
        assert not called.wait(timeout=0.4), (
            "on_change must not fire for a root-level file when floor is off"
        )
    finally:
        watcher.stop()
