"""Storage protocols for the search/index seam (#1230).

The managers depend on three distinct concepts, all currently served by two
concrete classes:

- :class:`KeywordIndex` — the relational document store plus full-text search
  and the build-state bookkeeping the indexing pipeline keeps.
- :class:`GraphStore` — the link graph.  Distinct from search because it
  answers a different question: *structure* (what links to what) rather than
  *relevance* (what matches a query).
- :class:`VectorStore` — the semantic surface.

``FTSIndex`` implements the first two over one SQLite database and
``VectorIndex`` implements the third over numpy; both satisfy these protocols
structurally, without inheriting from them.  This mirrors the move already
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

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from markdown_vault_mcp.fts_index import ChunkingMeta
    from markdown_vault_mcp.types import (
        FTSResult,
        ParsedNote,
        SkippedFile,
        SubtreeNote,
        TocEntry,
    )

__all__ = [
    "GraphStore",
    "KeywordGraphIndex",
    "KeywordIndex",
    "VectorStore",
]


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

    def list_chunks(self) -> list[dict[str, Any]]:
        """Return every stored chunk, for embedding and reconciliation."""
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
        """Return the chunk count for each of *paths*."""
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

    def set_build_completed(self) -> None:
        """Mark the full build as finished."""
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
    ) -> None:
        """Record the settings the current rows were derived under.

        Args:
            model: The embedding model in use, or ``None``.
            max_chunk_chars_override: The configured chunk-size override.
            title_field: The frontmatter field used as a title.
            searchable_fields: Serialized searchable frontmatter fields.
            indexed_frontmatter_fields: Serialized indexed frontmatter fields.
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
class VectorStore(Protocol):
    """The semantic surface: embeddings in, nearest neighbours out."""

    @property
    def count(self) -> int:
        """Return the number of stored vectors."""
        ...

    def chunks_by_path(self) -> dict[str, list[dict[str, Any]]]:
        """Return the stored chunk metadata, grouped by note path."""
        ...

    def add(self, texts: list[str], metadata: list[dict[str, Any]]) -> int:
        """Embed *texts* and store them against *metadata*.

        Returns:
            The number of vectors added.
        """
        ...

    def add_vectors(
        self, raw_vectors: list[list[float]], metadata: list[dict[str, Any]]
    ) -> int:
        """Store already-computed *raw_vectors* against *metadata*.

        Returns:
            The number of vectors added.
        """
        ...

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the chunks most similar to *query*.

        Args:
            query: Text to embed and compare against.
            limit: Maximum number of results.
            predicate: Keeps only the chunks it accepts, applied before
                *limit* so filtering does not silently shrink the result.

        Returns:
            The nearest chunks, most similar first.
        """
        ...

    def search_by_path(
        self,
        path: str,
        *,
        limit: int = 10,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the chunks most similar to the note at *path*.

        Args:
            path: The note to find neighbours for.
            limit: Maximum number of results.
            predicate: Keeps only the chunks it accepts.

        Returns:
            The nearest chunks, most similar first.
        """
        ...

    def delete_by_path(self, path: str) -> int:
        """Remove every vector belonging to *path*.

        Returns:
            The number of vectors removed.
        """
        ...

    def save(self, path: Path) -> None:
        """Persist the store to *path*."""
        ...
