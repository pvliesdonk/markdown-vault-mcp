"""Versioned identity of a concept as presented for review."""

from __future__ import annotations

import datetime as dt
import json
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

    if concept_snapshot(text) != snapshot:
        raise InvalidRequestError(
            "The reviewed snapshot is invalid or no longer current. Read the full "
            "note, review it, and pass its snapshot to okf_verify."
        )
    return snapshot


def _review_sequence(source: str, node: SequenceNode, value: dict[str, Any]) -> str:
    from markdown_vault_mcp._okf_author import _render

    if node.flow_style:
        pos = node.end_mark.index - 1
        tokens = [
            token
            for token in yaml.scan(source)
            if node.start_mark.index <= token.start_mark.index < pos
        ]
        trailing_comma = tokens and isinstance(tokens[-1], yaml.FlowEntryToken)
        prefix = ", " if node.value and not trailing_comma else ""
        return source[:pos] + prefix + _render(value) + source[pos:]
    pos = node.end_mark.index
    newline = "\r\n" if "\r\n" in source else "\n"
    return (
        source[:pos]
        + " " * node.start_mark.column
        + "- "
        + _render(value)
        + newline
        + source[pos:]
    )


def _review_nodes(node: MappingNode) -> dict[str, Any]:
    from markdown_vault_mcp.exceptions import InvalidRequestError

    result = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value in result:
            raise InvalidRequestError(
                "Duplicate review fields cannot be safely updated"
            )
        if value.start_mark.index < key.end_mark.index:
            raise InvalidRequestError("Cannot safely update an aliased review field")
        result[key.value] = value
    return result


def _bind_yaml_header(
    text: str,
    span: tuple[int, int] | None,
    expected: dict[str, Any],
    entry: dict[str, Any],
    claim: dict[str, Any],
) -> str:
    from markdown_vault_mcp._okf_author import (
        _edit_header,
        _generated_edits,
        _parse_root,
        _render,
    )

    ext = expected["x-mvmcp"]
    newline = "\r\n" if "\r\n" in text else "\n"
    if span is None:
        prefix = "\ufeff" if text.startswith("\ufeff") else ""
        header = "".join(
            f"{key}: {_render(expected[key])}{newline}"
            for key in ("verified", "x-mvmcp")
        )
        result = (
            prefix
            + "---"
            + newline
            + header
            + "---"
            + newline
            + text.removeprefix("\ufeff")
        )
    else:
        start, end = span
        source = text[start:end]
        root = _parse_root(source)
        nodes = _review_nodes(root)
        node = nodes.get("verified")
        if isinstance(node, SequenceNode):
            source = _review_sequence(source, node, entry)
        elif isinstance(node, MappingNode):
            a, b = node.start_mark.index, node.end_mark.index
            old_mapping = source[a:b]
            if node.flow_style:
                replacement = "[" + old_mapping + ", " + _render(entry) + "]"
            else:
                indent = " " * node.start_mark.column
                lines = old_mapping.splitlines(keepends=True)
                replacement = (
                    "- " + lines[0] + "".join("  " + line for line in lines[1:])
                )
                replacement += indent + "- " + _render(entry) + newline
            source = source[:a] + replacement + source[b:]
        else:
            source = _edit_header(source, {"verified": expected["verified"]}, newline)
        root = _parse_root(source)
        nodes = _review_nodes(root)
        node = nodes.get("x-mvmcp")
        if not isinstance(node, MappingNode):
            source = _edit_header(source, {"x-mvmcp": ext}, newline)
        else:
            nested = _review_nodes(node)
            review_node = nested.get("reviews")
            if isinstance(review_node, SequenceNode):
                source = _review_sequence(source, review_node, claim)
            else:
                for a, b, replacement in sorted(
                    _generated_edits(source, node, {"reviews": ext["reviews"]}),
                    reverse=True,
                ):
                    source = source[:a] + replacement + source[b:]
        result = text[:start] + source + text[end:]
    return result


def bind_review(text: str, entry: dict[str, Any], claim: dict[str, Any]) -> str:
    """Append review evidence while preserving other source text."""
    from markdown_vault_mcp._okf_author import _header_span
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.scanner import parse_frontmatter

    original = parse_frontmatter(text.removeprefix("\ufeff"))
    old = dict(original.metadata)
    verified = old.get("verified")
    extension = old.get("x-mvmcp")
    if verified is not None and not isinstance(verified, (list, dict)):
        raise InvalidRequestError("Existing verified field must be a list or mapping")
    if extension is not None and not isinstance(extension, dict):
        raise InvalidRequestError(
            "Existing x-mvmcp reviews must be a mapping with a list"
        )
    ext = dict(extension or {})
    reviews = ext.get("reviews")
    if reviews is not None and not isinstance(reviews, list):
        raise InvalidRequestError(
            "Existing x-mvmcp reviews must be a mapping with a list"
        )
    expected = dict(old)
    expected["verified"] = (
        [verified] if isinstance(verified, dict) else list(verified or [])
    ) + [entry]
    ext["reviews"] = [*list(reviews or []), claim]
    expected["x-mvmcp"] = ext
    try:
        span = _header_span(text)
    except InvalidRequestError as exc:
        raise InvalidRequestError(
            "Only closed YAML frontmatter can record a review; convert this note to YAML"
        ) from exc
    result = _bind_yaml_header(text, span, expected, entry, claim)
    parsed = parse_frontmatter(result.removeprefix("\ufeff"))
    if parsed.metadata != expected or parsed.content != original.content:
        raise InvalidRequestError(
            "Cannot safely preserve this note while recording the review"
        )
    return result
