"""Pure ranking-pipeline helpers shared by every search channel.

Everything here is stateless: score re-ranking (length downweight, folder
boost), file grouping (field collapse), snippet windowing, and the adapter
dataclasses that give heterogeneous channel rows a common shape.  The
:class:`~markdown_vault_mcp.managers.search.SearchManager` composes these
into the keyword / semantic / hybrid pipelines.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from typing import Protocol, TypeVar

# Regex for extracting query tokens (alphanumeric sequences).
_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class ScorableRow(Protocol):
    """Row contract consumed by the length-downweight helper.

    Both :class:`~markdown_vault_mcp.types.FTSResult` and the local
    :class:`SemanticRow` adapter satisfy this Protocol structurally; no
    nominal subclassing required.  All callers are dataclasses so
    :func:`dataclasses.replace` is used to produce adjusted-score copies
    without mutating the input.
    """

    score: float
    chunk_count: int


_ScorableT = TypeVar("_ScorableT", bound=ScorableRow)


def apply_length_downweight(
    rows: list[_ScorableT], *, alpha: float
) -> list[_ScorableT]:
    """Re-rank ``rows`` by ``score / (1 + alpha * log(chunk_count))``.

    Returns a new list sorted by descending adjusted score; input is not
    mutated.  Callers must pass dataclass instances (every caller in this
    codebase already does) so :func:`dataclasses.replace` can produce the
    adjusted-score copies.
    """
    if alpha <= 0 or not rows:
        return list(rows)

    adjusted: list[tuple[_ScorableT, float]] = []
    for row in rows:
        chunk_count = max(1, row.chunk_count)
        # log(1) = 0 -> factor = 1 -> no change for single-chunk docs.
        factor = 1.0 + alpha * math.log(chunk_count)
        new_score = row.score / factor
        # Protocols can't promise __dataclass_fields__; the helper's
        # contract is "callers pass dataclasses" (FTSResult / SemanticRow
        # both are), enforced at runtime by replace() itself.
        new_row = _dc_replace(row, score=new_score)  # type: ignore[type-var]
        adjusted.append((new_row, new_score))

    adjusted.sort(key=lambda t: t[1], reverse=True)
    return [r for r, _ in adjusted]


class FolderBoostableRow(Protocol):
    """Row contract consumed by the folder-boost helper.

    :class:`~markdown_vault_mcp.types.FTSResult`, :class:`SemanticRow`, and
    :class:`GroupableFTS` all satisfy this Protocol structurally.  All
    callers are dataclasses so :func:`dataclasses.replace` is used to
    produce adjusted-score copies without mutating the input.
    """

    folder: str
    score: float


_FolderBoostableT = TypeVar("_FolderBoostableT", bound=FolderBoostableRow)


def folder_weight(folder: str, weights: dict[str, float]) -> float:
    """Return the weight of the deepest configured prefix matching *folder*.

    A prefix ``K`` matches folder ``F`` when ``F == K`` or ``F`` starts with
    ``K + "/"`` (boundary match, so ``"Project"`` never matches
    ``"Projects"``).  When several prefixes match, the longest (deepest)
    one wins.  No match returns ``1.0``.
    """
    best_key: str | None = None
    for key in weights:
        if (folder == key or folder.startswith(key + "/")) and (
            best_key is None or len(key) > len(best_key)
        ):
            best_key = key
    return 1.0 if best_key is None else weights[best_key]


def apply_folder_boost(
    rows: list[_FolderBoostableT], *, weights: dict[str, float] | None
) -> list[_FolderBoostableT]:
    """Scale positive row scores by their folder's configured weight.

    Returns a new list re-sorted by descending adjusted score; input rows
    are not mutated (adjusted rows are :func:`dataclasses.replace` copies).
    Only positive scores are scaled — a negative score (possible only for
    raw cosine similarities) is left untouched so a demoting weight cannot
    accidentally promote it.  Empty/absent *weights* is the identity.
    """
    if not weights or not rows:
        return list(rows)

    out: list[_FolderBoostableT] = []
    for row in rows:
        weight = folder_weight(row.folder, weights)
        if weight != 1.0 and row.score > 0:
            # Protocols can't promise __dataclass_fields__; the helper's
            # contract is "callers pass dataclasses" (FTSResult /
            # SemanticRow / GroupableFTS all are), enforced at runtime by
            # replace() itself.
            row = _dc_replace(row, score=row.score * weight)  # type: ignore[type-var]
        out.append(row)
    out.sort(key=lambda r: r.score, reverse=True)
    return out


class ChannelRow(Protocol):
    """Full row contract of a ranked search-channel hit.

    The superset contract: path/section identity plus display payload.
    :class:`~markdown_vault_mcp.types.FTSResult`, :class:`SemanticRow`, and
    :class:`GroupableFTS` all satisfy it structurally.  Consumed by the RRF
    fold and the grouped-result assembly, which need every field.
    """

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    start_line: int
    section_id: int


class GroupableRow(Protocol):
    """Row contract consumed by :func:`group_by_path`.

    Adds ``heading``, ``start_line`` and ``section_id`` to the cap-helper's
    contract so grouped output preserves section identity and breaks score
    ties deterministically.  ``start_line`` defaults to ``0`` for legacy
    vector rows loaded from older .json sidecars; ``section_id`` is the
    final tie-break (the ``sections`` rowid) and is ``0`` for any channel
    that cannot resolve it (vector rows, legacy indices).
    """

    path: str
    heading: str | None
    score: float
    start_line: int
    section_id: int


_GroupableT = TypeVar("_GroupableT", bound=GroupableRow)


def group_by_path(
    rows: list[_GroupableT], *, chunks_per_file: int, file_limit: int
) -> list[list[_GroupableT]]:
    """Collapse score-desc rows into file groups.

    Walks ``rows`` (assumed already sorted DESC by score) and emits a list
    of groups.  Each group is a list of rows sharing the same ``path``,
    capped at ``chunks_per_file`` rows.  At most ``file_limit`` groups are
    returned.  Sections within a group are sorted ``(score DESC,
    start_line ASC, section_id ASC)`` so ties surface in document order.
    The ``section_id`` key (the ``sections`` rowid) makes the order fully
    deterministic even when chunks share a ``start_line`` — e.g. word-split
    fragments of a single oversize source line, which the chunker emits
    with identical ``start_line`` values.

    Args:
        rows: Rows pre-sorted by descending score.
        chunks_per_file: Maximum rows per group; must be >= 1.
        file_limit: Maximum number of groups emitted.

    Returns:
        List of groups; outer order = file rank (best file first).

    Raises:
        ValueError: If ``chunks_per_file`` < 1.
    """
    if chunks_per_file < 1:
        raise ValueError(f"chunks_per_file must be >= 1, got {chunks_per_file}")

    groups: dict[str, list[_GroupableT]] = {}
    order: list[str] = []
    for row in rows:
        existing = groups.get(row.path)
        if existing is None:
            if len(order) >= file_limit:
                continue
            order.append(row.path)
            groups[row.path] = [row]
        elif len(existing) < chunks_per_file:
            existing.append(row)

    # Sort each group's sections by (score DESC, start_line ASC, section_id
    # ASC) so ties within a file surface in document order — section_id is
    # the final tie-break for chunks sharing a start_line.
    return [
        sorted(groups[p], key=lambda r: (-r.score, r.start_line, r.section_id))
        for p in order
    ]


def _query_tokens(query: str) -> set[str]:
    """Tokenize *query* into matchable lowercase alphanumeric tokens.

    Emits both the joined-per-word form (matches our content normalization,
    e.g. ``"isn't"`` → ``"isnt"``) AND the individual alphanumeric runs
    (matches per-token content words, e.g. ``"se-cura"`` → ``{"se",
    "cura"}`` so a chunk that mentions ``"cura"`` alone still hits).
    """
    tokens: set[str] = set()
    for word in query.split():
        runs = _QUERY_TOKEN_RE.findall(word)
        if not runs:
            continue
        # Joined form: runs concatenated.
        tokens.add("".join(runs).lower())
        # Individual runs: each alphanumeric span.
        tokens.update(r.lower() for r in runs)
    tokens.discard("")
    return tokens


def _best_window_start(
    lower_words: list[str], query_tokens: set[str], snippet_words: int
) -> tuple[int, int]:
    """Slide a ``snippet_words`` window over *lower_words*, densest wins.

    Returns:
        Tuple ``(best_start, best_score)`` — the window offset with the
        most query-token hits (leftmost on ties) and its hit count.
    """
    best_start = 0
    best_score = sum(1 for w in lower_words[:snippet_words] if w in query_tokens)
    cur_score = best_score
    for i in range(1, len(lower_words) - snippet_words + 1):
        if lower_words[i - 1] in query_tokens:
            cur_score -= 1
        if lower_words[i + snippet_words - 1] in query_tokens:
            cur_score += 1
        if cur_score > best_score:
            best_score = cur_score
            best_start = i
    return best_start, best_score


def compute_snippet_window(content: str, query: str, *, snippet_words: int) -> str:
    """Pick a ``snippet_words``-wide window from ``content``.

    Returns the full content when ``snippet_words`` is 0, when the chunk is
    already shorter, or as a fallback when no query tokens overlap (in which
    case the first ``snippet_words`` words are returned with a trailing
    ellipsis).

    Uses simple case-insensitive substring matching on alphanumeric tokens.
    """
    if snippet_words <= 0:
        return content

    words = content.split()
    if len(words) <= snippet_words:
        return content

    query_tokens = _query_tokens(query)
    if not query_tokens:
        return " ".join(words[:snippet_words]) + "…"

    # Normalise each word: keep alphanumeric chars, lower-case, fall back to
    # the lowercased original if no alphanumeric chars were found.
    lower_words = [
        "".join(_QUERY_TOKEN_RE.findall(w)).lower() or w.lower() for w in words
    ]

    best_start, best_score = _best_window_start(
        lower_words, query_tokens, snippet_words
    )

    if best_score == 0:
        # No literal overlap anywhere — fall back to first-N words.
        return " ".join(words[:snippet_words]) + "…"

    snippet = " ".join(words[best_start : best_start + snippet_words])
    if best_start > 0:
        snippet = "…" + snippet
    if best_start + snippet_words < len(words):
        snippet = snippet + "…"
    return snippet


@dataclass
class SemanticRow:
    """Adapter row for vector search results so they expose .score / .chunk_count.

    ``section_id`` is always ``0``: the vector store keys chunks by metadata,
    not by ``sections`` rowid, and vector scores essentially never tie
    (distinct embeddings → distinct cosine), so the tie-break never engages
    for a pure semantic channel.  The field exists only to satisfy the
    :class:`GroupableRow` protocol.
    """

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    chunk_count: int
    start_line: int = 0
    section_id: int = 0


@dataclass
class GroupableFTS:
    """Adapter row exposing title/folder/content/start_line/section_id to
    group_by_path for the keyword and hybrid channels."""

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    start_line: int
    section_id: int = 0
