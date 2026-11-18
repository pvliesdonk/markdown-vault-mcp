"""Read-only, exact-string source joins from one returned note-text snapshot."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from markdown_it import MarkdownIt

from markdown_vault_mcp.scanner import parse_frontmatter, strip_frontmatter_block

if TYPE_CHECKING:
    from markdown_it.rules_block import StateBlock
    from markdown_it.rules_inline import StateInline

CITATION_SYNTAX = "commonmark-literal-footnote-markers-v1"
CITATION_MARKER_SCOPE = (
    "Only single-line labels without whitespace, brackets or backslashes are "
    "inspected, outside code, HTML blocks, links, images, wikilinks and footnote "
    "definitions. Other dialect extensions are not interpreted; absence is not "
    "an exhaustive citation audit."
)

# Labels are deliberately literal: no CommonMark reference-label normalization.
_MARKER = re.compile(r"\[\^([^\s\[\]\\]+)\]")


@dataclass(frozen=True)
class Citation:
    """One exact label, its occurrences and matching source-list positions."""

    id: str
    status: Literal["matched", "unmatched", "ambiguous"]
    occurrences: int
    source_indices: list[int]


@dataclass(frozen=True)
class CitationEdge:
    """A literal citation label and its resolved resource, if unique."""

    label: str
    status: Literal["matched", "unmatched", "ambiguous", "invalid_resource"]
    occurrences: int
    resource: str | None


@dataclass(frozen=True)
class CitationReport:
    """Literal source metadata joined without fetching or evaluating evidence."""

    path: str
    returned_text_sha256: str
    sources: Any
    citations: list[Citation]
    source_issues: list[str]
    duplicate_ids: list[str]
    unkeyed_source_indices: list[int]
    syntax: str = CITATION_SYNTAX
    scope: str = (
        "One returned note text, LF-normalized then UTF-8 hashed; not a raw-file "
        "etag or portable file identity. " + CITATION_MARKER_SCOPE + " "
        "Matches are literal attribution, "
        "not support or truth; unmatched markers may be ordinary footnotes. "
        "Source indices are zero-based, not stable keys; IDs are stable exact strings. "
        "No resources were opened."
    )


def _footnote_definition(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    pos = state.bMarks[start] + state.tShift[start]
    match = _MARKER.match(state.src, pos, state.eMarks[start])
    if not match or state.src[match.end() : match.end() + 1] != ":":
        return False
    if silent:
        return True
    # Definition prose does not identify a source. Omit its continuation too.
    line = start + 1
    while line < end and (
        state.isEmpty(line) or state.sCount[line] >= state.blkIndent + 4
    ):
        line += 1
    state.line = line
    return True


def _wikilink(state: StateInline, silent: bool) -> bool:
    if silent or not state.src.startswith("[[", state.pos):
        return False
    end = state.src.find("]]", state.pos + 2, state.posMax)
    # Unterminated wikilinks are outside this slice too: consume this inline
    # block rather than let an embedded marker look like an attribution.
    state.pos = end + 2 if end >= 0 else state.posMax
    return True


def _citation_marker(state: StateInline, silent: bool) -> bool:
    if silent:
        return False
    match = _MARKER.match(state.src, state.pos, state.posMax)
    if not match or state.linkLevel:
        return False
    # A reference label is navigation, even if its definition is absent.
    # Adjacent footnotes are different: the preceding token is our own marker.
    if (
        state.pos
        and state.src[state.pos - 1] == "]"
        and (
            state.pending
            or not state.tokens
            or state.tokens[-1].type != "citation_marker"
        )
    ):
        return False
    following = state.src[match.end() : match.end() + 2]
    if following.startswith(("(", "[]")) or (
        following.startswith("[") and not following.startswith("[^")
    ):
        return False
    token = state.push("citation_marker", "", 0)
    token.content = match[1]
    state.pos = match.end()
    return True


def _markers(body: str) -> dict[str, int]:
    parser = MarkdownIt("commonmark")
    parser.block.ruler.before(
        "reference",
        "citation_definition",
        _footnote_definition,
        {"alt": ["paragraph", "reference"]},
    )
    parser.inline.ruler.before("link", "citation_wikilink", _wikilink)
    # Navigation links win. Escapes, spans, HTML and block structure belong
    # to the parser, not to a regex on its rendered/decoded text.
    parser.inline.ruler.after("link", "citation_marker", _citation_marker)
    labels: dict[str, int] = {}
    for block in parser.parse(body):
        if block.type != "inline":
            continue
        for token in block.children or []:
            if token.type == "citation_marker":
                labels[token.content] = labels.get(token.content, 0) + 1
    return labels


def _source_ids(sources: Any) -> tuple[dict[str, list[int]], list[str], list[int]]:
    ids: dict[str, list[int]] = {}
    issues: list[str] = []
    unkeyed: list[int] = []
    if not isinstance(sources, list):
        return ids, ["sources is not a list; no source IDs joined"], unkeyed
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            issues.append(f"sources[{index}] is not a mapping")
            continue
        if not isinstance(source.get("resource"), str) or not source["resource"]:
            issues.append(
                f"sources[{index}].resource is missing or not a nonempty string"
            )
        if "id" not in source:
            unkeyed.append(index)
        elif isinstance(source["id"], str) and source["id"]:
            ids.setdefault(source["id"], []).append(index)
        else:
            issues.append(f"sources[{index}].id is not a nonempty string")
    return ids, issues, unkeyed


def inspect_citation_text(path: str, content: str) -> CitationReport:
    """Join plain footnote labels to literal source IDs in one text snapshot.

    Args:
        path: The selected note's path, used only as a report label.
        content: Full returned note text, including its frontmatter.

    Returns:
        Literal sources and exact-label joins with explicit syntax limits.
        The digest covers this text with CRLF and CR normalized to LF, encoded
        as UTF-8, not the original file bytes. Source metadata and body both
        come from this string, never a separately read frontmatter or etag.
    """
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    post = parse_frontmatter(text)
    sources = post.metadata.get("sources")
    ids, issues, unkeyed = (
        _source_ids(sources) if "sources" in post.metadata else ({}, [], [])
    )
    markers = _markers(strip_frontmatter_block(text))
    citations = _join(markers, ids)
    return CitationReport(
        path=path,
        returned_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        sources=sources,
        citations=citations,
        source_issues=issues,
        duplicate_ids=[label for label, indices in ids.items() if len(indices) > 1],
        unkeyed_source_indices=unkeyed,
    )


def _join(markers: dict[str, int], ids: dict[str, list[int]]) -> list[Citation]:
    citations = []
    for label, occurrences in markers.items():
        indices = ids.get(label, [])
        status: Literal["matched", "unmatched", "ambiguous"] = "unmatched"
        if indices:
            status = "matched" if len(indices) == 1 else "ambiguous"
        citations.append(Citation(label, status, occurrences, indices))
    return citations


def citation_edges(text: str) -> list[CitationEdge]:
    """Extract indexed citation edges from a single note snapshot.

    Args:
        text: Full decoded note text.

    Returns:
        Literal labels, statuses, counts and uniquely matched resources.
    """
    if "[^" not in text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    metadata = dict(parse_frontmatter(text).metadata)
    if "sources" not in metadata:
        return []
    sources = metadata["sources"]
    ids, _, _ = _source_ids(sources)
    citations = _join(_markers(strip_frontmatter_block(text)), ids)
    edges = []
    for citation in citations:
        resource = None
        if citation.status == "matched" and isinstance(sources, list):
            source = sources[citation.source_indices[0]]
            value = source.get("resource") if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                resource = value
        status: Literal["matched", "unmatched", "ambiguous", "invalid_resource"] = (
            citation.status
        )
        if status == "matched" and resource is None:
            status = "invalid_resource"
        edges.append(CitationEdge(citation.id, status, citation.occurrences, resource))
    return edges
