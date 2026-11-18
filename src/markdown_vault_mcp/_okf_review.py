"""Versioned identity of a concept as presented for review."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import frontmatter
import yaml
from frontmatter.default_handlers import JSONHandler, YAMLHandler
from yaml.constructor import SafeConstructor
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

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
    if isinstance(node, MappingNode):
        SafeConstructor().flatten_mapping(node)
        return [node.tag, _yaml_pairs(node, root=root)]
    raise TypeError(f"Unsupported YAML node: {type(node).__name__}")


def _yaml_pairs(node: MappingNode, *, root: bool) -> list[Any]:
    # Keys that the reader considers equal must retain their source order,
    # because its YAML constructor uses the last value for duplicate keys.
    constructor = SafeConstructor()
    groups: dict[Any, list[Any]] = {}
    for key, value in node.value:
        if root and isinstance(key, ScalarNode) and key.value in _EXCLUDED:
            continue
        identity = constructor.construct_object(key, deep=True)
        pair = [_yaml_node(key), _yaml_node(value)]
        groups.setdefault(identity, []).append(pair)
    ordered = sorted(groups.values(), key=lambda group: min(_json(p[0]) for p in group))
    return [pair for group in ordered for pair in group]


class _Number(str):
    """A JSON number retained as its source lexeme."""


class _Object(list[Any]):
    """JSON object pairs, including duplicates in source order."""


def _json_value(value: Any, *, root: bool = False) -> Any:
    if isinstance(value, _Object):
        pairs = [
            [["str", key], _json_value(item)]
            for key, item in value
            if not (root and key in _EXCLUDED)
        ]
        return ["object", sorted(pairs, key=lambda pair: _json(pair[0]))]
    if isinstance(value, list):
        return ["array", [_json_value(item) for item in value]]
    if isinstance(value, _Number):
        return ["number", str(value)]
    return [type(value).__name__, value]


def _other_value(value: Any, *, root: bool = False) -> Any:
    """Encode parsed values from an optional registered handler, such as TOML."""
    if isinstance(value, dict):
        pairs = [
            [_other_value(key), _other_value(item)]
            for key, item in value.items()
            if not (root and key in _EXCLUDED)
        ]
        return ["map", sorted(pairs, key=lambda pair: _json(pair[0]))]
    if isinstance(value, list):
        return ["list", [_other_value(item) for item in value]]
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, float):
        return ["float", value.hex()]
    return [type(value).__name__, value]


def _metadata(handler: Any, source: str) -> Any:
    if isinstance(handler, YAMLHandler):
        node = yaml.compose(source, Loader=yaml.SafeLoader)
        return _yaml_node(node, root=True) if node is not None else ["map", []]
    if isinstance(handler, JSONHandler):
        value = json.loads(
            source,
            object_pairs_hook=_Object,
            parse_int=_Number,
            parse_float=_Number,
            parse_constant=_Number,
        )
        return _json_value(value, root=True)
    return _other_value(handler.load(source) or {}, root=True)


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
            body = view[: len(view) - len(view.lstrip())] + view[end:]
    payload = _json([metadata, body]).encode("utf-8")
    return {"algo": "mvmcp-concept-v1", "digest": compute_etag(payload)}
