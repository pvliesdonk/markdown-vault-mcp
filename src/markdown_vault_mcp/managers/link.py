"""Link graph query manager.

Handles all link-related queries (backlinks, outlinks, broken links,
orphans, most-linked, connection paths) with dependency injection —
receives only :class:`~markdown_vault_mcp.fts_index.FTSIndex` and a
``source_dir`` path, no back-reference to :class:`Collection`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.types import (
    BacklinkInfo,
    BrokenLinkInfo,
    MostLinkedNote,
    NoteInfo,
    OutlinkInfo,
)

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.fts_index import FTSIndex

logger = logging.getLogger(__name__)


def _fts_row_to_note_info(row: dict[str, Any]) -> NoteInfo:
    """Convert an FTSIndex row dict to a :class:`NoteInfo`.

    Args:
        row: Dict returned by :meth:`FTSIndex.list_notes`,
            :meth:`FTSIndex.get_note`, or :meth:`FTSIndex.get_orphan_notes`.

    Returns:
        A populated :class:`NoteInfo` instance.
    """
    frontmatter: dict[str, Any] = {}
    raw_json = row.get("frontmatter_json")
    if raw_json:
        try:
            frontmatter = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Could not parse frontmatter_json for path %s", row.get("path")
            )
    return NoteInfo(
        path=row["path"],
        title=row["title"],
        folder=row["folder"],
        frontmatter=frontmatter,
        modified_at=row["modified_at"],
    )


class LinkManager:
    """Manages link graph queries against the FTS index.

    Args:
        fts: The FTS index to query.
        source_dir: Absolute path to the vault root directory.
    """

    def __init__(self, fts: FTSIndex, source_dir: Path) -> None:
        self._fts = fts
        self._source_dir = source_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_path(self, path: str) -> None:
        """Validate that *path* ends with ``.md`` and stays inside source_dir.

        Args:
            path: Relative vault path to validate.

        Raises:
            ValueError: If the path does not end with ``.md`` or escapes
                the source directory.
        """
        if not path.endswith(".md"):
            raise ValueError(f"Path must end with '.md': {path}")
        abs_path = (self._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._source_dir.resolve()):
            raise ValueError(f"Path traversal detected: {path}")

    def _require_note(self, path: str) -> None:
        """Validate *path* and ensure a document exists in the index.

        Args:
            path: Relative vault path.

        Raises:
            ValueError: If validation fails or the document is not indexed.
        """
        self._validate_path(path)
        if self._fts.get_note(path) is None:
            raise ValueError(f"Document not found: {path}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_backlinks(
        self, path: str, *, limit: int | None = None
    ) -> list[BacklinkInfo]:
        """Return all documents that link to the given document.

        Args:
            path: Relative path of the target document
                (e.g. ``"notes/topic.md"``).
            limit: Maximum number of results to return.  ``None`` means
                unlimited.

        Returns:
            List of :class:`~markdown_vault_mcp.types.BacklinkInfo` objects
            for each document that contains a link pointing to ``path``.

        Raises:
            ValueError: If no document exists at the given path.
        """
        self._require_note(path)
        rows = self._fts.get_backlinks(path, limit=limit)
        return [
            BacklinkInfo(
                source_path=row["source_path"],
                source_title=row["source_title"],
                link_text=row["link_text"],
                link_type=row["link_type"],
                fragment=row["fragment"],
                raw_target=row["raw_target"],
            )
            for row in rows
        ]

    def get_outlinks(self, path: str, *, limit: int | None = None) -> list[OutlinkInfo]:
        """Return all links from the given document to other documents.

        The ``exists`` field on each
        :class:`~markdown_vault_mcp.types.OutlinkInfo` indicates whether the
        target document is currently indexed.

        Args:
            path: Relative path of the source document
                (e.g. ``"notes/topic.md"``).
            limit: Maximum number of results to return.  ``None`` means
                unlimited.

        Returns:
            List of :class:`~markdown_vault_mcp.types.OutlinkInfo` objects for
            each link originating from ``path``.

        Raises:
            ValueError: If no document exists at the given path.
        """
        self._require_note(path)
        rows = self._fts.get_outlinks(path, limit=limit)
        return [
            OutlinkInfo(
                target_path=row["target_path"],
                link_text=row["link_text"],
                link_type=row["link_type"],
                fragment=row["fragment"],
                exists=bool(row["target_exists"]),
                raw_target=row["raw_target"],
            )
            for row in rows
        ]

    def get_broken_links(self, *, folder: str | None = None) -> list[BrokenLinkInfo]:
        """Return all links whose target does not exist in the collection.

        Args:
            folder: If provided, restrict to source documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.BrokenLinkInfo` objects.
        """
        rows = self._fts.get_broken_links(folder=folder)
        return [
            BrokenLinkInfo(
                source_path=row["source_path"],
                source_title=row["source_title"],
                target_path=row["target_path"],
                link_text=row["link_text"],
                link_type=row["link_type"],
                fragment=row["fragment"],
                raw_target=row["raw_target"],
            )
            for row in rows
        ]

    def get_orphan_notes(self) -> list[NoteInfo]:
        """Return all documents with no inbound or outbound links.

        A document is an orphan if it has zero outlinks and is not referenced
        by any other document's links.

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` objects,
            ordered by path.
        """
        rows = self._fts.get_orphan_notes()
        return [_fts_row_to_note_info(r) for r in rows]

    def get_most_linked(self, *, limit: int = 10) -> list[MostLinkedNote]:
        """Return the documents with the most inbound links.

        Args:
            limit: Maximum number of results to return. Default 10.

        Returns:
            List of :class:`~markdown_vault_mcp.types.MostLinkedNote` ordered
            by backlink_count descending.
        """
        return [MostLinkedNote(**row) for row in self._fts.get_most_linked(limit=limit)]

    def get_connection_path(
        self, source: str, target: str, *, max_depth: int = 10
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
            ValueError: If *source* or *target* is not found in the index.
        """
        self._validate_path(source)
        self._validate_path(target)
        return self._fts.get_connection_path(source, target, max_depth=max_depth)
