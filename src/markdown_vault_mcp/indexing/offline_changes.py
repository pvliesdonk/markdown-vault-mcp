"""Whether changes made while no server ran have reached the index (#1542).

A warm start trusts the index it finds without looking at the files, and
with ``boot_reindex`` off nothing compares the two until a reindex runs.
:class:`OfflineChanges` records whether a scan that compared the index with
every file on disk has finished in this process, so ``get_index_status`` can
say so. Only a successful build or reindex that saw the whole vault folder
counts: an empty folder keeps the index as it was (#1791), and a build that
could not walk every folder keeps their notes (#1785).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from concurrent.futures import Future

OfflineChangesState = Literal["reconciled", "reconciling", "not_reconciled"]


class OfflineChanges:
    """Tracks the scans that bring offline changes into the index."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending = 0
        self._reconciled = False

    def track(self, scan: Future[Any]) -> None:
        """Count *scan*, a full build or a reindex, until it settles.

        Args:
            scan: The future of the submitted build or reindex.
        """
        with self._lock:
            self._pending += 1
        scan.add_done_callback(self._settle)

    def state(self) -> OfflineChangesState:
        """Return where offline changes stand.

        Returns:
            ``"reconciled"`` once a scan covering the whole vault folder has
            finished, else ``"reconciling"`` while one is queued or running,
            else ``"not_reconciled"``.
        """
        with self._lock:
            if self._reconciled:
                return "reconciled"
            return "reconciling" if self._pending else "not_reconciled"

    def _settle(self, scan: Future[Any]) -> None:
        covered = (
            not scan.cancelled()
            and scan.exception() is None
            and _covers_vault(scan.result())
        )
        with self._lock:
            self._pending -= 1
            self._reconciled = self._reconciled or covered


def _covers_vault(result: object) -> bool:
    """Whether a finished scan compared the index with every file."""
    return getattr(result, "walk_complete", True) and not getattr(
        result, "vault_root_empty", False
    )
