"""Relocate Markdown body destinations for a subtree export."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from markdown_vault_mcp.okf_export_report import Dependency
from markdown_vault_mcp.scanner import (
    _RE_FENCED_CODE,
    _RE_INLINE_CODE,
    _blank_spans,
    _definition_destination_end,
    _is_external_target,
    _iter_definition_matches,
    _paragraph_regions,
)
from markdown_vault_mcp.utils.links import (
    _is_escaped,
    decode_markdown_destination,
    iter_inline_links,
    split_markdown_fragment,
)


def _destination_spans(body: str) -> list[tuple[int, int]]:
    masked = body
    for pattern in (_RE_FENCED_CODE, _RE_INLINE_CODE):
        masked = pattern.sub(lambda m: re.sub(r"[^\r\n]", " ", m.group()), masked)
    spans: list[tuple[int, int]] = []
    definition_spans = []
    for label, tail, start, end in _iter_definition_matches(masked):
        if label.startswith("^"):
            continue
        target_start = masked.find(tail, start)
        target_end = _definition_destination_end(masked, target_start)
        if target_end is not None:
            spans.append((target_start, target_end))
            definition_spans.append((start, end))
    masked = _blank_spans(masked, definition_spans)
    # The shared scanner omits images; treating their marker as whitespace
    # lets the same destination grammar handle both links and images.
    masked = re.sub(r"(?<!!)!(?=\[)", " ", masked)
    offset = 0
    for region in _paragraph_regions(masked):
        offset = masked.find(region, offset)
        for link in iter_inline_links(region):
            spans.append(
                (
                    offset + link.target_start,
                    offset + link.target_start + len(link.raw_target),
                )
            )
        offset += len(region)
    return sorted(spans)


def _path_and_suffix(raw: str) -> tuple[str, str]:
    written = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
    before_fragment, _ = split_markdown_fragment(written)
    suffix = written[len(before_fragment) :]
    for index, char in enumerate(before_fragment):
        if char == "?" and not _is_escaped(before_fragment, index):
            suffix = before_fragment[index:] + suffix
            before_fragment = before_fragment[:index]
            break
    return decode_markdown_destination(before_fragment), suffix


@dataclass
class ExportPaths:
    """Selected vault paths and their bundle-root mapping."""

    scope: str
    members: set[str]
    external_base: str | None
    bundle_members: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.bundle_members = {p[len(self.scope) + 1 :] for p in self.members}

    def _replacement(
        self, raw: str, source: str, dependencies: list[Dependency]
    ) -> str | None:
        path, suffix = _path_and_suffix(raw)
        if not path or _is_external_target(path):
            return None
        target = posixpath.normpath(
            path.lstrip("/")
            if path.startswith("/")
            else posixpath.join(posixpath.dirname(source), path)
        )
        replacement: str | None
        if target in self.members:
            if not path.startswith("/"):
                return None
            replacement = "/" + quote(target[len(self.scope) + 1 :], safe="/") + suffix
        else:
            dependencies.append(Dependency(source, raw))
            replacement = self._outside(target, source, raw)
        if replacement is not None and raw.startswith("<") and raw.endswith(">"):
            return "<" + replacement + ">"
        return replacement

    def _outside(self, target: str, source: str, raw: str) -> str | None:
        path, suffix = _path_and_suffix(raw)
        if self.external_base:
            return (
                self.external_base.rstrip("/")
                + "/"
                + quote(target.lstrip("/"), safe="/")
                + suffix
            )
        bundle_source = source[len(self.scope) + 1 :]
        apparent = posixpath.normpath(
            path.lstrip("/")
            if path.startswith("/")
            else posixpath.join(posixpath.dirname(bundle_source), path)
        )
        if apparent in self.bundle_members:
            raise ValueError(f"Export collision in {source}: {raw}")
        return None

    def relocate(self, body: str, source: str) -> tuple[str, list[Dependency]]:
        """Relocate body destinations and report outside dependencies.

        Args:
            body: Markdown body without frontmatter.
            source: Vault-relative source path.

        Returns:
            Rewritten body and outside dependencies.

        Raises:
            ValueError: An outside destination collides with a bundle member.
        """
        if not self.scope:
            return body, []
        changes = []
        dependencies: list[Dependency] = []
        for start, end in _destination_spans(body):
            replacement = self._replacement(body[start:end], source, dependencies)
            if replacement is not None:
                changes.append((start, end, replacement))
        for start, end, replacement in reversed(changes):
            body = body[:start] + replacement + body[end:]
        return body, dependencies


def relocate_body(
    body: str, source: str, scope: str, members: set[str], external_base: str | None
) -> tuple[str, list[Dependency]]:
    """Relocate a body using a selected bundle root and its members.

    Args:
        body: Markdown body without frontmatter.
        source: Vault-relative source path.
        scope: Vault-relative selected root.
        members: Selected vault-relative file paths.
        external_base: Optional URL prefix for outside references.

    Returns:
        Rewritten body and outside dependencies.

    Raises:
        ValueError: An outside destination collides with a bundle member.
    """
    return ExportPaths(scope, members, external_base).relocate(body, source)
