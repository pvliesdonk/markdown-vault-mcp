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
    from datetime import date
    from pathlib import Path

    from markdown_vault_mcp._commit_groups import CommitGroup
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


def test_empty_and_off_groups_do_not_create_log(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check empty and off groups do not create log."""
    vault = make_vault("off")
    group = vault._write_callback.open_group("No changes", None)
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "no_changes"
    )
    group = vault._write_callback.open_group("Change", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
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


def test_auto_close_uses_opener_identity(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check auto close uses opener identity."""
    vault = make_vault("events")
    principal = Principal("alice", "Alice", "alice@example.com", "human")
    group = vault._write_callback.open_group("Change", principal)
    with (
        bound_principal(principal),
        vault._write_callback.bind_group(group.handle, principal),
    ):
        vault.writer.write("a.md", "# A\n")
    result = vault._write_callback.close_group(
        group.handle, principal, _automatic=True
    ).result(timeout=10)
    assert result.status == "committed"
    assert (
        _run_git(git_repo, "log", "-1", "--format=%an <%ae>").strip()
        == "Alice <alice@example.com>"
    )
    assert "Change" in (git_repo / "log.md").read_text()


def test_removed_and_renamed_paths_are_recorded(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check removed and renamed paths are recorded."""
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
    assert all(path in log for path in ("delete.md", "old.md", "new.md"))


def test_group_records_separate_selected_logs(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check group records separate selected logs."""
    (git_repo / "one").mkdir()
    (git_repo / "two").mkdir()
    vault = make_vault("events", ("one", "two"))
    group = vault._write_callback.open_group("Across folders", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("one/a.md", "# A\n")
        vault.writer.write("two/b.md", "# B\n")
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
    assert "one/a.md" in (git_repo / "one/log.md").read_text()
    assert "two/b.md" in (git_repo / "two/log.md").read_text()


def test_log_failure_makes_group_result_failed(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check log failure makes group result failed."""
    vault = make_vault("events")
    (git_repo / "log.md").write_text("# Log\n\n```\nunclosed\n")
    group = vault._write_callback.open_group("Unsafe log", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    result = vault._write_callback.close_group(group.handle, None).result(timeout=10)
    assert result.status == "committed" and result.history_failed
    assert "unclosed" in (git_repo / "log.md").read_text()


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


def test_close_does_not_block_drain_holding_file_lock(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check close does not block drain holding file lock."""
    vault = make_vault("events")
    group = vault._write_callback.open_group("Drain", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    with vault.pause_writes():
        future = vault._write_callback.close_group(group.handle, None)
        assert vault._write_callback.drain(timeout=2)
    assert future.result(timeout=10).status == "committed"
    assert "Drain" in (git_repo / "log.md").read_text()


def test_event_date_is_close_date(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check event date is close date."""
    from datetime import date
    from unittest.mock import patch

    vault = make_vault("events")
    group = vault._write_callback.open_group("At midnight", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    with patch("markdown_vault_mcp.write_callback.date") as clock:
        clock.today.return_value = date(2026, 1, 2)
        result = vault._write_callback.close_group(group.handle, None).result(
            timeout=10
        )
    assert result.status == "committed"
    assert "## 2026-01-02" in (git_repo / "log.md").read_text()


def test_timer_closes_with_opener_identity(
    git_repo: Path, make_vault: Callable[..., Vault]
) -> None:
    """Check timer closes with opener identity."""
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
    assert result.status == "committed"
    assert (
        _run_git(git_repo, "log", "-1", "--format=%an <%ae>").strip()
        == "Alice <alice@example.com>"
    )
    assert "Timer change" in (git_repo / "log.md").read_text()


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
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
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
    """Removed paths remain readable history without becoming broken links."""
    vault = make_vault("events")
    vault.writer.write("old.md", "# Old\n")
    assert vault._write_callback.drain(timeout=10)
    group = vault._write_callback.open_group("Remove", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.delete("old.md")
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "committed"
    )
    log = (git_repo / "log.md").read_text()
    assert "`old.md`" in log
    assert "](/old.md)" not in log


def test_partial_history_failure_reports_location_and_git_succeeds(
    git_repo: Path, make_vault: Callable[..., Vault], caplog: pytest.LogCaptureFixture
) -> None:
    """A failed second log reports its destination and earlier successes."""
    for folder in ("a", "b"):
        (git_repo / folder).mkdir()
    (git_repo / "b/log.md").write_text("# Log\n\n```\nunclosed\n")
    vault = make_vault("events", ("a", "b"))
    group = vault._write_callback.open_group("Two folders", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("a/one.md", "# One\n")
        vault.writer.write("b/two.md", "# Two\n")
    result = vault._write_callback.close_group(group.handle, None).result(timeout=10)
    assert result.status == "committed" and result.history_failed
    assert "Two folders" in (git_repo / "a/log.md").read_text()
    assert "b/log.md" in caplog.text and "a/log.md" in caplog.text
    assert "unclosed" in caplog.text


def test_disabled_log_group_closes_while_writes_paused(
    make_vault: Callable[..., Vault],
) -> None:
    """Closing a group without history does not wait for the file lock."""
    vault = make_vault("off")
    group = vault._write_callback.open_group("No history", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    with vault.pause_writes():
        assert (
            vault._write_callback.close_group(group.handle, None)
            .result(timeout=2)
            .status
            == "committed"
        )


def test_history_failure_does_not_block_sync(
    git_repo: Path, make_vault: Callable[..., Vault], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed group with a history failure can proceed to Git sync."""
    import asyncio

    from markdown_vault_mcp._tools.transactions import settle_transactions

    vault = make_vault("events")
    (git_repo / "log.md").write_text("# Log\n\n```\nunclosed\n")
    group = vault._write_callback.open_group("Unsafe", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("a.md", "# A\n")
    monkeypatch.setattr(
        "markdown_vault_mcp._tools.transactions.resolve_mcp_principal", lambda: None
    )
    asyncio.run(settle_transactions(vault, group.handle))
    assert _run_git(git_repo, "log", "-1", "--format=%s").strip() == "Unsafe"


def test_shutdown_timeout_defers_teardown_until_retry(
    make_vault: Callable[..., Vault],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A slow finalizer keeps its resources and a later close completes cleanup."""
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
    finally:
        release.set()
    assert pending.result(timeout=5).status == "committed"
    vault.close()


def test_auto_close_racing_first_write_records_history(
    git_repo: Path, make_vault: Callable[..., Vault], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first write accepted during automatic closure appears in history."""
    from contextlib import contextmanager
    from threading import Event

    import markdown_vault_mcp.write_callback as callbacks

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
        monkeypatch.setattr(callbacks, "nullcontext", pause)
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


@pytest.mark.parametrize("failed", ["a", "b"])
def test_failed_log_does_not_skip_other_destinations(
    git_repo: Path,
    make_vault: Callable[..., Vault],
    failed: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One bad log leaves independent destinations writable and visible."""
    for folder in ("a", "b", "c"):
        (git_repo / folder).mkdir()
    bad = git_repo / failed / "log.md"
    original = "# Log\n\n```\nunclosed\n"
    bad.write_text(original)
    vault = make_vault("events", ("a", "b", "c"))
    dispatcher = vault._write_callback
    group = dispatcher.open_group("Three folders", None)
    with dispatcher.bind_group(group.handle, None):
        for folder in ("a", "b", "c"):
            vault.writer.write(f"{folder}/note.md", "# Note\n")
    result = dispatcher.close_group(group.handle, None).result(timeout=10)
    assert result.status == "committed" and result.history_failed
    assert bad.read_text() == original
    assert f"{failed}/log.md" in caplog.text
    for folder in set("abc") - {failed}:
        assert (git_repo / folder / "log.md").read_text().count("Three folders") == 1


def test_group_with_only_reserved_index_does_not_create_history(
    git_repo: Path,
    make_vault: Callable[..., Vault],
) -> None:
    """A generated or authored index alone does not receive an event."""
    vault = make_vault("events")
    dispatcher = vault._write_callback
    group = dispatcher.open_group("Index only", None)
    with dispatcher.bind_group(group.handle, None):
        vault.writer.write("index.md", '---\nokf_version: "0.2"\n---\n# Root updated\n')
    assert (
        not dispatcher.close_group(group.handle, None).result(timeout=10).history_failed
    )
    assert not is_regular_file(git_repo / "log.md")
