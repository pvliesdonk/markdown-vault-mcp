"""Surgical frontmatter edits for deliberate OKF authoring."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import yaml
from yaml.nodes import MappingNode

from markdown_vault_mcp.exceptions import InvalidRequestError

_FIELDS = frozenset(
    {"type", "title", "description", "tags", "status", "stale_after", "sources"}
)
_ACTOR = re.compile(r"^(?:human:.+|process:.+|[^/]+/.+)$")
_HEADER = re.compile(r"\A---\r?\n(.*?)^---[ \t]*(?:\r?\n|$)", re.MULTILINE | re.DOTALL)


def _render(value: Any) -> str:
    return (
        yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=100000)
        .removesuffix("\n...\n")
        .rstrip("\n")
    )


def author_text(text: str, fields: dict[str, Any], producer: str | None) -> str:
    """Apply named fields and optional producer without reserializing the note."""
    if fields.keys() - _FIELDS:
        raise InvalidRequestError("Only OKF editorial fields can be authored")
    if producer is not None and (
        not _ACTOR.fullmatch(producer) or producer != producer.strip()
    ):
        raise InvalidRequestError(
            "Producer must be a human:<id>, process:<id>, or <producer>/<version> actor"
        )
    changes = dict(fields)
    if producer is not None:
        changes["generated"] = {
            "by": producer,
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    if not changes:
        return text
    match = _HEADER.match(text)
    if not match:
        if text.startswith("---\n") or text.startswith("---\r\n"):
            raise InvalidRequestError("Cannot author unclosed frontmatter")
        return (
            "---\n"
            + "".join(f"{key}: {_render(value)}\n" for key, value in changes.items())
            + "---\n"
            + text
        )
    source = match.group(1)
    try:
        root = yaml.compose(source)
    except yaml.YAMLError as exc:
        raise InvalidRequestError("Cannot author malformed frontmatter") from exc
    if root is None:
        root = MappingNode(tag="tag:yaml.org,2002:map", value=[])
    if not isinstance(root, MappingNode) or root.flow_style:
        raise InvalidRequestError("Authoring requires block-mapping frontmatter")
    nodes = {}
    for key, value in root.value:
        if key.value in nodes:
            raise InvalidRequestError("Cannot author frontmatter with duplicate keys")
        nodes[key.value] = value
    edits = []
    additions = []
    for key, value in changes.items():
        node = nodes.get(key)
        if node is None:
            additions.append(f"{key}: {_render(value)}\n")
        elif key == "generated" and isinstance(node, MappingNode):
            if node.flow_style:
                existing = yaml.safe_load(
                    source[node.start_mark.index : node.end_mark.index]
                )
                existing.update(value)
                edits.append(
                    (node.start_mark.index, node.end_mark.index, _render(existing))
                )
            else:
                nested = {k.value: v for k, v in node.value}
                missing = []
                for child, child_value in value.items():
                    if child in nested:
                        n = nested[child]
                        edits.append(
                            (n.start_mark.index, n.end_mark.index, _render(child_value))
                        )
                    else:
                        missing.append(f"  {child}: {_render(child_value)}\n")
                if missing:
                    edits.append(
                        (node.end_mark.index, node.end_mark.index, "".join(missing))
                    )
        else:
            # A block value includes the newline before the next key.
            old = source[node.start_mark.index : node.end_mark.index]
            suffix = "\n" if old.endswith("\n") else ""
            edits.append(
                (node.start_mark.index, node.end_mark.index, _render(value) + suffix)
            )
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    if additions:
        if source and not source.endswith("\n"):
            source += "\n"
        source += "".join(additions)
    return text[: match.start(1)] + source + text[match.end(1) :]
