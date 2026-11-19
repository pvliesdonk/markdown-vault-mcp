"""Surgical frontmatter edits for deliberate OKF authoring."""

from __future__ import annotations

import re
from bisect import bisect_left
from datetime import UTC, datetime
from typing import Any

import frontmatter
import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from markdown_vault_mcp._okf_receipts import _review_key
from markdown_vault_mcp._okf_review import _review_nodes
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import (
    OKF_STATUS_VALUES,
    _parse_stale_after,
    okf_timestamp,
    verified_entries,
)
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


def _comments_in_span(
    source: str, start: int, end: int, tokens: list[Any] | None = None
) -> str:
    # Block-scalar header comments precede its data, despite sharing a token.
    scalars = []
    for token in tokens if tokens is not None else yaml.scan(source):
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


def _edit_header(source: str, changes: dict[str, Any], newline: str) -> str:
    root = _parse_root(source)
    edits, additions = _collect_edits(source, root, changes)
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


def _retire_entries(
    previous: Any, request: dict[str, Any], history: list[Any]
) -> list[Any]:
    previous = verified_entries({"verified": previous})
    indices = request["indices"]
    if (
        not isinstance(previous, list)
        or not isinstance(indices, list)
        or not indices
        or any(type(i) is not int or i < 0 or i >= len(previous) for i in indices)
        or len(set(indices)) != len(indices)
    ):
        raise InvalidRequestError("Invalid review indices")
    selected = set(indices)
    identities = [_review_key(item) for item in previous]
    if any(
        identities[i] is not None and identities[i] == identities[j]
        for i in selected
        for j in range(len(previous))
        if j not in selected
    ):
        raise InvalidRequestError("Cannot retire a review with a shared identity")
    history.extend(
        {"review": previous[i], "reason": request["reason"]} for i in sorted(selected)
    )
    return [item for i, item in enumerate(previous) if i not in selected]


def _claim_indices(metadata: dict[str, Any], indices: list[int]) -> list[int]:
    entries = verified_entries(metadata)
    keys = {_review_key(entries[i]) for i in indices} - {None}
    claims = metadata.get("x-mvmcp", {}).get("reviews", [])
    if not isinstance(claims, list):
        return []
    return [i for i, claim in enumerate(claims) if _review_key(claim) in keys]


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
            claims = extension.get("reviews", [])
            matched = _claim_indices(metadata, request["indices"])
            if matched:
                entries.extend(
                    {"binding": claims[i], "reason": request["reason"]} for i in matched
                )
                selected_claims = set(matched)
                extension["reviews"] = [
                    claim for i, claim in enumerate(claims) if i not in selected_claims
                ]
        else:
            actor = request["by"]
            if (
                previous is None
                or not isinstance(actor, str)
                or not _ACTOR.fullmatch(actor)
            ):
                raise InvalidRequestError(
                    "Replacement requires a non-null generated value and valid actor"
                )
            entries.append({"generated": previous, "reason": request["reason"]})
            changes[key] = {"by": actor, "at": okf_timestamp(datetime.now(UTC))}
        extension[history] = entries
    return changes


def _raw_flow(source: str, node: Any, tokens: list[Any], positions: list[int]) -> str:
    """Represent a node using its original scalar spellings."""
    if isinstance(node, ScalarNode):
        if node.style is None and node.tag != "tag:yaml.org,2002:str":
            return source[node.start_mark.index : node.end_mark.index]
        return _render(node.value)
    start = bisect_left(positions, node.start_mark.index)
    end = bisect_left(positions, node.end_mark.index)
    relevant = [
        token
        for token in tokens[start:end]
        if not isinstance(token, (yaml.BlockEndToken, yaml.FlowEntryToken))
    ]
    content_end = max(
        (token.end_mark.index for token in relevant), default=node.start_mark.index
    )
    if _comments_in_span(source, node.start_mark.index, content_end, relevant):
        raise InvalidRequestError("Cannot safely archive evidence containing comments")
    if any(isinstance(token, yaml.AliasToken) for token in relevant):
        raise InvalidRequestError("Cannot archive aliased evidence")
    if isinstance(node, SequenceNode):
        return (
            "["
            + ", ".join(
                _raw_flow(source, item, tokens, positions) for item in node.value
            )
            + "]"
        )
    _review_nodes(node)
    return (
        "{"
        + ", ".join(
            _raw_flow(source, key, tokens, positions)
            + ": "
            + _raw_flow(source, value, tokens, positions)
            for key, value in node.value
        )
        + "}"
    )


def _reject_merge(node: MappingNode) -> None:
    if any(k.tag == "tag:yaml.org,2002:merge" for k, _ in node.value):
        raise InvalidRequestError("Cannot repair fields with a YAML merge key")


def _touched(node: MappingNode, key: str) -> Any:
    _reject_merge(node)
    value = _review_nodes(node).get(key)
    if value is not None:
        key_node = next(k for k, _ in node.value if k.value == key)
        if value.start_mark.index < key_node.end_mark.index:
            raise InvalidRequestError(
                f"Cannot repair aliased {key}; remove the alias first"
            )
    return value


def _line_insertion(source: str, position: int) -> int:
    start = source.rfind("\n", 0, position) + 1
    return start if not source[start:position].strip() else position


def _insert_mapping(
    source: str, node: MappingNode, key: str, raw: str, newline: str
) -> str:
    if node.flow_style:
        start, end, value = _flow_insertion(source, node, [f"{key}: {raw}"])
    else:
        start = end = _line_insertion(source, node.end_mark.index)
        value = " " * node.start_mark.column + f"{key}: {raw}" + newline
    return source[:start] + value + source[end:]


