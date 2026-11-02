"""Pure read-payload helpers shared by local commands and MCP boundaries."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class ReadPayloadError(ValueError):
    """A malformed command predicate or read payload, without an SDK dependency."""

    def __init__(self, message: str, code: int = 2) -> None:
        """Store a safe diagnostic and distinguish input from payload failures."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SearchOptions:
    """Validated search controls shared by local and remote dispatch."""

    mode: str = "keyword"
    filters: dict[str, str] = field(default_factory=dict)
    chunks_per_file: int | None = None
    snippet_words: int | None = None
    wait_for_pending_writes: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"keyword", "semantic", "hybrid", "auto"}:
            raise ReadPayloadError("Mode must be keyword, semantic, hybrid or auto.")
        if self.chunks_per_file is not None and self.chunks_per_file < 1:
            raise ReadPayloadError("--chunks-per-file must be at least 1.")
        if self.snippet_words is not None and self.snippet_words < 0:
            raise ReadPayloadError("--snippet-words must be at least 0.")

    def arguments(self, query: str, limit: int, folder: str | None) -> dict[str, Any]:
        """Build a request, omitting unselected optional capabilities.

        Args:
            query: Search query.
            limit: Note limit.
            folder: Optional folder boundary.

        Returns:
            Tool arguments; auto leaves mode to the server or local library.
        """
        result: dict[str, Any] = {"query": query, "limit": limit, "folder": folder}
        if self.mode != "auto":
            result["mode"] = self.mode
        for key in (
            "filters",
            "chunks_per_file",
            "snippet_words",
            "wait_for_pending_writes",
        ):
            value = getattr(self, key)
            if value is not None and value is not False and value != {}:
                result[key] = value
        return result


def parse_filters(values: list[str]) -> dict[str, str]:
    """Parse unique KEY=VALUE predicates, preserving equals in values.

    Args:
        values: Repeated command-line predicates.

    Returns:
        Frontmatter predicates with unchanged values.

    Raises:
        ReadPayloadError: A predicate has no key/separator or repeats a key.
    """
    result: dict[str, str] = {}
    for value in values:
        key, separator, text = value.partition("=")
        if not separator or not key or key in result:
            raise ReadPayloadError("--filter requires unique, nonempty KEY=VALUE keys.")
        result[key] = text
    return result


def annotate_hits(
    results: list[dict[str, Any]],
    annotation: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Attach the native OKF hit annotation when a bundle is active.

    Args:
        results: Serialized hits, mutated in place.
        annotation: Native annotation function, or None for an inactive bundle.

    Returns:
        Hits with native annotations; ordinary vaults remain unchanged.
    """
    if annotation is not None:
        for hit in results:
            hit["okf"] = annotation(hit.get("frontmatter") or {})
    return results


def validate_search(data: Any) -> None:
    """Refuse a malformed search result before emitting any output.

    Args:
        data: Decoded local or remote payload.

    Raises:
        ReadPayloadError: The result cannot be rendered as ranked note hits.
    """
    if not isinstance(data, list) or any(
        not isinstance(hit, dict)
        or not isinstance(hit.get("path"), str)
        or not isinstance(hit.get("title"), (str, type(None)))
        or not isinstance(hit.get("score"), (float, int))
        or isinstance(hit.get("score"), bool)
        or abs(hit["score"]) > sys.float_info.max
        or not math.isfinite(hit["score"])
        for hit in data
    ):
        raise ReadPayloadError("Server returned an invalid search payload.", 1)
    try:
        json.dumps(data, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        raise ReadPayloadError(
            "Server returned a non-JSON search payload.", 1
        ) from None
