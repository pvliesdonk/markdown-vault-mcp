"""Conservative, byte-preserving union of date-grouped Markdown logs."""

from __future__ import annotations

import dataclasses
import datetime
import re
from typing import TYPE_CHECKING, Any

from markdown_it import MarkdownIt

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import _LOG_HEADING_RE
from markdown_vault_mcp.scanner import _normalise_line_endings, strip_frontmatter_block

if TYPE_CHECKING:
    from markdown_it.token import Token


@dataclasses.dataclass(frozen=True)
class LogMerge:
    """Result of attempting a log union."""

    text: str | None
    added: int
    reason: str


@dataclasses.dataclass
class _Section:
    date: str
    start: int
    end: int
    entries: list[tuple[str, ...]]
    spans: list[tuple[int, int]]


@dataclasses.dataclass
class _Log:
    prefix: str
    preamble: str
    lines: list[str]
    sections: list[_Section]
    refs: dict[str, tuple[str, str]]


def _lines(text: str) -> list[str]:
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)[:-1]


def _heading_date(
    markup: str, content: str, sections: list[_Section]
) -> tuple[str, str]:
    match = _LOG_HEADING_RE.fullmatch(f"## {content}")
    if not match or markup != "##":
        return "", "non_date_heading"
    date = match.group(1)
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        return "", "invalid_date"
    if any(section.date == date for section in sections):
        return "", "duplicate_date"
    if sections and sections[-1].date < date:
        return "", "date_order"
    return date, ""


def _capture_entry(section: _Section, lines: list[str], span: list[int]) -> None:
    item = lines[span[0] : span[1]]
    while item and not item[-1].strip(" \t\r\n"):
        item.pop()
    section.entries.append(tuple(line.rstrip("\r\n") for line in item))
    section.spans.append((span[0], span[0] + len(item)))


def _read_sections(
    lines: list[str], tokens: list[Token]
) -> tuple[list[_Section], int, set[int], str]:
    sections: list[_Section] = []
    first = len(lines)
    accounted: set[int] = set()
    for index, token in enumerate(tokens):
        if (
            token.type == "heading_open"
            and token.level == 0
            and token.tag == "h2"
            and token.map
        ):
            date, reason = _heading_date(
                token.markup, tokens[index + 1].content, sections
            )
            if reason:
                return [], 0, set(), reason
            if sections:
                sections[-1].end = token.map[0]
            accounted.update(range(*token.map))
            first = min(first, token.map[0])
            sections.append(_Section(date, token.map[0], len(lines), [], []))
        elif (
            token.type == "list_item_open"
            and token.level == 1
            and token.markup in {"-", "+", "*"}
            and token.map
            and sections
        ):
            accounted.update(range(*token.map))
            _capture_entry(sections[-1], lines, token.map)
    return sections, first, accounted, ""


def _parse(text: str) -> tuple[_Log | None, str]:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None, "invalid_utf8"
    body = strip_frontmatter_block(text)
    prefix = text[: len(text) - len(body)]
    lines = _lines(body)
    env: dict[str, Any] = {}
    tokens = MarkdownIt("commonmark").parse(body, env)
    refs = {
        label: (ref["href"], ref["title"])
        for label, ref in env.get("references", {}).items()
    }
    sections, first, accounted, reason = _read_sections(lines, tokens)
    if reason:
        return None, reason
    if not sections:
        return None, "no_date_heading"
    if any(
        line.strip(" \t\r\n") and i not in accounted
        for i, line in enumerate(lines)
        if i >= first
    ):
        return None, "unaccounted_content"
    preamble = lines[:first]
    while preamble and not preamble[-1].strip(" \t\r\n"):
        preamble.pop()
    return _Log(prefix, "".join(preamble).rstrip("\r\n"), lines, sections, refs), ""


