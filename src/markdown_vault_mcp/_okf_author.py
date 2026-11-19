"""Surgical frontmatter edits for deliberate OKF authoring."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import frontmatter
import yaml
from yaml.nodes import MappingNode, SequenceNode

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import OKF_STATUS_VALUES, _parse_stale_after, okf_timestamp
from markdown_vault_mcp.scanner import parse_frontmatter

_FIELDS = frozenset(
    {"type", "title", "description", "tags", "status", "stale_after", "sources"}
)
_ACTOR = re.compile(r"(?:human:|process:)\S+|[^\s/]+/\S+")


def _valid_sources(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("resource"), str)
        and bool(item["resource"].strip())
        for item in value
    )


def _valid_field(key: str, value: Any) -> bool:
    if key in {"type", "title", "description"}:
        return isinstance(value, str) and bool(value.strip())
    if key == "tags":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if key == "sources":
        return _valid_sources(value)
    if key == "status":
        return isinstance(value, str) and value in OKF_STATUS_VALUES
    if key == "stale_after":
        return _parse_stale_after(value) is not None
    return False


def _validate(
    fields: dict[str, Any], producer: str | None, repair: dict[str, Any] | None
) -> None:
    if fields.keys() - _FIELDS:
        raise InvalidRequestError("Only OKF editorial fields can be authored")
    if producer is not None and not _ACTOR.fullmatch(producer):
        raise InvalidRequestError(
            "Producer must be a human:<id>, process:<id>, or <producer>/<version> actor"
        )
    if not fields and producer is None and repair is None:
        raise InvalidRequestError("At least one field or producer is required")
    for key, value in fields.items():
        if not _valid_field(key, value):
            raise InvalidRequestError(f"Invalid value for {key}")


def _header_span(text: str) -> tuple[int, int] | None:
    offset = int(text.startswith("\ufeff"))
    candidate = text[offset:]
    offset += len(candidate) - len(candidate.lstrip())
    candidate = text[offset:]
    handler = frontmatter.detect_format(candidate, frontmatter.handlers)
    if handler is None and re.match(r"^\+{3,}[ \t]*(?:\r?\n|$)", candidate):
        raise InvalidRequestError("Authoring requires YAML frontmatter")
    if handler is None:
        return None
    if not isinstance(handler, frontmatter.default_handlers.YAMLHandler):
        raise InvalidRequestError("Authoring requires YAML frontmatter")
    boundary = handler.FM_BOUNDARY
    if boundary is None:
        raise InvalidRequestError("Cannot locate frontmatter")
    matches = list(boundary.finditer(candidate))
    if len(matches) < 2:
        raise InvalidRequestError("Cannot author unclosed frontmatter")
    return offset + matches[0].end(), offset + matches[1].start()


def _render(value: Any) -> str:
    return yaml.safe_dump(
        [value], default_flow_style=True, allow_unicode=True, width=100000
    ).strip()[1:-1]


def _flow_insertion(
    source: str, node: MappingNode | SequenceNode, additions: list[str]
) -> tuple[int, int, str]:
    position = node.end_mark.index - 1
    tokens = [
        t
        for t in yaml.scan(source)
        if node.start_mark.index <= t.start_mark.index < position
    ]
    trailing_comma = tokens and isinstance(tokens[-1], yaml.FlowEntryToken)
    prefix = ", " if node.value and not trailing_comma else ""
    return position, position, prefix + ", ".join(additions)


def _generated_edits(
    source: str, node: MappingNode, value: dict[str, str]
) -> list[tuple[int, int, str]]:
    nested = {k.value: (k, v) for k, v in node.value}
    edits = []
    missing = []
    for child, child_value in value.items():
        if child in nested:
            key, n = nested[child]
            edits.append(
                (
                    key.start_mark.index,
                    n.end_mark.index,
                    _replacement(source, key, n, child_value),
                )
            )
        else:
            missing.append(f"{child}: {_render(child_value)}")
    if missing and node.flow_style:
        edits.append(_flow_insertion(source, node, missing))
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
    # Block-scalar header comments precede its data, despite sharing a token.
    scalars = []
    for token in yaml.scan(source):
        if isinstance(token, yaml.ScalarToken):
            start_data = token.start_mark.index
            if token.style in {"|", ">"}:
                line_end = source.find("\n", start_data, token.end_mark.index)
                start_data = line_end if line_end >= 0 else token.end_mark.index
            scalars.append((start_data, token.end_mark.index))
    comments = []
    for match in re.finditer(r"(?m)(?<!\S)#[^\r\n]*", source[start:end]):
        position = start + match.start()
        if not any(a <= position < b for a, b in scalars):
            comments.append(match.group() + ("\r\n" if "\r\n" in source else "\n"))
    return "".join(comments)


def _replacement(source: str, key: Any, node: Any, value: Any) -> str:
    if node.end_mark.index < key.end_mark.index:
        raise InvalidRequestError("Cannot safely apply authoring changes")
    old = source[key.start_mark.index : node.end_mark.index]
    trailing = re.search(r"\r?\n[ \t]*$", old)
    indent = trailing.group().lstrip("\r\n") if trailing else ""
    comments = _comments_in_span(source, key.start_mark.index, node.end_mark.index)
    suffix = ("\r\n" if "\r\n" in source else "\n") if trailing else ""
    return f"{key.value}: {_render(value)}" + suffix + comments + indent


def _collect_edits(
    source: str,
    root: MappingNode,
    changes: dict[str, Any],
    replace_generated: bool = False,
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
        if (
            key == "generated"
            and isinstance(node, MappingNode)
            and not replace_generated
        ):
            if node.start_mark.index < key_node.end_mark.index:
                raise InvalidRequestError("Cannot replace an aliased field")
            edits.extend(_generated_edits(source, node, value))
        elif (
            key == "generated"
            and not replace_generated
            and node.tag != "tag:yaml.org,2002:null"
        ):
            raise InvalidRequestError("Existing generated field must be a mapping")
        else:
            edits.append(
                (
                    key_node.start_mark.index,
                    node.end_mark.index,
                    _replacement(source, key_node, node, value),
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
    if not isinstance(root, MappingNode):
        raise InvalidRequestError("Authoring requires mapping frontmatter")
    return root


def _edit_header(
    source: str, changes: dict[str, Any], newline: str, replace_generated: bool = False
) -> str:
    root = _parse_root(source)
    edits, additions = _collect_edits(source, root, changes, replace_generated)
    if additions and root.flow_style:
        edits.append(_flow_insertion(source, root, additions))
        additions = []
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
    return source


def _check_repair_nodes(node: Any, seen: set[int] | None = None) -> None:
    """Refuse duplicate keys or aliases in a repair's source header."""
    seen = set() if seen is None else seen
    if id(node) in seen:
        raise InvalidRequestError("Cannot repair frontmatter containing aliases")
    seen.add(id(node))
    if isinstance(node, MappingNode):
        keys: set[str] = set()
        for key, value in node.value:
            if key.tag != "tag:yaml.org,2002:str" or key.value in keys:
                raise InvalidRequestError("Cannot repair ambiguous frontmatter keys")
            keys.add(key.value)
            _check_repair_nodes(value, seen)
    elif isinstance(node, SequenceNode):
        for value in node.value:
            _check_repair_nodes(value, seen)


