"""Versioned identity of a concept as presented for review."""

from __future__ import annotations

import base64
import datetime as dt
import json
from typing import Any

import frontmatter

from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.scanner import parse_frontmatter


def _scalar(value: Any) -> list[Any] | None:
    """Tag scalar types without conflating dates, strings, and numbers."""
    data: Any
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        tag, data = "bool", value
    elif isinstance(value, int):
        tag, data = "int", str(value)
    elif isinstance(value, float):
        tag, data = "float", value.hex()
    elif isinstance(value, dt.datetime):
        tag, data = "datetime", value.isoformat()
    elif isinstance(value, dt.date):
        tag, data = "date", value.isoformat()
    elif isinstance(value, str):
        tag, data = "str", value
    elif isinstance(value, bytes):
        tag, data = "bytes", base64.b64encode(value).decode("ascii")
    else:
        return None
    return [tag, data]


def _encoded(value: Any) -> Any:
    """Encode YAML values with explicit types, including mapping keys."""
    scalar = _scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, (list, tuple)):
        tag = "tuple" if isinstance(value, tuple) else "list"
        return [tag, [_encoded(item) for item in value]]
    if isinstance(value, (set, frozenset)):
        items = [_encoded(item) for item in value]
        return ["set", sorted(items, key=_json)]
    if isinstance(value, dict):
        pairs = [[_encoded(key), _encoded(item)] for key, item in value.items()]
        return ["map", sorted(pairs, key=lambda pair: _json(pair[0]))]
    raise TypeError(f"Unsupported frontmatter value: {type(value).__name__}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _body(text: str) -> str:
    """Remove the detected frontmatter fence while preserving other text."""
    candidate = text.lstrip()
    handler = frontmatter.detect_format(candidate, frontmatter.handlers)
    if handler is None or handler.FM_BOUNDARY is None:
        return text
    prefix = text[: len(text) - len(candidate)]
    lines = candidate.splitlines(keepends=True)
    boundaries = [
        i
        for i, line in enumerate(lines)
        if handler.FM_BOUNDARY.fullmatch(line.rstrip("\n"))
    ]
    if len(boundaries) < 2:
        return text
    return prefix + "".join(lines[boundaries[1] + 1 :])


def concept_snapshot(text: str) -> dict[str, str]:
    """Return the versioned digest of a raw concept's body and metadata.

    Args:
        text: Complete markdown, including any frontmatter.

    Returns:
        Algorithm identifier and lowercase SHA-256 digest.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    metadata = dict(parse_frontmatter(normalized).metadata)
    metadata.pop("verified", None)
    metadata.pop("x-mvmcp", None)
    body = _body(normalized)
    payload = _json([_encoded(metadata), body]).encode("utf-8")
    return {"algo": "mvmcp-concept-v1", "digest": compute_etag(payload)}
