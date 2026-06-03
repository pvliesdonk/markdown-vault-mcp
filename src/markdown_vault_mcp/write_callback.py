"""Background dispatcher for deferred write callbacks (issue #175).

Extracted from :class:`~markdown_vault_mcp.collection.Collection` (issue #599)
so the git-commit dispatch concern lives apart from the index-write machinery
in :mod:`markdown_vault_mcp.indexing`.

A single daemon worker thread drains a FIFO queue, invoking the configured
``on_write`` callback (typically a git commit) off the write path, so write
methods return as soon as the FTS update lands.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.types import WriteCallback, WriteOperation

logger = logging.getLogger(__name__)


class WriteCallbackDispatcher:
    """Run write callbacks on a single background thread, in FIFO order.

    The worker starts lazily on the first :meth:`fire` (only when a callback
    is configured) and is joined by :meth:`close`. A ``None`` callback makes
    :meth:`fire` a no-op. A callback that raises is logged and skipped; the
    worker keeps processing subsequent items.
    """

    def __init__(self, on_write: WriteCallback | None) -> None:
        """Store the callback; the worker is started lazily by :meth:`fire`.

        Args:
            on_write: Invoked as ``on_write(abs_path, content, operation)`` for
                each fired write, or ``None`` to disable dispatch entirely.
        """
        self._on_write = on_write
        self._queue: queue.Queue[tuple[Path, str, WriteOperation] | None] = (
            queue.Queue()
        )
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()

    def fire(self, abs_path: Path, content: str, operation: WriteOperation) -> None:
        """Queue a callback invocation, starting the worker if needed.

        No-op when no callback is configured.

        Args:
            abs_path: Absolute path of the written file.
            content: File content at write time (empty for deletes).
            operation: The kind of write that occurred.
        """
        if self._on_write is None:
            return
        self._ensure_worker()
        self._queue.put((abs_path, content, operation))

    def _ensure_worker(self) -> None:
        """Start the background worker if it is not already running."""
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run, daemon=True, name="write-callback"
            )
            self._worker.start()

    def _run(self) -> None:
        """Worker loop: drain the queue until the sentinel is dequeued."""
        while True:
            item = self._queue.get()
            if item is None:
                break
            abs_path, content, operation = item
            on_write = self._on_write
            try:
                if on_write is None:
                    logger.error(
                        "Write callback is None in worker; dropping %s (%s)",
                        abs_path,
                        operation,
                    )
                    continue
                on_write(abs_path, content, operation)
            except Exception:
                logger.error(
                    "Write callback failed for %s (%s)",
                    abs_path,
                    operation,
                    exc_info=True,
                )

    def close(self, timeout: float = 30.0) -> None:
        """Drain pending callbacks and join the worker (bounded by ``timeout``).

        Safe to call when the worker was never started, and idempotent on a
        second call. Logs a warning if the worker does not finish in time.

        Args:
            timeout: Seconds to wait for the worker to drain and exit.
        """
        if self._worker is not None and self._worker.is_alive():
            self._queue.put(None)  # sentinel
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                logger.warning(
                    "Write-callback worker did not finish within %s s; "
                    "pending git commits may be lost.",
                    timeout,
                )
