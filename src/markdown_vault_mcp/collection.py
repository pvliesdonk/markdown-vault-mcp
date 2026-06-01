"""Thin facade tying all markdown-vault-mcp modules together.

:class:`Collection` is the primary public API for the library.  MCP tools,
LangChain wrappers, and CLI commands all go through this class.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import re
import threading
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, Literal

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
)
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.types import (
    AttachmentContent,
    AttachmentInfo,
    BacklinkInfo,
    BrokenLinkInfo,
    CollectionStats,
    CommitDiff,
    DeleteResult,
    EditResult,
    GroupedResult,
    HistoryEntry,
    IndexStats,
    MostLinkedNote,
    NoteContent,
    NoteContext,
    NoteInfo,
    OutlinkInfo,
    ReindexResult,
    RenameResult,
    WriteCallback,
    WriteResult,
)
from markdown_vault_mcp.utils import effective_attachment_extensions
from markdown_vault_mcp.writer import (
    BuildEmbeddings,
    BuildIndex,
    IndexWriter,
    ProcessDirtyPaths,
    ReindexAll,
    WriterContext,
    run_build_embeddings,
    run_build_index,
    run_flush_dirty_embeddings,
    run_process_dirty_paths,
    run_reindex_all,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

_DEFAULT_STATE_SUBDIR = ".markdown_vault_mcp"
_DEFAULT_STATE_FILENAME = "state.json"


def _resolve_chunk_strategy(strategy: str | ChunkStrategy) -> ChunkStrategy:
    """Return a concrete ChunkStrategy from a string name or pass-through.

    Args:
        strategy: Either ``"heading"``, ``"whole"``, or a :class:`ChunkStrategy`
            instance.

    Returns:
        A concrete :class:`ChunkStrategy` instance.

    Raises:
        ValueError: If *strategy* is an unrecognised string name.
    """
    if isinstance(strategy, str):
        if strategy == "heading":
            return HeadingChunker()
        if strategy == "whole":
            return WholeDocumentChunker()
        raise ValueError(
            f"Unknown chunk_strategy {strategy!r}. "
            "Valid string values: 'heading', 'whole'."
        )
    return strategy


class Collection:
    """Facade over FTS5 index, vector index, and change tracker.

    Instantiate once per collection root.  Callers must invoke
    :meth:`build_index` before bucket-3 relational/FTS-backed queries
    (:meth:`get_backlinks`, :meth:`get_outlinks`, :meth:`get_similar`,
    :meth:`get_context`, :meth:`get_connection_path`, :meth:`get_toc`)
    or the bucket-4 coordinators :meth:`reindex` and
    :meth:`build_embeddings`; otherwise
    :exc:`~markdown_vault_mcp.exceptions.IndexUnavailableError` is raised.
    :meth:`build_index` must also precede :meth:`start` — see
    :meth:`start` for the rationale.
    Bucket-1 file operations (:meth:`read`, :meth:`write`, :meth:`edit`,
    :meth:`delete`, :meth:`rename`, :meth:`write_attachment`) and bucket-2
    aggregate queries (:meth:`search`, :meth:`list`, :meth:`stats`, …)
    work on an unbuilt index — bucket-1 hits disk directly; bucket-2
    returns whatever is currently in the index (empty on cold start).
    See issue #525.

    **Index lifecycle (issues #513, #526, #559).** The MCP server
    lifespan submits a :class:`~markdown_vault_mcp.writer.BuildIndex`
    job to the single-owner
    :class:`~markdown_vault_mcp.writer.IndexWriter` via
    :meth:`build_index_async` and yields immediately. On a warm
    restart the persisted FTS completeness sentinel (PR #526) causes
    :meth:`build_index_async` to return an already-resolved
    ``Future`` in O(1) without touching the writer queue. On a cold
    restart the writer thread runs the job asynchronously while the
    lifespan yields; bucket-3/4 MCP tool *clients* block on the
    :class:`markdown_vault_mcp._server_queryable.needs_queryable`
    decorator, which calls :meth:`wait_until_queryable` with a
    bounded default timeout
    (``MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S``, default 60s). The
    library stays honest: bucket-3/4 *methods* keep the PR #525
    raise-immediately contract via :meth:`_require_built`.
    Internal callers (lifespan, git pull loop, CLI, direct library
    users) get the raise contract and handle "not ready" with
    caller-appropriate logic — never block.

    **Thread safety (issue #519):** every public method on this class is safe
    to call from any thread, concurrently with other reads and writes from
    any other thread. Index mutations (FTS + vector index) are serialised
    by the single-owner :class:`~markdown_vault_mcp.writer.IndexWriter`
    thread (#559); file-mutation operations on disk are serialised via
    ``_file_write_lock`` (RLock) so two MCP write tools racing on the
    same path do not tear. ``close()`` is safe from any thread; after
    ``close()`` the collection must not be used. Cross-method atomicity
    (e.g. read-then-write without intervening concurrent write) is the
    caller's responsibility — pass ``if_match=`` to write methods for
    optimistic concurrency. ``fork()`` is not supported. See ``docs/design.md``
    "Collection thread-safety contract" for the underlying per-thread
    SQLite-connection model.

    Args:
        source_dir: Root directory of the markdown collection.
        index_path: Path to the SQLite index file.  ``None`` (default) uses
            an in-memory database that is discarded when the object is
            collected.
        embeddings_path: Base path for the ``{path}.npy`` and
            ``{path}.json`` sidecar files.  ``None`` (default) means
            semantic search is disabled.
        embedding_provider: Provider used to generate embeddings.  Required
            when *embeddings_path* is set.
        read_only: When ``True`` (default), write operations raise
            :exc:`~markdown_vault_mcp.exceptions.ReadOnlyError`.
        state_path: Path to the hash-state JSON file used by
            :class:`~markdown_vault_mcp.tracker.ChangeTracker`.  Defaults to
            ``{source_dir}/.markdown_vault_mcp/state.json``.
        indexed_frontmatter_fields: Frontmatter keys whose values are
            promoted to the ``document_tags`` table for structured filtering.
        required_frontmatter: If provided, documents missing any listed field
            are excluded from the index entirely.
        chunk_strategy: ``"heading"`` (default), ``"whole"``, or a custom
            :class:`~markdown_vault_mcp.scanner.ChunkStrategy` instance.
        on_write: Optional callback invoked after every successful write
            operation.  Signature:
            ``Callable[[Path, str, Literal["write","edit","delete","rename"]], None]``.
        git_strategy: Optional git strategy used for background git tasks (e.g.
            periodic fetch + ff-only updates). Started via :meth:`start`.
        git_pull_interval_s: Interval in seconds for periodic pulls. ``0``
            disables the pull loop.
        exclude_patterns: Glob patterns (relative to *source_dir*) for files
            and directories to exclude from indexing.
        attachment_extensions: Allowlist of extensions (without leading dot)
            for binary attachments.  ``["*"]`` accepts all extensions.
        max_attachment_size_mb: Maximum binary attachment size in megabytes.
            ``0`` disables the limit (default ``1.0``).
        max_note_read_bytes: Maximum bytes returned by full-document reads.
            ``0`` disables the limit (default ``262144``, i.e. 256 KB).
    """

    def __init__(
        self,
        *,
        source_dir: Path,
        index_path: Path | None = None,
        embeddings_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        read_only: bool = True,
        state_path: Path | None = None,
        indexed_frontmatter_fields: list[str] | None = None,
        required_frontmatter: list[str] | None = None,
        chunk_strategy: str | ChunkStrategy = "heading",
        on_write: WriteCallback | None = None,
        git_strategy: GitWriteStrategy | None = None,
        git_pull_interval_s: int = 0,
        exclude_patterns: list[str] | None = None,
        attachment_extensions: list[str] | None = None,
        max_attachment_size_mb: float = 1.0,
        max_note_read_bytes: int = 262144,
        chunks_per_file: int = 2,
        snippet_words: int = 200,
        length_downweight_alpha: float = 0.25,
        max_chunk_words: int = 400,
    ) -> None:
        self._source_dir = source_dir
        self._index_path = index_path
        self._embeddings_path = embeddings_path
        self._embedding_provider = embedding_provider
        self._read_only = read_only
        self._indexed_frontmatter_fields: list[str] = indexed_frontmatter_fields or []
        self._required_frontmatter = required_frontmatter
        # Only inject max_chunk_words when the caller has not provided a
        # custom ChunkStrategy instance or an explicit string name override.
        if isinstance(chunk_strategy, str) and chunk_strategy == "heading":
            self._chunk_strategy: ChunkStrategy = HeadingChunker(
                max_chunk_words=max_chunk_words
            )
        else:
            # NOTE: When a caller passes an explicit chunk_strategy instance
            # (e.g. HeadingChunker(max_chunk_words=None) for legacy H1/H2-only
            # behaviour), we honour their construction as-is. The Collection-level
            # max_chunk_words only takes effect for the conventional default
            # ("heading" string), so explicit-instance callers retain full control.
            self._chunk_strategy = _resolve_chunk_strategy(chunk_strategy)
        self._on_write = on_write
        self._git_strategy = git_strategy
        self._git_pull_interval_s = git_pull_interval_s
        self._exclude_patterns = exclude_patterns
        self._attachment_extensions = attachment_extensions
        self._max_attachment_size_mb = max_attachment_size_mb
        self._max_note_read_bytes = max_note_read_bytes

        # Default state path: {source_dir}/.markdown_vault_mcp/state.json
        if state_path is None:
            self._state_path = (
                source_dir / _DEFAULT_STATE_SUBDIR / _DEFAULT_STATE_FILENAME
            )
        else:
            self._state_path = state_path

        # Sub-module construction.
        db_path: Path | str = index_path if index_path is not None else ":memory:"
        self._fts = FTSIndex(
            db_path=db_path,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields or None,
        )
        self._tracker = ChangeTracker(self._state_path)

        # True once build_index() has completed successfully; gates
        # bucket-3 (relational queries) and bucket-4 (reindex /
        # build_embeddings). See issue #525.
        self._index_built = False

        # Background-build coordination (issue #513 PR1 attempt 7). The
        # event is the blocking primitive `wait_until_queryable()` waits
        # on; it is pre-set so a freshly constructed Collection that never
        # called build_index() does not silently look "queryable". The
        # background path clears the event before spawning the thread
        # and the worker sets it in its finally clause.
        self._background_build_thread: threading.Thread | None = None
        self._background_build_done: threading.Event = threading.Event()
        self._background_build_done.set()
        self._background_build_error: BaseException | None = None
        self._background_started: bool = False

        # Lock for file-mutation atomicity only (#559). The IndexWriter
        # thread is the serialization point for index mutations; this lock
        # serialises ONLY the read-modify-write of files in DocumentManager
        # so two MCP write tools racing on the same path don't tear.
        self._file_write_lock = threading.RLock()

        # Manager modules (dependency-injected, no back-reference).
        from markdown_vault_mcp.managers.document import DocumentManager
        from markdown_vault_mcp.managers.index import IndexManager
        from markdown_vault_mcp.managers.link import LinkManager
        from markdown_vault_mcp.managers.search import SearchManager

        # 1. LinkManager (no deps)
        self._link_mgr = LinkManager(fts=self._fts, source_dir=self._source_dir)
        # 2. IndexManager (needs fts, tracker — NOT search_mgr)
        #    get_vectors/set_vectors use late-binding lambdas that capture
        #    self._search_mgr; they are only called at runtime after all
        #    managers are constructed.  No write_lock — the IndexWriter
        #    thread is the sole mutator of indices (#559).
        self._index_mgr = IndexManager(
            fts=self._fts,
            tracker=self._tracker,
            source_dir=self._source_dir,
            embeddings_path=self._embeddings_path,
            embedding_provider=self._embedding_provider,
            chunk_strategy=self._chunk_strategy,
            exclude_patterns=self._exclude_patterns,
            required_frontmatter=self._required_frontmatter,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields,
            # Late-binding closures: self._search_mgr is assigned below and
            # only accessed at call-time, not during IndexManager.__init__.
            get_vectors=lambda: self._search_mgr.vectors,
            set_vectors=lambda v: setattr(self._search_mgr, "vectors", v),
        )
        # Single-owner writer thread for all index mutations (#559).
        self._writer_ctx = WriterContext(index_manager=self._index_mgr)
        self._writer = IndexWriter(
            runners={
                "build_index": run_build_index,
                "reindex_all": run_reindex_all,
                "build_embeddings": run_build_embeddings,
                "process_dirty_paths": run_process_dirty_paths,
                "flush_dirty_embeddings": run_flush_dirty_embeddings,
            },
            ctx=self._writer_ctx,
        )
        self._writer_ctx.writer = self._writer
        self._writer.start()
        # 3. SearchManager (receives IndexManager callbacks via constructor)
        self._search_mgr = SearchManager(
            fts=self._fts,
            source_dir=self._source_dir,
            embeddings_path=self._embeddings_path,
            embedding_provider=self._embedding_provider,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields,
            exclude_patterns=self._exclude_patterns,
            attachment_extensions=self._attachment_extensions,
            link_manager=self._link_mgr,
            # rebuild_embeddings is invoked from SearchManager._load_vectors when a
            # VectorIndexCompatibilityError fires (embedding model upgrade); it runs
            # on the search thread (asyncio thread pool), not the writer thread.
            # Routing through self._writer.submit().result() preserves the
            # single-owner invariant (#559): only the writer thread mutates indexes.
            rebuild_embeddings=lambda: self._writer.submit(
                BuildEmbeddings(force=True)
            ).result(),
            chunks_per_file=chunks_per_file,
            snippet_words=snippet_words,
            length_downweight_alpha=length_downweight_alpha,
        )
        # 4. DocumentManager (mark_paths_dirty routes through the writer)
        self._doc_mgr = DocumentManager(
            fts=self._fts,
            source_dir=self._source_dir,
            write_lock=self._file_write_lock,
            chunk_strategy=self._chunk_strategy,
            read_only=self._read_only,
            exclude_patterns=self._exclude_patterns,
            attachment_extensions=self._attachment_extensions,
            max_attachment_size_mb=self._max_attachment_size_mb,
            max_note_read_bytes=self._max_note_read_bytes,
            on_write_callback=self._fire_write_callback,
            mark_paths_dirty=self._mark_paths_dirty_for_writer,
        )

        # Deferred write callback queue (issue #175).  Git commit (on_write
        # callback) runs in a background worker thread so write methods
        # return immediately after the FTS update.
        self._callback_queue: queue.Queue[tuple[Path, str, str] | None] = queue.Queue()
        self._callback_worker: threading.Thread | None = None
        self._callback_worker_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def pause_writes(self) -> Iterator[None]:
        """Block file-mutation write operations until the context exits.

        Holds the :attr:`_file_write_lock` so concurrent
        :class:`DocumentManager` write/edit/delete/rename calls block on
        the lock until the context exits. Index mutations on the
        :class:`IndexWriter` thread continue unaffected — the writer
        thread does not contend on this lock.  Reads and search remain
        unblocked at the Python level.
        """
        with self._file_write_lock:
            yield

    def sync_from_remote_before_index(self) -> None:
        """One-time git fetch + ff-only update before build_index().

        Intended to run during server startup before the initial index build.
        No reindex is triggered here because build_index() will scan the updated
        working tree.
        """
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.sync_once(self._source_dir)

    def start(self) -> None:
        """Start background tasks for this Collection (e.g. git pull loop).

        Call :meth:`build_index` **before** :meth:`start`. The git pull
        loop wires :meth:`reindex` (bucket 4) as its ``on_pull`` callback,
        and ``reindex`` raises :exc:`IndexUnavailableError` on an unbuilt
        index — so a pull event firing before the initial build would
        crash the loop thread.
        """
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.start(
            repo_path=self._source_dir,
            pull_interval_s=self._git_pull_interval_s,
            pause_writes=self.pause_writes,
            on_pull=self.reindex,
        )

    def stop(self) -> None:
        """Stop background tasks (e.g. git pull loop) without closing the collection.

        Safe to call multiple times.  A no-op if no pull loop was started.
        The SQLite connection and write callback remain open; only the pull
        loop thread is signalled to stop.
        """
        if self._git_strategy is not None:
            self._git_strategy.stop()

    def close(self) -> None:
        """Release resources held by the collection.

        Flushes deferred embeddings and pending write callbacks, then
        closes the SQLite connection and git strategy.
        """
        # 0. Join the legacy background-build thread FIRST.  Its worker
        # body calls self.build_index() → self._writer.submit(...).result(),
        # so the inner submit must complete against an OPEN writer.  If
        # the writer were closed before this join, a still-running bg
        # build would observe submit() raising RuntimeError, capture it
        # into _background_build_error, and leave the collection
        # non-queryable.  Only legacy tests exercise this path;
        # production uses build_index_async via lifespan.
        #
        # Read the thread reference under _file_write_lock (matches the
        # lock held when start_background_build_index assigns it).
        # daemon=True keeps a stuck thread from holding the process;
        # cooperative cancellation inside _index_mgr.build_index is a
        # follow-up.
        with self._file_write_lock:
            thread = self._background_build_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=30.0)
            if thread.is_alive():
                logger.warning(
                    "close: background build thread did not exit within "
                    "30s; abandoning (daemon thread does not block process)"
                )

        # 0a. THEN close the IndexWriter so no further index mutations
        # can be submitted while we tear down downstream resources. The
        # hasattr guard handles the case where __init__ failed mid-way
        # and _writer was never assigned (#559). The writer drains its
        # own pending jobs cleanly before returning, including any work
        # the bg-build thread submitted before it terminated.
        if hasattr(self, "_writer") and self._writer is not None:
            self._writer.close(timeout=30.0)

        # 1. Deferred embedding updates are flushed by the IndexWriter
        # before its close() returns; no further flush needed here (#559).

        # 2. Drain the write-callback queue (git commits).
        if self._callback_worker is not None and self._callback_worker.is_alive():
            self._callback_queue.put(None)  # sentinel
            self._callback_worker.join(timeout=30)
            if self._callback_worker.is_alive():
                logger.warning(
                    "Write-callback worker did not finish within 30 s; "
                    "pending git commits may be lost."
                )

        # 3. Close git strategy (flush push, etc.).
        if self._git_strategy is not None:
            self._git_strategy.close()
        if (
            self._on_write is not None
            and self._on_write is not self._git_strategy
            and hasattr(self._on_write, "close")
        ):
            self._on_write.close()  # type: ignore[union-attr]

        # 4. Close SQLite.
        self._fts.close()

    # ------------------------------------------------------------------
    # Indexing readiness (issue #525)
    # ------------------------------------------------------------------

    def _require_built(self) -> None:
        """Raise :exc:`IndexUnavailableError` if :meth:`build_index` has not run."""
        if not self._index_built:
            raise IndexUnavailableError(
                "Index not built. Call build_index() before this method.",
                reason="never_built",
            )

    def is_queryable(self) -> bool:
        """Return True when the structural preconditions for serving FTS
        queries are met (in-process precondition snapshot).

        Checks ``_index_built`` is True and ``_background_build_done`` is
        set. Necessary but not sufficient — actual queryability is
        determined at use time (a corrupted on-disk database satisfies
        these preconditions but queries still fail).

        A captured ``_background_build_error`` from a previous failed
        attempt does NOT demote queryability: the error is diagnostic
        state about the most recent build attempt, surfaced via
        :meth:`get_index_status`'s ``error`` field, not a control-flow
        gate.

        Lock-free by design: plain attribute reads + Event.is_set().
        Assumes CPython GIL semantics for cross-thread visibility.
        """
        if not self._index_built:
            return False
        return self._background_build_done.is_set()

    def start_background_build_index(self) -> None:
        """Spawn a daemon thread that runs :meth:`build_index` to completion.

        .. deprecated:: 1.28
           Superseded by :meth:`build_index_async`, which submits a
           :class:`BuildIndex` job to the single-owner
           :class:`~markdown_vault_mcp.writer.IndexWriter` thread (#559).
           The MCP server lifespan no longer calls this method; only
           legacy tests retain it. Prefer :meth:`build_index_async`
           for fire-and-forget initial builds and
           :meth:`build_index` for synchronous builds with
           Collection-level state management.

        Idempotent: second call after a successful start, after a
        clean completion, OR after a failed ``thread.start()`` is a
        no-op. The method is one-shot per Collection lifetime;
        operator recovery from a failed start is via CLI
        ``markdown-vault-mcp index`` or process restart, NOT by
        calling this method again.

        The worker thread catches ``BaseException`` → captures into
        ``_background_build_error`` → always sets
        ``_background_build_done`` in its finally clause.

        If ``thread.start()`` itself raises (system thread exhaustion
        is the realistic case), the same capture-and-set happens
        synchronously so callers waiting on the event never hang.
        """

        def _worker() -> None:
            try:
                self.build_index()
            except Exception as exc:
                self._background_build_error = exc
                logger.exception("Background index build failed")
            except BaseException as exc:
                # BaseException-family (KeyboardInterrupt / SystemExit /
                # asyncio.CancelledError): capture so waiters observe the
                # failure, set the done event in finally, then re-raise so
                # the thread terminates.
                self._background_build_error = exc
                logger.exception("Background index build interrupted")
                raise
            finally:
                self._background_build_done.set()

        with self._file_write_lock:
            if self._background_started:
                return
            self._background_started = True
            self._background_build_error = None
            self._background_build_done.clear()
            thread = threading.Thread(
                target=_worker,
                name="markdown-vault-mcp.background-build",
                daemon=True,
            )
            self._background_build_thread = thread
            try:
                thread.start()
            except Exception as exc:
                # Synchronously surface the failure so waiters unblock.
                self._background_build_error = exc
                self._background_build_done.set()
                raise

    def should_use_background_build(self) -> bool:
        """Return True iff the lifespan should route to the background
        FTS build path.

        .. deprecated:: 1.28
           The MCP server lifespan no longer branches on this predicate
           — it always submits a :class:`BuildIndex` job to the
           :class:`~markdown_vault_mcp.writer.IndexWriter` (#559). The
           method survives only for legacy tests that still exercise
           :meth:`start_background_build_index`.

        Returns True only for cold on-disk DBs (index_path is a real
        file path AND the FTS completeness sentinel from PR #526 is
        absent). Returns False for:
        - warm on-disk DBs (sentinel present — synchronous
          build_index() short-circuits in O(1));
        - in-memory DBs (no index_path or ":memory:" — no sentinel
          possible; full sync scan, acceptable for test scenarios).
        """
        # In-memory has no persistent state, so a "warm vs cold" notion
        # doesn't apply; always synchronous.
        if self._index_path is None or str(self._index_path) == ":memory:":
            return False
        return not self._fts.is_build_completed()

    def get_index_status(self) -> dict[str, Any]:
        """Return a non-blocking snapshot of background-build state.

        Shape: ``{"status": "queryable" | "building" | "failed",
        "documents_indexed": int, "error": str | None}`` merged with
        :meth:`IndexWriter.get_status` (``queue_depth``, ``in_flight``,
        ``dirty_paths``, ``dirty_embeddings``).

        - ``"queryable"``: ``_index_built`` is True and the build event is
          set — a completed build exists; captures an error as diagnostic
          context in ``error`` but does not demote the status.
        - ``"failed"``: precondition does not hold AND the build event is
          set AND ``_background_build_error`` is non-None; ``error`` carries
          its message.
        - ``"building"``: anything else — event cleared (writer in flight)
          OR event set but ``_index_built`` is False (never scheduled).

        ``documents_indexed`` is taken from :meth:`FTSIndex.list_notes` and
        so reflects whatever rows are currently committed — progress is
        observable in the ``"building"`` state as the count rises.

        ``error`` carries the diagnostic message from the last background
        build attempt that captured an exception, independent of ``status``.
        """
        if self.is_queryable():
            status = "queryable"
            error: str | None = (
                str(self._background_build_error)
                if self._background_build_error is not None
                else None
            )
        elif (
            self._background_build_done.is_set()
            and self._background_build_error is not None
        ):
            status = "failed"
            error = str(self._background_build_error)
        else:
            status = "building"
            error = None
        try:
            documents_indexed = len(self._fts.list_notes())
        except Exception:
            logger.debug(
                "get_index_status: list_notes failed; reporting 0",
                exc_info=True,
            )
            documents_indexed = 0
        result = {
            "status": status,
            "documents_indexed": documents_indexed,
            "error": error,
        }
        result.update(self._writer.get_status())
        return result

    def wait_until_queryable(self, timeout: float | None = None) -> None:
        """Block until the FTS index is queryable, or raise.

        Control flow (each step in order):

        1. ``_background_build_done.wait(timeout)`` — if False
           (timed out), raise
           :exc:`IndexUnavailableError("…timed out…")`.
        2. If ``_index_built`` is False, raise
           :exc:`IndexUnavailableError("…never scheduled…")` — guards
           the never-scheduled case (event pre-set, no error, no
           build, no thread). Without it, callers on a fresh
           Collection would silently return success.
        3. Otherwise return.

        A captured ``_background_build_error`` from a previous failed
        attempt does NOT raise: the error is diagnostic state about
        the most recent build attempt, surfaced via
        :meth:`get_index_status`'s ``error`` field, not a control-flow
        gate. Callers needing the diagnostic must read
        :meth:`get_index_status`.

        This method is opt-in for the MCP-layer `needs_queryable`
        decorator and for external callers that explicitly want to
        wait. Library bucket-3/4 methods do NOT call this — they call
        :meth:`_require_built` which raises immediately. That
        separation is the boundary that closed attempt 6's hole.

        Args:
            timeout: Maximum seconds to wait on the completion event.
                ``None`` (default) blocks indefinitely. MCP tool
                callers are protected from infinite hangs by the
                bounded default in the decorator (60s) and by
                client-side deadlines.

        Raises:
            IndexUnavailableError: Either the timeout expired before
                the build event was set (``reason="timeout"``), or the
                build did not complete successfully and ``_index_built``
                remained False (``reason="never_built"``).
        """
        if not self._background_build_done.wait(timeout=timeout):
            raise IndexUnavailableError(
                f"Index build still in progress; timed out after {timeout}s.",
                reason="timeout",
            )
        if not self._index_built:
            raise IndexUnavailableError(
                "Index not built: background build was never scheduled "
                "or did not complete successfully. "
                "Check get_index_status() for diagnostic details, or call "
                "build_index() or build_index_async() to retry.",
                reason="never_built",
            )

    @property
    def _vectors(self) -> VectorIndex | None:
        """Bridge property: vector index is owned by SearchManager."""
        return self._search_mgr.vectors

    @_vectors.setter
    def _vectors(self, value: VectorIndex | None) -> None:
        self._search_mgr.vectors = value

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
        filters: dict[str, str] | None = None,
        folder: str | None = None,
        chunks_per_file: int | None = None,
        snippet_words: int | None = None,
    ) -> list[GroupedResult]:
        """Search the collection.

        Args:
            query: Search string.
            limit: Maximum number of files (not chunks) to return.
            mode: ``"keyword"`` for BM25 FTS5, ``"semantic"`` for cosine
                similarity, or ``"hybrid"`` for Reciprocal Rank Fusion of both.
            filters: Dict of ``{frontmatter_key: value}`` pairs (AND semantics).
                Only works for fields in ``indexed_frontmatter_fields``.
            folder: If provided, restrict results to documents in this folder
                (and its sub-folders).
            chunks_per_file: Maximum number of sections returned per file.
                ``None`` uses the server default configured at startup.
            snippet_words: Width of the snippet window in words.  ``0`` returns
                the full chunk.  ``None`` uses the server default.

        Returns:
            List of :class:`~markdown_vault_mcp.types.GroupedResult` ordered
            by descending file score (max of section scores).  Each result
            wraps one document with up to ``chunks_per_file`` sections.

        Raises:
            ValueError: If *mode* is ``"semantic"`` or ``"hybrid"`` but no
                embedding provider or embeddings path is configured.
        """
        return self._search_mgr.search(
            query,
            limit=limit,
            mode=mode,
            filters=filters,
            folder=folder,
            chunks_per_file=chunks_per_file,
            snippet_words=snippet_words,
        )

    # ------------------------------------------------------------------
    # Read / list
    # ------------------------------------------------------------------

    def read(self, path: str, *, section: str | None = None) -> NoteContent | None:
        """Read the full content of a document from disk.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).
            section: When provided, return only the section whose heading
                matches *section* exactly (case-sensitive). Pass the
                ``heading`` value from a ``search`` result unchanged for
                guaranteed match. ``None`` (the default) returns the whole
                document. Raises :exc:`ValueError` if the section is not found.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent` instance, or ``None``
            if the file does not exist.
        """
        return self._doc_mgr.read(path, section=section)

    def list(
        self,
        *,
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
    ) -> list[NoteInfo | AttachmentInfo]:
        """List documents (and optionally attachments) in the collection.

        Args:
            folder: If provided, only return documents in this folder (and
                sub-folders).
            pattern: Unix glob matched against the relative path using
                :func:`fnmatch.fnmatch`.  Example: ``"Journal/*.md"``.
            include_attachments: When ``True``, also return non-.md files
                that match the attachment allowlist.  Each
                :class:`~markdown_vault_mcp.types.AttachmentInfo` entry
                includes ``kind="attachment"`` and ``mime_type``.

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` (and
            optionally :class:`~markdown_vault_mcp.types.AttachmentInfo`)
            objects.
        """
        return self._search_mgr.list(
            folder=folder, pattern=pattern, include_attachments=include_attachments
        )

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index.

        If the persisted FTS index already contains documents and *force*
        is ``False``, this is a no-op — the short-circuit is keyed solely
        on FTS state, so warm restarts (new process, same database file)
        return immediately rather than re-scanning the vault.
        ``force=True`` drops all existing data and rebuilds from scratch.

        .. note::
           Config changes (``exclude_patterns``, ``required_frontmatter``)
           do not re-trigger a scan on warm restart because they are not
           part of the short-circuit key. To apply a config change to a
           pre-existing index, call ``build_index(force=True)``. See
           issue #525.

        Args:
            force: When ``True``, drop and rebuild the index unconditionally.

        Returns:
            :class:`~markdown_vault_mcp.types.IndexStats` describing what was indexed.
        """
        if not force and self._fts.is_build_completed():
            existing = self._fts.list_notes()
            if existing:
                logger.debug(
                    "build_index: index already populated (%d docs), skipping",
                    len(existing),
                )
                self._index_built = True
                # Recovery: clear any captured background error + signal queryable.
                self._background_build_error = None
                self._background_build_done.set()
                return IndexStats(
                    documents_indexed=len(existing),
                    chunks_indexed=0,
                    skipped=0,
                )

        # Reset before the (potentially destructive) rebuild so a mid-build
        # exception leaves the Collection visibly not-queryable. The sentinel
        # is cleared too so a crash mid-loop is detectable by the next
        # process (rows without sentinel = partial — see issue #525).
        self._index_built = False
        self._fts.clear_build_completed()
        # Route the actual scan through the IndexWriter so all index
        # mutations are serialised on the single writer thread (#559).
        result = self._writer.submit(BuildIndex(force=force)).result()
        self._fts.set_build_completed()
        self._index_built = True
        # Recovery: clear any captured background error + signal queryable.
        self._background_build_error = None
        self._background_build_done.set()
        return result

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Returns:
            :class:`~markdown_vault_mcp.types.ReindexResult` with counts of changes
            applied.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
        """
        self._require_built()
        return self._writer.submit(ReindexAll()).result()

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index.

        Args:
            force: If ``True``, rebuild from scratch even if a vector index
                already exists on disk.

        Returns:
            Total number of chunks embedded.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If ``embedding_provider`` or ``embeddings_path`` is
                not configured.
        """
        self._require_built()
        return self._writer.submit(BuildEmbeddings(force=force)).result()

    def build_index_async(self, *, force: bool = False) -> Future[IndexStats]:
        """Submit a full FTS index build and return the Future.

        Caller may either ``.result()`` to wait or fire-and-forget.
        On submission, clears ``_index_built`` and the background-build
        completion event so concurrent readers see "building" until the
        writer thread finishes the job.  Attaches a done-callback that
        flips ``_index_built`` to True (and sets the completion event)
        on success, or captures the exception on
        ``_background_build_error`` while still setting the event so
        waiters unblock.  This is the async counterpart to the
        Collection-level state-management performed by the synchronous
        :meth:`build_index`.

        Warm-restart short-circuit: if the persisted FTS sentinel
        (PR #526) is set and the index already contains documents, this
        method returns an already-resolved ``Future`` without submitting
        a writer job.  This mirrors the synchronous
        :meth:`build_index` O(1) fast path so async and sync callers
        observe identical warm-restart behaviour.

        Args:
            force: When ``True``, drop and rebuild the index unconditionally.

        Returns:
            ``concurrent.futures.Future`` carrying the
            :class:`IndexStats` once the worker completes the job.
        """
        # Warm-restart short-circuit: if FTS is already populated and the
        # sentinel is set, return an already-resolved Future without
        # touching the writer queue.  Mirrors :meth:`build_index`.
        if not force and self._fts.is_build_completed():
            existing = self._fts.list_notes()
            if existing:
                logger.debug(
                    "build_index_async: index already populated (%d docs), skipping",
                    len(existing),
                )
                self._index_built = True
                self._background_build_error = None
                self._background_build_done.set()
                fut: Future[IndexStats] = Future()
                fut.set_result(
                    IndexStats(
                        documents_indexed=len(existing),
                        chunks_indexed=0,
                        skipped=0,
                    )
                )
                return fut

        # Cold (or forced) build: reset state before submission so
        # concurrent readers see the collection as "building" until the
        # job completes.  The sentinel is also cleared so a crash
        # mid-build is detectable by the next process.
        self._index_built = False
        self._fts.clear_build_completed()
        self._background_build_error = None
        self._background_build_done.clear()

        # If submit() raises (typically RuntimeError when the writer is
        # already closed, but also BaseException variants like
        # KeyboardInterrupt mid-put), the event would stay cleared and
        # the done-callback would never attach — any caller already on
        # ``wait_until_queryable()`` would block until timeout.  Restore
        # the event with the captured error so waiters unblock with the
        # real cause, then re-raise to the caller.
        try:
            future = self._writer.submit(BuildIndex(force=force))
        except BaseException as exc:
            self._background_build_error = exc
            self._background_build_done.set()
            raise

        def _on_done(fut: Future[IndexStats]) -> None:
            try:
                fut.result()
            except BaseException as exc:
                self._background_build_error = exc
                logger.exception("Async index build failed")
                # Leave _index_built False; sentinel left cleared so the
                # next process treats this as partial.
                self._background_build_done.set()
                return
            self._fts.set_build_completed()
            self._index_built = True
            self._background_build_error = None
            self._background_build_done.set()

        future.add_done_callback(_on_done)
        return future

    def reindex_async(self) -> Future[ReindexResult]:
        """Submit an incremental FTS reindex and return the Future.

        Unlike the synchronous :meth:`reindex`, this method does NOT
        require :meth:`build_index` to have run first.  The
        :class:`~markdown_vault_mcp.writer.IndexWriter`'s FIFO queue
        guarantees that any :class:`BuildIndex` job submitted earlier
        (e.g. from the server lifespan via
        :meth:`build_index_async`) runs before this :class:`ReindexAll`,
        so the writer thread sees a built index when it dequeues the
        job.
        """
        return self._writer.submit(ReindexAll())

    def build_embeddings_async(self, *, force: bool = False) -> Future[int]:
        """Submit a vector index build and return the Future.

        Unlike the synchronous :meth:`build_embeddings`, this method
        does NOT require :meth:`build_index` to have run first.  The
        :class:`~markdown_vault_mcp.writer.IndexWriter`'s FIFO queue
        guarantees that any :class:`BuildIndex` job submitted earlier
        (e.g. from the server lifespan via
        :meth:`build_index_async`) runs before this
        :class:`BuildEmbeddings`, so the writer thread sees a built
        index when it dequeues the job.
        """
        return self._writer.submit(BuildEmbeddings(force=force))

    def embeddings_status(self) -> dict:
        """Return status information about the vector index.

        Returns:
            Dict with keys ``provider``, ``chunk_count``, ``path``,
            ``available``.
        """
        return self._index_mgr.embeddings_status()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the indexed collection.

        Returns:
            Sorted list of folder strings (``""`` for the collection root).
        """
        return self._search_mgr.list_folders()

    def list_tags(self, field: str = "tags") -> list[str]:
        """Return all distinct values indexed for a given frontmatter field.

        If *field* was not in ``indexed_frontmatter_fields``, returns ``[]``.

        Args:
            field: Frontmatter key to query (default: ``"tags"``).

        Returns:
            Sorted list of distinct value strings.
        """
        return self._search_mgr.list_tags(field)

    def get_toc(self, path: str) -> list[dict[str, Any]]:
        """Return table of contents for a document.

        Queries the FTS sections table for headings and prepends the document
        title as a synthetic H1 entry. The result depends on the FTS index, so
        cold-start callers must build the index first (bucket 3).

        Args:
            path: Relative path to the document (e.g. ``"notes/intro.md"``).

        Returns:
            List of ``{"heading": str, "level": int}`` dicts ordered by
            position, with the document title prepended as level 1.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        self._require_built()
        return self._doc_mgr.get_toc(path)

    def get_backlinks(self, path: str) -> list[BacklinkInfo]:
        """Return all documents that link to the given document.

        Args:
            path: Relative path of the target document
                (e.g. ``"notes/topic.md"``).

        Returns:
            List of :class:`~markdown_vault_mcp.types.BacklinkInfo` objects
            for each document that contains a link pointing to ``path``.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        self._require_built()
        return self._link_mgr.get_backlinks(path)

    def get_outlinks(self, path: str) -> list[OutlinkInfo]:
        """Return all links from the given document to other documents.

        The ``exists`` field on each :class:`~markdown_vault_mcp.types.OutlinkInfo`
        indicates whether the target document is currently indexed.

        Args:
            path: Relative path of the source document
                (e.g. ``"notes/topic.md"``).

        Returns:
            List of :class:`~markdown_vault_mcp.types.OutlinkInfo` objects for
            each link originating from ``path``.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        self._require_built()
        return self._link_mgr.get_outlinks(path)

    def get_broken_links(self, *, folder: str | None = None) -> list[BrokenLinkInfo]:
        """Return all links whose target does not exist in the collection.

        Args:
            folder: If provided, restrict to source documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.BrokenLinkInfo` objects.
        """
        return self._link_mgr.get_broken_links(folder=folder)

    def get_similar(
        self,
        path: str,
        *,
        limit: int = 10,
        chunks_per_file: int | None = None,
    ) -> list[GroupedResult]:
        """Return semantically similar documents grouped by file.

        See :meth:`SearchManager.get_similar` for details.  Returns
        :class:`~markdown_vault_mcp.types.GroupedResult` objects ordered by
        descending file score; each result wraps one document with up to
        ``chunks_per_file`` sections.

        Args:
            path: Relative path of the reference document.
            limit: Maximum number of files to return.
            chunks_per_file: Maximum sections per result file.

        Returns:
            List of grouped results.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
        """
        self._require_built()
        return self._search_mgr.get_similar(
            path, limit=limit, chunks_per_file=chunks_per_file
        )

    def get_recent(
        self, *, limit: int = 20, folder: str | None = None
    ) -> list[NoteInfo]:
        """Return the most recently modified documents.

        Args:
            limit: Maximum number of documents to return.
            folder: If provided, restrict to documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` objects
            ordered by modification time (most recent first).
        """
        return self._search_mgr.get_recent(limit=limit, folder=folder)

    def get_context(
        self,
        path: str,
        *,
        similar_limit: int = 5,
        link_limit: int = 10,
    ) -> NoteContext:
        """Return a consolidated context dossier for a document.

        Combines backlinks, outlinks, similar notes, folder peers, and
        indexed frontmatter tags into a single response, saving the caller
        multiple round trips.

        Args:
            path: Relative path of the document (e.g. ``"notes/topic.md"``).
            similar_limit: Maximum number of similar notes to include.
            link_limit: Maximum number of backlinks and outlinks to include.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContext` object.  Its
            ``similar`` field is a list of
            :class:`~markdown_vault_mcp.types.GroupedResult` entries, each
            with exactly one section (chunks_per_file=1) so the dossier
            stays compact.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        self._require_built()
        return self._search_mgr.get_context(
            path, similar_limit=similar_limit, link_limit=link_limit
        )

    def get_orphan_notes(self) -> list[NoteInfo]:
        """Return all documents with no inbound or outbound links.

        A document is an orphan if it has zero outlinks and is not referenced
        by any other document's links.

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` objects,
            ordered by path.
        """
        return self._link_mgr.get_orphan_notes()

    def get_most_linked(self, *, limit: int = 10) -> list[MostLinkedNote]:
        """Return the documents with the most inbound links.

        Args:
            limit: Maximum number of results to return. Default 10.

        Returns:
            List of :class:`~markdown_vault_mcp.types.MostLinkedNote` ordered
            by backlink_count descending.
        """
        return self._link_mgr.get_most_linked(limit=limit)

    def get_connection_path(
        self, source: str, target: str, max_depth: int = 10
    ) -> list[str] | None:
        """Return the shortest undirected path between two notes.

        Treats the link graph as undirected — a link in either direction
        counts as a connection.  Uses BFS with a configurable depth cap.

        Args:
            source: Vault-relative path of the starting note.
            target: Vault-relative path of the destination note.
            max_depth: Maximum path length in edges.  Clamped to ``[1, 10]``.
                Defaults to ``10``.

        Returns:
            Ordered list of vault-relative paths from *source* to *target*
            (inclusive), or ``None`` if unreachable within *max_depth* hops.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If *source* or *target* is not found in the index.
        """
        self._require_built()
        return self._link_mgr.get_connection_path(source, target, max_depth=max_depth)

    # ------------------------------------------------------------------
    # Git history query methods
    # ------------------------------------------------------------------

    def get_history(
        self,
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
    ) -> list[HistoryEntry]:
        """Return commits that touched a note or the whole vault.

        When *path* is ``None``, queries the full vault history.  Returns an
        empty list for vaults whose source directory is not inside a git
        repository.

        Args:
            path: Vault-relative path of the note to filter on (e.g.
                ``"notes/alpha.md"``).  Must end with ``.md``.  ``None``
                returns vault-wide history.
            since: ISO 8601 datetime string or git date expression (e.g.
                ``"1 week ago"``).  Passed as ``--since`` to ``git log``.
                ``None`` disables the filter.
            until: ISO 8601 datetime string or git date expression, passed as
                ``--until`` to ``git log``.  ``None`` disables the filter.
                Both ``since`` and ``until`` boundaries are **inclusive**: a
                commit whose committer date equals either endpoint is included
                in the result.
            limit: Maximum number of commits to return.  Clamped to
                ``[1, 100]``.  Defaults to ``20``.

        Returns:
            List of :class:`~markdown_vault_mcp.types.HistoryEntry` ordered
            newest-first.  Empty list when the vault has no git history or
            the note has no commits in the given range.  The
            ``paths_changed`` field on each entry is populated for vault-wide
            queries (``path=None``); it is always empty for single-note
            queries, since the path is already determined by the query
            arguments — callers know which file the commit touched without
            needing it echoed back.

        Raises:
            ValueError: If *path* is provided but fails path validation.
        """
        if self._git_strategy is None:
            return []
        abs_path: Path | None = None
        if path is not None:
            abs_path = self._validate_path(path)
        return self._git_strategy.get_file_history(
            self._source_dir, abs_path, since, limit, until=until
        )

    def get_diff(
        self,
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
    ) -> str | list[CommitDiff]:
        """Return the diff of a note between a reference point and HEAD.

        Exactly one of *since_sha* or *since_timestamp* must be supplied.

        Args:
            path: Vault-relative path of the note to diff.  Must end with
                ``.md``.
            since_sha: A commit SHA (full or abbreviated, at least 4 hex
                digits) to diff from.  Mutually exclusive with
                *since_timestamp*.
            since_timestamp: ISO 8601 datetime string, resolved via
                ``git rev-list --before=<ts> -1 HEAD`` to the most recent
                commit at or before that instant.  Boundary is
                **inclusive**: a commit whose committer date equals
                *since_timestamp* IS the resolved ref.  Mutually exclusive
                with *since_sha*.
            per_commit: When ``False`` (default), return a single unified diff
                string from the reference point to HEAD.  When ``True``,
                return one :class:`~markdown_vault_mcp.types.CommitDiff` per
                intervening commit.
            limit: When *per_commit* is ``True``, cap the number of
                intervening commits returned to the *limit* most recent ones.
                Clamped to ``[1, 100]``.  ``None`` (the default) means
                unbounded (still bounded by the underlying ``since..HEAD``
                range).  Silently ignored when *per_commit* is ``False``.

        Returns:
            A unified diff string when *per_commit* is ``False``, or a list of
            :class:`~markdown_vault_mcp.types.CommitDiff` when *per_commit* is
            ``True``.  Returns an empty string / empty list when the note has
            no changes in the given range.

        Raises:
            ValueError: If exactly one of *since_sha* / *since_timestamp* is
                not supplied, *since_sha* contains invalid characters, or the
                resolved ref is not found in history.
        """
        if self._git_strategy is None:
            return [] if per_commit else ""

        if (since_sha is None) == (since_timestamp is None):
            raise ValueError(
                "Exactly one of 'since_sha' or 'since_timestamp' must be provided"
            )

        abs_path = self._validate_path(path)

        if since_sha is not None and not re.fullmatch(r"[0-9a-f]{4,40}", since_sha):
            raise ValueError(
                f"Invalid SHA {since_sha!r}: must be 4-40 lowercase hex digits"
            )

        return self._git_strategy.get_file_diff(
            self._source_dir,
            abs_path,
            ref=since_sha,
            per_commit=per_commit,
            since_timestamp=since_timestamp,
            limit=limit if per_commit else None,
        )

    def stats(self) -> CollectionStats:
        """Return collection-wide statistics.

        Returns:
            :class:`~markdown_vault_mcp.types.CollectionStats` snapshot.
        """

        rows = self._fts.list_notes()
        doc_count = len(rows)

        # Chunk count via the public FTSIndex method.
        chunk_count = self._fts.count_chunks()

        folders = self._fts.list_folders()
        folder_count = len(folders)

        semantic_available = (
            self._embedding_provider is not None and self._embeddings_path is not None
        )

        exts = effective_attachment_extensions(self._attachment_extensions)
        attachment_extensions = ["*"] if "*" in exts else sorted(exts)

        return CollectionStats(
            document_count=doc_count,
            chunk_count=chunk_count,
            folder_count=folder_count,
            semantic_search_available=semantic_available,
            indexed_frontmatter_fields=list(self._indexed_frontmatter_fields),
            attachment_extensions=attachment_extensions,
            link_count=self._fts.count_links(),
            broken_link_count=self._fts.count_broken_links(),
            orphan_count=self._fts.count_orphans(),
        )

    # ------------------------------------------------------------------
    # Write operations (delegated to DocumentManager)
    # ------------------------------------------------------------------

    def _validate_path(self, path: str) -> Path:
        """Resolve a relative path and validate it is inside source_dir.

        Args:
            path: Relative document path.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path escapes the source directory or does
                not end with ``.md``.
        """
        from markdown_vault_mcp.utils import validate_path

        return validate_path(path, self._source_dir)

    def _validate_attachment_path(self, path: str) -> Path:
        """Resolve and validate a non-.md attachment path."""
        return self._doc_mgr._validate_attachment_path(path)

    def _ensure_callback_worker(self) -> None:
        """Start the background write-callback worker if not running."""
        with self._callback_worker_lock:
            if self._callback_worker is not None and self._callback_worker.is_alive():
                return

            def _worker() -> None:
                while True:
                    item = self._callback_queue.get()
                    if item is None:
                        break
                    abs_path, content, operation = item
                    try:
                        if self._on_write is None:
                            logger.error(
                                "Write callback is None in worker; dropping %s (%s)",
                                abs_path,
                                operation,
                            )
                            continue
                        self._on_write(abs_path, content, operation)
                    except Exception:
                        logger.error(
                            "Write callback failed for %s (%s)",
                            abs_path,
                            operation,
                            exc_info=True,
                        )

            self._callback_worker = threading.Thread(
                target=_worker, daemon=True, name="write-callback"
            )
            self._callback_worker.start()

    def _fire_write_callback(
        self, abs_path: Path, content: str, operation: str
    ) -> None:
        """Submit a write callback to the background worker thread."""
        if self._on_write is None:
            return
        self._ensure_callback_worker()
        self._callback_queue.put((abs_path, content, operation))

    def _mark_paths_dirty_for_writer(self, paths: Iterable[str]) -> None:
        """Route DocumentManager dirty-marks through the writer (#559).

        Called by :class:`DocumentManager` after a successful write,
        edit, delete, or rename.  Stages the affected vault-relative
        paths on the :class:`IndexWriter` dirty set and submits a
        :class:`ProcessDirtyPaths` job so the FTS update runs on the
        single-owner writer thread.  The dirty set is updated
        unconditionally; only the follow-up :class:`ProcessDirtyPaths`
        submission is skipped when the writer is closed (avoiding a
        ``RuntimeError`` on a closed writer during shutdown).
        """
        self._writer.mark_dirty(paths)
        if self._writer.is_closed():
            return
        # Race: writer may close between is_closed() check and submit().
        # The dirty marks survive on the writer's set and will be picked
        # up by any future ProcessDirtyPaths (or discarded cleanly on
        # shutdown), so swallow the RuntimeError raised by submit() in
        # that narrow case.  Other RuntimeError variants (which submit()
        # does NOT raise today, but might gain in future) propagate so
        # they are not silently masked by a blanket suppress().
        try:
            self._writer.submit(ProcessDirtyPaths())
        except RuntimeError:
            # Confirm the cause was writer-closed; re-check the flag
            # rather than parsing the exception message.  If the writer
            # is NOT closed, the RuntimeError came from elsewhere and
            # must propagate.
            if not self._writer.is_closed():
                raise
            logger.debug(
                "mark_paths_dirty_writer_closed_after_submit "
                "marks_retained_on_writer_set=True"
            )

    def read_attachment(self, path: str) -> AttachmentContent:
        """Read the binary content of a non-.md attachment.

        Delegates to :meth:`DocumentManager.read_attachment`.
        """
        return self._doc_mgr.read_attachment(path)

    def write_attachment(
        self,
        path: str,
        content: bytes,
        if_match: str | None = None,
        *,
        skip_size_cap: bool = False,
    ) -> WriteResult:
        """Create or overwrite a non-.md attachment.

        Delegates to :meth:`DocumentManager.write_attachment`.  Pass
        ``skip_size_cap=True`` from callers that have their own size
        gate (e.g. the ``create_upload_link`` receiver path, which has
        already validated against ``MARKDOWN_VAULT_MCP_UPLOAD_MAX_BYTES``);
        leave ``False`` for base64 callers of the MCP ``write`` tool.
        """
        return self._doc_mgr.write_attachment(
            path, content, if_match=if_match, skip_size_cap=skip_size_cap
        )

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict | None = None,
        if_match: str | None = None,
    ) -> WriteResult:
        """Create or overwrite a document.

        Creates intermediate directories as needed.  If *frontmatter* is
        provided, it is serialised as a YAML header at the top of the file.

        Args:
            path: Relative document path (e.g. ``"notes/topic.md"``).
            content: Markdown body (excluding frontmatter).
            frontmatter: Optional frontmatter dict serialised as a YAML header.
            if_match: Optional etag from a previous :meth:`read` call.  When
                provided, the write is only performed if the current file hash
                matches this value, preventing overwrites of concurrent
                modifications.  Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            ValueError: If *path* escapes the source directory.
        """
        return self._doc_mgr.write(
            path, content, frontmatter=frontmatter, if_match=if_match
        )

    def edit(
        self,
        path: str,
        old_text: str | None = None,
        new_text: str = "",
        if_match: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> EditResult:
        """Patch a section of a document.

        Replaces the first occurrence of *old_text* with *new_text*, or
        replaces the line range [*line_start*, *line_end*] when line numbers
        are given instead.

        Args:
            path: Relative document path.
            old_text: Exact text to replace (must occur exactly once).
                Mutually exclusive with *line_start* / *line_end*.
            new_text: Replacement text (may be empty to delete *old_text*).
            if_match: Optional etag for optimistic concurrency; see
                :meth:`write`.
            line_start: 1-based start line for line-range mode.
            line_end: 1-based end line (inclusive) for line-range mode.

        Returns:
            :class:`~markdown_vault_mcp.types.EditResult`.

        Raises:
            EditConflictError: If *old_text* is not found or appears more than
                once.
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            ValueError: If *path* escapes the source directory.
        """
        return self._doc_mgr.edit(
            path,
            old_text=old_text,
            new_text=new_text,
            if_match=if_match,
            line_start=line_start,
            line_end=line_end,
        )

    def delete(self, path: str, if_match: str | None = None) -> DeleteResult:
        """Delete a document or attachment.

        Removes the file from disk and purges its entries from the FTS and
        vector indices.

        Args:
            path: Relative path of the document or attachment to remove.
            if_match: Optional etag for optimistic concurrency; see
                :meth:`write`.

        Returns:
            :class:`~markdown_vault_mcp.types.DeleteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            DocumentNotFoundError: If *path* does not exist.
        """
        return self._doc_mgr.delete(path, if_match=if_match)

    def rename(
        self,
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        *,
        update_links: bool = False,
    ) -> RenameResult:
        """Rename or move a document or attachment.

        Moves the file on disk and updates the FTS / vector indices.  When
        *update_links* is ``True``, all wikilinks and markdown links in other
        documents that pointed to *old_path* are rewritten to *new_path*.

        Args:
            old_path: Current relative path of the document or attachment.
            new_path: Desired relative path after the move.
            if_match: Optional etag for optimistic concurrency; see
                :meth:`write`.
            update_links: When ``True``, rewrite internal links across the
                vault to reflect the new path.  Defaults to ``False``.

        Returns:
            :class:`~markdown_vault_mcp.types.RenameResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            DocumentNotFoundError: If *old_path* does not exist.
            ValueError: If *old_path* or *new_path* escapes the source
                directory.
        """
        return self._doc_mgr.rename(
            old_path, new_path, if_match=if_match, update_links=update_links
        )
