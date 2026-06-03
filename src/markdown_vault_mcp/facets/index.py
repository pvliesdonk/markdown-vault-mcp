"""Index facet: the build / readiness / writer-status surface (#604).

A thin view over the
:class:`~markdown_vault_mcp.indexing.IndexWriteCoordinator` (build / reindex /
embeddings sync + async, plus the readiness and writer-status queries) and
:class:`~markdown_vault_mcp.managers.index.IndexManager`
(:meth:`embeddings_status`). It deliberately does NOT expose the coordinator's
internal surface (``close``, ``writer``, ``require_built``,
``mark_paths_dirty``, ``rebuild_embeddings``), which the root owns. Part of the
``collection.py`` facade decomposition (#576).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from concurrent.futures import Future
    from typing import Any

    from markdown_vault_mcp.indexing import IndexWriteCoordinator
    from markdown_vault_mcp.managers.index import IndexManager
    from markdown_vault_mcp.types import IndexStats, ReindexResult


class IndexFacet:
    """Index build, readiness, writer-status, and embeddings-status operations.

    Delegates 1:1 to the :class:`IndexWriteCoordinator` (build / readiness /
    writer status) and to :class:`IndexManager` (:meth:`embeddings_status`).
    """

    def __init__(
        self,
        *,
        coordinator: IndexWriteCoordinator,
        index_mgr: IndexManager,
    ) -> None:
        """Hold the collaborators the index operations delegate to.

        Args:
            coordinator: The shared :class:`IndexWriteCoordinator` owned by the
                root. Only its public operations are surfaced here.
            index_mgr: The shared :class:`IndexManager`, queried by
                :meth:`embeddings_status`.
        """
        self._coordinator = coordinator
        self._index_mgr = index_mgr

    def is_queryable(self) -> bool:
        """Return True when the FTS index is queryable (precondition snapshot).

        A captured build error does NOT demote queryability: it is
        diagnostic state surfaced via :meth:`get_index_status`, not a gate.
        """
        return self._coordinator.is_queryable()

    def start_background_build_index(self) -> None:
        """Spawn a daemon thread that runs :meth:`build_index` to completion.

        .. deprecated:: 1.28
           Superseded by :meth:`build_index_async`. Retained for legacy tests.
        """
        self._coordinator.start_background_build_index()

    def should_use_background_build(self) -> bool:
        """Return True iff the lifespan should route to the background build.

        .. deprecated:: 1.28
           Retained for legacy tests; the lifespan no longer branches on it.
        """
        return self._coordinator.should_use_background_build()

    def is_drained(self) -> bool:
        """Return True iff the IndexWriter has no pending or in-flight work.

        Reflects the moment of call only; pair with :meth:`write_generation`
        to detect a complete write cycle inside a read window.
        """
        return self._coordinator.is_drained()

    def write_generation(self) -> int:
        """Return the writer's monotonic completion counter.

        Increments once per completed job. Pair with :meth:`is_drained` to
        detect a write cycle inside a read window.
        """
        return self._coordinator.write_generation()

    def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Block until :meth:`is_drained`, or until *timeout* (best-effort)."""
        return self._coordinator.wait_for_drain(timeout)

    def get_index_status(self) -> dict[str, Any]:
        """Return a non-blocking eleven-key snapshot of build + writer state.

        Keys: ``status`` (``"queryable"`` | ``"building"`` | ``"failed"``),
        ``documents_indexed``, ``documents_indexed_error``, ``error``,
        ``last_reindex_error``, ``last_build_embeddings_error``, plus
        ``queue_depth``, ``in_flight``, ``dirty_paths``, ``dirty_embeddings``,
        ``write_generation`` merged from the writer. A captured build error
        appears in ``error`` as diagnostic context without demoting a
        ``queryable`` status; ``documents_indexed_error`` carries a SQLite
        read failure (``documents_indexed`` stays ``0``) (#583).
        """
        return self._coordinator.get_index_status()

    def wait_until_queryable(self, timeout: float | None = None) -> None:
        """Block until the FTS index is queryable, or raise.

        A captured build error does NOT block here; it surfaces as
        ``IndexUnavailableError(reason="build_failed")`` and is also readable
        via :meth:`get_index_status`. Library bucket-3/4 methods use the
        root's ``_require_built`` instead, which raises immediately.

        Raises:
            IndexUnavailableError: timeout expired (``reason="timeout"``), a
                build ran and failed (``reason="build_failed"``), or no build
                was ever scheduled (``reason="never_built"``).
        """
        self._coordinator.wait_until_queryable(timeout)

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index.

        Warm restarts (existing populated index, ``force=False``) are an O(1)
        no-op keyed on FTS state. ``force=True`` drops and rebuilds; config
        changes require ``force=True`` to apply (see issue #525).

        Returns:
            :class:`~markdown_vault_mcp.types.IndexStats` describing what was indexed.
        """
        return self._coordinator.build_index(force=force)

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Returns:
            :class:`~markdown_vault_mcp.types.ReindexResult` with counts applied.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
        """
        return self._coordinator.reindex()

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index.

        Args:
            force: If ``True``, rebuild from scratch even if a vector index
                already exists on disk.

        Returns:
            Total number of chunks embedded.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If ``embedding_provider`` or ``embeddings_path`` is unset.
        """
        return self._coordinator.build_embeddings(force=force)

    def build_index_async(self, *, force: bool = False) -> Future[IndexStats]:
        """Submit a full FTS index build and return the Future.

        Caller may ``.result()`` to wait or fire-and-forget. Warm-restart
        short-circuit returns an already-resolved Future without queuing a
        job, mirroring :meth:`build_index`.

        Args:
            force: When ``True``, drop and rebuild the index unconditionally.

        Returns:
            ``concurrent.futures.Future`` carrying the :class:`IndexStats`.
        """
        return self._coordinator.build_index_async(force=force)

    def reindex_async(self) -> Future[ReindexResult]:
        """Submit an incremental FTS reindex and return the Future.

        Does not require :meth:`build_index` first — the writer's FIFO queue
        orders any earlier :class:`BuildIndex` before this job. Writer-thread
        failures are surfaced via :meth:`get_index_status` (#561).
        """
        return self._coordinator.reindex_async()

    def build_embeddings_async(self, *, force: bool = False) -> Future[int]:
        """Submit a vector index build and return the Future.

        Does not require :meth:`build_index` first — FIFO ordering runs any
        earlier :class:`BuildIndex` first. Writer-thread failures are surfaced
        via :meth:`get_index_status` (#561).
        """
        return self._coordinator.build_embeddings_async(force=force)

    def embeddings_status(self) -> dict[str, Any]:
        """Return status information about the vector index.

        Returns:
            Dict with keys ``provider``, ``chunk_count``, ``path``,
            ``available``.
        """
        return self._index_mgr.embeddings_status()
