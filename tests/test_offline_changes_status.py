"""``get_index_status`` reports whether offline changes were reconciled (#1542)."""

from __future__ import annotations

from concurrent.futures import Future
from typing import TYPE_CHECKING, cast

from markdown_vault_mcp.indexing.offline_changes import OfflineChanges
from markdown_vault_mcp.types import IndexStats, ReindexResult
from tests.test_index_coordinator import make_coordinator

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.indexing import IndexWriteCoordinator


def _stats(**kwargs: bool) -> IndexStats:
    return IndexStats(documents_indexed=1, chunks_indexed=1, skipped=0, **kwargs)


def _reindexed(**kwargs: bool) -> ReindexResult:
    return ReindexResult(added=0, modified=0, deleted=0, unchanged=1, **kwargs)


def _warm_coordinator(tmp_path: Path) -> IndexWriteCoordinator:
    """A coordinator over an index an earlier one built and closed."""
    first = make_coordinator(tmp_path)
    first.build_index()
    first.close(timeout=5)
    cast("FTSIndex", first._fts).close()
    return make_coordinator(tmp_path)


class TestOfflineChanges:
    def test_nothing_run_is_not_reconciled(self) -> None:
        assert OfflineChanges().state() == "not_reconciled"

    def test_a_pending_scan_is_reconciling(self) -> None:
        tracker = OfflineChanges()
        tracker.track(Future())
        assert tracker.state() == "reconciling"

    def test_a_finished_scan_reconciles(self) -> None:
        tracker = OfflineChanges()
        scan: Future[ReindexResult] = Future()
        tracker.track(scan)
        scan.set_result(_reindexed())
        assert tracker.state() == "reconciled"

    def test_a_failed_scan_does_not(self) -> None:
        tracker = OfflineChanges()
        scan: Future[ReindexResult] = Future()
        tracker.track(scan)
        scan.set_exception(OSError("disk gone"))
        assert tracker.state() == "not_reconciled"

    def test_a_cancelled_scan_does_not(self) -> None:
        tracker = OfflineChanges()
        scan: Future[ReindexResult] = Future()
        tracker.track(scan)
        scan.cancel()
        assert tracker.state() == "not_reconciled"

    def test_a_scan_of_an_empty_vault_folder_does_not(self) -> None:
        # The index is kept as it was when the folder is empty (#1791).
        tracker = OfflineChanges()
        scan: Future[ReindexResult] = Future()
        tracker.track(scan)
        scan.set_result(_reindexed(vault_root_empty=True))
        assert tracker.state() == "not_reconciled"

    def test_a_build_that_could_not_walk_every_folder_does_not(self) -> None:
        tracker = OfflineChanges()
        scan: Future[IndexStats] = Future()
        tracker.track(scan)
        scan.set_result(_stats(walk_complete=False))
        assert tracker.state() == "not_reconciled"

    def test_reconciled_stays_reconciled_through_a_later_scan(self) -> None:
        tracker = OfflineChanges()
        first: Future[ReindexResult] = Future()
        tracker.track(first)
        first.set_result(_reindexed())
        later: Future[ReindexResult] = Future()
        tracker.track(later)
        assert tracker.state() == "reconciled"
        later.set_exception(OSError("disk gone"))
        assert tracker.state() == "reconciled"


class TestCoordinatorReportsIt:
    def test_a_cold_build_reconciles(self, tmp_path: Path) -> None:
        coord = make_coordinator(tmp_path)
        try:
            coord.build_index()
            assert coord.get_index_status()["offline_changes"] == "reconciled"
        finally:
            coord.close(timeout=5)

    def test_a_warm_start_is_not_reconciled_until_a_reindex(
        self, tmp_path: Path
    ) -> None:
        warm = _warm_coordinator(tmp_path)
        try:
            warm.build_index()
            assert warm.get_index_status()["offline_changes"] == "not_reconciled"
            warm.reindex()
            assert warm.get_index_status()["offline_changes"] == "reconciled"
        finally:
            warm.close(timeout=5)

    def test_an_async_reindex_reconciles_a_warm_start(self, tmp_path: Path) -> None:
        warm = _warm_coordinator(tmp_path)
        try:
            warm.build_index()
            warm.reindex_async().result(timeout=10)
            assert warm.get_index_status()["offline_changes"] == "reconciled"
        finally:
            warm.close(timeout=5)

    def test_a_forced_build_reconciles_a_warm_start(self, tmp_path: Path) -> None:
        warm = _warm_coordinator(tmp_path)
        try:
            warm.build_index(force=True)
            assert warm.get_index_status()["offline_changes"] == "reconciled"
        finally:
            warm.close(timeout=5)
