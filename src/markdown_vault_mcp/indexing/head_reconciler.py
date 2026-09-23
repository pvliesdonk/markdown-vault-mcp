"""Keep the index reconciled with the git HEAD it was last built against (#1532).

A pull that moves HEAD used to reindex once, from the one call site that saw
the move.  If that reindex was lost (the index still building, a writer error,
a pull reported as not applied although HEAD advanced), nothing retried it:
the next pull found HEAD equal to the remote and had nothing to report, so the
index kept serving notes the working tree no longer held until someone
reindexed by hand.

:class:`IndexHeadReconciler` makes the reindex level-triggered instead.  It
records the HEAD the index was last reconciled against and reindexes whenever
the current HEAD differs, whatever moved it.  The record advances only on a
reindex that completed, so a lost one is retried by the next caller: the pull
loop's tick, a webhook delivery, or the ``git_sync`` tool.

The server's own commits move HEAD too, but describe writes the index already
holds.  :meth:`IndexHeadReconciler.note_own_commit` advances the record past
such a commit, and only when the commit sits directly on the recorded head, so
a server commit stacked on an unindexed external one never hides it.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Literal

from markdown_vault_mcp.exceptions import IndexUnavailableError

if TYPE_CHECKING:
    import contextlib
    from collections.abc import Callable
    from concurrent.futures import Future

logger = logging.getLogger(__name__)

ReconcileOutcome = Literal["current", "reindexed", "deferred", "failed"]
"""What :meth:`IndexHeadReconciler.reconcile` did.

``current``: the index already reflects HEAD (or HEAD is unreadable), nothing
ran.  ``reindexed``: a reindex completed and the record moved to HEAD.
``deferred``: the index is not built yet; a later call retries.  ``failed``:
the reindex raised; a later call retries.
"""


class IndexHeadReconciler:
    """Reindex until the index reflects the working tree's current HEAD.

    Thread-safe: the pull loop, webhook handlers, the ``git_sync`` tool and
    the write dispatcher's commits call it from different threads.
    """

    def __init__(
        self,
        *,
        read_head: Callable[[], str | None],
        reindex: Callable[[], object],
        pause_writes: Callable[[], contextlib.AbstractContextManager[None]],
    ) -> None:
        """Initialise with no head recorded, so the first reconcile reindexes.

        Args:
            read_head: Returns the working tree's current HEAD, or ``None``
                when it cannot be read.
            reindex: Runs an incremental reindex synchronously; raises
                :class:`IndexUnavailableError` while the index is unbuilt.
            pause_writes: Context manager holding writes still while the
                reindex runs, as every post-pull reindex did before.
        """
        self._read_head = read_head
        self._reindex = reindex
        self._pause_writes = pause_writes
        self._state_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._indexed_head: str | None = None
        self._pending: Future[Any] | None = None

    @property
    def indexed_head(self) -> str | None:
        """The HEAD the index was last reconciled against, if known."""
        return self._indexed_head

    def mark_reconciled(self, head: str | None) -> None:
        """Record that the index reflects *head*, without reindexing.

        Used where a reindex already ran outside this class (the boot
        reindex) or where the caller deliberately accepts the current index
        (boot reindex disabled).

        Args:
            head: The HEAD the index reflects; ``None`` forgets the record.
        """
        with self._state_lock:
            self._indexed_head = head

    def adopt_pending_reindex(self, pending: Future[Any], head: str | None) -> None:
        """Treat an already-submitted reindex as the reconcile of *head*.

        Until *pending* finishes, :meth:`reconcile` defers instead of pausing
        writes behind it and scanning a second time.  When it completes
        successfully, *head* is recorded; a failed or cancelled one records
        nothing, so the next reconcile reindexes.

        Args:
            pending: The submitted reindex (the server's boot reindex).
            head: The HEAD that reindex covers.
        """
        with self._state_lock:
            self._pending = pending

        def _finished(done: Future[Any]) -> None:
            with self._state_lock:
                if self._pending is done:
                    self._pending = None
                if not done.cancelled() and done.exception() is None:
                    self._indexed_head = head

        pending.add_done_callback(_finished)

    def note_own_commit(self, parent: str, new_head: str) -> None:
        """Advance the record past a commit of the server's own writes.

        Args:
            parent: HEAD before the commit.
            new_head: HEAD after it.
        """
        with self._state_lock:
            if self._indexed_head is not None and self._indexed_head == parent:
                self._indexed_head = new_head

    def reconcile(self, *, source: str) -> ReconcileOutcome:
        """Reindex when HEAD differs from the recorded head.

        Args:
            source: Which caller asked, for the log line.

        Returns:
            The :data:`ReconcileOutcome`.
        """
        pending = self._pending
        if pending is not None and not pending.done():
            return "deferred"
        head = self._read_head()
        if head is None or head == self._indexed_head:
            return "current"
        with self._run_lock:
            # Another caller may have reconciled while this one waited.
            if head == self._indexed_head:
                return "current"
            try:
                with self._pause_writes():
                    self._reindex()
            except IndexUnavailableError as exc:
                logger.debug(
                    "index_head_reconcile_deferred source=%s reason=%s",
                    source,
                    exc.reason,
                )
                return "deferred"
            except Exception:
                logger.error(
                    "index_head_reconcile_failed source=%s head=%s",
                    source,
                    head,
                    exc_info=True,
                )
                return "failed"
            with self._state_lock:
                self._indexed_head = head
        logger.info("index_head_reconciled source=%s head=%s", source, head)
        return "reindexed"
