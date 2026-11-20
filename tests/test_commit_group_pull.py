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
from tests.fixtures.git import GitRepoPair


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


async def test_deferred_webhook_retries_failed_reindex(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    from markdown_vault_mcp._webhooks import _process_push
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
    from markdown_vault_mcp.vault import Vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    local = git_repo_pair.local_path
    strategy = GitWriteStrategy(enable_push=False, git_lfs=False, repo_path=local)
    vault = Vault(
        source_dir=local,
        settings=VaultSettings(
            index_path=tmp_path / "index.db", state_path=tmp_path / "state.json"
        ),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        vault.mark_index_reconciled(vault.git_head())
        other = _git_clone(git_repo_pair, tmp_path)
        _git_commit(other, "remote.md", "# Starfruitquasar\n", "remote")
        _run_git(other, "push", "origin", "main")
        with patch.object(
            vault.index, "reindex", side_effect=RuntimeError("lost reindex")
        ):
            assert (
                await _process_push(vault, "github_webhook", "first")
            ).status_code == 200
        assert not vault.reader.search("starfruitquasar", mode="keyword")
        group = vault._write_callback.open_group("Grouped", None)
        first = local / "first.md"
        first.write_text("# First\n")
        with vault._write_callback.bind_group(group.handle, None):
            vault._write_callback.fire(first, "# First\n", "write")
        head = _run_git(local, "rev-parse", "HEAD").strip()
        assert (
            await _process_push(vault, "github_webhook", "second")
        ).status_code == 503
        assert vault.reader.search("starfruitquasar", mode="keyword")
        assert _run_git(local, "rev-parse", "HEAD").strip() == head
        second = local / "second.md"
        second.write_text("# Second\n")
        with vault._write_callback.bind_group(group.handle, None):
            vault._write_callback.fire(second, "# Second\n", "write")
        assert (
            vault._write_callback.close_group(group.handle, None)
            .result(timeout=5)
            .status
            == "committed"
        )
        assert set(
            _run_git(local, "show", "--format=", "--name-only", "HEAD").splitlines()
        ) == {"first.md", "second.md"}
    finally:
        vault.close()
        strategy.close()


@pytest.mark.parametrize("trigger", ["timer", "webhook"])
async def test_pending_generated_recovery_waits_for_group_close(
    git_repo_pair: GitRepoPair, trigger: str
) -> None:
    from markdown_vault_mcp._webhooks import _process_push
    from tests.fixtures.conflict_recovery import (
        _setup_generated_conflict,
        recovery_vault,
    )
    from tests.fixtures.git import _run_git

    root, vault_root, index = _setup_generated_conflict(git_repo_pair, False)
    strategy, vault = recovery_vault(vault_root, build=True)
    try:
        assert strategy.force_pull().reason == "rebased"
        assert index in strategy._pending_generated
        original = index.read_bytes()
        group = vault._write_callback.open_group("Grouped", None)
        note = vault_root / "grouped.md"
        note.write_text("# Grouped\n")
        with vault._write_callback.bind_group(group.handle, None):
            vault._write_callback.fire(note, "# Grouped\n", "write")
        head = _run_git(root, "rev-parse", "HEAD").strip()
        if trigger == "timer":
            assert not strategy.sync_once(vault_root, timer_driven=True)
            vault._reconcile_on_tick()
        else:
            assert (
                await _process_push(vault, "github_webhook", "deferred")
            ).status_code == 503
        assert {n.path for n in vault.reader.list_documents()} >= {"remote.md"}
        assert index.read_bytes() == original
        assert _run_git(root, "rev-parse", "HEAD").strip() == head
        assert index in strategy._pending_generated
        vault._write_callback.close_group(group.handle, None).result(timeout=5)
        assert vault.reconcile_index_with_head(source="test") == "current"
        assert index not in strategy._pending_generated
        assert "local.md" in index.read_text()
        assert (
            "index.md"
            in _run_git(root, "show", "--format=", "--name-only", "HEAD").splitlines()
        )
    finally:
        vault.close()
        strategy.close()


async def test_explicit_sync_repairs_generated_index_with_group_open(
    git_repo_pair: GitRepoPair,
) -> None:
    from markdown_vault_mcp._tools.git import _reconcile_after_pull
    from tests.fixtures.conflict_recovery import (
        _setup_generated_conflict,
        recovery_vault,
    )
    from tests.fixtures.git import _run_git

    root, vault_root, index = _setup_generated_conflict(git_repo_pair, False)
    strategy, vault = recovery_vault(vault_root, build=True)
    try:
        group = vault._write_callback.open_group("Grouped", None)
        note = vault_root / "grouped.md"
        note.write_text("# Grouped\n")
        with vault._write_callback.bind_group(group.handle, None):
            vault._write_callback.fire(note, "# Grouped\n", "write")
        result = vault.force_pull()
        assert result is not None and result.reason == "rebased"
        assert index in strategy._pending_generated
        await _reconcile_after_pull(vault, {})
        assert index not in strategy._pending_generated
        assert "local.md" in index.read_text()
        assert (
            "index.md"
            in _run_git(root, "show", "--format=", "--name-only", "HEAD").splitlines()
        )
        with vault._write_callback.bind_group(group.handle, None):
            assert not group.closed
        vault._write_callback.close_group(group.handle, None).result(timeout=5)
    finally:
        vault.close()
        strategy.close()
