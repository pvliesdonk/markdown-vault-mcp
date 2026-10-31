"""Whether changes made while no server ran have reached the index (#1542).

A warm start trusts the index it finds without looking at the files, and
with ``boot_reindex`` off nothing compares the two until a reindex runs.
:class:`OfflineChanges` records whether a scan that compared the index with
every file on disk has finished in this process, so ``get_index_status`` can
say so. Only a successful build or reindex that saw the whole vault folder
counts: an empty folder keeps the index as it was (#1791), and a scan that
could not read a folder keeps the notes under it (#1625, #1785).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Literal

from markdown_vault_mcp.types import IndexStats, ReindexResult

if TYPE_CHECKING:
    from concurrent.futures import Future

OfflineChangesState = Literal["reconciled", "reconciling", "not_reconciled"]


class OfflineChanges:
    """Tracks the scans that bring offline changes into the index."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[Future[Any]] = set()
        self._reconciled = False

    def track(self, scan: Future[Any]) -> None:
        """Count *scan*, a full build or a reindex, until it settles.

        Args:
            scan: The future of the submitted build or reindex.
        """
        with self._lock:
            self._pending.add(scan)
        scan.add_done_callback(self._absorb)

    def state(self) -> OfflineChangesState:
        """Return where offline changes stand.

        A scan counts as soon as its future is done, which is before any
        caller waiting on it wakes, so a caller that has just waited on its
        own reindex reads its outcome here.

        Returns:
            ``"reconciled"`` once a scan covering the whole vault folder has
            finished, else ``"reconciling"`` while one is queued or running,
            else ``"not_reconciled"``.
        """
        with self._lock:
            for scan in [scan for scan in self._pending if scan.done()]:
                self._settle(scan)
            if self._reconciled:
                return "reconciled"
            return "reconciling" if self._pending else "not_reconciled"

    def _absorb(self, scan: Future[Any]) -> None:
        with self._lock:
            if scan in self._pending:
                self._settle(scan)

    def _settle(self, scan: Future[Any]) -> None:
        """Fold a done *scan* into the state; the caller holds the lock."""
        self._pending.discard(scan)
        if scan.cancelled() or scan.exception() is not None:
            return
        self._reconciled = self._reconciled or _covers_vault(scan.result())


def _covers_vault(result: object) -> bool:
    """Whether a finished scan compared the index with every file."""
    if isinstance(result, (IndexStats, ReindexResult)):
        return result.walk_complete and not result.vault_root_empty
    return False
