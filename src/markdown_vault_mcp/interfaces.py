"""Storage protocols for the search/index seam (#1230).

The managers depend on these concepts:

- :class:`KeywordIndex` — the relational document store plus full-text search
  and the build-state bookkeeping the indexing pipeline keeps.
- :class:`GraphStore` — the link graph.  Distinct from search because it
  answers a different question: *structure* (what links to what) rather than
  *relevance* (what matches a query).
- :class:`StoreBackend`, :class:`StoreLifecycle` and :class:`SourceProbe`
  — the storage contract every store shares (#1766): how a store is opened
  and by whom it is closed, and the one question the indexing side asks a
  source about an identity.
- :class:`VectorReader`, :class:`VectorPublisher` and :class:`EmbeddingStore`
  — the vector store contract (#1767): vectors in, nearest rows out, with
  no embedding provider inside the store; :class:`ReportsResources` is the
  optional part a store implements to report its search mode and sizes
  (#1377), :class:`DescribesStore` the optional part a store
  implements to say where it lives (#1504), and
  :class:`ReadsMetadataInBatches` the optional part a store implements to
  serve many documents' row metadata in one read (#1937).

``FTSIndex`` implements the first two over one SQLite database and
``NumpySidecarStore`` the vector contract over numpy; both satisfy these
protocols structurally, without inheriting from them.  This mirrors the move already
made for the embedding *provider*
(:class:`~markdown_vault_mcp.providers.EmbeddingProvider`) and applies it to
the storage that provider feeds — which is what the design doc's standing note
about evaluating a different vector backend at scale would need.

**Membership rule:** each protocol carries exactly what its consumers call
today, not everything its current implementation happens to expose.  Grow one
when a consumer needs more, rather than up front — a protocol wider than its
consumers is a god interface with extra steps.

The module imports nothing at runtime beyond ``typing``, so depending on the
seam never drags in an implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Sequence
    from pathlib import Path

    from markdown_vault_mcp.fts_index import ChunkingMeta
    from markdown_vault_mcp.types import (
        DocumentRows,
        EmbeddingIdentity,
        ExpectedToken,
        FTSResult,
        ParsedNote,
        PublicationOutcome,
        RevisionToken,
        SkippedFile,
        SourceState,
        StoreFamily,
        StoreResources,
        SubtreeNote,
        TocEntry,
        VectorRow,
    )

__all__ = [
    "DescribesStore",
    "EmbeddingStore",
    "GraphStore",
    "KeywordGraphIndex",
    "KeywordIndex",
    "ReadsMetadataInBatches",
    "ReportsResources",
    "SourceProbe",
    "StoreBackend",
    "StoreLifecycle",
    "VectorBackend",
    "VectorPublisher",
    "VectorReader",
]

StoreT_co = TypeVar("StoreT_co", covariant=True)


@runtime_checkable
class KeywordIndex(Protocol):
    """The document store: full-text search, enumeration, and build state.

    Covers what the managers need to put documents in, get them back out by
    path or query, enumerate them, and track how far a build got.
    """

    # -- search ---------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        folder: str | None = None,
        filters: dict[str, str] | None = None,
        snippet_words: int | None = None,
    ) -> list[FTSResult]:
        """Return chunks matching *query*, best first.

        Args:
            query: The full-text query.
            limit: Maximum number of results.
            folder: Restrict to this folder, or ``None`` for the whole vault.
            filters: Frontmatter field/value pairs a result must match.
            snippet_words: Approximate snippet length, or ``None`` for the
                backend default.

        Returns:
            The matching chunks, ranked.
        """
        ...

    # -- reads ----------------------------------------------------------

    def get_note(self, path: str) -> dict[str, Any] | None:
        """Return the stored row for *path*, or ``None`` if absent."""
        ...

    def list_notes(self, *, folder: str | None = None) -> list[dict[str, Any]]:
        """Return every stored note, optionally restricted to *folder*."""
        ...

    def list_folders(self) -> list[str]:
        """Return every folder that contains at least one note."""
        ...

    def list_field_values(self, field: str) -> list[str]:
        """Return the distinct values indexed for frontmatter *field*."""
        ...

    def get_recent(
        self, *, limit: int = 20, folder: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the most recently modified notes, newest first."""
        ...

    def list_chunks(self, paths: Collection[str] | None = None) -> list[dict[str, Any]]:
        """Return stored chunks with their document's ``content_hash``, for embedding.

        Args:
            paths: Only these documents' chunks; ``None`` for every document.
        """
        ...

    def get_toc(self, path: str, *, max_level: int | None = None) -> list[TocEntry]:
        """Return the heading outline of the note at *path*."""
        ...

    def get_subtree_toc(
        self, prefix: str, *, max_level: int | None = None, max_notes: int = 200
    ) -> tuple[list[SubtreeNote], bool]:
        """Return outlines for every note under *prefix*.

        Returns:
            The notes and whether *max_notes* truncated the result.
        """
        ...

    # -- counts ---------------------------------------------------------

    def count_documents(self) -> int:
        """Return the number of indexed documents."""
        ...

    def count_chunks(self) -> int:
        """Return the number of indexed chunks."""
        ...

    def get_chunk_counts(self, paths: Iterable[str]) -> dict[str, int]:
        """Return the chunk count for each of *paths* that is a live note.

        A tombstoned or absent path is left out, never defaulted: semantic
        search drops vector rows of the paths left out (#1505).
        """
        ...

    def has_documents(self) -> bool:
        """Return whether the index holds any document at all."""
        ...

    # -- writes ---------------------------------------------------------

    def upsert_note(self, note: ParsedNote) -> int:
        """Insert or replace *note*, returning the number of chunks stored."""
        ...

    def delete_by_path(self, path: str) -> int:
        """Remove the note at *path*, returning the number of chunks dropped."""
        ...

    def optimize(self) -> bool:
        """Compact the index, returning whether the backend did any work."""
        ...

    # -- tombstones (#1129) ---------------------------------------------

    def upsert_tombstone(
        self, skip: SkippedFile, *, content_hash: str, modified_at: float
    ) -> None:
        """Record that a file exists but was deliberately not indexed.

        Args:
            skip: Why the file was skipped.
            content_hash: The hash the skip decision was made against.
            modified_at: The file's modification time at that point.
        """
        ...

    def get_tombstone(self, path: str) -> dict[str, Any] | None:
        """Return the tombstone for *path*, or ``None`` if it is not skipped."""
        ...

    def list_tombstones(self) -> list[dict[str, Any]]:
        """Return every recorded tombstone."""
        ...

    # -- build state ----------------------------------------------------

    def is_build_completed(self) -> bool:
        """Return whether a full build has finished against this index."""
        ...

    def set_build_completed(self, *, walk_complete: bool = True) -> None:
        """Mark the full build as finished, and whether it read every directory."""
        ...

    def is_build_walk_complete(self) -> bool:
        """Return whether the last completed build read every directory (#1785)."""
        ...

    def clear_build_completed(self) -> None:
        """Clear the build-completed marker, so readiness re-gates."""
        ...

    def set_chunking_meta(
        self,
        *,
        model: str | None,
        max_chunk_chars_override: int | None,
        title_field: str = "title",
        searchable_fields: str = "",
        indexed_frontmatter_fields: str = "",
        attachment_extensions: str = "",
    ) -> None:
        """Record the settings the current rows were derived under.

        Args:
            model: The embedding model in use, or ``None``.
            max_chunk_chars_override: The configured chunk-size override.
            title_field: The frontmatter field used as a title.
            searchable_fields: Serialized searchable frontmatter fields.
            indexed_frontmatter_fields: Serialized indexed frontmatter fields.
            attachment_extensions: Serialized attachment allowlist consulted
                by link extraction (``""`` for the default set).
        """
        ...

    def get_chunking_meta(self) -> ChunkingMeta:
        """Return the settings the stored rows were derived under."""
        ...


