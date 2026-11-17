"""Backend-independent push and optional batch versioning seams."""

from __future__ import annotations

import subprocess
import threading
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from markdown_vault_mcp.git import GitWriteStrategy
from markdown_vault_mcp.git.bootstrap import RepoBootstrap
from markdown_vault_mcp.git.health import SyncHealthTracker
from markdown_vault_mcp.git.push_scheduler import PushScheduler
from markdown_vault_mcp.git.types import PUSH_REASON_PUSH_FAILED

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))


def make_scheduler(
    root: Path,
    *,
    push: Callable[[Path], None] | None = None,
    has_unpushed: Callable[[Path], bool] | None = None,
) -> tuple[PushScheduler, SyncHealthTracker]:
    lock = threading.Lock()
    bootstrap = RepoBootstrap(lock=lock, token=None, username="user", repo_url=None)
    bootstrap.git_root = root
    health = SyncHealthTracker()
    scheduler = PushScheduler(
        lock=lock,
        bootstrap=bootstrap,
        health=health,
        enable_push=True,
        push_delay_s=0,
        push=push,
        has_unpushed=has_unpushed,
    )
    return scheduler, health


def test_injected_push_and_flush(tmp_path: Path) -> None:
    calls: list[Path] = []
    scheduler, health = make_scheduler(tmp_path, push=calls.append)
    health.push_failed(PUSH_REASON_PUSH_FAILED)
    scheduler.schedule_push()
    scheduler.do_push()
    assert calls == [tmp_path]
    assert health.snapshot() is None
    scheduler.flush()
    assert calls == [tmp_path]
    scheduler.schedule_push()
    scheduler.flush()
    assert calls == [tmp_path, tmp_path]


def test_failed_injected_push_remains_pending(tmp_path: Path) -> None:
    push = Mock(side_effect=RuntimeError("failure"))
    scheduler, health = make_scheduler(tmp_path, push=push)
    scheduler.schedule_push()
    with pytest.raises(RuntimeError, match="failure"):
        scheduler.do_push()
    assert health.snapshot() is not None
    assert health.snapshot().reason == PUSH_REASON_PUSH_FAILED  # type: ignore[union-attr]
    push.side_effect = None
    scheduler.flush()
    assert push.call_count == 2
    assert health.snapshot() is None


@pytest.mark.parametrize("unpushed", [False, True])
def test_injected_startup_probe_avoids_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unpushed: bool
) -> None:
    monkeypatch.setattr(
        subprocess, "run", Mock(side_effect=AssertionError("git called"))
    )
    calls: list[Path] = []
    scheduler, _ = make_scheduler(
        tmp_path, push=calls.append, has_unpushed=lambda _: unpushed
    )
    scheduler.push_if_unpushed()
    assert calls == ([tmp_path] if unpushed else [])


def test_default_push_is_looked_up_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduler, _ = make_scheduler(tmp_path)
    push = Mock()
    monkeypatch.setattr("markdown_vault_mcp.git.push_scheduler._push", push)
    scheduler.schedule_push()
    scheduler.do_push()
    push.assert_called_once_with(tmp_path, None, "user")


@pytest.mark.parametrize("case", ["missing", "no_ref", "failed", "empty", "ahead"])
def test_default_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    from markdown_vault_mcp.git import push_scheduler

    if case == "missing":
        monkeypatch.setattr(
            push_scheduler, "resolve_tracking_ref", Mock(side_effect=FileNotFoundError)
        )
    else:
        monkeypatch.setattr(
            push_scheduler,
            "resolve_tracking_ref",
            lambda _: None if case == "no_ref" else "origin/main",
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            Mock(
                return_value=subprocess.CompletedProcess(
                    [],
                    1 if case == "failed" else 0,
                    stdout="commit\n" if case == "ahead" else "",
                )
            ),
        )
    assert push_scheduler._has_unpushed_commits(tmp_path) is (case == "ahead")


@pytest.mark.parametrize(
    "repo_url,token,interval",
    [
        ("https://example.invalid/repo", None, 600),
        (None, "fixture-token", 600),
        (None, None, 0),
    ],
)
def test_assembly_returns_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repo_url: str | None,
    token: str | None,
    interval: int,
) -> None:
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.config_sections._assembly import _resolve_git
    from markdown_vault_mcp.git import VersionedStore

    monkeypatch.setattr(RepoBootstrap, "ensure_managed_repo", lambda _self, _path: None)
    store, actual_interval = _resolve_git(
        ProjectConfig(source_dir=tmp_path, git_repo_url=repo_url, git_token=token)
    )
    try:
        assert isinstance(store, VersionedStore)
        assert actual_interval == interval
    finally:
        store.close()


@pytest.mark.parametrize("fails_at", ["probe", "push"])
def test_startup_backend_failure_preserves_scoped_write(
    git_repo: Path, fails_at: str, caplog: pytest.LogCaptureFixture
) -> None:
    from markdown_vault_mcp._commit_scope import bound_commit_scope
    from markdown_vault_mcp.write_callback import WriteCallbackDispatcher

    secret = "fixture-secret"
    attempts: list[Path] = []

    def fail(path: Path) -> None:
        attempts.append(path)
        raise ConnectionError(f"backend unavailable {secret}")

    strategy = GitWriteStrategy(
        token=secret,
        enable_pull=False,
        enable_push=True,
        push_delay_s=0,
        git_lfs=False,
    )
    strategy._push_scheduler._has_unpushed = (
        fail if fails_at == "probe" else lambda _: True
    )
    strategy._push_scheduler._push = fail
    dispatcher = WriteCallbackDispatcher(strategy)
    try:
        note = git_repo / "new.md"
        note.write_text("saved")
        with bound_commit_scope("write") as scope:
            dispatcher.fire(note, "saved", "write")
            dispatcher.end_scope(scope)
        dispatcher.close()
        result = subprocess.run(
            ["git", "-C", str(git_repo), "show", "--pretty=", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "new.md" in result.stdout.splitlines()
        health = strategy.sync_health()
        assert health is not None and health.reason == PUSH_REASON_PUSH_FAILED
        assert secret not in caplog.text
        assert "***" in caplog.text
        assert strategy._push_pending
        strategy._push_scheduler._push = attempts.append
        strategy.flush()
        assert not strategy._push_pending
        assert strategy.sync_health() is None
    finally:
        dispatcher.close()
        strategy.close()


@pytest.mark.parametrize("fails_at", ["probe", "push"])
def test_startup_failure_is_retried_without_a_write(
    tmp_path: Path, fails_at: str
) -> None:
    def fail(_path: Path) -> None:
        raise ConnectionError("offline")

    scheduler, health = make_scheduler(
        tmp_path,
        has_unpushed=fail if fails_at == "probe" else lambda _: True,
        push=fail,
    )
    scheduler.push_if_unpushed()
    assert health.snapshot() is not None
    pushed: list[Path] = []
    scheduler._push = pushed.append
    scheduler.flush()
    assert pushed == [tmp_path]
    assert health.snapshot() is None
