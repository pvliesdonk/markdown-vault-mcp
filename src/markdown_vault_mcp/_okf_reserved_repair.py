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
    from markdown_vault_mcp.bundle_inventory import BundleEntry
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
    if not isinstance(mapping, dict) or not isinstance(node, MappingNode):
        return text, ()
    if node.flow_style:
        raise InvalidRequestError("flow-style frontmatter needs manual inspection")
    edits = _candidate_edits(header, node, mapping, required, (title_field, title))
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
    output = (
        match["bom"] + text[match.end() :]
        if keys and not changed.strip()
        else text[: match.start("header")] + changed + text[match.end("header") :]
    )
    return output, keys


def _candidate_edits(
    header: str,
    node: MappingNode,
    mapping: dict[object, object],
    required: list[str],
    title_spec: tuple[str, str],
) -> list[tuple[int, int, str]]:
    title_field, title = title_spec
    edits: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    anchors = [
        token.start_mark.index
        for token in yaml.scan(header)
        if isinstance(token, (yaml.AnchorToken, yaml.AliasToken))
    ]
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value in seen:
            raise InvalidRequestError("ambiguous frontmatter needs manual inspection")
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
        # Only a standalone scalar line is safe to remove without discarding
        # user comments, aliases, anchors, or adjacent fields.
        if (
            key.start_mark.column != 0
            or value.start_mark.line != key.start_mark.line
            or header[value.end_mark.index : end].strip()
            or any(start <= position < end for position in anchors)
        ):
            raise InvalidRequestError(
                "commented or anchored field needs manual inspection"
            )
        edits.append((start, end, name))
    return edits


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
            if page.state != "inspected":
                raise InvalidRequestError(
                    "Bundle inventory is incomplete; repair stopped"
                )
            for entry in page.entries:
                candidate = self._inspect_entry(
                    entry, apply=apply, allow_protected=allow_protected
                )
                if candidate:
                    result.append(candidate)
            if page.enumeration == "unavailable":
                raise InvalidRequestError(
                    "Bundle inventory is incomplete; repair stopped"
                )
            cursor = page.next_cursor
            if cursor is None:
                return result

    def _inspect_entry(
        self, entry: BundleEntry, *, apply: bool, allow_protected: bool
    ) -> ReservedRepair | None:
        name = entry.path.rsplit("/", 1)[-1]
        if entry.role == "directory" and entry.state == "unavailable":
            raise InvalidRequestError(
                f"Cannot inspect directory {entry.path!r}: {entry.reason}"
            )
        if name in {"index.md", "log.md"} and entry.role not in {
            "navigation",
            "history",
        }:
            raise InvalidRequestError(
                f"Cannot inspect reserved file {entry.path!r}: {entry.reason}"
            )
        if entry.role not in {"navigation", "history"}:
            return None
        if entry.state != "inspected" and entry.reason not in {
            "note_limit",
            "byte_budget",
        }:
            raise InvalidRequestError(
                f"Cannot inspect reserved file {entry.path!r}: {entry.reason}"
            )
        return self._one(entry.path, apply=apply, allow_protected=allow_protected)

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
        try:
            text, keys = _strip(
                raw.decode("utf-8"),
                vault._required_frontmatter or [],
                vault._title_field,
                title,
            )
        except InvalidRequestError as exc:
            raise InvalidRequestError(f"{path!r}: {exc}") from exc
        if not keys:
            return None
        if apply:
            with doc.exact_byte_writes():
                doc.write(
                    path,
                    text,
                    if_match=etag,
                    allow_overwrite=True,
                    allow_protected=allow_protected,
                )
        return ReservedRepair(path, keys)
