"""Relocate Markdown body destinations for a subtree export."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from markdown_vault_mcp.okf_export_report import Dependency
from markdown_vault_mcp.scanner import (
    _RE_INLINE_CODE,
    _RE_REF_DEF_OPEN,
    _RE_REF_DEF_TAIL,
    _blank_spans,
    _definition_destination_end,
    _is_external_target,
    _iter_definition_matches,
    _paragraph_regions,
    _resolve_link_path,
    _unfenced_lines,
)
from markdown_vault_mcp.utils.links import (
    _is_escaped,
    decode_markdown_destination,
    find_bracket_span,
    iter_inline_links,
    split_markdown_fragment,
)


def _mask_blocks(body: str) -> str:
    # Keep every offset stable while normalizing line endings for the scanner.
    normalized = re.sub(r"\r(?=\n)", " ", body).replace("\r", "\n")
    normalized = re.sub(
        r"<!--.*?(?:-->|\Z)",
        lambda m: re.sub(r"[^\n]", " ", m.group()),
        normalized,
        flags=re.DOTALL,
    )
    lines = normalized.splitlines(keepends=True)
    unfenced = {index for index, _ in _unfenced_lines(lines)}
    indented = False
    blank_before = True
    for index, line in enumerate(lines):
        blank = not line.strip()
        starts_code = line.startswith(("    ", "\t"))
        if not blank:
            indented = starts_code and (indented or blank_before)
        if index not in unfenced or indented:
            lines[index] = re.sub(r"[^\n]", " ", line)
        blank_before = blank
    masked = "".join(lines)
    return _RE_INLINE_CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), masked)


def _destination_spans(body: str) -> list[tuple[int, int]]:
    masked = _mask_blocks(body)
    spans: list[tuple[int, int]] = []
    definition_spans = []
    for label, _tail, start, end in _iter_definition_matches(masked):
        if label.startswith("^"):
            continue
        opener = _RE_REF_DEF_OPEN.match(masked, start)
        if opener is None:
            raise RuntimeError("Reference definition opener missing")
        label_span = find_bracket_span(masked, opener.end() - 1)
        if label_span is None:
            raise RuntimeError("Reference definition label missing")
        match = _RE_REF_DEF_TAIL.match(masked, label_span[1] + 2)
        if match is None:
            raise RuntimeError("Reference definition destination missing")
        target_start = match.start(1)
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
        if self.external_base is not None:
            try:
                parts = urlsplit(self.external_base)
                _ = parts.port  # Raises for an invalid port.
                valid = (
                    parts.scheme in {"http", "https"}
                    and bool(parts.hostname)
                    and not parts.netloc.endswith(":")
                    and "?" not in self.external_base
                    and "#" not in self.external_base
                    and not any(c.isspace() for c in self.external_base)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(
                    "external_base must be an absolute HTTP(S) URL without query or fragment"
                )
        self.bundle_members = {p[len(self.scope) + 1 :] for p in self.members}

    def _replacement(
        self, raw: str, source: str, dependencies: list[Dependency]
    ) -> str | None:
        path, suffix = _path_and_suffix(raw)
        if not path or _is_external_target(path):
            return None
        target, _ = _resolve_link_path(path, source)
        replacement: str | None
        if target in self.members:
            bundle_target = target[len(self.scope) + 1 :]
            apparent, _ = _resolve_link_path(path, source[len(self.scope) + 1 :])
            if not path.startswith("/") and apparent == bundle_target:
                return None
            relocated = (
                "/" + bundle_target
                if path.startswith("/")
                else posixpath.relpath(
                    bundle_target,
                    posixpath.dirname(source[len(self.scope) + 1 :]) or ".",
                )
            )
            replacement = quote(relocated, safe="/") + suffix
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
        apparent, _ = _resolve_link_path(path, bundle_source)
        if apparent in self.bundle_members:
            raise ValueError(
                f"Export collision in {source}: {raw}; supply external_base to resolve outside destinations"
            )
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