def _retire_entries(
    previous: Any, request: dict[str, Any], history: list[Any]
) -> list[Any]:
    if isinstance(previous, dict):
        previous = [previous]
    indices = request["indices"]
    if (
        not isinstance(previous, list)
        or not isinstance(indices, list)
        or not indices
        or any(type(i) is not int or i < 0 or i >= len(previous) for i in indices)
        or len(set(indices)) != len(indices)
    ):
        raise InvalidRequestError("Invalid review indices")
    if any(not isinstance(previous[i], dict) for i in indices):
        raise InvalidRequestError("Selected reviews must be mappings")
    selected = set(indices)
    history.extend(
        {"review": previous[i], "reason": request["reason"]} for i in sorted(selected)
    )
    return [item for i, item in enumerate(previous) if i not in selected]


def _repair_changes(metadata: dict[str, Any], repair: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(repair, dict)
        or not repair
        or repair.keys() - {"retire_reviews", "replace_generated"}
    ):
        raise InvalidRequestError("Invalid repair request")
    extension = metadata.get("x-mvmcp", {})
    if not isinstance(extension, dict):
        raise InvalidRequestError("x-mvmcp must be a mapping")
    extension = dict(extension)
    changes: dict[str, Any] = {"x-mvmcp": extension}
    for action, key, history in (
        ("retire_reviews", "verified", "review_history"),
        ("replace_generated", "generated", "generated_history"),
    ):
        if action not in repair:
            continue
        request = repair[action]
        required = {"reason", "indices" if action == "retire_reviews" else "by"}
        if (
            not isinstance(request, dict)
            or request.keys() != required
            or not isinstance(request["reason"], str)
            or not request["reason"].strip()
        ):
            raise InvalidRequestError(f"Invalid {action} request")
        previous = metadata.get(key)
        entries = extension.get(history, [])
        if not isinstance(entries, list):
            raise InvalidRequestError(f"{history} must be a list")
        entries = list(entries)
        if action == "retire_reviews":
            changes[key] = _retire_entries(previous, request, entries)
        else:
            actor = request["by"]
            if (
                not isinstance(previous, dict)
                or not isinstance(actor, str)
                or not _ACTOR.fullmatch(actor)
            ):
                raise InvalidRequestError(
                    "Replacement requires existing generated mapping and valid actor"
                )
            entries.append({"generated": previous, "reason": request["reason"]})
            changes[key] = {"by": actor, "at": okf_timestamp(datetime.now(UTC))}
        extension[history] = entries
    return changes


