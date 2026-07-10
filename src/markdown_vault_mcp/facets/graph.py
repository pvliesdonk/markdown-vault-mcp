"""Graph facet: the link-graph query surface (#604).

A thin view over :class:`~markdown_vault_mcp.managers.link.LinkManager`
exposing backlinks / outlinks / broken-links / orphans / most-linked /
connection-path queries. Part of the ``vault.py`` facade decomposition
(#576). The bucket-3 methods (:meth:`GraphFacet.get_backlinks`, :meth:`GraphFacet.get_outlinks`,
:meth:`GraphFacet.get_connection_path`) gate on the index-readiness callback before
delegating; the bucket-2 methods operate on a cold index.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

from markdown_vault_mcp.types import GraphEdge, GraphNode, GraphView

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.managers.link import LinkManager
    from markdown_vault_mcp.managers.search import SearchManager
    from markdown_vault_mcp.types import (
        BacklinkInfo,
        BrokenLinkInfo,
        MostLinkedNote,
        NoteInfo,
        OutlinkInfo,
    )

logger = logging.getLogger(__name__)


class GraphFacet:
    """Link-graph queries, backed by :class:`LinkManager`."""

    def __init__(
        self,
        *,
        link_mgr: LinkManager,
        search_mgr: SearchManager,
        require_built: Callable[[], None],
    ) -> None:
        """Hold the managers and the index-readiness gate.

        Args:
            link_mgr: The shared :class:`LinkManager` owned by the root.
            search_mgr: The shared :class:`SearchManager`; the graph-view
                assembly methods (#880) use it for node labels
                (:meth:`SearchManager.get_metadata`) and semantic edges
                (:meth:`SearchManager.get_similar`).
            require_built: Raises :exc:`IndexUnavailableError` if the index has
                not been built; called by the bucket-3 query methods.
        """
        self._link_mgr = link_mgr
        self._search_mgr = search_mgr
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

    # ------------------------------------------------------------------
    # Graph-view assembly (#880) — moved out of the app-tool closures so the
    # traversal is unit-testable and runs in a single worker thread instead
    # of one asyncio.to_thread hop per node.
    # ------------------------------------------------------------------

    def _node_label(self, path: str) -> tuple[str, str]:
        """Return ``(label, folder)`` for *path* from indexed metadata.

        Only indexed metadata (title/folder) is consulted — reading the full
        document would load it into memory and, for a note over
        ``MAX_NOTE_READ_BYTES``, raise and fail the whole graph. Falls back
        to the filename stem for unindexed paths.
        """
        meta = self._search_mgr.get_metadata(path)
        label = meta.title if meta else path.rsplit("/", 1)[-1].replace(".md", "")
        folder = meta.folder if meta else ""
        return label, folder

    def _expand_node(
        self, current: str
    ) -> tuple[GraphNode, list[GraphEdge], list[str]]:
        """Build *current*'s interior node, its edges, and its link partners.

        Fetches backlinks/outlinks (orphan detection + edges); a
        ``ValueError`` from either query (e.g. an invalid path discovered
        via a link) yields an empty edge set for that direction.

        Returns:
            Tuple ``(node, edges, partners)`` — the node, its inbound and
            existing-outbound edges, and the partner paths for the caller
            to enqueue (in backlink-then-outlink order).
        """
        label, folder = self._node_label(current)
        try:
            backlinks = self.get_backlinks(current)
        except ValueError:
            backlinks = []
        try:
            outlinks = self.get_outlinks(current)
        except ValueError:
            outlinks = []
        is_orphan = len(backlinks) == 0 and len(outlinks) == 0

        node = GraphNode(
            id=current,
            label=label,
            group="orphan" if is_orphan else "note",
            folder=folder,
            backlink_count=len(backlinks),
        )

        edges: list[GraphEdge] = []
        partners: list[str] = []
        for bl in backlinks:
            edges.append(
                GraphEdge(source=bl.source_path, target=current, link_type=bl.link_type)
            )
            partners.append(bl.source_path)
        for ol in outlinks:
            if ol.exists:
                edges.append(
                    GraphEdge(
                        source=current, target=ol.target_path, link_type=ol.link_type
                    )
                )
                partners.append(ol.target_path)
        return node, edges, partners

    def _add_semantic_edges(
        self,
        nodes: dict[str, GraphNode],
        edges: list[GraphEdge],
        *,
        max_nodes: int,
    ) -> bool:
        """Append dashed similarity edges for each current node.

        Returns ``True`` when the node cap stopped the expansion. A
        ``ValueError`` from :meth:`SearchManager.get_similar` (embeddings
        not configured) silently skips the node; unexpected failures are
        logged and skipped.
        """
        truncated = False
        sem_seen: set[frozenset[str]] = set()
        for node_path in list(nodes.keys()):
            if len(nodes) >= max_nodes:
                truncated = True
                break
            try:
                similar = self._search_mgr.get_similar(node_path, limit=5)
            except ValueError:
                # Expected when embeddings are not configured for this vault.
                continue
            except Exception:
                logger.warning("get_similar failed for %s", node_path, exc_info=True)
                continue
            seen_sr: set[str] = set()
            for sr in similar:
                if sr.path == node_path or sr.path in seen_sr:
                    continue
                seen_sr.add(sr.path)
                pair: frozenset[str] = frozenset({node_path, sr.path})
                if pair in sem_seen:
                    continue
                sem_seen.add(pair)
                if sr.path not in nodes:
                    if len(nodes) >= max_nodes:
                        truncated = True
                        break
                    label, folder = self._node_label(sr.path)
                    nodes[sr.path] = GraphNode(
                        id=sr.path,
                        label=label,
                        group="note",
                        folder=folder,
                        backlink_count=0,
                    )
                edges.append(
                    GraphEdge(source=node_path, target=sr.path, link_type="semantic")
                )
        return truncated

    def get_neighborhood(
        self,
        path: str,
        *,
        depth: int = 1,
        include_semantic: bool = False,
        max_nodes: int = 200,
    ) -> GraphView:
        """Return the link neighborhood of a note as a node/edge graph.

        Breadth-first traversal from *path*: interior nodes (within *depth*
        hops) contribute their backlink/outlink edges and orphan grouping;
        boundary nodes appear with ``backlink_count=0``. Explicit edges are
        deduplicated per ``(source, target)``.

        Args:
            path: Center note path.
            depth: How many hops to traverse (default 1).
            include_semantic: When ``True``, add ``"semantic"`` similarity
                edges for each node (requires embeddings; silently omitted
                when unavailable).
            max_nodes: Soft cap on returned node count. Traversal and any
                semantic expansion both stop once the cap is hit, setting
                :attr:`GraphView.truncated`. Bounds dense-vault depth=2
                traversals.

        Returns:
            A :class:`~markdown_vault_mcp.types.GraphView`.

        Raises:
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not
                been called.
        """
        # Gate up front: with depth=0 the traversal never reaches the gated
        # get_backlinks/get_outlinks calls, and the semantic pass swallows
        # exceptions by design — without this, the documented
        # IndexUnavailableError contract would silently degrade (#891 review).
        self._require_built()
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(path, 0)])
        truncated = False

        while queue:
            if len(nodes) >= max_nodes:
                truncated = True
                break
            current, hop = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            if hop >= depth:
                # Boundary nodes are reachable by definition — skip DB calls.
                label, folder = self._node_label(current)
                nodes[current] = GraphNode(
                    id=current,
                    label=label,
                    group="note",
                    folder=folder,
                    backlink_count=0,
                )
                continue

            node, node_edges, partners = self._expand_node(current)
            nodes[current] = node
            edges.extend(node_edges)
            for partner in partners:
                if partner not in visited:
                    queue.append((partner, hop + 1))

        # Deduplicate explicit edges.
        seen_edges: set[tuple[str, str]] = set()
        unique_edges: list[GraphEdge] = []
        for e in edges:
            key = (e.source, e.target)
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(e)

        if include_semantic:
            truncated = (
                self._add_semantic_edges(nodes, unique_edges, max_nodes=max_nodes)
                or truncated
            )

        return GraphView(
            nodes=list(nodes.values()), edges=unique_edges, truncated=truncated
        )

    def get_hub_graph(self, *, limit: int = 20) -> GraphView:
        """Return the most-linked notes and their inbound links as a graph.

        Hub nodes come from :meth:`get_most_linked`; each hub's backlink
        sources join as ``"note"`` nodes with edges deduplicated per
        ``(source, hub)``.

        Args:
            limit: Maximum number of hub notes to include.

        Returns:
            A :class:`~markdown_vault_mcp.types.GraphView` (``truncated`` is
            always ``False`` — hub views have no node cap).
        """
        hubs = self.get_most_linked(limit=limit)
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        seen_edges: set[tuple[str, str]] = set()

        for hub in hubs:
            nodes[hub.path] = GraphNode(
                id=hub.path,
                label=hub.title,
                group="hub",
                folder=hub.folder,
                backlink_count=hub.backlink_count,
            )

        for hub in hubs:
            try:
                backlinks = self.get_backlinks(hub.path)
            except ValueError:
                backlinks = []
            for bl in backlinks:
                if bl.source_path not in nodes:
                    label, folder = self._node_label(bl.source_path)
                    nodes[bl.source_path] = GraphNode(
                        id=bl.source_path,
                        label=label,
                        group="note",
                        folder=folder,
                        backlink_count=0,
                    )
                edge_key = (bl.source_path, hub.path)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(
                        GraphEdge(
                            source=bl.source_path,
                            target=hub.path,
                            link_type=bl.link_type,
                        )
                    )

        return GraphView(nodes=list(nodes.values()), edges=edges)
