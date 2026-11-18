"""Versioned identity of a concept as presented for review."""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import Any, cast

import frontmatter
import yaml
from frontmatter.default_handlers import JSONHandler, SafeLoader, YAMLHandler
from yaml.constructor import SafeConstructor
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.resolver import BaseResolver

from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.managers.document import _head_end, _read_view

_EXCLUDED = frozenset({"verified", "x-mvmcp"})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _yaml_node(node: yaml.Node, *, root: bool = False) -> Any:
    if isinstance(node, ScalarNode):
        return [node.tag, node.value]
    if isinstance(node, SequenceNode):
        return [node.tag, [_yaml_node(item) for item in node.value]]
    mapping = cast("MappingNode", node)
    SafeConstructor().flatten_mapping(mapping)
    return [mapping.tag, _yaml_pairs(mapping, root=root)]


def _yaml_pairs(node: MappingNode, *, root: bool) -> list[Any]:
    # Match the reader's last-wins rule for parser-equal keys.
    constructor = SafeConstructor()
    last: dict[Any, Any] = {}
    for key, value in node.value:
        if root and isinstance(key, ScalarNode) and key.value in _EXCLUDED:
            continue
        last[constructor.construct_object(key, deep=True)] = [
            _yaml_node(key),
            _yaml_node(value),
        ]
    return sorted(last.values(), key=lambda pair: _json(pair[0]))


class _Number(str):
    """A JSON number retained as its source lexeme."""


def _json_value(value: Any, *, root: bool = False) -> Any:
    if isinstance(value, dict):
        pairs = [
            [["str", key], _json_value(item)]
            for key, item in value.items()
            if not (root and key in _EXCLUDED)
        ]
        return ["object", sorted(pairs, key=lambda pair: _json(pair[0]))]
    if isinstance(value, list):
        return ["array", [_json_value(item) for item in value]]
    if isinstance(value, _Number):
        return ["number", str(value)]
    if isinstance(value, (dt.date, dt.time)):
        return [type(value).__name__, value.isoformat()]
    return [type(value).__name__, value]


def _metadata(handler: Any, source: str) -> Any:
    if isinstance(handler, YAMLHandler):
        node = yaml.compose(source, Loader=SafeLoader)
        if (
            not isinstance(node, MappingNode)
            or node.tag != BaseResolver.DEFAULT_MAPPING_TAG
        ):
            return ["map", []]
        return _yaml_node(node, root=True)
    if isinstance(handler, JSONHandler):
        value = json.loads(
            source,
            parse_int=_Number,
            parse_float=_Number,
            parse_constant=_Number,
        )
        return _json_value(value, root=True)
    return _json_value(handler.load(source) or {}, root=True)


def concept_snapshot(text: str) -> dict[str, str]:
    """Return the versioned digest of a raw concept's body and metadata.

    Args:
        text: Complete markdown, including any frontmatter.

    Returns:
        Algorithm identifier and lowercase SHA-256 digest.
    """
    view = _read_view(text)
    end = _head_end(view)
    metadata: Any = ["map", []]
    body = view
    if end:
        candidate = view[:end].lstrip()
        handler = frontmatter.detect_format(candidate, frontmatter.handlers)
        if handler is not None:
            source, _ = handler.split(candidate)
            metadata = _metadata(handler, source)
            if isinstance(metadata, list) and len(metadata) == 2 and metadata[1] == []:
                metadata = ["map", []]
            body = view[: len(view) - len(view.lstrip())] + view[end:]
    payload = _json([metadata, body]).encode("utf-8")
    return {"algo": "mvmcp-concept-v1", "digest": compute_etag(payload)}


def validate_reviewed_snapshot(text: str, snapshot: dict[str, str]) -> dict[str, str]:
    """Check that a supplied snapshot identifies the current concept.

    Args:
        text: Complete current note text.
        snapshot: Snapshot returned by a whole-note read.

    Returns:
        The matching canonical snapshot.

    Raises:
        InvalidRequestError: If the snapshot is malformed or stale.
    """
    from markdown_vault_mcp.exceptions import InvalidRequestError

    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != {"algo", "digest"}
        or snapshot.get("algo") != "mvmcp-concept-v1"
        or not isinstance(snapshot.get("digest"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", snapshot["digest"])
        or concept_snapshot(text) != snapshot
    ):
        raise InvalidRequestError(
            "The reviewed snapshot is invalid or no longer current. Read the full "
            "note, review it, and pass its snapshot to okf_verify."
        )
    return snapshot


@dataclass(frozen=True)
class ReviewRequest:
    """Reviewed snapshot and exact bytes to verify as one operation."""

    snapshot: dict[str, str]
    etag: str
    check: str = "human-review"


def _json_space(source: str, position: int) -> int:
    tail = source[position:]
    return position + len(tail) - len(tail.lstrip())


def edit_json_review_header(text: str, changes: dict[str, Any]) -> str | None:
    """Update review fields in JSON frontmatter without rewriting other values.

    Args:
        text: Complete note, including frontmatter.
        changes: Top-level review fields to replace or add.

    Returns:
        Updated note, or None when its frontmatter is not JSON.
    """
    from markdown_vault_mcp.exceptions import InvalidRequestError

    offset = len(text) - len(text.lstrip("\ufeff \t\r\n"))
    candidate = text[offset:]
    handler = frontmatter.detect_format(candidate, frontmatter.handlers)
    if not isinstance(handler, JSONHandler):
        return None
    source, _ = handler.split(candidate)
    source_length = len(source)
    decoder = json.JSONDecoder()
    decoder.raw_decode(source)  # Validate delimiters before scanning individual values.
    pos = 1
    edits: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    while True:
        pos = _json_space(source, pos)
        if source[pos] == "}":
            break
        key, end = decoder.raw_decode(source, pos)
        pos = _json_space(source, _json_space(source, end) + 1)
        start = pos
        _, pos = decoder.raw_decode(source, pos)
        if key in changes:
            if key in seen:
                raise InvalidRequestError(
                    "Duplicate review fields cannot be safely updated"
                )
            seen.add(key)
            edits.append((start, pos, json.dumps(changes[key], ensure_ascii=False)))
        pos = _json_space(source, pos)
        pos += int(source[pos] == ",")
    additions = [
        f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}"
        for k, v in changes.items()
        if k not in seen
    ]
    if additions:
        separator = ", " if source[1:pos].strip() else ""
        edits.append(
            (
                pos,
                pos,
                separator
                + ", ".join(additions)
                + ("\r\n" if "\r\n" in source else "\n"),
            )
        )
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return text[:offset] + source + candidate[source_length:]
