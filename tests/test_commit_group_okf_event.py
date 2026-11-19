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


def test_ungrouped_write_waits_for_group_history_commit(
    git_repo: Path, make_vault: Callable[..., Vault], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later ungrouped bullet cannot enter the earlier group commit."""
    import threading

    vault = make_vault("writes")
    strategy = vault._write_callback._on_write
    assert isinstance(strategy, GitWriteStrategy)
    original = strategy.on_write_batch
    entered, release, wrote = threading.Event(), threading.Event(), threading.Event()

    def paused(*args: object, **kwargs: object) -> object:
        if kwargs.get("group_subject") == "First":
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(strategy, "on_write_batch", paused)
    group = vault._write_callback.open_group("First", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("first.md", "# First\n")
    result = vault._write_callback.close_group(group.handle, None)
    assert entered.wait(5)

    def write() -> None:
        vault.writer.write("later.md", "# Later\n")
        wrote.set()

    thread = threading.Thread(target=write)
    thread.start()
    try:
        assert not wrote.wait(0.1)
    finally:
        release.set()
        thread.join(5)
    first = result.result(timeout=10)
    assert first.sha is not None
    assert "later.md" not in _run_git(git_repo, "show", f"{first.sha}:log.md")
    assert wrote.is_set()


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


def test_overlapping_groups_commit_only_their_own_events(
    git_repo: Path, make_vault: Callable[..., Vault], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent closing groups do not stage each other's history."""
    import threading

    vault = make_vault("events")
    strategy = vault._write_callback._on_write
    assert isinstance(strategy, GitWriteStrategy)
    original = strategy.on_write_batch
    entered, release = threading.Event(), threading.Event()

    def paused(*args: object, **kwargs: object) -> object:
        if kwargs.get("group_subject") == "First change":
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(strategy, "on_write_batch", paused)
    alice = Principal("alice", "Alice", "alice@example.com", "human")
    bob = Principal("bob", "Bob", "bob@example.com", "human")
    first = vault._write_callback.open_group("First change", alice)
    second = vault._write_callback.open_group("Second change", bob)
    with bound_principal(alice), vault._write_callback.bind_group(first.handle, alice):
        vault.writer.write("first.md", "# First\n")
    with bound_principal(bob), vault._write_callback.bind_group(second.handle, bob):
        vault.writer.write("second.md", "# Second\n")
    first_done = vault._write_callback.close_group(first.handle, alice)
    try:
        assert entered.wait(5)
        second_done = vault._write_callback.close_group(second.handle, bob)
        assert not second_done.done()
    finally:
        release.set()
    first_result = first_done.result(timeout=10)
    second_result = second_done.result(timeout=10)
    assert second_result.status == "committed" and second_result.sha is not None
    assert first_result.sha is not None
    first_log = _run_git(git_repo, "show", f"{first_result.sha}:log.md")
    assert "First change" in first_log and "Second change" not in first_log
    assert (
        _run_git(git_repo, "show", "-s", "--format=%ae", first_result.sha).strip()
        == "alice@example.com"
    )
    assert (
        _run_git(git_repo, "show", "-s", "--format=%ae", second_result.sha).strip()
        == "bob@example.com"
    )


def test_custom_strategy_followup_write_does_not_deadlock(git_repo: Path) -> None:
    """A custom callback can perform a follow-up vault write during commit."""
    from typing import Any

    class FollowupStrategy(GitWriteStrategy):
        def on_write_batch(self, *args: Any, **kwargs: Any) -> Any:
            """Commit, then issue a follow-up write."""
            result = super().on_write_batch(*args, **kwargs)
            vault.writer.write("followup.md", "# Followup\n")
            return result

    (git_repo / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    strategy = FollowupStrategy(
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
            okf_capabilities=ContentConfig(okf_log="events").okf_capabilities,
        ),
        on_write=strategy,
    )
    try:
        group = vault._write_callback.open_group("Custom", None)
        with vault._write_callback.bind_group(group.handle, None):
            vault.writer.write("a.md", "# A\n")
        assert (
            vault._write_callback.close_group(group.handle, None)
            .result(timeout=5)
            .status
            == "committed"
        )
        assert vault._write_callback.drain(timeout=5)
        assert is_regular_file(git_repo / "followup.md")
    finally:
        vault.close()
        strategy.close()


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

    def slow(group: CommitGroup, day: date) -> None:
        entered.set()
        assert release.wait(5)
        record(group, day)

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


def test_commit_wait_timeout_releases_lock_but_keeps_result_pending(
    make_vault: Callable[..., Vault],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A slow Git callback releases the file lock without reporting completion."""
    import threading

    import markdown_vault_mcp.write_callback as dispatcher_module

    vault = make_vault("writes")
    strategy = vault._write_callback._on_write
    assert isinstance(strategy, GitWriteStrategy)
    original = strategy.on_write_batch
    entered, release, wrote = threading.Event(), threading.Event(), threading.Event()

    def paused(*args: object, **kwargs: object) -> object:
        if kwargs.get("group_subject") == "Slow":
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(strategy, "on_write_batch", paused)
    monkeypatch.setattr(dispatcher_module, "_GROUP_COMMIT_WAIT_TIMEOUT", 0.1)
    group = vault._write_callback.open_group("Slow", None)
    with vault._write_callback.bind_group(group.handle, None):
        vault.writer.write("first.md", "# First\n")
    result = vault._write_callback.close_group(group.handle, None)
    assert entered.wait(5)

    def write() -> None:
        vault.writer.write("later.md", "# Later\n")
        wrote.set()

    thread = threading.Thread(target=write)
    thread.start()
    try:
        assert wrote.wait(5)
        assert not result.done()
        assert "group_commit_wait_timeout" in caplog.text
    finally:
        release.set()
        thread.join(5)
    assert result.result(timeout=10).status == "committed"
