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
    from pathlib import Path

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
    assert (
        vault._write_callback.close_group(group.handle, None).result(timeout=10).status
        == "failed"
    )
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
