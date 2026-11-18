"""Relocate body destinations and frontmatter paths for subtree export."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

import frontmatter
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from markdown_vault_mcp.fts_index import _pick_wikilink_candidate
from markdown_vault_mcp.okf import convert_wikilinks_to_markdown
from markdown_vault_mcp.okf_export_report import Dependency, OkfExportReport
from markdown_vault_mcp.scanner import (
    _RE_LINE_QUOTE_PREFIX,
    _RE_TITLE_ONLY,
    _blank_spans,
    _blank_wikilinks,
    _closes_fence,
    _definition_destination_end,
    _fence_opener,
    _find_wikilink,
    _is_external_target,
    _iter_definition_matches,
    _make_reference_follow,
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
    decode_link_target,
    decode_markdown_destination,
    encode_plain_destination,
    escape_link_text,
    escape_unparsable_parentheses,
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
_HTML_CLOSE = re.compile(r"</(?:pre|script|style|textarea)>", re.I)
_CONTAINER = re.compile(
    r"^(?:(?: {0,3}>[ \t]?)|(?:[ \t]*(?:[-+*]|[0-9]{1,9}[.)])[ \t]+))+", re.MULTILINE
)
# Line endings a link's parentheses may hold: after "(", before a title or ")".
_LINE_END = re.compile(r"[ \t]*\n")  # a masked CRLF is a space, then a newline
_SOFT = re.compile(r"\((?=[ \t]*\n)[ \t]*\n|\n(?=[ \t]*[\"')(])")


@dataclass
class _BlockState:
    items: list[int] = field(default_factory=list)
    depth: int = 0
    fence: tuple[str, int] | None = None
    html_end: Callable[[str], bool | re.Match[str] | None] | None = None
    blank_before: bool = True
    in_code: bool = False

    def _quote(self, line: str) -> str:
        """Strip the quote prefix and track depth; a deeper ``>`` in a block is content."""
        quote = _RE_LINE_QUOTE_PREFIX.match(line)
        prefix = quote.group() if quote else ""
        depth = prefix.count(">")
        if (self.fence or self.html_end) and depth > self.depth:
            cut = re.match(rf"(?: {{0,3}}>[ \t]?){{{self.depth}}}", line)
            return line[cut.end() :] if cut else line
        if depth != self.depth:
            self.items.clear()
            self.fence = self.html_end = None
            self.depth = depth
        return line[len(prefix) :]

    def _container(self, line: str) -> tuple[str, int, re.Match[str] | None]:
        text = self._quote(line.rstrip("\n").expandtabs(4))
        indent = len(text) - len(text.lstrip(" "))
        marker = _MARKER.match(text)
        block = bool(self.fence or self.html_end)
        while (
            self.items
            and indent < self.items[-1]
            and (
                (block and text.strip())
                or (not block and (self.blank_before or marker))
            )
        ):
            self.items.pop()
            self.fence = self.html_end = None
        return text, indent, marker

    def _open(self, inner: str) -> bool:
        self.fence = _fence_opener(inner)
        if self.fence:
            return True
        if _HTML_OPEN.match(inner):
            self.html_end = _HTML_CLOSE.search
        elif inner.lstrip(" ").startswith("<!--") and "-->" not in inner:
            self.html_end = lambda line: "-->" in line
        return self._active(inner)

    def _active(self, inner: str) -> bool:
        if self.fence:
            if _closes_fence(inner, self.fence):
                self.fence = None
            return True
        if self.html_end:
            if self.html_end(inner):
                self.html_end = None
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


def _label_key(label: str) -> str:
    return " ".join(label.split()).casefold()


class _ReferenceLabels(Mapping[str, str]):
    def __init__(self, labels: set[str]) -> None:
        self.labels = labels

    def __getitem__(self, key: str) -> str:
        key = _label_key(key)
        if key not in self.labels:
            raise KeyError(key)
        return key

    def __iter__(self) -> Iterator[str]:
        return iter(self.labels)

    def __len__(self) -> int:
        return len(self.labels)


def _definition_spans(
    masked: str,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    definitions = []
    first: dict[str, tuple[int, int]] = {}
    unquoted = _CONTAINER.sub(lambda match: " " * len(match.group()), masked)
    for label, tail, start, end in _iter_definition_matches(unquoted):
        key = _label_key(label)
        if not key or key.startswith("^"):
            continue
        line_end = masked.find("\n", end)
        target_start = (len(masked) if line_end < 0 else line_end) - len(tail)
        target_end = _definition_destination_end(masked, target_start)
        if target_end is not None:
            first.setdefault(key, (target_start, target_end))
            definitions.append((start, end))
    usages = _blank_wikilinks(_blank_spans(masked, definitions))
    labels = _ReferenceLabels(set(first))
    used: set[str] = set()
    follow_reference = _make_reference_follow(labels)

    def follow(region: str, opener: int, closer: int) -> tuple[str, int] | None:
        result = follow_reference(region, opener, closer)
        if result is not None:
            used.add(result[0])
        return result

    for _offset, region in _regions(usages):
        for _ in iter_bracket_links(region, follow):
            pass
    return [span for key, span in first.items() if key in used], definitions


def _inline_parts(region: str, offset: int) -> list[tuple[int, int, int]]:
    parts: list[tuple[int, int, int]] = []

    def follow(
        text: str, opener: int, closer: int
    ) -> tuple[tuple[str, int], int] | None:
        result = _follow_inline_destination(text, opener, closer)
        if result is not None:
            (raw, start), end = result
            parts.append((offset + start, offset + start + len(raw), offset + end - 1))
        return result

    flat = _SOFT.sub(lambda match: match.group().replace("\n", " "), region)
    for _ in iter_bracket_links(flat, follow):
        pass
    return parts


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
    masked = _wiki_text(_masked_body(body))
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
        spans.extend((start, end) for start, end, _ in _inline_parts(region, offset))
    return sorted(spans)


def _title_end(masked: str, end: int) -> int:
    """End of a definition ending at *end*, past a title on the next line."""
    line = _LINE_END.match(masked, end)
    title = _RE_TITLE_ONLY.match(masked, line.end()) if line else None
    if title and masked[title.end() : title.end() + 1] in {"", "\n"}:
        return title.end()
    return end


def _wiki_text(masked: str) -> str:
    """Blank every Markdown link destination and title, used or not.

    A destination and its title are text, never wikilinks, so the wikilink
    scans must not read them.

    Args:
        masked: Body with protected blocks masked.

    Returns:
        *masked* with each definition, and each inline destination with its
        title, replaced by spaces.
    """
    if "[[" not in masked:
        return masked
    _used, defined = _definition_spans(masked)
    spans = [(start, _title_end(masked, end)) for start, end in defined]
    rest = _blank_spans(masked, spans)
    spans = spans + [
        (start, end)
        for offset, region in _regions(rest)
        for start, _stop, end in _inline_parts(region, offset)
    ]
    return _blank_spans(masked, spans)


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


def _wikilink_spans(masked: str) -> Iterator[tuple[int, int, int, int]]:
    position = 0
    while (found := _find_wikilink(masked, position)) is not None:
        position, target, _alias, start = found
        left = len(target) - len(target.lstrip())
        path = target.strip().removesuffix("\\").split("#", 1)[0]
        if path and not _is_external_target(path):
            yield start + 2 + left, start + 2 + left + len(path), start, position


def _external_wikilink(body: str, start: int, end: int, url: str) -> tuple[int, str]:
    target, _pipe, alias = body[start + 2 : end - 2].partition("|")
    target = target.strip().removesuffix("\\")
    path, separator, fragment = target.partition("#")
    display = alias.strip() if alias and alias.strip() else path
    if separator:
        url += "#" + quote(fragment, safe="/%?", errors="surrogateescape")
    url = encode_plain_destination(escape_unparsable_parentheses(url))
    embed = start > 0 and body[start - 1] == "!" and not _is_escaped(body, start - 1)
    if embed:
        start -= 1
    return start, f"{'!' if embed else ''}[{escape_link_text(display)}]({url})"


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

    def _external_url(self, target: str) -> str:
        base = (self.external_base or "").rstrip("/")
        return (
            base + "/" + quote(target.lstrip("/"), safe="/", errors="surrogateescape")
        )

    def _outside_target(
        self, target: str, source: str, raw: str, apparent: str
    ) -> str | None:
        if self.external_base:
            return self._external_url(target)
        if apparent in self._archive_members:
            raise _ExportCollision(
                f"Export collision in {_safe_text(source)}: {_safe_text(raw)}; supply external_base to resolve outside destinations"
            )
        return None

    def _outside(self, target: str, source: str, raw: str) -> str | None:
        path, suffix = _path_and_suffix(raw)
        apparent, _ = _resolve_link_path(path, source[len(self.scope) + 1 :])
        url = self._outside_target(target, source, raw, apparent)
        if url is None:
            return None
        url += suffix
        return f"<{url}>" if is_pointy_destination(raw) else url

    def _wiki_candidates(self, raw: str, name: str, source: str) -> set[str]:
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
        return self._candidates.get(name.rpartition("/")[2], set())

    def _wiki_target(self, name: str, raw: str, source: str) -> str | None:
        if name in self.members:
            return name
        return self._wiki_match(name, source, self._wiki_candidates(raw, name, source))

    @staticmethod
    def _wiki_match(name: str, source: str, candidates: set[str]) -> str | None:
        if name in candidates:
            return name
        matches = [path for path in candidates if path.endswith("/" + name)]
        return _pick_wikilink_candidate(matches, source) if matches else None

    def _wiki_outside(
        self,
        target: str | None,
        name: str,
        source: str,
        dependencies: list[Dependency],
        collision: bool,
    ) -> str | None:
        dependencies.append(
            Dependency(source[len(self.scope) + 1 :], _safe_text(target or name))
        )
        if self.external_base and target:
            return self._external_url(target)
        if collision:
            raise ValueError(
                f"Export collision in {_safe_text(source)}: {_safe_text(name)}; "
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
            target = self._wiki_target(name, raw, source)
            apparent = self._wiki_match(name, archive_source, self._archive_members)
            collision = apparent is not None
        if target not in self.members:
            return self._wiki_outside(target, name, source, dependencies, collision)
        bundle_target = target[len(self.scope) + 1 :]
        if relative and apparent == bundle_target:
            return None
        if not relative and name == bundle_target:
            return None
        if attachment:
            return bundle_target
        return compute_new_raw_target("wikilink", raw, None, bundle_target)

    def _field_replacement(
        self, value: str, source: str, dependencies: list[Dependency]
    ) -> str | None:
        written, suffix = _field_parts(value)
        path = decode_link_target(written)
        target, _ = _resolve_link_path(path, source)
        archive_source = source[len(self.scope) + 1 :]
        apparent, _ = _resolve_link_path(path, archive_source)
        if target not in self.members:
            dependencies.append(Dependency(archive_source, _safe_text(value)))
            if self.external_base and path == written:
                base = self.external_base.rstrip("/")
                return base + "/" + _quote_field_uri(target) + suffix
            url = self._outside_target(target, source, value, apparent)
            return url + suffix if url is not None else None
        bundle_target = target[len(self.scope) + 1 :]
        if not written.startswith("/") and apparent == bundle_target:
            return None
        if written.startswith("/"):
            replacement = "/" + bundle_target
        else:
            replacement = posixpath.relpath(
                bundle_target, posixpath.dirname(archive_source) or "."
            )
            if not replacement.startswith("."):
                replacement = "./" + replacement
        if "%" in written:
            replacement = quote(
                replacement,
                safe="/%" if path == written else "/",
                errors="surrogateescape",
            )
        return replacement + suffix

    def _field_changes(
        self, root: object, source: str
    ) -> tuple[list[tuple[ScalarNode, str]], list[Dependency], list[Dependency]]:
        changes: list[tuple[ScalarNode, str]] = []
        dependencies: list[Dependency] = []
        ambiguous: list[Dependency] = []
        for node in _frontmatter_paths(root):
            value = node.value.rstrip("\n") if node.style in ("|", ">") else node.value
            trailing = node.value[len(value) :]
            if not value or _is_external_target(value):
                continue
            if not value.startswith(("/", "./", "../")):
                ambiguous.append(
                    Dependency(source[len(self.scope) + 1 :], _safe_text(value))
                )
                continue
            replacement = self._field_replacement(value, source, dependencies)
            if replacement is not None and replacement != value:
                changes.append((node, replacement + trailing))
        return changes, dependencies, ambiguous

    def _rewrite_header(
        self,
        header: str,
        loader: _AliasLoader,
        root: object,
        changes: list[tuple[ScalarNode, str]],
        source: str,
    ) -> str:
        if loader.aliases:
            header = _isolate(
                header, root, loader.aliases, {id(node) for node, _ in changes}
            )
            root = yaml.compose(header, Loader=yaml.SafeLoader)
            changes, _, _ = self._field_changes(root, source)
        return _splice_fields(header, changes)

    def relocate_frontmatter(
        self, prefix: str, source: str, report: OkfExportReport
    ) -> str:
        """Relocate frontmatter paths and record dependencies and inspection limits.

        Args:
            prefix: Original frontmatter including delimiters.
            source: Vault-relative source path.
            report: Export observations to update.

        Returns:
            Prefix with eligible path values relocated.

        Raises:
            ValueError: An outside path collides with a bundle member.
        """
        if not self.scope:
            return prefix
        candidate = prefix.removeprefix("\ufeff").lstrip()
        handler = frontmatter.detect_format(candidate, frontmatter.handlers)
        if handler is None:
            return prefix
        try:
            header, _ = handler.split(candidate)
            offset = len(prefix) - len(candidate) + candidate.index(header)
        except ValueError:
            return prefix
        end = offset + len(header)
        try:
            loader = _AliasLoader(header)
            try:
                root = loader.get_single_node()
                changes, dependencies, ambiguous = self._field_changes(root, source)
                report.dependencies.extend(dependencies)
                report.ambiguous_path.extend(ambiguous)
                if not changes:
                    return prefix
                header = self._rewrite_header(header, loader, root, changes, source)
            finally:
                loader.dispose()
        except (_MetadataComplexity, RecursionError):
            report.uninspected.setdefault(source, "metadata_complexity")
        except _ExportCollision:
            raise
        except ValueError:
            report.uninspected.setdefault(source, "metadata_encoding")
        except yaml.YAMLError:
            pass
        else:
            return prefix[:offset] + header + prefix[end:]
        return prefix

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
        spans: list[tuple[int, int, tuple[int, int] | None]] = [
            (start, end, None) for start, end in _destination_spans(masked)
        ]
        spans.extend(
            (start, end, (opening, closing))
            for start, end, opening, closing in _wikilink_spans(_wiki_text(masked))
        )
        for start, end, wiki in sorted(spans, key=lambda span: (span[0], span[1])):
            raw = body[start:end]
            replacement = (
                self._wiki_replacement(raw, source, dependencies)
                if wiki
                else self._replacement(raw, source, dependencies)
            )
            if replacement is not None:
                if wiki and _is_external_target(replacement):
                    start, replacement = _external_wikilink(body, *wiki, replacement)
                    end = wiki[1]
                changes.append((start, end, replacement))
        for start, end, replacement in reversed(changes):
            body = body[:start] + replacement + body[end:]
        return body, dependencies


class _ExportCollision(ValueError):
    """An outside path would address a different archive member."""


def _quote_field_uri(path: str) -> str:
    parts = re.split(r"(%[0-9A-Fa-f]{2})", path.lstrip("/"))
    return "".join(
        part
        if re.fullmatch(r"%[0-9A-Fa-f]{2}", part)
        else quote(part, safe="/", errors="surrogateescape")
        for part in parts
    )


class _MetadataComplexity(Exception):
    """The metadata graph cannot be inspected or copied within the budget."""


def _mapping_values(
    node: object, key: str, memo: dict[tuple[int, str], list[object] | None]
) -> list[object]:
    if not isinstance(node, MappingNode):
        return []
    cache_key = (id(node), key)
    if cache_key in memo:
        result = memo[cache_key]
        if result is None:
            raise _MetadataComplexity
        return result
    memo[cache_key] = None
    direct = [
        value
        for name, value in node.value
        if isinstance(name, ScalarNode) and name.value == key
    ]
    if direct:
        memo[cache_key] = direct[-1:]
        return direct[-1:]
    for name, value in node.value:
        if isinstance(name, ScalarNode) and name.tag == "tag:yaml.org,2002:merge":
            candidates = value.value if isinstance(value, SequenceNode) else [value]
            for candidate in candidates:
                found = _mapping_values(candidate, key, memo)
                if found:
                    memo[cache_key] = found
                    return found
    memo[cache_key] = []
    return []


def _frontmatter_paths(root: object) -> list[ScalarNode]:
    memo: dict[tuple[int, str], list[object] | None] = {}
    nodes = _mapping_values(root, "resource", memo) + _mapping_values(
        root, "computation", memo
    )
    for key in ("executor", "attester"):
        for parent in _mapping_values(root, key, memo):
            nodes.extend(_mapping_values(parent, "resource", memo))
    for sources in _mapping_values(root, "sources", memo):
        if isinstance(sources, SequenceNode):
            for item in sources.value:
                nodes.extend(_mapping_values(item, "resource", memo))
    unique: dict[int, ScalarNode] = {}
    for node in nodes:
        if isinstance(node, ScalarNode) and node.tag == "tag:yaml.org,2002:str":
            unique[id(node)] = node
    return list(unique.values())


def _field_parts(value: str) -> tuple[str, str]:
    index = min((value.find(c) for c in "?#" if c in value), default=len(value))
    return value[:index], value[index:]


def _quoted_scalar(value: str) -> str:
    encoded = json.dumps(value, ensure_ascii=False)
    return "".join(
        f"\\u{ord(char):04x}"
        if 0x7F <= ord(char) <= 0x9F or char in "\u2028\u2029\ufeff"
        else char
        for char in encoded
    )


def _yaml_scalar(value: str, style: str | None, original: str) -> str:
    if style in ("|", ">"):
        return _quoted_scalar(value) + original[len(original.rstrip("\r\n")) :]
    if style == "'":
        return "'" + value.replace("'", "''") + "'"
    if style == '"':
        return _quoted_scalar(value)
    scalar = ScalarNode("tag:yaml.org,2002:str", value)
    return _emit(scalar)


class _AliasLoader(yaml.SafeLoader):
    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.aliases: list[tuple[int, int, Node]] = []

    def compose_node(self, parent: Node | None, index: int) -> Node | None:
        event = self.peek_event()  # type: ignore[no-untyped-call]
        node = super().compose_node(parent, index)
        if (
            isinstance(event, yaml.AliasEvent)
            and node is not None
            and event.start_mark is not None
            and event.end_mark is not None
        ):
            self.aliases.append((event.start_mark.index, event.end_mark.index, node))
        return node


def _relevant_nodes(root: object, selected: set[int]) -> set[int]:
    pending = [root]
    seen: set[int] = set()
    parents: dict[int, set[int]] = {}
    while pending:
        node = pending.pop()
        ident = id(node)
        if ident in seen:
            continue
        seen.add(ident)
        children: list[object] = []
        if isinstance(node, MappingNode):
            children = [child for pair in node.value for child in pair]
        elif isinstance(node, SequenceNode):
            children = list(node.value)
        for child in children:
            parents.setdefault(id(child), set()).add(ident)
            pending.append(child)
    relevant = set(selected)
    queue = list(selected)
    while queue:
        for parent in parents.get(queue.pop(), set()):
            if parent not in relevant:
                relevant.add(parent)
                queue.append(parent)
    return relevant


def _copy_node(node: Node, budget: list[int], active: set[int]) -> Node:
    if id(node) in active or budget[0] <= 0:
        raise _MetadataComplexity
    budget[0] -= 1
    active.add(id(node))
    try:
        if isinstance(node, ScalarNode):
            return ScalarNode(
                node.tag,
                node.value,
                style=None if node.style in ("|", ">") else node.style,
            )
        if isinstance(node, SequenceNode):
            return SequenceNode(
                node.tag,
                [_copy_node(child, budget, active) for child in node.value],
                flow_style=True,
            )
        if not isinstance(node, MappingNode):
            raise _MetadataComplexity
        return MappingNode(
            node.tag,
            [
                (_copy_node(key, budget, active), _copy_node(value, budget, active))
                for key, value in node.value
            ],
            flow_style=True,
        )
    finally:
        active.remove(id(node))


def _emit(node: Node) -> str:
    return (
        yaml.serialize(node, allow_unicode=True, width=1000000)
        .removesuffix("\n...\n")
        .removesuffix("\n")
    )


def _isolate(
    text: str, root: object, aliases: list[tuple[int, int, Node]], changed: set[int]
) -> str:
    relevant = _relevant_nodes(root, changed)
    budget = [4096]
    for start, end, node in reversed(aliases):
        if id(node) in relevant:
            text = text[:start] + _emit(_copy_node(node, budget, set())) + text[end:]
    return text


def _splice_fields(text: str, changes: list[tuple[ScalarNode, str]]) -> str:
    starts = {
        token.end_mark.index: token.start_mark.index
        for token in yaml.scan(text)
        if isinstance(token, yaml.ScalarToken)
    }
    replacements = []
    for node, value in changes:
        start = starts.get(node.end_mark.index, node.start_mark.index)
        replacements.append(
            (
                start,
                node.end_mark.index,
                _yaml_scalar(value, node.style, text[start : node.end_mark.index]),
            )
        )
    pieces: list[str] = []
    previous = 0
    for start, end, replacement in sorted(replacements):
        pieces.extend((text[previous:start], replacement))
        previous = end
    pieces.append(text[previous:])
    return "".join(pieces)
