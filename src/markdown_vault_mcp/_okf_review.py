"""Versioned identity of a concept as presented for review."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from typing import Any

from markdown_vault_mcp.scanner import parse_frontmatter, strip_frontmatter_block


def _encoded(value: Any) -> Any:
    """Encode YAML values with explicit types, including mapping keys."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, dt.datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, dt.date):
        return ["date", value.isoformat()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, list):
        return ["list", [_encoded(item) for item in value]]
    if isinstance(value, (set, frozenset)):
        items = [_encoded(item) for item in value]
        return ["set", sorted(items, key=_json)]
    if isinstance(value, dict):
        pairs = [[_encoded(key), _encoded(item)] for key, item in value.items()]
        return ["map", sorted(pairs, key=lambda pair: _json(pair[0]))]
    raise TypeError(f"Unsupported frontmatter value: {type(value).__name__}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


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
    body = strip_frontmatter_block(normalized)
    payload = _json([_encoded(metadata), body]).encode("utf-8")
    return {"algo": "mvmcp-concept-v1", "digest": hashlib.sha256(payload).hexdigest()}
