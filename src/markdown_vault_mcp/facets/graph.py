"""Graph facet: the link-graph query surface (#604).

A thin view over :class:`~markdown_vault_mcp.managers.link.LinkManager`
exposing backlinks / outlinks / broken-links / orphans / most-linked /
connection-path queries. Part of the ``vault.py`` facade decomposition
(#576). The bucket-3 methods (:meth:`GraphFacet.get_backlinks`, :meth:`GraphFacet.get_outlinks`,
:meth:`GraphFacet.get_connection_path`) gate on the index-readiness callback before
delegating; the bucket-2 methods operate on a cold index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.managers.link import LinkManager
    from markdown_vault_mcp.types import (
        BacklinkInfo,
        BrokenLinkInfo,
        MostLinkedNote,
        NoteInfo,
        OutlinkInfo,
    )


class GraphFacet:
    """Link-graph queries, backed by :class:`LinkManager`."""

    def __init__(
        self, *, link_mgr: LinkManager, require_built: Callable[[], None]
    ) -> None:
        """Hold the link manager and the index-readiness gate.

        Args:
            link_mgr: The shared :class:`LinkManager` owned by the root.
            require_built: Raises :exc:`IndexUnavailableError` if the index has
                not been built; called by the bucket-3 query methods.
        """
        self._link_mgr = link_mgr
        self._require_built = require_built

    def get_backlinks(
        self, path: str, *, limit: int | None = None
    ) -> list[BacklinkInfo]:
        """Return all documents that link to the given document.

        Args:
            path: Relative path of the target document
                (e.g. ``"notes/topic.md"``).
            limit: Maximum number of results to return.  ``None`` (default)
                means unlimited.

        Returns:
            List of :class:`~markdown_vault_mcp.types.BacklinkInfo` objects
            for each document that contains a link pointing to ``path``.

        Raises:
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        self._require_built()
        return self._link_mgr.get_backlinks(path, limit=limit)

    def get_outlinks(self, path: str, *, limit: int | None = None) -> list[OutlinkInfo]:
        """Return all links from the given document to other documents.

        The ``exists`` field on each :class:`~markdown_vault_mcp.types.OutlinkInfo`
        indicates whether the target document is currently indexed.

        Args:
            path: Relative path of the source document
                (e.g. ``"notes/topic.md"``).
            limit: Maximum number of results to return.  ``None`` (default)
                means unlimited.

        Returns:
            List of :class:`~markdown_vault_mcp.types.OutlinkInfo` objects for
            each link originating from ``path``.

        Raises:
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        self._require_built()
        return self._link_mgr.get_outlinks(path, limit=limit)

    def get_broken_links(self, *, folder: str | None = None) -> list[BrokenLinkInfo]:
        """Return all links whose target does not exist in the vault.

        Args:
            folder: If provided, restrict to source documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.BrokenLinkInfo` objects.
        """
        return self._link_mgr.get_broken_links(folder=folder)

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
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not been called.
            ValueError: If *source* or *target* is not found in the index.
        """
        self._require_built()
        return self._link_mgr.get_connection_path(source, target, max_depth=max_depth)
