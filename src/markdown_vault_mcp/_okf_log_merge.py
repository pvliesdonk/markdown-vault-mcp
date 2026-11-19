"""Union date-grouped Git conflicts and migrate descendant logs."""

from __future__ import annotations

import dataclasses
import datetime
import posixpath
import re
from typing import TYPE_CHECKING, Any

import yaml
from markdown_it import MarkdownIt

from markdown_vault_mcp.exceptions import InvalidRequestError, MarkdownMCPError
from markdown_vault_mcp.okf import _LOG_HEADING_RE
from markdown_vault_mcp.scanner import (
    _normalise_line_endings,
    parse_frontmatter,
    strip_frontmatter_block,
)

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
    left: _Log, right: _Log, eol: str, *, dedupe: bool = True
) -> tuple[dict[int, list[str]], set[tuple[str, tuple[str, ...]]], int]:
    known = {(s.date, entry) for s in left.sections for entry in s.entries}
    insertions: dict[int, list[str]] = {}
    added = 0
    for section in right.sections:
        entries = []
        for entry in section.entries:
            key = (section.date, entry)
            if not dedupe or key not in known:
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


def _splice(
    prefix: str,
    output: list[str],
    insertions: dict[int, list[str]],
    eol: str,
    *,
    separate_headings: bool = False,
) -> str:
    for pos in sorted(insertions, reverse=True):
        if pos and output[pos - 1] and not output[pos - 1].endswith(("\n", "\r")):
            output[pos - 1] += eol
        if (
            separate_headings
            and pos
            and pos < len(output)
            and output[pos - 1].strip(" \t\r\n")
            and insertions[pos][0].startswith("## ")
        ):
            insertions[pos][0] = eol + insertions[pos][0]
        output[pos:pos] = insertions[pos]
    return prefix + "".join(output)


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
    merged = _splice(left.prefix, output, insertions, eol)
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


@dataclasses.dataclass(frozen=True)
class LogMigration:
    """Merged or mergeable logs and sources kept with their reasons."""

    merged: tuple[LogMove, ...]
    kept: tuple[tuple[str, str], ...]
    target_frontmatter: tuple[str, ...]
    source_frontmatter: tuple[str, ...]


def _parse_migration_target(text: str) -> tuple[_Log | None, str]:
    """Parse a BOM-prefixed target while retaining its original leading bytes."""
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    body = text[len(bom) :]
    parsed, reason = _parse(body)
    if parsed is not None:
        try:
            parse_frontmatter(body)
        except yaml.YAMLError:
            return None, "invalid frontmatter"
        parsed.prefix = bom + parsed.prefix
    return parsed, reason


def _merge_migration(
    left: _Log | None, right: _Log, source: str, target: str | None
) -> tuple[str | None, int, str]:
    if left is None:
        text = source[len(right.prefix) :]
        checked, reason = _parse(text)
        return (
            (text if checked is not None else None),
            sum(len(s.entries) for s in right.sections),
            reason,
        )
    eol_match = re.search(r"\r\n|\r|\n", target or "")
    eol = eol_match.group() if eol_match else "\n"
    insertions, _, count = _insertions(left, right, eol, dedupe=False)
    if right.preamble and _normalise_line_endings(
        left.preamble
    ) != _normalise_line_endings(right.preamble):
        insertions.setdefault(left.sections[0].start, []).insert(
            0, right.preamble + eol + eol
        )
    merged = _splice(
        left.prefix, left.lines[:], insertions, eol, separate_headings=True
    )
    checked, reason = _parse_migration_target(merged)
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
        rel
        for p in paths
        if (rel := p.relative_to(doc._source_dir).as_posix()).endswith("/log.md")
        and not inspector._internal(rel)
        and not designation(posixpath.dirname(rel))
    ]
    runner = _LogMigrationRunner(doc, designation, apply, allow_protected)
    with okf_write_suppressed(), doc.write_scope():
        for index, source in enumerate(sources):
            try:
                runner.process(source)
            except _LogMigrationInterrupted as exc:
                exc.partial = runner.result()
                exc.pending = tuple(sources[index:])
                raise
    return runner.result()