def _insertions(
    left: _Log, right: _Log, eol: str
) -> tuple[dict[int, list[str]], set[tuple[str, tuple[str, ...]]], int]:
    known = {(s.date, entry) for s in left.sections for entry in s.entries}
    insertions: dict[int, list[str]] = {}
    added = 0
    for section in right.sections:
        entries = []
        for entry in section.entries:
            key = (section.date, entry)
            if key not in known:
                known.add(key)
                entries.append(entry)
                added += 1
        if not entries:
            continue
        existing = next((s for s in left.sections if s.date == section.date), None)
        if existing:
            pos = existing.end
            while pos > existing.start + 1 and not left.lines[pos - 1].strip(" \t\r\n"):
                pos -= 1
            content = "".join(eol.join(entry) + eol for entry in entries)
        else:
            pos = next(
                (s.start for s in left.sections if s.date < section.date),
                len(left.lines),
            )
            content = (
                f"## {section.date}{eol}{eol}"
                + "".join(eol.join(entry) + eol for entry in entries)
                + eol
            )
        insertions.setdefault(pos, []).append(content)
    return insertions, known, added


def _duplicate_lines(log: _Log) -> set[int]:
    duplicates: set[int] = set()
    for section in log.sections:
        seen: set[tuple[str, ...]] = set()
        for entry, (start, end) in zip(section.entries, section.spans, strict=True):
            if entry in seen:
                duplicates.update(range(start, end))
            seen.add(entry)
    return duplicates


def merge_log_texts(upstream: str, local: str) -> LogMerge:
    """Union complete list entries while retaining upstream formatting.

    Args:
        upstream: The current upstream log.
        local: The replayed local log.

    Returns:
        Merged text and count, or a reason why the union is unsafe.
    """
    left, reason = _parse(upstream)
    if left is None:
        return LogMerge(None, 0, reason)
    right, reason = _parse(local)
    if right is None:
        return LogMerge(None, 0, reason)
    mismatch = (
        "reference_definitions"
        if left.refs != right.refs
        else "frontmatter_differs"
        if _normalise_line_endings(left.prefix) != _normalise_line_endings(right.prefix)
        else "preamble_differs"
        if _normalise_line_endings(left.preamble)
        != _normalise_line_endings(right.preamble)
        else ""
    )
    if mismatch:
        return LogMerge(None, 0, mismatch)
    eol_match = re.search(r"\r\n|\r|\n", upstream)
    eol = eol_match.group() if eol_match else "\n"
    insertions, known, added = _insertions(left, right, eol)
    duplicates = _duplicate_lines(left)
    if not added and not duplicates:
        return LogMerge(upstream, 0, "")
    output = [
        "" if index in duplicates else line for index, line in enumerate(left.lines)
    ]
    for pos in sorted(insertions, reverse=True):
        if pos and output[pos - 1] and not output[pos - 1].endswith(("\n", "\r")):
            output[pos - 1] += eol
        output[pos:pos] = insertions[pos]
    merged = left.prefix + "".join(output)
    check, reason = _parse(merged)
    if (
        check is None
        or check.refs != left.refs
        or not known <= {(s.date, e) for s in check.sections for e in s.entries}
    ):
        return LogMerge(None, 0, "self_check_" + reason)
    return LogMerge(merged, added, "")


@dataclasses.dataclass(frozen=True)
class LogMove:
    """A source log and its destination in a migration."""

    source: str
    target: str
    entries: int
    frontmatter: bool


@dataclasses.dataclass(frozen=True)
class LogMigration:
    """Merged or mergeable logs and sources kept with their reasons."""

    merged: tuple[LogMove, ...]
    kept: tuple[tuple[str, str], ...]
    target_frontmatter: tuple[str, ...]
    source_frontmatter: tuple[str, ...]


