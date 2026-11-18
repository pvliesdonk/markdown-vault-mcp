"""Relocate Markdown body destinations for a subtree export."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from markdown_vault_mcp.okf import convert_wikilinks_to_markdown
from markdown_vault_mcp.okf_export_report import Dependency
from markdown_vault_mcp.scanner import (
    _RE_LINE_QUOTE_PREFIX,
    _RE_REF_DEF_OPEN,
    _RE_REF_DEF_TAIL,
    _blank_spans,
    _closes_fence,
    _definition_destination_end,
    _fence_opener,
    _find_wikilink,
    _is_external_target,
    _iter_definition_matches,
    _paragraph_regions,
    _resolve_link_path,
)
from markdown_vault_mcp.utils.links import (
    _follow_inline_destination,
    _is_escaped,
    compute_new_raw_target,
    decode_markdown_destination,
    find_bracket_span,
    iter_bracket_links,
    split_markdown_fragment,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from markdown_vault_mcp.types import OutlinkInfo

_MARKER = re.compile(r"^( *)(?:[-+*]|[0-9]{1,9}[.)])(?= |$)( *)")
_HTML_OPEN = re.compile(r"^ {0,3}<(pre|script|style|textarea)(?:[\s>]|$)", re.I)
_TICKS = re.compile(r"`+|<!--")


@dataclass
class _BlockState:
    items: list[int] = field(default_factory=list)
    depth: int = 0
    fence: tuple[str, int] | None = None
    html: str | None = None
    blank_before: bool = True
    in_code: bool = False

    def _container(self, line: str) -> tuple[str, int, re.Match[str] | None]:
        quote = _RE_LINE_QUOTE_PREFIX.match(line)
        prefix = quote.group() if quote else ""
        depth = prefix.count(">")
        if depth != self.depth:
            self.items.clear()
            if self.fence and self.depth:
                self.fence = None
            self.depth = depth
        text = line[len(prefix) :].rstrip("\n").expandtabs(4)
        indent = len(text) - len(text.lstrip(" "))
        marker = _MARKER.match(text)
        while self.items and indent < self.items[-1] and (self.blank_before or marker):
            self.items.pop()
        return text, indent, marker

    def _open(self, inner: str) -> bool:
        self.fence = _fence_opener(inner)
        if self.fence:
            return True
        tag = _HTML_OPEN.match(inner)
        if tag:
            self.html = r"</" + tag.group(1) + r"\s*>"
        elif inner.lstrip(" ").startswith("<!--") and "-->" not in inner:
            self.html = r"-->"
        if self.html:
            if re.search(self.html, inner, re.I):
                self.html = None
            return True
        return False

    def _active(self, inner: str) -> bool:
        if self.fence:
            if _closes_fence(inner, self.fence):
                self.fence = None
            return True
        if self.html:
            if re.search(self.html, inner, re.I):
                self.html = None
            return True
        return False

    def protected(self, line: str) -> bool:
        text, indent, marker = self._container(line)
        blank = not text.strip()
        base = self.items[-1] if self.items else 0
        inner = text[base:] if indent >= base else text.lstrip(" ")
        active = self._active(inner)
        if not active and not blank:
            self.in_code = indent - base >= 4 and (self.blank_before or self.in_code)
            active = self.in_code or self._new_line(text, indent, base, marker, inner)
        self.blank_before = blank
        return active

    def _new_line(
        self,
        text: str,
        indent: int,
        base: int,
        marker: re.Match[str] | None,
        inner: str,
    ) -> bool:
        if marker and 0 <= indent - base <= 3:
            spacing = len(marker.group(2))
            column = marker.end() if 1 <= spacing <= 4 else marker.end() - spacing + 1
            self.items.append(column)
            if spacing >= 5:
                self.in_code = True
                return True
            inner = text[column:]
        return self._open(inner)


def _protected_lines(lines: list[str]) -> set[int]:
    state = _BlockState()
    return {index for index, line in enumerate(lines) if state.protected(line)}


def _mask_blocks(body: str) -> str:
    normalized = re.sub(r"\r(?=\n)", " ", body).replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    protected = _protected_lines(lines)
    return "".join(
        re.sub(r"[^\n]", " ", line) if index in protected else line
        for index, line in enumerate(lines)
    )


def _mask_inline(region: str) -> str:
    # Index each backtick run once so unmatched runs stay linear-time.
    matches = list(_TICKS.finditer(region))
    next_run: dict[int, int] = {}
    closes: dict[int, int] = {}
    for match in reversed(matches):
        token = match.group()
        if token.startswith("`"):
            length = len(token)
            if length in next_run:
                closes[match.start()] = next_run[length]
            next_run[length] = match.start()
    spans = []
    pos = 0
    for match in matches:
        if match.start() < pos or _is_escaped(region, match.start()):
            continue
        if match.group() == "<!--":
            end = region.find("-->", match.end())
            if end < 0:
                continue
            pos = end + 3
        else:
            closing = closes.get(match.start())
            if closing is None:
                continue
            pos = closing + len(match.group())
        spans.append((match.start(), pos))
    return _blank_spans(region, spans)


def _regions(masked: str) -> Iterator[tuple[int, str]]:
    offset = 0
    for region in _paragraph_regions(masked):
        start = masked.find(region, offset)
        while start > 0 and masked[start - 1] != "\n":
            start = masked.find(region, start + 1)
        if start < 0:
            raise RuntimeError("Paragraph region missing")
        yield start, region
        offset = start + len(region)


def _definition_spans(
    masked: str,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    spans = []
    definitions = []
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
            definitions.append((start, end))
    return spans, definitions


def _inline_destinations(region: str, offset: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []

    def follow(
        text: str, opener: int, closer: int
    ) -> tuple[tuple[str, int], int] | None:
        result = _follow_inline_destination(text, opener, closer)
        if result is not None:
            (raw, start), _end = result
            spans.append((offset + start, offset + start + len(raw)))
        return result

    for _ in iter_bracket_links(region, follow):
        pass
    return spans


def _masked_body(body: str) -> str:
    masked = _mask_blocks(body)
    chars = list(masked)
    for start, region in _regions(masked):
        chars[start : start + len(region)] = _mask_inline(region)
    return "".join(chars)


def convert_body_wikilinks(body: str, outlinks: list[OutlinkInfo]) -> str:
    """Convert indexed wikilinks outside protected Markdown content.

    Args:
        body: Markdown body without frontmatter.
        outlinks: The graph's last-observed links from the note.

    Returns:
        Body with eligible wikilinks converted.
    """
    masked = _masked_body(body)
    changes = []
    position = 0
    while (found := _find_wikilink(masked, position)) is not None:
        end, _target, _alias, start = found
        converted, _, _ = convert_wikilinks_to_markdown(body[start:end], outlinks)
        changes.append((start, end, converted))
        position = end
    for start, end, converted in reversed(changes):
        body = body[:start] + converted + body[end:]
    return body


def _destination_spans(body: str) -> list[tuple[int, int]]:
    masked = _masked_body(body)
    spans, definitions = _definition_spans(masked)
    masked = _blank_spans(masked, definitions)
    for offset, region in _regions(masked):
        spans.extend(_inline_destinations(region, offset))
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
        self.bundle_members = (
            {p[len(self.scope) + 1 :] for p in self.members} if self.scope else set()
        )

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
            written = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
            path_written = written[: len(written) - len(suffix)] if suffix else written
            pointy = raw.startswith("<") and raw.endswith(">")
            raw_path = "<" + path_written + ">" if pointy else path_written
            replacement = compute_new_raw_target(
                "markdown",
                raw_path,
                None,
                bundle_target,
                source_path=source[len(self.scope) + 1 :],
                old_path=target,
            )
            if pointy:
                replacement = replacement[:-1] + suffix + ">"
            else:
                replacement += suffix
        else:
            dependencies.append(Dependency(source, raw))
            replacement = self._outside(target, source, raw)
        if (
            target not in self.members
            and replacement is not None
            and raw.startswith("<")
            and raw.endswith(">")
        ):
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

    def relocate(
        self, body: str, source: str, archive_path: str
    ) -> tuple[str, list[Dependency]]:
        """Relocate body destinations and report outside dependencies.

        Args:
            body: Markdown body without frontmatter.
            source: Vault-relative source path.
            archive_path: Source path in the exported archive.

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
        return body, [Dependency(archive_path, d.target) for d in dependencies]
