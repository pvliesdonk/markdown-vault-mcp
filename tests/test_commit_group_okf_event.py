"""A transaction records one editorial event in OKF history."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._identity import Principal, bound_principal
from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.utils.fs import is_regular_file
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from concurrent.futures import Future
    from datetime import date
    from pathlib import Path

    from markdown_vault_mcp._commit_groups import CommitGroup, GroupCommitResult
    from markdown_vault_mcp.types import WriteOperation


@pytest.fixture
def make_vault(git_repo: Path) -> Iterator[Callable[..., Vault]]:
    """Check make vault."""
    (git_repo / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    made: list[tuple[Vault, GitWriteStrategy]] = []

    def create(mode: str = "writes", folders: tuple[str, ...] = ()) -> Vault:
        """Check create."""
        strategy = GitWriteStrategy(
            token=None,
            repo_url=None,
            managed=False,
            enable_pull=False,
            enable_push=False,
            repo_path=git_repo,
        )
        vault = Vault(
            source_dir=git_repo,
            settings=VaultSettings(
                read_only=False,
                okf_mode="on",
                okf_capabilities=ContentConfig(
                    okf_log=mode, okf_folders=folders
                ).okf_capabilities,
            ),
            on_write=strategy,
        )
        made.append((vault, strategy))
        return vault

    yield create
    for vault, strategy in reversed(made):
        vault.close()
        strategy.close()


@pytest.mark.parametrize("mode", ["writes", "events"])
def test_group_records_one_event(
    git_repo: Path, make_vault: Callable[..., Vault], mode: str
) -> None:
    """Check group records one event."""
    vault = make_vault(mode)
    group = vault._write_callback.open_group("Change policy", None)
    with vault._write_callback.bind_group(group.handle, None):
        for n in range(4):
            vault.writer.write(f"note{n}.md", "# Note\n")
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
    log = (git_repo / "log.md").read_text()
    assert log.count("\n- ") == 1
    assert "Change policy" in log
    assert all(f"note{n}.md" in log for n in range(4))
    assert not log.startswith("---")
    assert "log.md" in _run_git(git_repo, "show", "--pretty=", "--name-only", "HEAD")


@pytest.mark.parametrize("mode", ["events", "off", "reserved"])
def test_empty_and_off_groups_do_not_create_log(
    git_repo: Path, make_vault: Callable[..., Vault], mode: str
) -> None:
    """Empty, disabled and reserved-only groups create no log."""
    vault = make_vault("events" if mode == "reserved" else mode)
    group = vault._write_callback.open_group("Change", None)
    if mode in {"off", "reserved"}:
        with vault._write_callback.bind_group(group.handle, None):
            if mode == "reserved":
                vault.writer.write(
                    "index.md", '---\nokf_version: "0.2"\n---\n# Root updated\n'
                )
            else:
                vault.writer.write("a.md", "# A\n")
    result = vault._write_callback.close_group(group.handle, None).result(timeout=10)
    assert result.status == ("no_changes" if mode == "events" else "committed")
    assert not result.history_failed
    assert not is_regular_file(git_repo / "log.md")


def test_group_survives_drain_and_long_description(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check group survives drain and long description."""
    vault = make_vault("events")
    description = "Policy " + "é" * 600
    group = vault._write_callback.open_group(description, None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("first.md", "# First\n")
    assert vault._write_callback.drain(timeout=10)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("second.md", "# Second\n")
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
    log = (git_repo / "log.md").read_text()
    assert description in log and "first.md" in log and "second.md" in log
    assert log.count("\n- ") == 1


def test_no_git_group_records_event(git_repo: Path) -> None:
    """Check no git group records event."""
    (git_repo / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = Vault(
        source_dir=git_repo,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=ContentConfig(okf_log="writes").okf_capabilities,
        ),
    )
    try:
        group = vault._write_callback.open_group("No Git", None)
        with vault._write_callback.bind_group(group.handle, None):
            vault.writer.write("a.md", "# A\n")
        assert (
            vault._write_callback.close_group(group.handle, None)
            .result(timeout=10)
            .status
            == "no_changes"
        )
        log = (git_repo / "log.md").read_text()
        assert "No Git" in log and log.count("\n- ") == 1
    finally:
        vault.close()


def test_shutdown_records_open_group(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check shutdown records open group."""
    vault = make_vault("events")
    group = vault._write_callback.open_group("Shutdown", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    vault.close()
    assert "Shutdown" in (git_repo / "log.md").read_text()
    assert "log.md" in _run_git(git_repo, "show", "--pretty=", "--name-only", "HEAD")


@pytest.mark.parametrize("mode", ["events", "off"])
def test_close_does_not_block_drain_holding_file_lock(
    git_repo: Path, make_vault: Callable[..., Vault], mode: str
) -> None:
    """Drain completes during closure; off-mode closure needs no file lock."""
    vault = make_vault(mode)
    group = vault._write_callback.open_group("Drain", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    with vault.pause_writes():
        future = vault._write_callback.close_group(group.handle, None)
        assert vault._write_callback.drain(timeout=2)
        if mode == "off":
            assert future.result(timeout=2).status == "committed"
    assert future.result(timeout=10).status == "committed"
    if mode == "events":
        assert "Drain" in (git_repo / "log.md").read_text()
    else:
        assert not is_regular_file(git_repo / "log.md")


def test_event_date_is_close_date(
    git_repo: Path, make_vault: Callable[..., Vault], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request date survives a delayed finalizer across midnight."""
    from datetime import date
    from threading import Event
    from unittest.mock import patch

    vault = make_vault("events")
    dispatcher = vault._write_callback
    group = dispatcher.open_group("At midnight", None)
    with dispatcher.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    release = Event()
    original = dispatcher._finish_group

    def delayed(
        group: CommitGroup,
        result: Future[GroupCommitResult],
        automatic: bool,
        day: date,
    ) -> None:
        assert release.wait(5)
        original(group, result, automatic, day)

    monkeypatch.setattr(dispatcher, "_finish_group", delayed)
    try:
        with patch("markdown_vault_mcp.write_callback.date") as clock:
            clock.today.return_value = date(2026, 1, 2)
            future = dispatcher.close_group(group.handle, None)
    finally:
        release.set()
    result = future.result(timeout=10)
    assert result.status == "committed"
    assert "## 2026-01-02" in (git_repo / "log.md").read_text()


def test_timer_closes_and_records_history(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """A timer commits its history with the opener as author."""
    import time

    vault = make_vault("events")
    vault._write_callback._group_idle_s = 0.05
    principal = Principal("alice", "Alice", "alice@example.com", "human")
    group = vault._write_callback.open_group("Timer change", principal)
    with (
        bound_principal(principal),
        vault._write_callback.bind_group(group.handle, principal),
    ):
        vault.writer.write("a.md", "# A\n")
    deadline = time.monotonic() + 5
    while not group.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert group.closed
    result = vault._write_callback.close_group(group.handle, principal).result(
        timeout=10
    )
    assert result.status == "committed" and result.sha
    assert _run_git(
        git_repo, "show", "-s", "--format=%an <%ae>", result.sha
    ).strip() == ("Alice <alice@example.com>")
    assert "Timer change" in _run_git(git_repo, "show", f"{result.sha}:log.md")


def test_auto_close_after_write_before_maintenance_has_no_extra_bullet(
    git_repo: Path, make_vault: Callable[..., Vault], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check auto close after write before maintenance has no extra bullet."""
    import threading

    vault = make_vault("writes")
    entered, resume = threading.Event(), threading.Event()
    maintainer = vault._okf_convention
    assert maintainer is not None
    original = maintainer.maintain

    def paused(path: str, operation: WriteOperation) -> None:
        """Check paused."""
        entered.set()
        assert resume.wait(5)
        original(path, operation)

    monkeypatch.setattr(maintainer, "maintain", paused)
    group = vault._write_callback.open_group("One event", None)

    def write() -> None:
        """Check write."""
        with vault._write_callback.bind_group(group.handle, None):
            vault.writer.write("a.md", "# A\n")

    thread = threading.Thread(target=write)
    thread.start()
    try:
        assert entered.wait(5)
        result = vault._write_callback.close_group(
            group.handle, None, _automatic=True
        ).result(timeout=5)
        assert result.status == "committed"
    finally:
        resume.set()
        thread.join(5)
    log = (git_repo / "log.md").read_text()
    assert log.count("\n- ") == 1
    assert "One event" in log


def test_move_does_not_recreate_removed_source(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """A closing event does not recreate a directory the group moved."""
    from markdown_vault_mcp.utils.fs import is_directory

    old = git_repo / "old"
    old.mkdir()
    (old / "a.md").write_text("# A\n")
    (old / "b.md").write_text("# B\n")
    vault = make_vault("writes", ("old", "new"))
    group = vault._write_callback.open_group("Move notes", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.move_folder("old", "new")
    result = vault._write_callback.close_group(group.handle, None).result(timeout=10)
    assert result.status == "committed" and not result.history_failed
    assert "old/a.md" in (git_repo / "log.md").read_text()
    assert not is_directory(old)


def test_group_accepts_newline_in_note_path(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """An accepted newline path is represented safely in the log."""
    vault = make_vault("events")
    group = vault._write_callback.open_group("Change", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("line\nbreak.md", "# Note\n")
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
    log = (git_repo / "log.md").read_text()
    assert "line%0Abreak.md" in log
    assert r"line\\nbreak.md" in log


def test_removed_paths_are_not_broken_links(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Deleted and renamed sources use code; the new path remains a link."""
    vault = make_vault("events")
    vault.writer.write("delete.md", "# Delete\n")
    vault.writer.write("old.md", "# Old\n")
    assert vault._write_callback.drain(timeout=10)
    group = vault._write_callback.open_group("Reorganize", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.delete("delete.md")
        vault.writer.rename("old.md", "new.md")
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
    log = (git_repo / "log.md").read_text()
    assert "`delete.md`" in log and "`old.md`" in log
    assert "[new.md](/new.md)" in log
    assert "](/delete.md)" not in log and "](/old.md)" not in log


def test_shutdown_timeout_defers_teardown_until_retry(
    make_vault: Callable[..., Vault],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A slow finalizer keeps its resources and a later close completes cleanup."""
    import sqlite3
    import threading

    vault = make_vault("events")
    dispatcher = vault._write_callback
    record = dispatcher.group_event
    assert record is not None
    entered, release = threading.Event(), threading.Event()

    def slow(group: CommitGroup, paths: set[Path], day: date) -> None:
        entered.set()
        assert release.wait(5)
        record(group, paths, day)

    original_close = dispatcher.close_groups
    monkeypatch.setattr(dispatcher, "group_event", slow)
    monkeypatch.setattr(
        dispatcher,
        "close_groups",
        lambda timeout=30: original_close(timeout=min(timeout, 0.1)),
    )
    group = dispatcher.open_group("Slow", None)
    with dispatcher.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    pending = dispatcher.close_group(group.handle, None)
    try:
        assert entered.wait(5)
        vault.close()
        assert "group_finalize_timeout" in caplog.text
        assert not pending.done()
        assert vault._fts.count_documents() >= 0
    finally:
        release.set()
    assert pending.result(timeout=5).status == "committed"
    vault.close()
    with pytest.raises(sqlite3.ProgrammingError):
        vault._fts.count_documents()


def test_auto_close_racing_first_write_records_history(
    git_repo: Path, make_vault: Callable[..., Vault], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first write accepted during automatic closure appears in history."""
    from contextlib import contextmanager
    from threading import Event

    vault = make_vault("writes")
    dispatcher = vault._write_callback
    group = dispatcher.open_group("First change", None)
    entered, resume = Event(), Event()

    @contextmanager
    def pause() -> Iterator[None]:
        entered.set()
        assert resume.wait(5)
        yield

    original_scope = dispatcher.group_write_scope

    @contextmanager
    def paused_scope() -> Iterator[None]:
        with pause(), original_scope():
            yield

    with dispatcher.bind_group(group.handle, None):
        monkeypatch.setattr(dispatcher, "group_write_scope", paused_scope)
        future = dispatcher.close_group(group.handle, None, _automatic=True)
        assert entered.wait(5)
        try:
            vault.writer.write("first.md", "# First\n")
        finally:
            resume.set()
        assert not future.result(timeout=10).history_failed
        assert dispatcher.close_group(group.handle, None) is future
        vault.writer.write("later.md", "# Later\n")
    log = (git_repo / "log.md").read_text()
    assert log.count("First change") == 1
    assert "later.md" in log


def _fail_log_selection(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path, failure: str, failed: str
) -> None:
    """Inject a failure during anchor or directory selection."""
    from markdown_vault_mcp import _okf_log_event

    original_anchor = _okf_log_event.log_anchor
    original_directory = _okf_log_event.is_directory

    def failing_anchor(
        folder: str, patterns: tuple[str, ...], source_dir: Path | None
    ) -> str:
        if folder == f"{failed}/nested":
            raise OSError("anchor lookup failed")
        return original_anchor(folder, patterns, source_dir)

    def failing_directory(path: Path) -> bool:
        if path == git_repo / failed / "nested":
            raise PermissionError("directory inaccessible")
        return original_directory(path)

    if failure == "anchor":
        monkeypatch.setattr(_okf_log_event, "log_anchor", failing_anchor)
    elif failure == "folder":
        monkeypatch.setattr(_okf_log_event, "is_directory", failing_directory)


@pytest.mark.parametrize(
    "case",
    [
        (failure, failed)
        for failure in ("log", "anchor", "folder")
        for failed in ("a", "b")
    ],
)
def test_failed_log_does_not_skip_other_destinations(
    git_repo: Path,
    make_vault: Callable[..., Vault],
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Selection and write failures leave other destinations writable."""
    failure, failed = case
    for folder in ("a", "b", "c"):
        (git_repo / folder / "nested").mkdir(parents=True)
    bad = git_repo / failed / "nested/log.md"
    original = "# Log\n\n```\nunclosed\n"
    if failure == "log":
        bad.write_text(original)
    folders = tuple(f"{name}/nested" for name in "abc")
    vault = make_vault("events", folders)
    dispatcher = vault._write_callback
    group = dispatcher.open_group("Three folders", None)
    with dispatcher.bind_group(group.handle, None):
        for folder in ("a", "b", "c"):
            vault.writer.write(f"{folder}/nested/note.md", "# Note\n")
    _fail_log_selection(monkeypatch, git_repo, failure, failed)
    result = dispatcher.close_group(group.handle, None).result(timeout=10)
    assert result.status == "committed" and result.history_failed
    if failure == "log":
        assert bad.read_text() == original
        assert f"{failed}/nested/log.md" in caplog.text
        if failed == "b":
            assert "a/nested/log.md" in caplog.text
    else:
        assert f"{failed}/nested/note.md" in caplog.text
    for folder in set("abc") - {failed}:
        log = (git_repo / folder / "nested/log.md").read_text()
        assert log.count("Three folders") == 1
        assert f"{folder}/nested/note.md" in log


@pytest.mark.parametrize("point", ["policy", "scope", "queue"])
def test_finalizer_failure_before_history_still_completes(
    make_vault: Callable[..., Vault],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    point: str,
) -> None:
    """An early history failure resolves the close and permits shutdown."""
    from contextlib import contextmanager

    vault = make_vault("events")
    dispatcher = vault._write_callback
    group = dispatcher.open_group("Early failure", None)
    with dispatcher.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")

    def fail_policy() -> bool:
        raise OSError("policy unavailable")

    @contextmanager
    def fail_scope() -> Iterator[None]:
        raise OSError("write scope unavailable")
        yield

    def fail_queue(_scope: object) -> None:
        raise OSError("queue unavailable")

    if point == "policy":
        monkeypatch.setattr(dispatcher, "group_event_enabled", fail_policy)
    elif point == "scope":
        monkeypatch.setattr(dispatcher, "group_write_scope", fail_scope)
    else:
        monkeypatch.setattr(dispatcher, "_enqueue_scope_end", fail_queue)
    future = dispatcher.close_group(group.handle, None)
    try:
        result = future.result(timeout=1)
        if point == "queue":
            assert result.status == "failed"
            assert "group_commit_queue_failed" in caplog.text
        else:
            assert result.status == "committed" and result.history_failed
            assert "group_history_failed" in caplog.text
        assert group.closed
    finally:
        if not future.done():
            group.closed = True
            future.set_result(group.result)


def test_nested_deferral_restores_outer_context() -> None:
    """A group's suppression decision cannot change an outer deferral."""
    from markdown_vault_mcp._okf_log_event import (
        deferred_event,
        end_event_deferral,
        event_deferred,
    )

    assert not event_deferred()
    with deferred_event():
        with deferred_event():
            end_event_deferral()
            assert not event_deferred()
        assert event_deferred()
    assert not event_deferred()


def test_service_shutdown_closes_git_after_group_timeout(
    git_repo: Path,
    make_vault: Callable[..., Vault],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Service shutdown can leave group files uncommitted after its bound."""
    import threading

    vault = make_vault("events")
    dispatcher = vault._write_callback
    strategy = dispatcher._on_write
    assert isinstance(strategy, GitWriteStrategy)
    original_batch = strategy.on_write_batch
    entered, release = threading.Event(), threading.Event()

    def paused(*args: object, **kwargs: object) -> object:
        entered.set()
        assert release.wait(5)
        return original_batch(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(strategy, "on_write_batch", paused)
    original_close = dispatcher.close_groups
    monkeypatch.setattr(
        dispatcher,
        "close_groups",
        lambda timeout=30: original_close(timeout=min(timeout, 0.1)),
    )
    group = dispatcher.open_group("Shutdown timeout", None)
    with dispatcher.bind_group(group.handle, None):
        vault.writer.write("note.md", "# Note\n")
    pending = dispatcher.close_group(group.handle, None)
    try:
        assert entered.wait(5)
        vault.close()
        assert "group_finalize_timeout outcome=may_be_lost" in caplog.text
        assert vault._fts.count_documents() >= 0
        strategy.close()
    finally:
        release.set()
    assert pending.result(timeout=5).status == "failed"
    assert is_regular_file(git_repo / "note.md")
    assert "Shutdown timeout" in (git_repo / "log.md").read_text()
    assert "note.md" in _run_git(git_repo, "status", "--porcelain")
    assert "Shutdown timeout" not in _run_git(git_repo, "log", "--format=%s")
    vault.close()


@pytest.mark.parametrize("operation", ["rename", "move"])
@pytest.mark.parametrize("same_log", [False, True])
def test_backlink_rewrite_contributes_to_group_history(
    git_repo: Path, make_vault: Callable[..., Vault], operation: str, same_log: bool
) -> None:
    """Backlink rewrites contribute to their selected log and the commit."""
    (git_repo / "source").mkdir()
    (git_repo / "source/note.md").write_text("# Source\n")
    ref = "ref.md" if same_log else "references/ref.md"
    if not same_log:
        (git_repo / "references").mkdir()
    (git_repo / ref).write_text("[Source](/source/note.md)\n")
    _run_git(git_repo, "add", ".")
    _run_git(git_repo, "commit", "-m", "Seed notes")
    vault = make_vault("events", ("source", "destination", "references"))
    vault.index.build_index()
    group = vault._write_callback.open_group("Repair links", None)
    with vault._write_callback.bind_group(group.handle, None):
        if same_log:
            vault.writer.write("anchor.md", "# Anchor\n")
        if operation == "rename":
            vault.writer.rename(
                "source/note.md", "destination/note.md", update_links=True
            )
        else:
            vault.writer.move_folder("source", "destination")
    result = vault._write_callback.close_group(group.handle, None).result(timeout=10)
    assert result.status == "committed" and not result.history_failed and result.sha
    assert "/destination/note.md" in (git_repo / ref).read_text()
    log_path = "log.md" if same_log else "references/log.md"
    log = (git_repo / log_path).read_text()
    assert log.count("Repair links") == 1 and ref in log
    committed = _run_git(git_repo, "show", "--format=", "--name-only", result.sha)
    assert ref in committed and log_path in committed
