"""Independent structured-query corpus builders."""

from typing import Any

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.types import Chunk, ParsedNote


def add_note(index: FTSIndex, path: str, **values: Any) -> None:
    """Index a controlled note with optional metadata and section overrides.

    Args:
        index: Destination keyword index.
        path: Relative note identity.
        **values: ParsedNote fields or a list of section content strings.
    """
    bodies = values.pop("bodies", ["target body"])
    index.upsert_note(
        ParsedNote(
            path=path,
            title=str(values.pop("title", "Plain")),
            frontmatter=values.pop("frontmatter", {}),
            chunks=[
                Chunk(heading=None, heading_level=0, content=b, start_line=i * 5)
                for i, b in enumerate(bodies)
            ],
            content_hash=path,
            modified_at=values.pop("modified_at", 1000.0),
        )
    )
