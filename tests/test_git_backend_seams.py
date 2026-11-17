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
    token: str | None = None,
) -> tuple[PushScheduler, SyncHealthTracker]:
    lock = threading.Lock()
    bootstrap = RepoBootstrap(lock=lock, token=token, username="user", repo_url=None)
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
    with pytest.raises(PushBackendError, match="failure"):
        scheduler.do_push()
    assert health.snapshot() is not None
    assert health.snapshot().reason == PUSH_REASON_PUSH_FAILED  # type: ignore[union-attr]
    push.side_effect = None
    scheduler.flush()
    assert push.call_count == 2
    assert health.snapshot() is None


def test_structured_backend_reason_survives_redaction(tmp_path: Path) -> None:
    def fail(_path: Path) -> None:
        raise PushBackendError("fixture-secret", reason="non_fast_forward")

    scheduler, health = make_scheduler(tmp_path, token="fixture-secret", push=fail)
    scheduler.schedule_push()
    with pytest.raises(PushBackendError) as caught:
        scheduler.do_push()
    assert caught.value.reason == "non_fast_forward"
    assert "fixture-secret" not in str(caught.value)
    assert health.snapshot() is not None
    assert health.snapshot().reason == "non_fast_forward"  # type: ignore[union-attr]


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
def test_startup_failure_does_not_schedule_push(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, site: str
) -> None:
    secret = "fixture-secret"

    def fail(_path: Path) -> None:
        raise ConnectionError(f"offline {secret}")

    push = Mock(side_effect=fail if site == "push" else None)
    scheduler, health = make_scheduler(
        tmp_path,
        token=secret,
        push=push,
        has_unpushed=fail if site == "probe" else lambda _: True,
    )
    scheduler.push_if_unpushed()
    snapshot = health.snapshot()
    if site == "probe":
        assert snapshot is None
    else:
        assert snapshot is not None and snapshot.reason == PUSH_REASON_PUSH_FAILED
    assert secret not in caplog.text and "***" in caplog.text
    before = push.call_count
    push.side_effect = None
    scheduler.flush()
    assert push.call_count == before
    scheduler.schedule_push()
    scheduler.flush()
    assert push.call_count == before + 1
    assert health.snapshot() is None


@pytest.mark.parametrize("unhealthy", [False, True])
def test_probe_failure_preserves_existing_pending_work(
    tmp_path: Path, unhealthy: bool
) -> None:
    def fail(_path: Path) -> None:
        raise ConnectionError("offline")

    push = Mock()
    scheduler, health = make_scheduler(tmp_path, push=push, has_unpushed=fail)
    scheduler.schedule_push()
    if unhealthy:
        health.push_failed(PUSH_REASON_PUSH_FAILED)
    before = health.snapshot()
    scheduler.push_if_unpushed()
    assert health.snapshot() == before
    scheduler.flush()
    push.assert_called_once_with(tmp_path)


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

    def fail(_path: Path) -> None:
        if failure == "process":
            raise subprocess.CalledProcessError(
                1, [secret], output=secret, stderr=secret
            )
        raise ConnectionError(secret)

    scheduler, _ = make_scheduler(tmp_path, token=secret, push=fail)
    scheduler.schedule_push()
    with pytest.raises(PushBackendError) as caught:
        scheduler.do_push()
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert secret not in str(caught.value)
    assert "***" in str(caught.value)


@pytest.mark.parametrize(
    "site,stderr,reason",
    [
        (site, stderr, reason)
        for site in ("startup_probe", "startup_push", "deferred", "retry", "flush")
        for stderr, reason in (
            ("non-fast-forward", "non_fast_forward"),
            ("permission denied", "push_failed"),
        )
    ]
    + [(site, "connection", "push_failed") for site in ("deferred", "retry", "flush")],
)
def test_backend_process_rejection_preserves_sanitized_diagnostics(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    site: str,
    stderr: str,
    reason: str,
) -> None:
    import logging

    secret = "fixture-secret"

    def fail(_path: Path) -> None:
        if stderr == "connection":
            raise ConnectionError(f"offline {secret}")
        raise subprocess.CalledProcessError(
            1,
            ["/repos/non-fast-forward/hooks/push", secret],
            output=secret,
            stderr=f"rejected {stderr} for {secret}",
        )

    scheduler, health = make_scheduler(
        tmp_path,
        token=secret,
        push=fail,
        has_unpushed=fail if site == "startup_probe" else lambda _: True,
    )
    with caplog.at_level(logging.DEBUG):
        if site.startswith("startup"):
            scheduler.push_if_unpushed()
        else:
            scheduler.schedule_push()
            if site == "flush":
                scheduler.flush()
            else:
                scheduler.do_push_safe(retry=site == "retry")
    snapshot = health.snapshot()
    if site == "startup_probe":
        assert snapshot is None
    else:
        assert snapshot is not None
        assert snapshot.reason == reason
    if stderr != "connection":
        assert stderr in caplog.text
        assert "exit status 1" in caplog.text
    assert secret not in caplog.text
    assert "***" in caplog.text
    assert "Traceback" not in caplog.text


def test_sanitized_backend_exception_inside_exception_handler(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import traceback

    secret = "fixture-secret"

    def fail(_path: Path) -> None:
        raise ConnectionError(f"backend failed {secret}")

    scheduler, _ = make_scheduler(
        tmp_path, token=secret, push=fail, has_unpushed=lambda _: True
    )
    scheduler.schedule_push()
    startup, health = make_scheduler(
        tmp_path, token=secret, push=fail, has_unpushed=lambda _: True
    )
    try:
        raise ValueError(secret)
    except ValueError:
        with pytest.raises(PushBackendError) as caught:
            scheduler.do_push()
        formatted = "".join(traceback.format_exception(caught.value))
        caplog.clear()
        startup.push_if_unpushed()
    assert secret not in formatted
    assert "***" in formatted
    assert secret not in caplog.text
    assert "***" in caplog.text
    assert "git_remote_unsynced" in caplog.text
    snapshot = health.snapshot()
    assert snapshot is not None and snapshot.reason == PUSH_REASON_PUSH_FAILED