def _unsafe_relocation(text: str) -> bool:
    """Refuse links whose destination changes when the source moves."""
    if "[[" in text:
        return True
    for token in MarkdownIt("commonmark").parse(text):
        for item in (token, *(token.children or [])):
            if item.type in {"html_block", "html_inline"}:
                return True
            if item.type in {"link_open", "image"}:
                destination = item.attrGet(
                    "href" if item.type == "link_open" else "src"
                )
                if destination is not None and not re.match(
                    r"(?:[a-zA-Z][\w+.-]*:|/)", str(destination)
                ):
                    return True
    return False


def _migration_text(target: str | None, source: str) -> tuple[str | None, int, str]:
    """Merge entries without deduplication; refuse content that cannot move safely."""
    right, reason = _parse(source)
    if right is None:
        return None, 0, reason
    # Relative Markdown and wiki links would acquire a different meaning.
    if _unsafe_relocation(source) or any(
        not re.match(r"(?:[a-zA-Z][\w+.-]*:|/)", href)
        for href, _ in right.refs.values()
    ):
        return None, 0, "relative or wiki link"
    if target is None:
        return (
            source[len(right.prefix) :],
            sum(len(s.entries) for s in right.sections),
            "",
        )
    left, reason = _parse(target)
    if left is None:
        return None, 0, "target: " + reason
    reason = (
        "preamble differs"
        if _normalise_line_endings(left.preamble)
        != _normalise_line_endings(right.preamble)
        else "reference definitions differ"
        if left.refs != right.refs
        else ""
    )
    if reason:
        return None, 0, reason
    return _combine_migration(left, right, target)


def _combine_migration(
    left: _Log, right: _Log, target: str
) -> tuple[str | None, int, str]:
    """Insert every source entry into the target's dated sections."""
    eol_match = re.search(r"\r\n|\r|\n", target)
    eol = eol_match.group() if eol_match else "\n"
    insertions: dict[int, list[str]] = {}
    count = 0
    for section in right.sections:
        if not section.entries:
            continue
        count += len(section.entries)
        existing = next((s for s in left.sections if s.date == section.date), None)
        pos = (
            existing.end
            if existing
            else next(
                (s.start for s in left.sections if s.date < section.date),
                len(left.lines),
            )
        )
        while existing and pos > existing.start + 1 and not left.lines[pos - 1].strip():
            pos -= 1
        body = "".join(eol.join(entry) + eol for entry in section.entries)
        if not existing:
            body = f"## {section.date}{eol}{eol}" + body + eol
        insertions.setdefault(pos, []).append(body)
    output = left.lines[:]
    for pos in sorted(insertions, reverse=True):
        if pos and output[pos - 1] and not output[pos - 1].endswith(("\n", "\r")):
            output[pos - 1] += eol
        output[pos:pos] = insertions[pos]
    merged = left.prefix + "".join(output)
    checked, reason = _parse(merged)
    if (
        checked is None
        or sum(len(s.entries) for s in checked.sections)
        != sum(len(s.entries) for s in left.sections) + count
    ):
        return None, 0, "self-check: " + reason
    return merged, count, ""


def migrate_logs(
    doc: Any, designation: Any, *, apply: bool = False, allow_protected: bool = False
) -> LogMigration:
    """Preview or apply a conservative migration of descendant logs.

    Args:
        doc: Vault document manager.
        designation: Active folder designation policy.
        apply: Apply eligible moves.
        allow_protected: Allow operator-protected paths.

    Returns:
        Eligible or completed moves and the sources retained with reasons.
    """
    from markdown_vault_mcp._okf_write import okf_write_suppressed
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.utils.fs import walk_files_strict

    if designation is None or designation("") is None:
        raise InvalidRequestError("An active OKF bundle is required to merge logs.")
    if apply:
        doc.ensure_writable()
    inspector = doc._inspector()
    if inspector.path_presence(".md") == "blocked":
        raise InvalidRequestError("Cannot inspect vault root.")
    paths = walk_files_strict(
        doc._source_dir,
        prune=lambda p: inspector._internal(p.relative_to(doc._source_dir).as_posix()),
    )
    sources = [
        p.relative_to(doc._source_dir).as_posix()
        for p in paths
        if p.name == "log.md"
        and not inspector._internal(p.relative_to(doc._source_dir).as_posix())
    ]
    runner = _LogMigrationRunner(doc, designation, apply, allow_protected)
    with okf_write_suppressed(), doc.write_scope():
        for source in sources:
            runner.process(source)
    return LogMigration(
        tuple(runner.merged),
        tuple(runner.kept),
        tuple(sorted(runner.headers)),
        tuple(sorted(runner.source_headers)),
    )


