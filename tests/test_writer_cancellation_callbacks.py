"""A crashed writer settles pending work without invoking callbacks under locks."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from concurrent.futures import Future

import pytest

from markdown_vault_mcp.indexing import BuildIndex, IndexWriter


class _StopWorker(BaseException):
    pass


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_crash_cancellation_callbacks_can_submit_without_deadlock() -> None:
    entered, release, callback_done = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    rejected: list[str] = []

    def fatal(_job: object, _ctx: object) -> None:
        entered.set()
        assert release.wait(5)
        raise _StopWorker()

    writer = IndexWriter(runners={"build_index": fatal}, ctx=None)
    writer.start()

    def external_submit() -> None:
        try:
            writer.submit(BuildIndex())
        except RuntimeError:
            rejected.append("external")

    def cancelled(_future: Future[object]) -> None:
        # Same-thread callbacks must not use the normal shutdown allowance to
        # enqueue an orphaned job on a crashed worker.
        try:
            writer.submit(BuildIndex())
        except RuntimeError:
            rejected.append("worker")
        caller = threading.Thread(target=external_submit)
        caller.start()
        caller.join(timeout=2)
        if not caller.is_alive():
            callback_done.set()

    try:
        active = writer.submit(BuildIndex())
        assert entered.wait(5)
        pending = writer.submit(BuildIndex())
        pending.add_done_callback(cancelled)
        release.set()
        with pytest.raises(_StopWorker):
            active.result(5)
        assert callback_done.wait(5)
        assert pending.cancelled()
        assert rejected == ["worker", "external"]
        assert writer.get_status()["queue_depth"] == 0
    finally:
        release.set()
        writer.close(timeout=5)


def test_close_from_worker_callback_is_joined_by_external_close() -> None:
    entered, release, callback_done = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def held(_job: object, _ctx: object) -> None:
        entered.set()
        assert release.wait(5)

    writer = IndexWriter(runners={"build_index": held}, ctx=None)
    writer.start()
    callback_threads: list[threading.Thread] = []

    def close_in_callback(_future: Future[object]) -> None:
        callback_threads.append(threading.current_thread())
        writer.close(timeout=5)
        callback_done.set()

    try:
        future = writer.submit(BuildIndex())
        assert entered.wait(5)
        future.add_done_callback(close_in_callback)
        release.set()
        assert callback_done.wait(5)
        writer.close(timeout=5)
        assert callback_threads == [writer._thread]
        assert writer._thread is not None and not writer._thread.is_alive()
    finally:
        release.set()
        writer.close(timeout=5)


def test_close_before_writer_start_is_terminal() -> None:
    writer = IndexWriter(runners={}, ctx=None)
    writer.close(timeout=0)
    assert writer.is_closed()
    with pytest.raises(RuntimeError, match="closed"):
        writer.submit(BuildIndex())
