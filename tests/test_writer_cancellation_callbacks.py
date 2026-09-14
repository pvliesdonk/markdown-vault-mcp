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
