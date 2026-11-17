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
from markdown_vault_mcp.git.push_scheduler import PushBackendError, PushScheduler
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
    with pytest.raises(PushBackendError, match="failure") as error:
        scheduler.do_push()
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
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


@pytest.mark.parametrize("site", ["probe", "push"])
@pytest.mark.parametrize("failure", ["connection", "process"])
def test_startup_backend_failure_is_sanitized_and_does_not_schedule(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, site: str, failure: str
) -> None:
    secret = "fixture-secret"
    lock = threading.Lock()
    bootstrap = RepoBootstrap(lock=lock, token=secret, username="user", repo_url=None)
    bootstrap.git_root = tmp_path
    health = SyncHealthTracker()

    def fail(_path: Path) -> None:
        if failure == "process":
            raise subprocess.CalledProcessError(
                1, ["backend", secret], output=secret, stderr=secret
            )
        raise ConnectionError(f"offline {secret}")

    push = Mock(side_effect=fail if site == "push" else None)
    scheduler = PushScheduler(
        lock=lock,
        bootstrap=bootstrap,
        health=health,
        enable_push=True,
        push_delay_s=0,
        push=push,
        has_unpushed=fail if site == "probe" else lambda _: True,
    )
    scheduler.push_if_unpushed()
    assert health.snapshot() is not None
    assert health.snapshot().reason == PUSH_REASON_PUSH_FAILED  # type: ignore[union-attr]
    assert secret not in caplog.text
    assert "***" in caplog.text
    before = push.call_count
    scheduler.flush()
    assert push.call_count == before


def test_startup_failure_preserves_scoped_write(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from markdown_vault_mcp._commit_scope import bound_commit_scope
    from markdown_vault_mcp.write_callback import WriteCallbackDispatcher

    push = Mock(side_effect=ConnectionError("offline"))
    monkeypatch.setattr(
        "markdown_vault_mcp.git.strategy.PushScheduler",
        partial(PushScheduler, push=push, has_unpushed=lambda _: True),
    )
    strategy = GitWriteStrategy(enable_push=True, push_delay_s=0, git_lfs=False)
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
        assert strategy.sync_health() is not None
        push.side_effect = None
        strategy.flush()
        assert strategy.sync_health() is None
        assert push.call_count == 2
    finally:
        dispatcher.close()
        strategy.close()


@pytest.mark.parametrize("failure", ["connection", "process"])
@pytest.mark.parametrize("route", ["normal", "retry", "flush"])
def test_injected_push_failure_never_logs_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failure: str, route: str
) -> None:
    import logging

    secret = "fixture-secret"
    lock = threading.Lock()
    bootstrap = RepoBootstrap(lock=lock, token=secret, username="user", repo_url=None)
    bootstrap.git_root = tmp_path
    health = SyncHealthTracker()

    def fail(_path: Path) -> None:
        if failure == "process":
            raise subprocess.CalledProcessError(
                1, ["backend", secret], output=secret, stderr=secret
            )
        raise ConnectionError(f"offline {secret}")

    scheduler = PushScheduler(
        lock=lock,
        bootstrap=bootstrap,
        health=health,
        enable_push=True,
        push_delay_s=0,
        push=fail,
    )
    scheduler.schedule_push()
    with caplog.at_level(logging.DEBUG):
        if route == "flush":
            scheduler.flush()
        else:
            scheduler.do_push_safe(retry=route == "retry")
    assert secret not in caplog.text
    assert "***" in caplog.text
    assert "Traceback" not in caplog.text
    assert health.snapshot() is not None


def test_default_cli_startup_failure_does_not_set_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduler, _ = make_scheduler(tmp_path, has_unpushed=lambda _: True)
    monkeypatch.setattr(
        "markdown_vault_mcp.git.push_scheduler._push",
        Mock(side_effect=subprocess.CalledProcessError(1, "git", stderr="rejected")),
    )
    scheduler.push_if_unpushed()
    push = Mock()
    monkeypatch.setattr("markdown_vault_mcp.git.push_scheduler._push", push)
    scheduler.flush()
    push.assert_not_called()


@pytest.mark.parametrize("site", ["probe", "push"])
def test_default_startup_oserror_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    scheduler, _ = make_scheduler(tmp_path)
    monkeypatch.setattr(
        "markdown_vault_mcp.git.push_scheduler._has_unpushed_commits",
        Mock(side_effect=OSError("probe"))
        if site == "probe"
        else Mock(return_value=True),
    )
    if site == "push":
        monkeypatch.setattr(
            "markdown_vault_mcp.git.push_scheduler._push",
            Mock(side_effect=OSError("push")),
        )
    with pytest.raises(OSError, match=site):
        scheduler.push_if_unpushed()


@pytest.mark.parametrize("failure", ["connection", "process"])
def test_injected_exception_has_no_original_context(
    tmp_path: Path, failure: str
) -> None:
    secret = "fixture-secret"
    lock = threading.Lock()
    bootstrap = RepoBootstrap(lock=lock, token=secret, username="user", repo_url=None)
    bootstrap.git_root = tmp_path
    health = SyncHealthTracker()

    def fail(_path: Path) -> None:
        if failure == "process":
            raise subprocess.CalledProcessError(
                1, [secret], output=secret, stderr=secret
            )
        raise ConnectionError(secret)

    scheduler = PushScheduler(
        lock=lock,
        bootstrap=bootstrap,
        health=health,
        enable_push=True,
        push_delay_s=0,
        push=fail,
    )
    scheduler.schedule_push()
    with pytest.raises(PushBackendError) as caught:
        scheduler.do_push()
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert secret not in str(caught.value)
    assert "***" in str(caught.value)
