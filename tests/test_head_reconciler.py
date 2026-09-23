"""Tests for IndexHeadReconciler: reindex until the index reflects HEAD (#1532)."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.indexing.head_reconciler import IndexHeadReconciler

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest


class _Repo:
    """A HEAD that tests move by hand, and a reindex that counts its calls."""

    def __init__(self, head: str | None = "a") -> None:
        self.head = head
        self.reindexes = 0
        self.fail_with: BaseException | None = None
        self.paused = 0

    def read_head(self) -> str | None:
        return self.head

    def reindex(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.reindexes += 1

    @contextlib.contextmanager
    def pause_writes(self) -> Iterator[None]:
        self.paused += 1
        yield


def _reconciler(repo: _Repo) -> IndexHeadReconciler:
    return IndexHeadReconciler(
        read_head=repo.read_head,
        reindex=repo.reindex,
        pause_writes=repo.pause_writes,
    )


def test_unknown_head_reindexes_once_then_is_current() -> None:
    repo = _Repo()
    rec = _reconciler(repo)

    assert rec.reconcile(source="test") == "reindexed"
    assert rec.reconcile(source="test") == "current"
    assert repo.reindexes == 1
    assert repo.paused == 1


def test_recorded_head_skips_the_reindex() -> None:
    repo = _Repo()
    rec = _reconciler(repo)
    rec.mark_reconciled("a")

    assert rec.reconcile(source="test") == "current"
    assert repo.reindexes == 0


def test_moved_head_reindexes() -> None:
    repo = _Repo()
    rec = _reconciler(repo)
    rec.mark_reconciled("a")
    repo.head = "b"

    assert rec.reconcile(source="test") == "reindexed"
    assert rec.indexed_head == "b"


def test_failed_reindex_is_retried_on_the_next_call() -> None:
    """The defect: a lost reindex was never retried once HEAD matched origin."""
    repo = _Repo()
    rec = _reconciler(repo)
    rec.mark_reconciled("a")
    repo.head = "b"
    repo.fail_with = RuntimeError("disk full")

    assert rec.reconcile(source="test") == "failed"
    assert rec.indexed_head == "a"

    repo.fail_with = None
    assert rec.reconcile(source="test") == "reindexed"
    assert rec.indexed_head == "b"


def test_unbuilt_index_defers_and_retries(caplog: pytest.LogCaptureFixture) -> None:
    repo = _Repo()
    rec = _reconciler(repo)
    repo.fail_with = IndexUnavailableError("not built", reason="never_built")

    with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp"):
        assert rec.reconcile(source="test") == "deferred"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    repo.fail_with = None
    assert rec.reconcile(source="test") == "reindexed"


def test_unreadable_head_does_nothing() -> None:
    repo = _Repo(head=None)
    rec = _reconciler(repo)

    assert rec.reconcile(source="test") == "current"
    assert repo.reindexes == 0


def test_own_commit_on_the_indexed_head_advances_it() -> None:
    repo = _Repo()
    rec = _reconciler(repo)
    rec.mark_reconciled("a")

    rec.note_own_commit("a", "b")
    repo.head = "b"

    assert rec.indexed_head == "b"
    assert rec.reconcile(source="test") == "current"
    assert repo.reindexes == 0


def test_own_commit_on_an_unindexed_head_does_not_hide_it() -> None:
    """A server commit stacked on an external one must not skip that one."""
    repo = _Repo()
    rec = _reconciler(repo)
    rec.mark_reconciled("a")

    rec.note_own_commit("external", "b")
    repo.head = "b"

    assert rec.indexed_head == "a"
    assert rec.reconcile(source="test") == "reindexed"


def test_own_commit_before_any_reconcile_is_ignored() -> None:
    repo = _Repo()
    rec = _reconciler(repo)

    rec.note_own_commit("a", "b")

    assert rec.indexed_head is None


def test_head_captured_before_the_reindex_is_what_gets_recorded() -> None:
    """HEAD moving during the reindex leaves the newer head to the next call."""
    repo = _Repo()
    repo.head = "b"

    def reindex_then_move() -> None:
        repo.reindexes += 1
        repo.head = "c"

    rec_moving = IndexHeadReconciler(
        read_head=repo.read_head,
        reindex=reindex_then_move,
        pause_writes=repo.pause_writes,
    )
    rec_moving.mark_reconciled("a")

    assert rec_moving.reconcile(source="test") == "reindexed"
    assert rec_moving.indexed_head == "b"
    assert rec_moving.reconcile(source="test") == "reindexed"
    assert rec_moving.indexed_head == "c"
