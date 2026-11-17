"""Backend-independent push and optional batch versioning seams."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from markdown_vault_mcp.git import GitWriteStrategy, Versioner
from markdown_vault_mcp.git.bootstrap import RepoBootstrap
from markdown_vault_mcp.git.health import SyncHealthTracker
from markdown_vault_mcp.git.push_scheduler import PushScheduler
from markdown_vault_mcp.git.types import PUSH_REASON_PUSH_FAILED

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from markdown_vault_mcp.types import WriteBatchItem


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


def test_optional_batch_protocol() -> None:
    from markdown_vault_mcp.git import BatchVersioner

    class PerWrite:
        accepts_principal = True

        def __call__(self, *args: object, **kwargs: object) -> None:
            pass

    assert isinstance(GitWriteStrategy(), BatchVersioner)
    assert isinstance(PerWrite(), Versioner)
    assert not isinstance(PerWrite(), BatchVersioner)


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


def test_batch_and_per_write_dispatch() -> None:
    from markdown_vault_mcp._commit_scope import bound_commit_scope
    from markdown_vault_mcp.git import BatchVersioner
    from markdown_vault_mcp.write_callback import WriteCallbackDispatcher

    class PerWrite:
        accepts_principal = True

        def __init__(self) -> None:
            self.singles: list[Path] = []

        def __call__(
            self,
            path: Path,
            content: str,  # noqa: ARG002
            operation: str,  # noqa: ARG002
            **kwargs: object,  # noqa: ARG002
        ) -> None:
            self.singles.append(path)

    class Batched(PerWrite):
        accepts_batch = True

        def __init__(self) -> None:
            super().__init__()
            self.batches: list[tuple[str, list[Path]]] = []

        def on_write_batch(
            self, items: Sequence[WriteBatchItem], tool_name: str
        ) -> None:
            self.batches.append((tool_name, [item[0] for item in items]))

    plain, batch = PerWrite(), Batched()
    assert isinstance(batch, BatchVersioner)
    for callback in (plain, batch):
        dispatcher = WriteCallbackDispatcher(callback)
        try:
            with bound_commit_scope("write") as scope:
                dispatcher.fire(Path("a.md"), "a", "write")
                dispatcher.fire(Path("b.md"), "b", "write")
                dispatcher.end_scope(scope)
        finally:
            dispatcher.close()
    assert plain.singles == [Path("a.md"), Path("b.md")]
    assert batch.singles == []
    assert batch.batches == [("write", [Path("a.md"), Path("b.md")])]
