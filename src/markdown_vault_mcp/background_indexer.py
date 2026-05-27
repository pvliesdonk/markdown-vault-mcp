"""Background index orchestrator for issue #513.

Owns the daemon thread that runs ``Collection.build_index()`` and, when an
embedding provider is configured, ``Collection.build_embeddings()`` after
the MCP server lifespan has yielded. ``Collection`` itself stays purely
synchronous — indexing concurrency lives only here.

Lifecycle: ``start()`` spawns the worker (idempotent), ``stop(timeout)``
signals it and joins with a bounded wait, ``status`` returns a thread-safe
snapshot.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from markdown_vault_mcp.collection import Collection


logger = logging.getLogger(__name__)


class IndexStatus(TypedDict):
    """Snapshot of background index progress."""

    state: Literal["idle", "indexing", "embedding", "ready", "failed"]
    error: str | None
    documents_indexed: int
    chunks_indexed: int


class BackgroundIndexer:
    """Drives the post-handshake index build on a daemon thread.

    Args:
        collection: The synchronous :class:`Collection` to drive.
        has_provider: Whether an embedding provider is configured. When
            ``False``, the worker skips the embedding phase.
    """

    def __init__(self, collection: Collection, *, has_provider: bool) -> None:
        self._collection = collection
        self._has_provider = has_provider
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._status: IndexStatus = {
            "state": "idle",
            "error": None,
            "documents_indexed": 0,
            "chunks_indexed": 0,
        }

    @property
    def status(self) -> IndexStatus:
        """Thread-safe snapshot of the current status."""
        with self._lock:
            return dict(self._status)  # type: ignore[return-value]

    def start(self) -> None:
        """Spawn the worker thread. Idempotent — a second call is a no-op."""
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name="markdown-vault-background-indexer",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 30.0) -> bool:
        """Signal the worker to stop and join with a bounded wait.

        Returns:
            ``True`` if the thread exited within the timeout (or was never
            started); ``False`` if the join timed out and the daemon was
            abandoned.
        """
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        self._stop_event.set()
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning("background_indexer_stop_timed_out timeout=%.1f", timeout)
            return False
        return True

    # ------------------------------------------------------------------
    # Worker body
    # ------------------------------------------------------------------

    def _set(
        self,
        *,
        state: (
            Literal["idle", "indexing", "embedding", "ready", "failed"] | None
        ) = None,
        error: str | None = None,
        documents_indexed: int | None = None,
        chunks_indexed: int | None = None,
    ) -> None:
        with self._lock:
            if state is not None:
                self._status["state"] = state
            if error is not None:
                self._status["error"] = error
            if documents_indexed is not None:
                self._status["documents_indexed"] = documents_indexed
            if chunks_indexed is not None:
                self._status["chunks_indexed"] = chunks_indexed

    def _run(self) -> None:
        try:
            self._set(state="indexing")
            stats = self._collection.build_index()
            self._set(
                documents_indexed=stats.documents_indexed,
                chunks_indexed=stats.chunks_indexed,
            )
            if self._stop_event.is_set():
                logger.info("background_indexer_stopped_after_index")
                return
            if self._has_provider:
                self._set(state="embedding")
                self._collection.build_embeddings()
            self._set(state="ready")
            logger.info(
                "background_indexer_ready documents=%d chunks=%d",
                stats.documents_indexed,
                stats.chunks_indexed,
            )
        except Exception as exc:
            logger.error("background_indexer_failed", exc_info=True)
            self._set(state="failed", error=str(exc))