class _LogMigrationRunner:
    """Execute one migration with shared preview state."""

    def __init__(
        self, doc: Any, designation: Any, apply: bool, allow_protected: bool
    ) -> None:
        self.doc = doc
        self.designation = designation
        self.apply = apply
        self.allow_protected = allow_protected
        self.merged: list[LogMove] = []
        self.kept: list[tuple[str, str]] = []
        self.headers: set[str] = set()
        self.source_headers: set[str] = set()
        self.simulated: dict[str, str] = {}

    def process(self, source: str) -> None:
        """Choose a destination and record a source's outcome."""
        import posixpath

        from markdown_vault_mcp.exceptions import MarkdownMCPError

        folder = posixpath.dirname(source)
        if not folder or self.designation(folder):
            return
        parent = posixpath.dirname(folder)
        while parent and not self.designation(parent):
            parent = posixpath.dirname(parent)
        target = f"{parent}/log.md" if parent else "log.md"
        try:
            self._move(source, target, folder, parent)
        except _LogMigrationInterrupted:
            raise
        except (MarkdownMCPError, OSError) as exc:
            self.kept.append((source, str(exc)))

    def _move(self, source: str, target: str, folder: str, parent: str) -> None:
        from markdown_vault_mcp._okf_convention import destination_home
        from markdown_vault_mcp.exceptions import InvalidRequestError

        doc = self.doc
        if destination_home(folder, "log.md", doc._source_dir) is None or (
            destination_home(parent, "log.md", doc._source_dir) is None
        ):
            raise InvalidRequestError("aliased log")
        for path in (source, target):
            doc._refuse_submodule(doc._validate_path(path), "path", path)
            doc.refuse_protected(path, allow_protected=self.allow_protected)
        src = doc._read_note(source, capped=False)
        dst = doc._read_note(target, capped=False)
        if (
            src is None
            or src.path != source
            or (dst is not None and dst.path != target)
        ):
            raise InvalidRequestError("missing or aliased log")
        if strip_frontmatter_block(src.content) != src.content:
            self.source_headers.add(source)
        previous = self.simulated.get(target, dst.content if dst else None)
        if previous is not None and strip_frontmatter_block(previous) != previous:
            self.headers.add(target)
        text, count, reason = _migration_text(previous, src.content)
        if text is None:
            raise InvalidRequestError(reason)
        if self.apply:
            self._write(source, target, text, src.etag, dst.etag if dst else None)
        else:
            self.simulated[target] = text
        self.merged.append(
            LogMove(source, target, count, source in self.source_headers)
        )

    def _write(
        self,
        source: str,
        target: str,
        text: str,
        source_etag: str | None,
        target_etag: str | None,
    ) -> None:
        from markdown_vault_mcp.exceptions import MarkdownMCPError

        try:
            self.doc.write(
                target,
                text,
                if_match=target_etag,
                allow_overwrite=True,
                allow_protected=self.allow_protected,
            )
            self.doc.delete(
                source, if_match=source_etag, allow_protected=self.allow_protected
            )
        except (MarkdownMCPError, OSError) as exc:
            raise _LogMigrationInterrupted(
                f"Merge interrupted: {target} may have changed; {source} may remain: {exc}"
            ) from exc


class _LogMigrationInterrupted(InvalidRequestError):
    """A target may have changed before source removal failed."""
