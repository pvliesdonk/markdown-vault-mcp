"""A transaction records one editorial event in OKF history."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.utils.fs import is_directory, is_regular_file
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
    description = "Policy " + "é" * 600
    group = vault._write_callback.open_group(description, None)
    with vault._write_callback.bind_group(group.handle, None):
        for n in range(4):
            vault.writer.write(f"note{n}.md", "# Note\n")
            if n == 1:
                assert vault._write_callback.drain(timeout=10)
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
    log = (git_repo / "log.md").read_text()
    assert log.count("\n- ") == 1
    assert description in log
    assert all(f"note{n}.md" in log for n in range(4))
    assert not log.startswith("---")
    assert "log.md" in _run_git(git_repo, "show", "--pretty=", "--name-only", "HEAD")


@pytest.mark.parametrize("mode", ["off", "reserved"])
def test_off_and_reserved_only_groups_create_no_log(
    git_repo: Path, make_vault: Callable[..., Vault], mode: str
) -> None:
    """Disabled and reserved-only groups create no log."""
    vault = make_vault("events" if mode == "reserved" else mode)
    group = vault._write_callback.open_group("Change", None)
    with vault._write_callback.bind_group(group.handle, None):
        if mode == "reserved":
            vault.writer.write(
                "index.md", '---\nokf_version: "0.2"\n---\n# Root updated\n'
            )
        else:
            vault.writer.write("a.md", "# A\n")
    result = vault._write_callback.close_group(group.handle, None).result(timeout=10)
    assert result.status == "committed"
    assert not result.history_failed
    assert not is_regular_file(git_repo / "log.md")


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
        assert result.status == "committed" and result.sha
        assert "One event" in _run_git(git_repo, "show", f"{result.sha}:log.md")
    finally:
        resume.set()
        thread.join(5)
    log = (git_repo / "log.md").read_text()
    assert log.count("\n- ") == 1
    assert "One event" in log


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_removed_paths_are_not_broken_links(
    git_repo: Path, make_vault: Callable[..., Vault], operation: str
) -> None:
    """Absent paths use code, including a delete-only event and backticks."""
    vault = make_vault("events")
    old = "delete`note.md" if operation == "delete" else "old.md"
    vault.writer.write(old, "# Old\n")
    assert vault._write_callback.drain(timeout=10)
    group = vault._write_callback.open_group("Reorganize", None)
    with vault._write_callback.bind_group(group.handle, None):
        if operation == "delete":
            vault.writer.delete(old)
        else:
            vault.writer.rename(old, "new.md")
            vault.writer.write("line\nbreak.md", "# Note\n")
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
    log = (git_repo / "log.md").read_text()
    if operation == "delete":
        assert "`` delete`note.md ``" in log
    else:
        assert "`old.md`" in log and "](/old.md)" not in log
        assert "[new.md](/new.md)" in log
        assert "line%0Abreak.md" in log and r"line\\nbreak.md" in log


@pytest.mark.parametrize("point", ["history", "commit"])
def test_shutdown_timeout_defers_teardown_until_retry(
    git_repo: Path,
    make_vault: Callable[..., Vault],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    point: str,
) -> None:
    """A timed-out finalizer retains stores, with distinct shutdown outcomes."""
    import sqlite3
    import threading

    vault = make_vault("events")
    dispatcher = vault._write_callback
    strategy = dispatcher._on_write
    assert isinstance(strategy, GitWriteStrategy)
    entered, release = threading.Event(), threading.Event()
    record = dispatcher.group_event
    assert record is not None
    original_batch = strategy.on_write_batch

    def slow(group: CommitGroup, paths: set[Path], day: date) -> None:
        entered.set()
        assert release.wait(5)
        record(group, paths, day)

    def paused(*args: object, **kwargs: object) -> object:
        entered.set()
        assert release.wait(5)
        return original_batch(*args, **kwargs)  # type: ignore[arg-type]

    if point == "history":
        monkeypatch.setattr(dispatcher, "group_event", slow)
    else:
        monkeypatch.setattr(strategy, "on_write_batch", paused)
    original_close = dispatcher.close_groups
    monkeypatch.setattr(
        dispatcher,
        "close_groups",
        lambda timeout=30: original_close(timeout=min(timeout, 0.1)),
    )
    group = dispatcher.open_group("Shutdown timeout", None)
    with dispatcher.bind_group(group.handle, None):
        vault.writer.write("note.md", "# A\n")
    pending = dispatcher.close_group(group.handle, None)
    try:
        assert entered.wait(5)
        vault.close()
        assert "group_finalize_timeout outcome=may_be_lost" in caplog.text
        assert not pending.done()
        assert vault._fts.count_documents() >= 0
        if point == "commit":
            strategy.close()
    finally:
        release.set()
    assert pending.result(timeout=5).status == (
        "committed" if point == "history" else "failed"
    )
    if point == "commit":
        assert is_regular_file(git_repo / "note.md")
        assert "Shutdown timeout" in (git_repo / "log.md").read_text()
        assert "note.md" in _run_git(git_repo, "status", "--porcelain")
        assert "Shutdown timeout" not in _run_git(git_repo, "log", "--format=%s")
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
    [("log", "a"), ("log", "b"), ("anchor", "a"), ("folder", "a")],
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


@pytest.mark.parametrize("operation", ["rename", "move"])
def test_backlink_rewrite_contributes_to_group_history(
    git_repo: Path, make_vault: Callable[..., Vault], operation: str
) -> None:
    """Backlink rewrites contribute to their selected log and the commit."""
    (git_repo / "source").mkdir()
    (git_repo / "source/note.md").write_text("# Source\n")
    ref = "references/ref.md"
    (git_repo / "references").mkdir()
    (git_repo / ref).write_text("[Source](/source/note.md)\n")
    _run_git(git_repo, "add", ".")
    _run_git(git_repo, "commit", "-m", "Seed notes")
    vault = make_vault("events", ("source", "destination", "references"))
    vault.index.build_index()
    group = vault._write_callback.open_group("Repair links", None)
    with vault._write_callback.bind_group(group.handle, None):
        if operation == "rename":
            vault.writer.rename(
                "source/note.md", "destination/note.md", update_links=True
            )
        else:
            vault.writer.move_folder("source", "destination")
    result = vault._write_callback.close_group(group.handle, None).result(timeout=10)
    assert result.status == "committed" and not result.history_failed and result.sha
    assert "/destination/note.md" in (git_repo / ref).read_text()
    log_path = "references/log.md"
    log = (git_repo / log_path).read_text()
    assert log.count("Repair links") == 1 and ref in log
    if operation == "move":
        assert not is_directory(git_repo / "source")
        assert "source/note.md" in (git_repo / "log.md").read_text()
    committed = _run_git(git_repo, "show", "--format=", "--name-only", result.sha)
    assert ref in committed and log_path in committed
