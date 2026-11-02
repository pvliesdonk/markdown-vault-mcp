"""Pure read-payload helpers shared by local commands and MCP boundaries."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.query_types import NoteGrouping, NoteQuery


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


def query_request(
    tool: str, arguments: dict[str, Any]
) -> tuple[NoteQuery, NoteGrouping | None]:
    """Validate pure query inputs before any local or remote I/O.

    Args:
        tool: Query, count or aggregation tool name.
        arguments: Public wire arguments; presentation and grouping stay distinct.

    Returns:
        Validated native population and optional bucket projection.

    Raises:
        ReadPayloadError: A query or grouping input is invalid.
    """
    from markdown_vault_mcp.query_types import NoteGrouping, NoteQuery

    values = dict(arguments)
    values.pop("wait_for_pending_writes", None)
    try:
        grouping = None
        if tool == "aggregate_documents":
            grouping = NoteGrouping(
                by=values.pop("by"),
                field=values.pop("field", "tags"),
                limit=values.pop("limit", 50),
            )
        return NoteQuery(**values), grouping
    except ValueError as exc:
        raise ReadPayloadError(str(exc)) from None


def validate_query_payload(
    tool: str, data: Any, *, remote_meta: dict[str, Any] | None = None
) -> None:
    """Refuse malformed query envelopes before emitting a partial response.

    Args:
        tool: Query, count or aggregation tool name.
        data: Native payload; extra fields are preserved.
        remote_meta: Remote freshness observation, or None for local unknown state.

    Raises:
        ReadPayloadError: Counts, shapes or freshness contradict the native contract.
    """
    valid = isinstance(data, dict) and _natural(data.get("total"))
    if valid and "index_stale" in data:
        valid = type(data["index_stale"]) is bool
    if valid and remote_meta is not None:
        observed = remote_meta.get("index_stale")
        valid = type(observed) is bool and data.get("index_stale") is observed
    if valid and tool == "query_documents":
        valid = _valid_query_page(data)
    if valid and tool == "aggregate_documents":
        valid = _valid_query_aggregate(data)
    if not valid:
        raise ReadPayloadError("Server returned an invalid query payload.", 1)
    try:
        json.dumps(data, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        raise ReadPayloadError("Server returned a non-JSON query payload.", 1) from None


def _natural(value: Any) -> bool:
    return type(value) is int and value >= 0


def _number(value: Any) -> bool:
    return type(value) in (int, float) and abs(value) <= sys.float_info.max


def _valid_query_page(data: dict[str, Any]) -> bool:
    items = data.get("items")
    if not (
        _natural(data.get("offset"))
        and _natural(data.get("limit"))
        and 1 <= data["limit"] <= 1000
        and type(data.get("truncated")) is bool
        and isinstance(items, list)
        and all(_valid_query_item(item) for item in items)
    ):
        return False
    return (
        len(items) == min(data["limit"], max(0, data["total"] - data["offset"]))
        and len({item["path"] for item in items}) == len(items)
        and data["truncated"] == (data["offset"] + len(items) < data["total"])
    )


def _valid_query_item(item: Any) -> bool:
    if not (
        isinstance(item, dict)
        and all(isinstance(item.get(key), str) for key in ("path", "title", "folder"))
        and isinstance(item.get("frontmatter"), dict)
        and _number(item.get("modified_at"))
        and "score" in item
        and (item["score"] is None or _number(item["score"]))
    ):
        return False
    return "sections" not in item or (
        isinstance(item["sections"], list)
        and all(
            isinstance(section, dict)
            and "heading" in section
            and isinstance(section["heading"], (str, type(None)))
            and isinstance(section.get("content"), str)
            and _number(section.get("score"))
            for section in item["sections"]
        )
    )


def _valid_query_aggregate(data: dict[str, Any]) -> bool:
    buckets = data.get("buckets")
    if not (
        _natural(data.get("missing"))
        and data["missing"] <= data["total"]
        and type(data.get("truncated")) is bool
        and isinstance(buckets, list)
        and all(
            isinstance(bucket, dict)
            and "key" in bucket
            and isinstance(bucket["key"], (str, type(None)))
            and _natural(bucket.get("count"))
            and bucket["count"] <= data["total"]
            for bucket in buckets
        )
    ):
        return False
    counts = {bucket["key"]: bucket["count"] for bucket in buckets}
    if len(counts) != len(buckets):
        return False
    if not buckets:
        return data["total"] == 0 and not data["truncated"]
    if data["total"] == 0:
        return False
    if (None in counts or not data["truncated"]) and counts.get(None, 0) != data[
        "missing"
    ]:
        return False
    # Multi-value tags may count a note in several buckets; sums can exceed total.
    return data["truncated"] is True or sum(counts.values()) >= data["total"]
