"""Validated exhaustive keyword-query requests and bounded result shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from collections.abc import Mapping

QueryScope = Literal["all", "metadata", "body"]
QueryOrder = Literal["auto", "path", "folded_path"]
PatternMode = Literal["glob", "prefix", "substring"]


def _instant(value: str | None) -> float | None:
    if value is None:
        return None
    matched = re.fullmatch(
        r"\d{4}-\d\d-\d\d[Tt]\d\d:\d\d:\d\d(?P<fraction>\.\d+)?"
        r"(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)",
        value,
    )
    if matched is None:
        raise ValueError("Modified bounds must be RFC3339 instants with a timezone")
    fraction = matched.group("fraction") or ""
    whole = value.replace(fraction, "") if fraction else value
    try:
        seconds = datetime.fromisoformat(
            whole.upper().replace("Z", "+00:00")
        ).timestamp()
    except ValueError as exc:
        raise ValueError("Modified bounds must be valid RFC3339 instants") from exc
    return seconds + (float("0" + fraction) if fraction else 0.0)


def _bounded(name: str, value: int, minimum: int, maximum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")


@dataclass(frozen=True)
class NoteQuery:
    """One exhaustive keyword population with bounded page presentation.

    Attributes:
        query: FTS5 expression; malformed syntax falls back to literal terms.
        scope: Indexed all, metadata or body columns.
        path: Case-sensitive exact note path, independent of lexical matching.
        folder: Normalized subtree; empty selects root notes only.
        pattern: Path glob, or literal prefix/substring in internal modes.
        filters: AND equality predicates over configured indexed fields.
        modified_after: Inclusive RFC3339 lower bound.
        modified_before: Exclusive RFC3339 upper bound.
        offset: Nonnegative live-view page offset.
        limit: Page size,1-1000.
        chunks_per_file: Maximum sections per body/all item, at least1.
        snippet_words: Maximum excerpt words;0 returns the full section.
        order: Internal storage ordering; not an MCP/CLI query parameter.
        pattern_mode: Internal literal lookup seam; not an MCP/CLI parameter.
    """

    query: str = ""
    scope: QueryScope = "all"
    path: str | None = None
    folder: str | None = None
    pattern: str | None = None
    filters: Mapping[str, str] = field(default_factory=dict)
    modified_after: str | None = None
    modified_before: str | None = None
    offset: int = 0
    limit: int = 20
    chunks_per_file: int = 2
    snippet_words: int = 200
    order: QueryOrder = "auto"
    pattern_mode: PatternMode = "glob"
    _lower: float | None = field(init=False, repr=False)
    _upper: float | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if "\x00" in self.query:
            raise ValueError("query must not contain NUL characters")
        if self.scope not in ("all", "metadata", "body"):
            raise ValueError("scope must be all, metadata or body")
        if self.order not in ("auto", "path", "folded_path"):
            raise ValueError("order must be auto, path or folded_path")
        if self.pattern_mode not in ("glob", "prefix", "substring"):
            raise ValueError("pattern_mode must be glob, prefix or substring")
        _bounded("offset", self.offset, 0)
        _bounded("limit", self.limit, 1, 1000)
        _bounded("chunks_per_file", self.chunks_per_file, 1)
        _bounded("snippet_words", self.snippet_words, 0)
        if any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in self.filters.items()
        ):
            raise ValueError("filters require string keys and values")
        object.__setattr__(self, "filters", MappingProxyType(dict(self.filters)))
        lower, upper = _instant(self.modified_after), _instant(self.modified_before)
        if lower is not None and upper is not None and lower >= upper:
            raise ValueError("modified_after must precede modified_before")
        object.__setattr__(self, "_lower", lower)
        object.__setattr__(self, "_upper", upper)


class NoteSection(TypedDict):
    """A bounded physical indexed section with its keyword score."""

    heading: str | None
    content: str
    score: float


class NoteQueryItem(TypedDict):
    """Note metadata; body/all projections additionally supply sections."""

    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    score: float | None
    sections: NotRequired[list[NoteSection]]


@dataclass(frozen=True)
class NoteQueryPage:
    """Distinct-note page and exact population size from one backend read view."""

    items: list[NoteQueryItem]
    total: int
    offset: int
    limit: int
    truncated: bool


@dataclass(frozen=True)
class NoteCount:
    """Exact distinct-note population size, independent of presentation limits."""

    total: int


@dataclass(frozen=True)
class NoteGrouping:
    """Bucket projection; limit bounds output buckets, never input notes.

    Attributes:
        by: Folder, indexed tag value or UTC modification month.
        field: Configured indexed field for tag grouping.
        limit: Maximum output buckets,1-1000.
    """

    by: Literal["folder", "tag", "month"]
    field: str = "tags"
    limit: int = 50

    def __post_init__(self) -> None:
        if self.by not in ("folder", "tag", "month"):
            raise ValueError("by must be folder, tag or month")
        _bounded("grouping limit", self.limit, 1, 1000)


class NoteBucket(TypedDict):
    """Distinct-note count for one value; null represents missing tags."""

    key: str | None
    count: int


@dataclass(frozen=True)
class NoteAggregate:
    """Buckets over the full population; multi-value bucket sums may exceed total."""

    total: int
    buckets: list[NoteBucket]
    missing: int
    truncated: bool
