"""Deliberate removal of historical reserved-file placeholder fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml
from yaml.nodes import MappingNode, ScalarNode

from markdown_vault_mcp._okf_author import _header_span
from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
from markdown_vault_mcp.exceptions import InvalidRequestError, MarkdownMCPError
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.okf import OKF_LOG_TITLE, OKF_ROOT_INDEX_TITLE

if TYPE_CHECKING:
    from markdown_vault_mcp.bundle_inventory import BundleEntry
    from markdown_vault_mcp.vault import Vault

_PROTECTED = frozenset({"okf_version", "conflict_with"})


@dataclass(frozen=True)
class ReservedRepair:
    """A candidate path and the fields selected for removal."""

    path: str
    keys: tuple[str, ...]
    skip_reason: str | None = None


@dataclass(frozen=True)
class _PendingRepair:
    result: ReservedRepair
    text: str
    etag: str


def _strip(
    text: str, required: list[str], title_field: str, title: str
) -> tuple[str, tuple[str, ...]]:
    span = _header_span(text)
    if span is None:
        return text, ()
    start, end = span
    header = text[start:end]
    try:
        mapping = yaml.safe_load(header)
        node = yaml.compose(header)
    except (yaml.YAMLError, ValueError):
        raise InvalidRequestError("invalid YAML frontmatter") from None
    if not isinstance(mapping, dict) or not isinstance(node, MappingNode):
        return text, ()
    if node.flow_style:
        raise InvalidRequestError("flow-style frontmatter needs manual inspection")
    edits = _candidate_edits(header, node, mapping, required, (title_field, title))
    changed = header
    for edit_start, edit_end, _ in reversed(edits):
        changed = changed[:edit_start] + changed[edit_end:]
    try:
        remaining = yaml.safe_load(changed) or {}
    except (yaml.YAMLError, ValueError):
        return text, ()
    keys = tuple(item[2] for item in edits)
    if remaining != {k: v for k, v in mapping.items() if k not in keys}:
        return text, ()
    if keys and not changed.strip():
        # The boundary helper skips only a BOM and whitespace before the fence.
        prefix = text[: text.index("-")]
        closing = text.find("\n", end)
        output = prefix + (text[closing + 1 :] if closing >= 0 else "")
    else:
        output = text[:start] + changed + text[end:]
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
        if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str":
            raise InvalidRequestError("ambiguous frontmatter needs manual inspection")
        name = key.value
        if name in required and name not in _PROTECTED:
            if name in seen:
                raise InvalidRequestError(
                    "ambiguous frontmatter needs manual inspection"
                )
            seen.add(name)
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
            Each reserved path and matching keys, or a reason it was skipped.

        Raises:
            InvalidRequestError: If OKF scope or inventory cannot be established.
        """
        vault = self._vault
        state = vault._okf.state()
        if state.probe_error or not state.active:
            raise InvalidRequestError("An active, readable OKF vault is required")
        if apply:
            vault._doc_mgr.ensure_writable()
        pending = self._discover()
        if apply:
            return [self._apply(item, allow_protected) for item in pending]
        return [
            item.result if isinstance(item, _PendingRepair) else item
            for item in pending
        ]

    def _discover(self) -> list[_PendingRepair | ReservedRepair]:
        result: list[_PendingRepair | ReservedRepair] = []
        cursor = None
        while True:
            page = self._vault.reader.inspect_bundle_tree(
                BundleTreeRequest(root="", entry_limit=1000, cursor=cursor)
            )
            if page.state != "inspected":
                raise InvalidRequestError(
                    "Bundle inventory is incomplete; repair stopped"
                )
            for entry in page.entries:
                candidate: _PendingRepair | ReservedRepair | None
                try:
                    candidate = self._inspect_entry(entry)
                except (MarkdownMCPError, OSError, UnicodeError, RecursionError) as exc:
                    candidate = ReservedRepair(entry.path, (), str(exc))
                if candidate:
                    result.append(candidate)
            if page.enumeration == "unavailable":
                raise InvalidRequestError(
                    "Bundle inventory is incomplete; repair stopped"
                )
            cursor = page.next_cursor
            if cursor is None:
                return result

    def _inspect_entry(self, entry: BundleEntry) -> _PendingRepair | None:
        name = entry.path.rsplit("/", 1)[-1]
        if name not in {"index.md", "log.md"}:
            return None
        if entry.role not in {"navigation", "history"} or (
            entry.state != "inspected"
            and entry.reason not in {"note_limit", "byte_budget"}
        ):
            raise InvalidRequestError(
                f"Cannot inspect reserved file {entry.path!r}: {entry.reason}"
            )
        return self._one(entry.path)

    def _one(self, path: str) -> _PendingRepair | None:
        vault = self._vault
        original = vault._doc_mgr._validate_path(path).read_bytes().decode("utf-8")
        etag = compute_etag(original.encode("utf-8"))
        folder = path.rpartition("/")[0]
        title = (
            OKF_LOG_TITLE
            if path.rsplit("/", 1)[-1] == "log.md"
            else (folder.rsplit("/", 1)[-1] if folder else OKF_ROOT_INDEX_TITLE)
        )
        try:
            text, keys = _strip(
                original,
                vault._required_frontmatter or [],
                vault._title_field,
                title,
            )
        except InvalidRequestError as exc:
            raise InvalidRequestError(f"{path!r}: {exc}") from exc
        if not keys:
            return None
        return _PendingRepair(ReservedRepair(path, keys), text, etag)

    def _apply(
        self, item: _PendingRepair | ReservedRepair, allow_protected: bool
    ) -> ReservedRepair:
        if isinstance(item, ReservedRepair):
            return item
        doc = self._vault._doc_mgr
        try:
            with doc.exact_byte_writes():
                doc.write(
                    item.result.path,
                    item.text,
                    if_match=item.etag,
                    allow_protected=allow_protected,
                    allow_designated=True,
                )
        except (MarkdownMCPError, OSError) as exc:
            return ReservedRepair(item.result.path, (), str(exc))
        return item.result
