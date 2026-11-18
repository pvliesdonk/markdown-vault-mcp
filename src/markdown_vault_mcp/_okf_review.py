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
