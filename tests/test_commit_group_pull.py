"""Automatic pulls respect open commit groups."""

import logging
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest

from markdown_vault_mcp._commit_groups import CommitGroup
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.types import PullResult
from markdown_vault_mcp.write_callback import WriteCallbackDispatcher


@pytest.mark.parametrize("back_to_back", [False, True])
def test_global_bounded_deferrals_split_and_reset(
    git_repo: Path, back_to_back: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """The global cap survives back-to-back groups and covers both triggers."""
    caplog.set_level(logging.INFO, logger="markdown_vault_mcp.git.strategy")
    strategy = GitWriteStrategy(
        token=None, repo_url=None, managed=False, repo_path=git_repo
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    strategy.set_write_quiescer(nullcontext, dispatcher.drain)
    strategy.set_group_pull_guard(dispatcher.guard_open_groups)
    group = dispatcher.open_group("Group", None)
    note = git_repo / "note.md"
    note.write_text("one", encoding="utf-8")
    with dispatcher.bind_group(group.handle, None):
        dispatcher.fire(note, "one", "write")
    result = PullResult(True, True, 0, "a", "a")
    try:
        with (
            patch.object(strategy, "_pull_locked", return_value=result) as pull,
            patch.object(strategy, "_tracking_ref", return_value="origin/main"),
        ):
            assert strategy.automatic_pull().reason == "group_open"
            assert not strategy.sync_once(git_repo, timer_driven=True)
            assert strategy.automatic_pull().reason == "group_open"
            pull.assert_not_called()
            records = [
                r
                for r in caplog.records
                if r.msg == "git_pull_deferred reason=%s deferrals=%d"
            ]
            assert [r.args for r in records] == [
                ("group_open", 1),
                ("group_open", 2),
                ("group_open", 3),
            ]
            if back_to_back:
                dispatcher.close_group(group.handle, None).result(timeout=5)
                group = dispatcher.open_group("Second group", None)
                note.write_text("two", encoding="utf-8")
                with dispatcher.bind_group(group.handle, None):
                    dispatcher.fire(note, "two", "write")
            assert strategy.automatic_pull() == result
            assert pull.call_count == 1
            from tests.fixtures.git import _run_git

            assert _run_git(git_repo, "log", "-1", "--format=%s").strip() == (
                "Second group" if back_to_back else "Group"
            )
            for _ in range(3):
                assert strategy.automatic_pull().reason == "group_open"
            dispatcher.close_group(group.handle, None).result(timeout=5)
            assert strategy.automatic_pull() == result
            second = dispatcher.open_group("Second", None)
            assert strategy.automatic_pull().reason == "group_open"
            dispatcher.close_group(second.handle, None).result(timeout=5)
    finally:
        dispatcher.close()
        strategy.close()


def test_explicit_pull_does_not_defer(git_repo: Path) -> None:
    strategy = GitWriteStrategy(
        token=None, repo_url=None, managed=False, repo_path=git_repo
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    strategy.set_write_quiescer(nullcontext, dispatcher.drain)
    strategy.set_group_pull_guard(dispatcher.guard_open_groups)
    group = dispatcher.open_group("Group", None)
    try:
        with patch.object(
            strategy, "_pull_locked", return_value=PullResult(True, True, 0, "a", "a")
        ) as pull:
            assert strategy.force_pull().applied
            pull.assert_called_once()
    finally:
        dispatcher.close_group(group.handle, None).result(timeout=5)
        dispatcher.close()
        strategy.close()


def test_auto_closed_group_does_not_defer(git_repo: Path) -> None:
    strategy = GitWriteStrategy(
        token=None, repo_url=None, managed=False, repo_path=git_repo
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    strategy.set_group_pull_guard(dispatcher.guard_open_groups)
    group = dispatcher.open_group("Group", None)
    try:
        with dispatcher.bind_group(group.handle, None):
            dispatcher.close_group(group.handle, None, _automatic=True).result(
                timeout=5
            )
            assert group.handle in dispatcher._groups
            with patch.object(
                strategy,
                "_pull_locked",
                return_value=PullResult(True, True, 0, "a", "a"),
            ) as pull:
                assert strategy.automatic_pull().applied
                pull.assert_called_once()
    finally:
        dispatcher.close()
        strategy.close()


def test_vault_wires_group_guard_and_webhook_pull(git_repo: Path) -> None:
    from markdown_vault_mcp.vault import Vault

    strategy = GitWriteStrategy(
        token=None, repo_url=None, managed=False, repo_path=git_repo
    )
    vault = Vault(source_dir=git_repo, git_strategy=strategy)
    group = vault._write_callback.open_group("Group", None)
    try:
        assert vault._automatic_pull().reason == "group_open"
        vault._write_callback.close_group(group.handle, None).result(timeout=5)
    finally:
        vault.close()


def test_vault_automatic_pull_without_git_and_with_other_store(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from markdown_vault_mcp.vault import Vault

    vault = Vault(source_dir=tmp_path)
    try:
        assert vault._automatic_pull() is None
        store = MagicMock()
        result = PullResult(True, True, 0, "a", "a")
        store.force_pull.return_value = result
        vault._git_strategy = store
        assert vault._automatic_pull() == result
    finally:
        vault.close()


def test_automatic_pull_respects_disabled_pulls(git_repo: Path) -> None:
    strategy = GitWriteStrategy(
        token=None, repo_url=None, managed=False, enable_pull=False, repo_path=git_repo
    )
    try:
        assert strategy.automatic_pull().reason == "pull_disabled"
    finally:
        strategy.close()


def test_group_idle_clock_starts_after_pull_gate(git_repo: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=git_repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy, group_idle_s=0.3, group_max_age_s=5)
    started = Event()

    def open_group() -> CommitGroup:
        started.set()
        return dispatcher.open_group("After pull", None)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with dispatcher.guard_open_groups():
                future = executor.submit(open_group)
                assert started.wait(timeout=2)
                Event().wait(0.5)
            group = future.result(timeout=2)
            note = git_repo / "after.md"
            note.write_text("after", encoding="utf-8")
            with dispatcher.bind_group(group.handle, None):
                dispatcher.fire(note, "after", "write")
            assert (
                dispatcher.close_group(group.handle, None).result(timeout=5).status
                == "committed"
            )
    finally:
        dispatcher.close()
        strategy.close()
