"""Serialization helpers for converting library result types to JSON-able dicts."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from markdown_vault_mcp.types import SubtreeToc, TocEntry


def toc_payload(
    data: list[TocEntry] | SubtreeToc,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Convert a ``get_toc`` result to its JSON-able dict form.

    Note mode returns a ``list[TocEntry]`` → a list of dicts; folder mode
    returns a ``SubtreeToc`` → a nested dict (``asdict`` recurses the
    ``SubtreeNote`` / ``TocEntry`` children).

    The dict keys match the dataclass field names verbatim, so a field
    rename or addition in ``types.py`` changes the serialized wire shape.
    """
    # Discriminate on type, not truthiness: an empty note TOC is a falsy [].
    if isinstance(data, SubtreeToc):
        return asdict(data)
    return [asdict(entry) for entry in data]
