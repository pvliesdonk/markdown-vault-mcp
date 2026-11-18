"""Surgical frontmatter edits for deliberate OKF authoring."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import yaml
from yaml.nodes import MappingNode

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import okf_timestamp

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


def _generated_edits(
    source: str, node: MappingNode, value: dict[str, str]
) -> list[tuple[int, int, str]]:
    nested = {k.value: (k, v) for k, v in node.value}
    edits = []
    missing = []
    for child, child_value in value.items():
        if child in nested:
            key, n = nested[child]
            _check_editable(source, key, n)
            edits.append(
                (
                    n.start_mark.index,
                    n.end_mark.index,
                    _replacement(source, n, child_value),
                )
            )
        else:
            missing.append(f"{child}: {_render(child_value)}")
    if missing and node.flow_style:
        position = node.end_mark.index - 1
        tokens = [
            t
            for t in yaml.scan(source)
            if node.start_mark.index <= t.start_mark.index < position
        ]
        trailing_comma = tokens and isinstance(tokens[-1], yaml.FlowEntryToken)
        prefix = ", " if node.value and not trailing_comma else ""
        edits.append((position, position, prefix + ", ".join(missing)))
    elif missing:
        newline = "\r\n" if "\r\n" in source else "\n"
        indent = " " * node.start_mark.column
        edits.append(
            (
                node.end_mark.index,
                node.end_mark.index,
                "".join(indent + item + newline for item in missing),
            )
        )
    return edits


def _comments_in_span(source: str, start: int, end: int) -> str:
    # Scalar tokens include quoted and block scalars, where # is data.
    scalars = [
        (t.start_mark.index, t.end_mark.index)
        for t in yaml.scan(source)
        if isinstance(t, yaml.ScalarToken)
    ]
    comments = []
    for match in re.finditer(r"(?m)(?<!\S)#[^\r\n]*", source[start:end]):
        position = start + match.start()
        if not any(a <= position < b for a, b in scalars):
            comments.append(match.group() + ("\r\n" if "\r\n" in source else "\n"))
    return "".join(comments)


def _replacement(source: str, node: Any, value: Any) -> str:
    old = source[node.start_mark.index : node.end_mark.index]
    trailing = re.search(r"\r?\n[ \t]*$", old)
    indent = trailing.group().lstrip("\r\n") if trailing else ""
    comments = _comments_in_span(source, node.start_mark.index, node.end_mark.index)
    suffix = ("\r\n" if "\r\n" in source else "\n") if trailing else ""
    prefix = " " if node.start_mark.index == node.end_mark.index else ""
    return prefix + _render(value) + suffix + comments + indent


def _check_editable(source: str, key: Any, node: Any) -> None:
    if node.start_mark.index < key.end_mark.index:
        raise InvalidRequestError("Cannot replace an aliased field")
    if any(
        isinstance(t, yaml.AnchorToken)
        and node.start_mark.index <= t.start_mark.index < node.end_mark.index
        for t in yaml.scan(source)
    ):
        raise InvalidRequestError("Cannot replace an anchored field")


def _collect_edits(
    source: str, root: MappingNode, changes: dict[str, Any]
) -> tuple[list[tuple[int, int, str]], list[str]]:
    nodes = {}
    for key, value in root.value:
        if key.value in nodes:
            raise InvalidRequestError("Cannot author frontmatter with duplicate keys")
        nodes[key.value] = (key, value)
    edits = []
    additions = []
    for key, value in changes.items():
        entry = nodes.get(key)
        if entry is None:
            additions.append(f"{key}: {_render(value)}")
            continue
        key_node, node = entry
        if key == "generated" and isinstance(node, MappingNode):
            if node.start_mark.index < key_node.end_mark.index:
                raise InvalidRequestError("Cannot replace an aliased field")
            edits.extend(_generated_edits(source, node, value))
        elif key == "generated" and node.tag != "tag:yaml.org,2002:null":
            raise InvalidRequestError("Existing generated field must be a mapping")
        else:
            _check_editable(source, key_node, node)
            edits.append(
                (
                    node.start_mark.index,
                    node.end_mark.index,
                    _replacement(source, node, value),
                )
            )
    return edits, additions


def _parse_root(source: str) -> MappingNode:
    try:
        root = yaml.compose(source)
    except yaml.YAMLError as exc:
        raise InvalidRequestError("Cannot author malformed frontmatter") from exc
    if root is None:
        return MappingNode(tag="tag:yaml.org,2002:map", value=[])
    if not isinstance(root, MappingNode) or root.flow_style:
        raise InvalidRequestError("Authoring requires block-mapping frontmatter")
    return root


def _verify_header(source: str, expected: dict[str, Any]) -> None:
    try:
        parsed = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise InvalidRequestError("Cannot safely apply authoring changes") from exc
    if parsed != expected:
        raise InvalidRequestError("Cannot safely apply authoring changes")


def _edit_header(
    source: str, fields: dict[str, Any], changes: dict[str, Any], newline: str
) -> str:
    root = _parse_root(source)
    original = yaml.safe_load(source) or {}
    edits, additions = _collect_edits(source, root, changes)
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    if additions:
        end = next(
            (
                token.start_mark.index
                for token in yaml.scan(source)
                if isinstance(token, yaml.DocumentEndToken)
            ),
            len(source),
        )
        insertion = "".join(item + newline for item in additions)
        source = source[:end] + insertion + source[end:]
    expected = dict(original)
    expected.update(fields)
    if "generated" in changes:
        generated = dict(expected.get("generated") or {})
        generated.update(changes["generated"])
        expected["generated"] = generated
    _verify_header(source, expected)
    return source


def author_text(text: str, fields: dict[str, Any], producer: str | None) -> str:
    """Apply named fields and optional producer without reserializing the note."""
    if fields.keys() - _FIELDS:
        raise InvalidRequestError("Only OKF editorial fields can be authored")
    if producer is not None and (
        not _ACTOR.fullmatch(producer)
        or producer != producer.strip()
        or "\n" in producer
        or "\r" in producer
    ):
        raise InvalidRequestError(
            "Producer must be a human:<id>, process:<id>, or <producer>/<version> actor"
        )
    changes = dict(fields)
    if producer is not None:
        changes["generated"] = {
            "by": producer,
            "at": okf_timestamp(datetime.now(UTC)),
        }
    if not changes:
        return text
    text = text.removeprefix("\ufeff")
    newline = "\r\n" if "\r\n" in text else "\n"
    match = _HEADER.match(text)
    if not match:
        if text.startswith("---\n") or text.startswith("---\r\n"):
            raise InvalidRequestError("Cannot author unclosed frontmatter")
        source = "".join(
            f"{key}: {_render(value)}" + newline for key, value in changes.items()
        )
        _verify_header(source, changes)
        return "---" + newline + source + "---" + newline + text
    source = _edit_header(match.group(1), fields, changes, newline)
    return text[: match.start(1)] + source + text[match.end(1) :]
