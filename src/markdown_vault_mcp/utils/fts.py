"""FTS row conversion utilities shared across managers."""

from __future__ import annotations

import json
import logging
from typing import Any

from markdown_vault_mcp.types import NoteInfo

logger = logging.getLogger(__name__)


def fts_row_to_note_info(row: dict[str, Any]) -> NoteInfo:
    """Convert an FTSIndex row dict to a :class:`NoteInfo`.

    Args:
        row: Dict returned by :meth:`FTSIndex.list_notes`,
            :meth:`FTSIndex.get_note`, :meth:`FTSIndex.get_recent`,
            or :meth:`FTSIndex.get_orphan_notes`.

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
