"""Reverse lookup of indexed literal citation attribution."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import OKF_RESERVED_FILENAMES

if TYPE_CHECKING:
    from markdown_vault_mcp.fts_index import FTSIndex


class CitationManager:
    """Find concepts citing an exact source resource."""

    def __init__(self, fts: FTSIndex) -> None:
        """Store the index used for lookups.

        Args:
            fts: Index containing citation rows.
        """
        self._fts = fts

    def find_citing(self, resource: str) -> dict[str, Any]:
        """Find attribution and unresolved labels for a resource.

        Args:
            resource: Exact source resource string.

        Returns:
            Citing notes and diagnostics for notes declaring the resource.
        """
        if not resource.strip():
            raise InvalidRequestError(
                "resource must be nonempty. Call find_citing_notes with a source resource."
            )
        matched, unresolved = self._fts.get_citation_rows(resource)
        citing: dict[str, dict[str, Any]] = {}
        for path, title, label, occurrences in matched:
            if PurePosixPath(path).name in OKF_RESERVED_FILENAMES:
                continue
            entry = citing.setdefault(
                path, {"path": path, "title": title, "labels": []}
            )
            entry["labels"].append({"label": label, "occurrences": occurrences})
        diagnostics = [
            {"path": path, "label": label, "status": status, "occurrences": count}
            for path, label, status, count in unresolved
            if PurePosixPath(path).name not in OKF_RESERVED_FILENAMES
        ]
        return {
            "resource": resource,
            "citing": list(citing.values()),
            "diagnostics": diagnostics,
            "scope": "Exact resource string; indexed non-reserved notes only. No resources fetched. A label match is attribution, not support.",
        }
