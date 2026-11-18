"""Groups left open by a client eventually commit."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._identity import Principal, bound_principal
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.write_callback import WriteCallbackDispatcher
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from pathlib import Path


def _wait_for_subject(repo: Path, subject: str) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if _run_git(repo, "log", "-1", "--format=%s").strip() == subject:
            return
        time.sleep(0.01)
    pytest.fail(f"Automatic commit {subject!r} did not occur")


def test_idle_group_commits_as_opener_and_late_close_returns_result(
    git_repo: Path,
) -> None:
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=git_repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy, group_idle_s=0.05, group_max_age_s=2)
    alice = Principal("alice", "Alice", "alice@example.com", "human")
    refreshed = Principal("alice", "Changed", "changed@example.com", "human")
    try:
        group = dispatcher.open_group("Organize notes", alice)
        path = git_repo / "note.md"
        path.write_text("note", encoding="utf-8")
        with bound_principal(refreshed), dispatcher.bind_group(group.handle, refreshed):
            dispatcher.fire(path, "note", "write")
        _wait_for_subject(git_repo, "Organize notes")
        result = dispatcher.close_group(group.handle, alice).result(timeout=5)
        assert result.status == "committed"
        assert (
            _run_git(git_repo, "log", "-1", "--format=%s|%an|%ae").strip()
            == "Organize notes|Alice|alice@example.com"
        )
    finally:
        dispatcher.close()
        strategy.close()


def test_maximum_age_closes_busy_group(git_repo: Path) -> None:
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=git_repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy, group_idle_s=1, group_max_age_s=0.15)
    try:
        group = dispatcher.open_group("Busy group", None)
        path = git_repo / "busy.md"
        for i in range(3):
            path.write_text(str(i), encoding="utf-8")
            with dispatcher.bind_group(group.handle, None):
                dispatcher.fire(path, str(i), "write")
            time.sleep(0.05)
        _wait_for_subject(git_repo, "Busy group")
        assert (
            dispatcher.close_group(group.handle, None).result(timeout=5).status
            == "committed"
        )
        assert _run_git(git_repo, "log", "-1", "--format=%s").strip() == "Busy group"
    finally:
        dispatcher.close()
        strategy.close()


def test_maximum_age_rejects_new_binds_until_active_write_finishes() -> None:
    from markdown_vault_mcp._commit_groups import CommitGroupError

    dispatcher = WriteCallbackDispatcher(None, group_idle_s=2, group_max_age_s=0.05)
    try:
        group = dispatcher.open_group("Long write", None)
        with dispatcher.bind_group(group.handle, None):
            time.sleep(0.1)
            errors: list[Exception] = []

            def bind_again() -> None:
                try:
                    with dispatcher.bind_group(group.handle, None):
                        pass
                except CommitGroupError as exc:
                    errors.append(exc)

            thread = threading.Thread(target=bind_again)
            thread.start()
            thread.join()
            assert errors
        time.sleep(0.1)
        assert (
            dispatcher.close_group(group.handle, None).result(timeout=2).status
            == "no_changes"
        )
    finally:
        dispatcher.close()


def test_shutdown_does_not_restart_group_timer() -> None:
    dispatcher = WriteCallbackDispatcher(None, group_idle_s=0.05, group_max_age_s=1)
    group = dispatcher.open_group("Shutdown", None)
    with dispatcher.bind_group(group.handle, None):
        dispatcher.close()
    time.sleep(0.1)
    from markdown_vault_mcp._commit_groups import CommitGroupError

    with pytest.raises(CommitGroupError):
        dispatcher.open_group("Too late", None)


def test_pending_expiry_does_not_close_group_after_shutdown() -> None:
    from markdown_vault_mcp._commit_groups import CommitGroupError

    dispatcher = WriteCallbackDispatcher(None, group_idle_s=0.05, group_max_age_s=1)
    group = dispatcher.open_group("Shutdown race", None)
    with dispatcher._group_lock:
        worker = threading.Thread(target=dispatcher._expire_group, args=(group.handle,))
        worker.start()
        time.sleep(0.08)
        dispatcher.close()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert (
        dispatcher.close_group(group.handle, None).result(timeout=2).status
        == "no_changes"
    )
    with pytest.raises(CommitGroupError):
        dispatcher.close_group(group.handle, None)


def test_auto_closed_result_is_only_available_to_owner() -> None:
    from markdown_vault_mcp._commit_groups import CommitGroupError

    dispatcher = WriteCallbackDispatcher(None, group_idle_s=0.05, group_max_age_s=1)
    alice = Principal("alice", "Alice", "alice@example.com", "human")
    bob = Principal("bob", "Bob", "bob@example.com", "human")
    try:
        group = dispatcher.open_group("Private", alice)
        deadline = time.monotonic() + 2
        while (
            group.handle not in dispatcher._finished_groups
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert group.handle in dispatcher._finished_groups
        with pytest.raises(CommitGroupError):
            dispatcher.close_group(group.handle, bob)
        assert (
            dispatcher.close_group(group.handle, alice).result(timeout=2).status
            == "no_changes"
        )
    finally:
        dispatcher.close()


def test_max_age_commits_during_active_binding_and_later_write_is_not_lost(
    git_repo: Path,
) -> None:
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=git_repo,
    )
    dispatcher = WriteCallbackDispatcher(
        strategy, group_idle_s=0.05, group_max_age_s=0.12
    )
    alice = Principal("alice", "Alice", "alice@example.com", "human")
    try:
        group = dispatcher.open_group("Deadline", alice)
        first = git_repo / "first.md"
        second = git_repo / "second.md"
        with dispatcher.bind_group(group.handle, alice):
            first.write_text("first", encoding="utf-8")
            dispatcher.fire(first, "first", "write")
            _wait_for_subject(git_repo, "Deadline")
            second.write_text("second", encoding="utf-8")
            dispatcher.fire(second, "second", "write")
        assert dispatcher.drain()
        assert _run_git(git_repo, "show", "HEAD:second.md").strip() == "second"
        assert (
            _run_git(git_repo, "log", "-1", "--format=%an|%ae").strip()
            == "Alice|alice@example.com"
        )
        assert (
            dispatcher.close_group(group.handle, alice).result(timeout=2).status
            == "committed"
        )
    finally:
        dispatcher.close()
        strategy.close()


def test_expired_idle_cannot_be_revived_by_delayed_timer() -> None:
    from markdown_vault_mcp._commit_groups import CommitGroupError

    dispatcher = WriteCallbackDispatcher(None, group_idle_s=0.05, group_max_age_s=2)
    try:
        group = dispatcher.open_group("Idle", None)
        with dispatcher._group_lock:
            time.sleep(0.08)
            with (
                pytest.raises(CommitGroupError, match="automatically"),
                dispatcher.bind_group(group.handle, None),
            ):
                pass
        assert (
            dispatcher.close_group(group.handle, None).result(timeout=2).status
            == "no_changes"
        )
    finally:
        dispatcher.close()


def test_automatic_results_survive_many_later_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Timer:
        daemon = False

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    monkeypatch.setattr("markdown_vault_mcp.write_callback.threading.Timer", Timer)
    dispatcher = WriteCallbackDispatcher(None, group_idle_s=1, group_max_age_s=2)
    try:
        first = dispatcher.open_group("first", None)
        for group in [first] + [
            dispatcher.open_group(str(i), None) for i in range(1025)
        ]:
            group.created -= 3
            dispatcher._expire_group(group.handle)
        assert (
            dispatcher.close_group(first.handle, None).result(timeout=2).status
            == "no_changes"
        )
    finally:
        dispatcher.close()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), 1e100])
def test_dispatcher_rejects_invalid_group_timeout(value: float) -> None:
    from markdown_vault_mcp.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="group_idle_s"):
        WriteCallbackDispatcher(None, group_idle_s=value)


def test_vault_passes_group_timeouts_to_dispatcher(git_repo: Path) -> None:
    from markdown_vault_mcp.config_sections import VaultSettings
    from markdown_vault_mcp.vault import Vault

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
            read_only=False, git_group_idle_s=0.05, git_group_max_age_s=1
        ),
        on_write=strategy,
    )
    try:
        group = vault._write_callback.open_group("Vault timeout", None)
        path = git_repo / "vault.md"
        path.write_text("note", encoding="utf-8")
        with vault._write_callback.bind_group(group.handle, None):
            vault._write_callback.fire(path, "note", "write")
        _wait_for_subject(git_repo, "Vault timeout")
    finally:
        vault.close()
        strategy.close()