def _append_history(source: str, history: str, items: list[str], newline: str) -> str:
    root = _parse_root(source)
    ext = _touched(root, "x-mvmcp")
    if ext is None:
        return _insert_mapping(
            source,
            root,
            "x-mvmcp",
            "{" + history + ": [" + ", ".join(items) + "]}",
            newline,
        )
    existing = _touched(ext, history)
    if existing is None:
        return _insert_mapping(
            source, ext, history, "[" + ", ".join(items) + "]", newline
        )
    if existing.flow_style:
        start, end, value = _flow_insertion(source, existing, items)
    else:
        start = end = _line_insertion(source, existing.end_mark.index)
        value = "".join(
            " " * existing.start_mark.column + "- " + item + newline for item in items
        )
    return source[:start] + value + source[end:]


def _remove_reviews(
    source: str, node: Any, selected: set[int], tokens: list[Any]
) -> str:
    if isinstance(node, MappingNode) or len(selected) == len(node.value):
        comments = _comments_in_span(
            source, node.start_mark.index, node.end_mark.index, tokens
        )
        newline = "\r\n" if "\r\n" in source else "\n"
        end = (
            node.end_mark.index
            if node.flow_style
            else _line_insertion(source, node.end_mark.index)
        )
        suffix = newline + comments if comments or not node.flow_style else ""
        return source[: node.start_mark.index] + "[]" + suffix + source[end:]
    if node.flow_style:
        kept = [
            source[item.start_mark.index : item.end_mark.index]
            for i, item in enumerate(node.value)
            if i not in selected
        ]
        if _comments_in_span(
            source, node.start_mark.index, node.end_mark.index, tokens
        ):
            raise InvalidRequestError("Cannot safely edit a commented flow review list")
        return (
            source[: node.start_mark.index]
            + "["
            + ", ".join(kept)
            + "]"
            + source[node.end_mark.index :]
        )
    for i in sorted(selected, reverse=True):
        item = node.value[i]
        start = source.rfind("\n", 0, item.start_mark.index) + 1
        end = (
            source.rfind("\n", 0, node.value[i + 1].start_mark.index) + 1
            if i + 1 < len(node.value)
            else _line_insertion(source, node.end_mark.index)
        )
        comments = _comments_in_span(source, start, end, tokens)
        source = source[:start] + comments + source[end:]
    return source


def _selected_nodes(node: Any, indices: list[int]) -> list[Any]:
    members = node.value if isinstance(node, SequenceNode) else [node]
    if any(
        members[i].start_mark.index < node.start_mark.index
        or (i > 0 and members[i].start_mark.index < members[i - 1].end_mark.index)
        for i in indices
    ):
        raise InvalidRequestError("Cannot archive aliased evidence")
    return members


def _repair_header(
    source: str,
    repair: dict[str, Any],
    changes: dict[str, Any],
    newline: str,
    metadata: dict[str, Any],
) -> str:
    root = _parse_root(source)
    ext = _touched(root, "x-mvmcp")
    if isinstance(ext, MappingNode):
        _reject_merge(ext)
    tokens = list(yaml.scan(source))
    positions = [token.start_mark.index for token in tokens]
    retire = repair.get("retire_reviews")
    replace = repair.get("replace_generated")
    if retire is not None:
        node = _touched(root, "verified")
        members = _selected_nodes(node, retire["indices"])
        items = [
            "{review: "
            + _raw_flow(source, members[i], tokens, positions)
            + ", reason: "
            + _render(retire["reason"])
            + "}"
            for i in sorted(retire["indices"])
        ]
        matched = _claim_indices(metadata, retire["indices"])
        if matched:
            claims_node = _touched(ext, "reviews")
            claim_members = _selected_nodes(claims_node, matched)
            items.extend(
                "{binding: "
                + _raw_flow(source, claim_members[i], tokens, positions)
                + ", reason: "
                + _render(retire["reason"])
                + "}"
                for i in matched
            )
        source = _remove_reviews(source, node, set(retire["indices"]), tokens)
        if matched:
            root = _parse_root(source)
            ext = _touched(root, "x-mvmcp")
            claims_node = _touched(ext, "reviews")
            source = _remove_reviews(
                source, claims_node, set(matched), list(yaml.scan(source))
            )
        source = _append_history(source, "review_history", items, newline)
    if replace is not None:
        root = _parse_root(source)
        node = _touched(root, "generated")
        tokens = list(yaml.scan(source))
        positions = [token.start_mark.index for token in tokens]
        item = (
            "{generated: "
            + _raw_flow(source, node, tokens, positions)
            + ", reason: "
            + _render(replace["reason"])
            + "}"
        )
        key = next(key for key, _ in root.value if key.value == "generated")
        start, end = key.start_mark.index, node.end_mark.index
        source = (
            source[:start]
            + _replacement(source, key, node, changes["generated"])
            + source[end:]
        )
        source = _append_history(source, "generated_history", [item], newline)
    return source


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
    changes = dict(fields)
    if producer is not None:
        changes["generated"] = {"by": producer, "at": okf_timestamp(datetime.now(UTC))}
    span = _header_span(text)
    header = text[: span[1]] if span else text
    line_end = re.search(r"\r?\n", header[max(0, span[0] - 1) :] if span else header)
    newline = line_end.group() if line_end else "\n"
    try:
        original = parse_frontmatter(text.removeprefix("\ufeff"))
        repair_changes = (
            _repair_changes(original.metadata, repair) if repair is not None else {}
        )
        if producer is not None and "generated" in repair_changes:
            raise InvalidRequestError("Choose producer or replace_generated, not both")
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
            source = _edit_header(text[start:end], changes, newline)
            if repair is not None:
                source = _repair_header(
                    source, repair, repair_changes, newline, original.metadata
                )
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
