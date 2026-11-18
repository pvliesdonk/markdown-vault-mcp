"""Deliberate removal of historical reserved-file placeholder fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml
from yaml.nodes import MappingNode, ScalarNode

from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.okf import OKF_LOG_TITLE, OKF_ROOT_INDEX_TITLE

if TYPE_CHECKING:
    from markdown_vault_mcp.vault import Vault

_HEADER = re.compile(
    r"\A(?P<bom>\ufeff?)---\r?\n(?P<header>.*?)(?m:^---[ \t]*\r?$)(?:\n|$)", re.DOTALL
)
_PROTECTED = frozenset({"okf_version", "conflict_with"})


@dataclass(frozen=True)
class ReservedRepair:
    """A candidate path and the fields selected for removal."""

    path: str
    keys: tuple[str, ...]


def _strip(
    text: str, required: list[str], title_field: str, title: str
) -> tuple[str, tuple[str, ...]]:
    match = _HEADER.match(text)
    if match is None:
        return text, ()
    header = match["header"]
    try:
        mapping = yaml.safe_load(header)
        node = yaml.compose(header)
    except (yaml.YAMLError, ValueError):
        return text, ()
    if (
        not isinstance(mapping, dict)
        or not isinstance(node, MappingNode)
        or node.flow_style
    ):
        return text, ()
    edits: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value in seen:
            return text, ()
        seen.add(key.value)
        name = key.value
        if (
            name not in required
            or name in _PROTECTED
            or not isinstance(value, ScalarNode)
        ):
            continue
        expected = title if name == title_field else None
        if mapping.get(name) != expected:
            continue
        start = header.rfind("\n", 0, key.start_mark.index) + 1
        end = header.find("\n", value.end_mark.index)
        end = len(header) if end < 0 else end + 1
        line = header[start:end]
        # Only a standalone scalar line is safe to remove without discarding
        # user comments, aliases, anchors, or adjacent fields.
        if (
            key.start_mark.column != 0
            or value.start_mark.line != key.start_mark.line
            or "#" in line
            or "&" in line
            or "*" in line
        ):
            continue
        edits.append((start, end, name))
    if not edits:
        return text, ()
    changed = header
    for start, end, _ in reversed(edits):
        changed = changed[:start] + changed[end:]
    try:
        remaining = yaml.safe_load(changed) or {}
    except (yaml.YAMLError, ValueError):
        return text, ()
    keys = tuple(item[2] for item in edits)
    if remaining != {k: v for k, v in mapping.items() if k not in keys}:
        return text, ()
    return text[: match.start("header")] + changed + text[match.end("header") :], keys


class ReservedFrontmatterRepair:
    """Inspect and optionally remove matching historical placeholders in a vault."""

    def __init__(self, vault: Vault) -> None:
        """Bind the repair to the vault's settings and access policy.

        Args:
            vault: Vault to inspect and, when requested, modify.
        """
        self._vault = vault

    def run(
        self, *, apply: bool = False, allow_protected: bool = False
    ) -> list[ReservedRepair]:
        """List candidates, optionally removing them through the vault write path.

        Args:
            apply: Explicitly authorize writes; the default only inspects.
            allow_protected: Allow a trusted caller to repair protected paths.

        Returns:
            Each reserved path and the matching keys.

        Raises:
            InvalidRequestError: If OKF scope or inventory cannot be established.
        """
        vault = self._vault
        state = vault._okf.state()
        if state.probe_error or not state.active:
            raise InvalidRequestError("An active, readable OKF vault is required")
        if apply:
            vault._doc_mgr.ensure_writable()
        result: list[ReservedRepair] = []
        cursor = None
        while True:
            page = vault.reader.inspect_bundle_tree(
                BundleTreeRequest(root="", entry_limit=1000, cursor=cursor)
            )
            if page.state != "inspected" or page.enumeration == "unavailable":
                raise InvalidRequestError(
                    "Bundle inventory is incomplete; repair stopped"
                )
            for entry in page.entries:
                if entry.role not in {"navigation", "history"}:
                    continue
                if entry.state != "inspected":
                    raise InvalidRequestError(
                        f"Cannot inspect reserved file {entry.path!r}; repair stopped"
                    )
                candidate = self._one(
                    entry.path, apply=apply, allow_protected=allow_protected
                )
                if candidate:
                    result.append(candidate)
            cursor = page.next_cursor
            if cursor is None:
                return result

    def _one(
        self, path: str, *, apply: bool, allow_protected: bool
    ) -> ReservedRepair | None:
        vault = self._vault
        doc = vault._doc_mgr
        note = doc.read(path)
        if note is None:
            raise InvalidRequestError(f"Reserved file {path!r} disappeared")
        raw = doc._validate_path(path).read_bytes()
        etag = compute_etag(raw)
        if note.etag != etag:
            raise InvalidRequestError(
                f"Reserved file {path!r} changed during inspection"
            )
        folder = path.rpartition("/")[0]
        title = (
            OKF_LOG_TITLE
            if path.rsplit("/", 1)[-1] == "log.md"
            else (folder.rsplit("/", 1)[-1] if folder else OKF_ROOT_INDEX_TITLE)
        )
        text, keys = _strip(
            raw.decode("utf-8"),
            vault._required_frontmatter or [],
            vault._title_field,
            title,
        )
        if not keys:
            return None
        if apply:
            doc.write(
                path,
                text,
                if_match=etag,
                allow_overwrite=True,
                allow_protected=allow_protected,
            )
        return ReservedRepair(path, keys)