def author_text(
    text: str,
    fields: dict[str, Any],
    producer: str | None,
    *,
    repair: dict[str, Any] | None = None,
) -> str:
    """Apply editorial fields and explicit repairs without reserializing the note.

    Args:
        text: Original note text.
        fields: Editorial fields to update.
        producer: Optional producer actor.
        repair: Explicit provenance repair requests.

    Returns:
        The edited note text.
    """
    _validate(fields, producer, repair)
    if repair is not None and producer is not None and "replace_generated" in repair:
        raise InvalidRequestError("Choose producer or replace_generated, not both")
    changes = dict(fields)
    if producer is not None:
        changes["generated"] = {"by": producer, "at": okf_timestamp(datetime.now(UTC))}
    span = _header_span(text)
    header = text[: span[1]] if span else text
    line_end = re.search(r"\r?\n", header[max(0, span[0] - 1) :] if span else header)
    newline = line_end.group() if line_end else "\n"
    try:
        original = parse_frontmatter(text.removeprefix("\ufeff"))
        if repair is not None and span is not None:
            _check_repair_nodes(_parse_root(text[span[0] : span[1]]))
        repair_changes = (
            _repair_changes(original.metadata, repair) if repair is not None else {}
        )
        changes.update(repair_changes)
        replacing = "generated" in repair_changes
        if span is None:
            prefix = "\ufeff" if text.startswith("\ufeff") else ""
            source = "".join(
                f"{key}: {_render(value)}" + newline for key, value in changes.items()
            )
            result = (
                prefix
                + "---"
                + newline
                + source
                + "---"
                + newline
                + text.removeprefix("\ufeff")
            )
        else:
            start, end = span
            source = _edit_header(text[start:end], changes, newline, replacing)
            result = text[:start] + source + text[end:]
        parsed = parse_frontmatter(result.removeprefix("\ufeff"))
        expected = dict(original.metadata)
        expected.update(fields)
        if producer is not None:
            previous = expected.get("generated")
            if previous is not None and not isinstance(previous, dict):
                raise InvalidRequestError("Existing generated field must be a mapping")
            generated = dict(previous or {})
            generated.update(changes["generated"])
            expected["generated"] = generated
        expected.update(repair_changes)
        if parsed.metadata != expected or parsed.content != original.content:
            raise InvalidRequestError("Cannot safely apply authoring changes")
        return result
    except (yaml.YAMLError, TypeError) as exc:
        raise InvalidRequestError("Cannot safely apply authoring changes") from exc