class _LogMigrationRunner:
    """Execute one migration."""

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

    def result(self) -> LogMigration:
        return LogMigration(
            tuple(self.merged),
            tuple(self.kept),
            tuple(sorted(self.headers)),
            tuple(sorted(self.source_headers)),
        )

    def process(self, source: str) -> None:
        """Choose a destination and record a source's outcome."""
        folder = posixpath.dirname(source)
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
        self._validate_move(source, target, folder, parent)
        doc = self.doc
        src = doc._read_note(source, capped=False)
        if src is None or src.path != source:
            raise InvalidRequestError("missing or aliased log")
        if strip_frontmatter_block(src.content) != src.content:
            self.source_headers.add(source)
        self._merge_source(target, src)

    def _validate_move(
        self, source: str, target: str, folder: str, parent: str
    ) -> None:
        from markdown_vault_mcp._okf_convention import destination_home

        doc = self.doc
        if destination_home(folder, "log.md", doc._source_dir) is None or (
            destination_home(parent, "log.md", doc._source_dir) is None
        ):
            raise InvalidRequestError("aliased log")
        for path in (source, target):
            doc._refuse_submodule(doc._validate_path(path), "path", path)
            doc.refuse_protected(path, allow_protected=self.allow_protected)

    def _merge_source(self, target: str, src: Any) -> None:
        source = src.path
        raw_target, target_etag = self._raw_target(target)
        previous = raw_target
        right, reason = _parse(src.content)
        if right is None:
            raise InvalidRequestError(reason)
        left, reason = (
            _parse_migration_target(previous) if previous is not None else (None, "")
        )
        if previous is not None and left is None:
            raise InvalidRequestError("target: " + reason)
        if left is not None and left.prefix.lstrip("\ufeff"):
            self.headers.add(target)
        text, count, reason = _merge_migration(left, right, src.content, previous)
        if text is None:
            raise InvalidRequestError(reason)
        if self.apply:
            self._write(source, target, text, src.etag, (target_etag, raw_target))
        self.merged.append(LogMove(source, target, count))

    def _raw_target(self, target: str) -> tuple[str | None, str | None]:
        from markdown_vault_mcp.hashing import compute_etag
        from markdown_vault_mcp.utils.fs import is_regular_file, path_exists

        path = self.doc._validate_path(target)
        if not is_regular_file(path):
            if path_exists(path):
                raise InvalidRequestError("target is not a regular file")
            return None, None
        raw = path.read_bytes()
        return raw.decode("utf-8", "surrogateescape"), compute_etag(raw)

    def _write(
        self,
        source: str,
        target: str,
        text: str,
        source_etag: str | None,
        target_state: tuple[str | None, str | None],
    ) -> None:
        target_etag, original = target_state
        try:
            with self.doc.exact_byte_writes():
                self.doc.write(
                    target,
                    text,
                    if_match=target_etag,
                    allow_overwrite=True,
                    allow_protected=self.allow_protected,
                )
        except (MarkdownMCPError, OSError) as exc:
            raise _LogMigrationInterrupted(
                f"Merge interrupted: {target} may have changed; {source} may remain: {exc}"
            ) from exc
        try:
            self.doc.delete(
                source, if_match=source_etag, allow_protected=self.allow_protected
            )
        except (MarkdownMCPError, OSError) as exc:
            restored = self._restore(source, target, source_etag, text, original)
            detail = "target restored" if restored else "target may have changed"
            raise _LogMigrationInterrupted(
                f"Merge interrupted: {detail}; {source} may remain: {exc}"
            ) from exc

    def _restore(
        self,
        source: str,
        target: str,
        source_etag: str | None,
        text: str,
        original: str | None,
    ) -> bool:
        """Undo a published target only while both observations still match."""
        from markdown_vault_mcp.hashing import compute_etag

        try:
            note = self.doc._read_note(source, capped=False)
            if note is None or note.etag != source_etag:
                return False
            written_etag = compute_etag(text.encode("utf-8"))
            if original is None:
                self.doc.delete(
                    target, if_match=written_etag, allow_protected=self.allow_protected
                )
            else:
                with self.doc.exact_byte_writes():
                    self.doc.write(
                        target,
                        original,
                        if_match=written_etag,
                        allow_overwrite=True,
                        allow_protected=self.allow_protected,
                    )
            return True
        except (MarkdownMCPError, OSError):
            return False


class _LogMigrationInterrupted(InvalidRequestError):
    """A target may have changed before source removal failed."""

    partial: LogMigration = LogMigration((), (), (), ())
    pending: tuple[str, ...] = ()