@runtime_checkable
class GraphStore(Protocol):
    """The link graph: what points at what, and what nothing points at.

    A separate concept from search — structure rather than relevance — even
    though one SQLite database serves both today.
    """

    def get_backlinks(
        self, path: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the notes linking to *path*."""
        ...

    def get_outlinks(
        self, path: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the notes *path* links to."""
        ...

    def get_broken_links(self, *, folder: str | None = None) -> list[dict[str, Any]]:
        """Return links whose target does not exist."""
        ...

    def get_orphan_notes(self) -> list[dict[str, Any]]:
        """Return notes nothing links to."""
        ...

    def get_most_linked(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most linked-to notes, most first."""
        ...

    def get_connection_path(
        self, source_path: str, target_path: str, max_depth: int = 10
    ) -> list[str] | None:
        """Return a shortest link path between two notes.

        Args:
            source_path: Where to start.
            target_path: Where to end.
            max_depth: How far to search before giving up.

        Returns:
            The paths along the route, or ``None`` when none exists within
            *max_depth*.
        """
        ...

    def count_links(self) -> int:
        """Return the total number of links."""
        ...

    def count_broken_links(self) -> int:
        """Return the number of links with no target."""
        ...

    def count_orphans(self) -> int:
        """Return the number of notes nothing links to."""
        ...

    def resolve_vault_wikilinks(self) -> int:
        """Resolve stored wikilink targets to real paths.

        A write, unlike the rest of this protocol: link targets are only
        resolvable once every note is present, so the indexing pipeline runs
        this pass at the end of a build.

        Returns:
            The number of links resolved.
        """
        ...


@runtime_checkable
class KeywordGraphIndex(KeywordIndex, GraphStore, Protocol):
    """Both facets on one object, as the SQLite backend serves them.

    Several managers legitimately need both — searching and then following
    links, for instance — and today one database answers both. Naming the
    combination keeps those annotations honest without collapsing the two
    concepts, so a backend that separates them stays expressible.
    """


@runtime_checkable
class StoreLifecycle(Protocol):
    """What every open store owes its owner (#1766).

    Whoever opened a store closes it: the vault closes the stores its
    backends opened, a caller closes the store it passed in.
    """

    def close(self) -> None:
        """Release the store's resources. Idempotent; use after close raises."""
        ...

    def checkpoint(self) -> None:
        """Make committed state durable where that is a separate step.

        A no-op for a store with nothing to flush: in memory, or opened
        read-only.
        """
        ...


@runtime_checkable
class StoreBackend(Protocol[StoreT_co]):
    """A named opener for one kind of store (#1766).

    A backend carries its own configuration; :meth:`open` takes only what
    varies per vault: where the store is and whether this process owns its
    files. The registry in :mod:`markdown_vault_mcp.stores.registry` maps
    ``(family, name)`` to a backend factory.
    """

    @property
    def family(self) -> StoreFamily:
        """Which kind of store this backend opens."""
        ...

    @property
    def name(self) -> str:
        """The registry name a configuration value selects."""
        ...

    def open(
        self,
        location: Path | str | None,
        identity: object | None = None,
        *,
        owns_files: bool = True,
    ) -> StoreT_co:
        """Open the store at *location*.

        Args:
            location: Where the store lives, in the backend's own terms; a
                file-backed backend takes a path, ``None`` means in memory.
            identity: The family's identity value, checked against what the
                store holds; ``None`` for a family without one.
            owns_files: Whether this process owns the store's files. ``False``
                opens read-only and forbids any self-heal.

        Returns:
            The open store.

        Raises:
            StoreOpenError: One of its five subclasses, never an empty store.
        """
        ...


@runtime_checkable
class SourceProbe(Protocol):
    """The one question the indexing side asks a source about an identity."""

    def probe(self, path: str) -> SourceState:
        """Return whether *path* is present (with its token), absent or unavailable."""
        ...


@runtime_checkable
class VectorReader(Protocol):
    """Read a vector store: nearest rows, per-document rows, tokens (#1767).

    One call observes one committed state; a document is never half
    visible. The store holds no embedding provider: callers embed queries.
    """

    @property
    def identity(self) -> EmbeddingIdentity:
        """The embedding space of the rows the store holds."""
        ...

    @property
    def generation(self) -> int:
        """Bumped each time :meth:`VectorPublisher.publish_generation` lands."""
        ...

    @property
    def count(self) -> int:
        """The number of stored rows."""
        ...

    def search(
        self,
        vector: Sequence[float],
        *,
        limit: int = 10,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        folder: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the rows nearest *vector*, most similar first.

        Args:
            vector: The query embedding, of the store's dimension.
            limit: Maximum number of rows; zero or less returns ``[]``.
            predicate: Keeps only the rows it accepts, applied before
                *limit*.
            folder: Keeps only rows in this folder or below it, applied
                before *limit*; a backend may push it down.

        Returns:
            Copies of the rows' metadata, each with a ``score``: the
            normalised dot product, ``0.0`` for a zero vector.
        """
        ...

    def similar_to_document(
        self,
        path: str,
        *,
        limit: int = 10,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        folder: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the rows nearest the mean of *path*'s rows, excluding *path*.

        Args:
            path: The document whose rows are averaged.
            limit: Maximum number of rows; zero or less returns ``[]``.
            predicate: Keeps only the rows it accepts, applied before
                *limit*.
            folder: Keeps only rows in this folder or below it, applied
                before *limit*; a backend may push it down.

        Returns:
            As :meth:`search`; ``[]`` when *path* has no rows.
        """
        ...

    def rows_for(self, path: str) -> list[VectorRow]:
        """Return *path*'s stored rows, vectors normalised, in storage order."""
        ...

    def tokens(self) -> dict[str, RevisionToken | None]:
        """Return each stored document's token; ``None`` when unknown."""
        ...


@runtime_checkable
class ReadsMetadataInBatches(Protocol):
    """A vector store that reads many documents' row metadata at once (#1937); optional.

    A store whose every read is a round trip implements it, so a reconcile
    pass compares what it holds without one read per document, and without
    reading vectors it does not need.
    """

    def metadata_for(self, paths: Collection[str]) -> dict[str, list[dict[str, Any]]]:
        """Return each of *paths*' row metadata in storage order, without vectors.

        Args:
            paths: The documents to read.

        Returns:
            The metadata by path; a path with no rows is left out.
        """
        ...


@runtime_checkable
class DescribesStore(Protocol):
    """A store that says where it lives, for ``embeddings_status`` (#1504); optional."""

    def describe(self) -> dict[str, Any]:
        """Return details of where the store lives, never a credential.

        Raises:
            RuntimeError: If the store is closed.
            StoreOpenError: If a live detail cannot be read (*unavailable*),
                or the store's location is gone (*not configured*).
        """
        ...


@runtime_checkable
class ReportsResources(Protocol):
    """A store that reports what it costs (#1377); optional for a vector store."""

    def resources(self) -> StoreResources:
        """Return the store's search mode, workspace and measured sizes.

        Raises:
            RuntimeError: If the store is closed.
            OSError: If a stat of one of its files is refused.
        """
        ...


@runtime_checkable
class VectorPublisher(Protocol):
    """Publish to a vector store, conditionally on the stored token (#1767)."""

    def replace_document(
        self, document: DocumentRows, *, expected_token: ExpectedToken
    ) -> PublicationOutcome:
        """Replace every row of ``document.path`` with ``document.rows``.

        Args:
            document: The document's new rows and the token they came from.
            expected_token: The token the caller saw when it read the
                source; ``ANY_TOKEN`` publishes whatever is stored.

        Returns:
            ``"published"``, or ``"conflict"`` when the stored token differs
            from *expected_token*; nothing changes on a conflict.
        """
        ...

    def remove_document(
        self, path: str, *, expected_token: ExpectedToken
    ) -> PublicationOutcome:
        """Remove every row and the token of *path*, with the same condition."""
        ...

    def publish_generation(
        self, identity: EmbeddingIdentity, documents: Iterable[DocumentRows]
    ) -> None:
        """Replace the whole store with *documents* under *identity*, in one step.

        A reader sees the previous generation until this returns; an
        interrupted publication leaves the previous generation in place.
        """
        ...


@runtime_checkable
class EmbeddingStore(VectorReader, VectorPublisher, StoreLifecycle, Protocol):
    """What a vector backend opens: read, publish and close (#1767)."""


@runtime_checkable
class VectorBackend(StoreBackend[EmbeddingStore], Protocol):
    """A backend that opens vector stores, including a fresh one (#1777)."""

    def open_fresh(
        self, location: Path | str | None, identity: EmbeddingIdentity
    ) -> EmbeddingStore:
        """Open *location* as an empty store at *identity*, reading nothing there.

        What the location holds stays in place until the fresh store's first
        persist replaces it, so a rebuild that never completes leaves the
        previous data for the next open. Used to rebuild a store that would
        not open (corrupt) or holds another identity.
        """
        ...
