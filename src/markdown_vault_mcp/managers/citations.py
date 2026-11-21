"""Reverse lookup of indexed literal citation attribution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.citations import CITATION_MARKER_SCOPE, CITATION_SYNTAX
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import is_okf_reserved_file

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.fts_index import FTSIndex


class CitationManager:
    """Find concepts citing an exact source resource."""

    def __init__(self, fts: FTSIndex, okf_active: Callable[[], bool]) -> None:
        """Store the index used for lookups.

        Args:
            fts: Index containing citation rows.
            okf_active: Whether OKF reserved-file rules currently apply.
        """
        self._fts = fts
        self._okf_active = okf_active

    def find_citing(self, resource: str) -> dict[str, Any]:
        """Find attribution and unresolved labels for a resource.

        Args:
            resource: Exact source resource string.

        Returns:
            Citing notes and diagnostics for notes declaring the resource.
        """
        if not resource.strip():
            raise InvalidRequestError(
                "resource is blank. Call find_citing_notes with the resource "
                "string of a source record, as inspect_citations reports it."
            )
        rows, unconverted = self._fts.get_citation_rows(resource)
        okf_active = self._okf_active()
        citing: dict[str, dict[str, Any]] = {}
        diagnostics = []
        for path, title, label, status, occurrences in rows:
            if okf_active and is_okf_reserved_file(path):
                continue
            if status != "matched":
                diagnostics.append(
                    {
                        "path": path,
                        "label": label,
                        "status": status,
                        "occurrences": occurrences,
                    }
                )
                continue
            entry = citing.setdefault(
                path, {"path": path, "title": title, "labels": []}
            )
            entry["labels"].append({"label": label, "occurrences": occurrences})
        return {
            "resource": resource,
            "citing": list(citing.values()),
            "diagnostics": diagnostics,
            "unconverted_notes": unconverted,
            "syntax": CITATION_SYNTAX,
            "scope": (
                "Exact resource string; indexed notes only, excluding reserved files when OKF is active. Diagnostics cover only notes declaring the resource. Unconverted indexed notes are omitted; reindex with force=true converts them. Results are unbounded. No resources fetched. A label match is attribution, not support. "
                + CITATION_MARKER_SCOPE
            ),
        }
