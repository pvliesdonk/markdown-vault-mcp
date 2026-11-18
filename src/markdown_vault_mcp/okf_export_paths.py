"""Relocate Markdown body destinations for a subtree export."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from markdown_vault_mcp.fts_index import _pick_wikilink_candidate
from markdown_vault_mcp.okf import convert_wikilinks_to_markdown
from markdown_vault_mcp.okf_export_report import Dependency
from markdown_vault_mcp.scanner import (
    _RE_LINE_QUOTE_PREFIX,
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
from markdown_vault_mcp.utils.content_kind import (
    effective_attachment_extensions,
    names_attachment,
)
from markdown_vault_mcp.utils.links import (
    _follow_inline_destination,
    _is_escaped,
    compute_new_raw_target,
    decode_markdown_destination,
    is_pointy_destination,
    iter_bracket_links,
    split_markdown_fragment,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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
        leaving_fence = bool(self.fence and text.strip())
        while (
            self.items
            and indent < self.items[-1]
            and (leaving_fence or (not self.fence and (self.blank_before or marker)))
        ):
            self.items.pop()
            self.fence = None
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
    lines = re.split(r"(?<=\n)", normalized)
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
        yield start, region
        offset = start + len(region)


def _definition_spans(
    masked: str,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    spans = []
    definitions = []
    unquoted = re.sub(
        re.compile(_RE_LINE_QUOTE_PREFIX.pattern, re.MULTILINE),
        lambda match: " " * len(match.group()),
        masked,
    )
    for label, tail, start, end in _iter_definition_matches(unquoted):
        if label.startswith("^"):
            continue
        # The yielded tail runs greedily to the end of the destination's line.
        line_end = masked.find("\n", end)
        target_start = (len(masked) if line_end < 0 else line_end) - len(tail)
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


def _destination_spans(masked: str) -> list[tuple[int, int]]:
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


def _safe_text(text: str) -> str:
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _wikilink_spans(masked: str) -> Iterator[tuple[int, int]]:
    position = 0
    while (found := _find_wikilink(masked, position)) is not None:
        position, target, _alias, start = found
        left = len(target) - len(target.lstrip())
        path = target.strip().removesuffix("\\").split("#", 1)[0]
        if path and not _is_external_target(path):
            yield start + 2 + left, start + 2 + left + len(path)


@dataclass
class ExportPaths:
    """Selected vault paths and their bundle-root mapping."""

    scope: str
    members: set[str]
    external_base: str | None
    inventory: Callable[[], set[str]] | None = None
    extensions: frozenset[str] = field(
        default_factory=lambda: effective_attachment_extensions(None)
    )
    _candidates: dict[str, set[str]] | None = field(default=None, init=False)
    _archive_members: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self._archive_members = {path[len(self.scope) + 1 :] for path in self.members}
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

    def _replacement(
        self, raw: str, source: str, dependencies: list[Dependency]
    ) -> str | None:
        path, suffix = _path_and_suffix(raw)
        if not path or _is_external_target(path):
            return None
        target, _ = _resolve_link_path(path, source)
        archive_source = source[len(self.scope) + 1 :]
        if target not in self.members:
            dependencies.append(Dependency(archive_source, _safe_text(raw)))
            return self._outside(target, source, raw)
        bundle_target = target[len(self.scope) + 1 :]
        apparent, _ = _resolve_link_path(path, archive_source)
        if not path.startswith("/") and apparent == bundle_target:
            return None
        pointy = is_pointy_destination(raw)
        head = raw[: len(raw) - len(suffix) - int(pointy)]
        replacement = compute_new_raw_target(
            "markdown",
            head + ">" if pointy else head,
            None,
            bundle_target,
            source_path=archive_source,
            old_path=target,
        )
        return replacement[:-1] + suffix + ">" if pointy else replacement + suffix

    def _outside(self, target: str, source: str, raw: str) -> str | None:
        path, suffix = _path_and_suffix(raw)
        if self.external_base:
            url = (
                self.external_base.rstrip("/")
                + "/"
                + quote(target.lstrip("/"), safe="/", errors="surrogateescape")
                + suffix
            )
            return f"<{url}>" if is_pointy_destination(raw) else url
        apparent, _ = _resolve_link_path(path, source[len(self.scope) + 1 :])
        if f"{self.scope}/{apparent}" in self.members:
            raise ValueError(
                f"Export collision in {_safe_text(source)}: {_safe_text(raw)}; supply external_base to resolve outside destinations"
            )
        return None

    def _wiki_candidates(self, raw: str, source: str) -> set[str]:
        if self._candidates is None:
            try:
                inventory = self.inventory() if self.inventory else self.members
                self._candidates = {}
                for path in inventory:
                    self._candidates.setdefault(path.rpartition("/")[2], set()).add(
                        path
                    )
            except ValueError as exc:
                raise ValueError(
                    f"Cannot resolve [[{_safe_text(raw)}]] in {_safe_text(source)}: "
                    f"vault inventory unavailable: {exc}; use an explicit path or fix permissions"
                ) from exc
        name = (
            raw
            if names_attachment(raw, self.extensions) or raw.lower().endswith(".md")
            else raw + ".md"
        )
        return self._candidates.get(name.rpartition("/")[2], set())

    @staticmethod
    def _wiki_match(name: str, source: str, candidates: set[str]) -> str | None:
        if name in candidates:
            return name
        matches = [path for path in candidates if path.endswith("/" + name)]
        return _pick_wikilink_candidate(matches, source) if matches else None

    def _wiki_outside(
        self,
        target: str,
        raw: str,
        source: str,
        dependencies: list[Dependency],
        collision: bool,
    ) -> str | None:
        dependencies.append(
            Dependency(source[len(self.scope) + 1 :], _safe_text(target))
        )
        if self.external_base:
            return (
                self.external_base.rstrip("/")
                + "/"
                + quote(target.lstrip("/"), safe="/", errors="surrogateescape")
            )
        if collision:
            raise ValueError(
                f"Export collision in {_safe_text(source)}: {_safe_text(raw)}; "
                "supply external_base to resolve outside destinations"
            )
        return None

    def _wiki_replacement(
        self, raw: str, source: str, dependencies: list[Dependency]
    ) -> str | None:
        attachment = names_attachment(raw, self.extensions)
        name = raw if attachment or raw.lower().endswith(".md") else raw + ".md"
        archive_source = source[len(self.scope) + 1 :]
        relative = name.startswith(("./", "../"))
        target: str | None
        apparent: str | None
        if relative:
            target, _ = _resolve_link_path(name, source)
            apparent, _ = _resolve_link_path(name, archive_source)
            collision = apparent in self._archive_members
        else:
            target = self._wiki_match(name, source, self._wiki_candidates(raw, source))
            apparent = self._wiki_match(name, archive_source, self._archive_members)
            collision = apparent is not None
        if target not in self.members:
            return self._wiki_outside(
                target or name, raw, source, dependencies, collision
            )
        bundle_target = target[len(self.scope) + 1 :]
        if relative and apparent == bundle_target:
            return None
        if not relative and name == bundle_target:
            return None
        if attachment:
            return bundle_target
        return compute_new_raw_target("wikilink", raw, None, bundle_target)

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
        masked = _masked_body(body)
        spans = [(start, end, False) for start, end in _destination_spans(masked)]
        spans.extend((start, end, True) for start, end in _wikilink_spans(masked))
        for start, end, wiki in sorted(spans):
            raw = body[start:end]
            replacement = (
                self._wiki_replacement(raw, source, dependencies)
                if wiki
                else self._replacement(raw, source, dependencies)
            )
            if replacement is not None:
                changes.append((start, end, replacement))
        for start, end, replacement in reversed(changes):
            body = body[:start] + replacement + body[end:]
        return body, dependencies
