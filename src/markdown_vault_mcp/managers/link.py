"""Manager for link graph and connection operations."""

from __future__ import annotations

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
    from markdown_vault_mcp.collection import Collection

logger = logging.getLogger(__name__)


def _fts_row_to_note_info(row: dict[str, Any]) -> NoteInfo:
    """Helper to convert FTS row to NoteInfo."""
    import contextlib
    import json

    fm_raw = row.get("frontmatter_json")
    fm_data: dict[str, Any] = {}
    if fm_raw:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            data = json.loads(fm_raw)
            if isinstance(data, dict):
                fm_data = data

    return NoteInfo(
        path=row["path"],
        title=row["title"],
        folder=row["folder"],
        frontmatter=fm_data,
        modified_at=row["modified_at"],
    )


class LinkManager:
    """Handles link extraction, backlinks, and graph traversal."""

    def __init__(self, collection: Collection) -> None:
        self._collection = collection

    def get_backlinks(self, path: str) -> list[BacklinkInfo]:
        """Return all documents that link to the given document."""
        self._collection._ensure_initialized()
        self._collection._validate_path(path)
        if self._collection._fts.get_note(path) is None:
            raise ValueError(f"Document not found: {path}")
        rows = self._collection._fts.get_backlinks(path)
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

    def get_outlinks(self, path: str) -> list[OutlinkInfo]:
        """Return all links from the given document to other documents."""
        self._collection._ensure_initialized()
        self._collection._validate_path(path)
        if self._collection._fts.get_note(path) is None:
            raise ValueError(f"Document not found: {path}")
        rows = self._collection._fts.get_outlinks(path)
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
        """Return all links whose target does not exist in the collection."""
        self._collection._ensure_initialized()
        rows = self._collection._fts.get_broken_links(folder=folder)
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
        """Return all documents with no inbound or outbound links."""
        self._collection._ensure_initialized()
        rows = self._collection._fts.get_orphan_notes()
        return [_fts_row_to_note_info(r) for r in rows]

    def get_most_linked(self, *, limit: int = 10) -> list[MostLinkedNote]:
        """Return the documents with the most inbound links."""
        self._collection._ensure_initialized()
        return [
            MostLinkedNote(**row)
            for row in self._collection._fts.get_most_linked(limit=limit)
        ]

    def get_connection_path(
        self, source: str, target: str, max_depth: int = 10
    ) -> list[str] | None:
        """Return the shortest undirected path between two notes."""
        self._collection._ensure_initialized()
        self._collection._validate_path(source)
        self._collection._validate_path(target)
        return self._collection._fts.get_connection_path(
            source, target, max_depth=max_depth
        )
