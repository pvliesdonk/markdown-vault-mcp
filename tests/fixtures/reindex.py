"""Delayed native reindex results with joined, cancellation-safe timer cleanup."""

from __future__ import annotations

from concurrent.futures import Future
from contextlib import contextmanager
from threading import Timer
from typing import TYPE_CHECKING

from markdown_vault_mcp.types import ReindexResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from markdown_vault_mcp.facets.index import IndexFacet


@contextmanager
def delayed_reindex(
    delay: float,
) -> Iterator[Callable[[IndexFacet], Future[ReindexResult]]]:
    """Yield a native reindex producer and join its timers on exit.

    Args:
        delay: Seconds before each submitted reindex future completes.

    Yields:
        A producer matching IndexFacet.reindex_async with fixed result counts.
    """
    workers: list[Timer] = []

    def submit(_self: IndexFacet) -> Future[ReindexResult]:
        future: Future[ReindexResult] = Future()

        def finish() -> None:
            if future.set_running_or_notify_cancel():
                future.set_result(
                    ReindexResult(added=1, modified=0, deleted=0, unchanged=0)
                )

        worker = Timer(delay, finish)
        workers.append(worker)
        worker.start()
        return future

    try:
        yield submit
    finally:
        for worker in workers:
            worker.join()
