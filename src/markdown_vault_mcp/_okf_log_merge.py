"""Union date-grouped Git conflicts and migrate descendant logs."""

from __future__ import annotations

import dataclasses
import datetime
import re
from typing import TYPE_CHECKING, Any

from markdown_it import MarkdownIt

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import _LOG_HEADING_RE
from markdown_vault_mcp.scanner import (
    _collect_reference_definitions,
    _is_external_target,
    _normalise_line_endings,
    _resolve_link_path,
    extract_links,
    strip_frontmatter_block,
)
from markdown_vault_mcp.utils.links import (
    apply_link_replacement,
    compute_new_raw_target,
    decode_markdown_destination,
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


@dataclasses.dataclass(frozen=True)
class LogMigration:
    """Merged or mergeable logs and sources kept with their reasons."""

    merged: tuple[LogMove, ...]
    kept: tuple[tuple[str, str], ...]
    target_frontmatter: tuple[str, ...]
    source_frontmatter: tuple[str, ...]
    unrewritten: tuple[tuple[str, str], ...]


def _relocation_notes(text: str) -> set[str]:
    """Describe URLs the shared rewriter does not change."""
    notes: set[str] = set()
    for token in MarkdownIt("commonmark").parse(text):
        for item in (token, *(token.children or [])):
            if (
                item.type in {"html_block", "html_inline"}
                and not item.content.lstrip().startswith("<!--")
                and re.search(
                    r"\b(?:href|src|srcset)\s*=|url\s*\(", item.content, re.IGNORECASE
                )
            ):
                notes.add("HTML URL not rewritten; check after merge")
            if item.type == "image":
                destination = item.attrGet("src")
                if destination is not None and not (
                    _is_external_target(str(destination))
                    or str(destination).startswith(("/", "#"))
                ):
                    notes.add("relative image URL not rewritten; check after merge")
    return notes


def _migration_text(
    target: str | None, source: str, source_path: str = "", target_path: str = ""
) -> tuple[str | None, int, str]:
    """Merge entries without deduplication; refuse content that cannot move safely."""
    if source_path:
        source = _relocate_links(source, source_path, target_path)
    right, reason = _parse(source)
    if right is None:
        return None, 0, reason
    if target is None:
        return (
            source[len(right.prefix) :],
            sum(len(s.entries) for s in right.sections),
            "",
        )
    left, reason = _parse(target)
    if left is None:
        return None, 0, "target: " + reason
    if right.preamble and _normalise_line_endings(
        left.preamble
    ) != _normalise_line_endings(right.preamble):
        eol_match = re.search(r"\r\n|\r|\n", target)
        eol = eol_match.group() if eol_match else "\n"
        first = left.sections[0].start
        left.lines[first:first] = [right.preamble + eol + eol]
        for section in left.sections:
            section.start += 1
            section.end += 1
    return _combine_migration(left, right, target)


def _relocate_links(source: str, source_path: str, target_path: str) -> str:
    """Use the shared link scanner and rewriter to retain relative destinations."""
    from urllib.parse import quote

    for raw in _collect_reference_definitions(_normalise_line_endings(source)).values():
        decoded = decode_markdown_destination(raw)
        if _is_external_target(decoded) or decoded.startswith(("/", "#")):
            continue
        resolved, fragment = _resolve_link_path(raw, source_path, decode_markdown=True)
        if resolved == source_path and target_path:
            resolved = target_path
        destination = "/" + quote(resolved, safe="/-._~")
        if fragment:
            destination += "#" + fragment
        source = apply_link_replacement(source, "reference", raw, destination)
    for link in extract_links(source, source_path, attachment_extensions=frozenset()):
        resolved = (
            target_path
            if link.target_path == source_path and target_path
            else link.target_path
        )
        if link.link_type == "wikilink":
            if (
                link.raw_target.startswith(("./", "../"))
                or resolved != link.target_path
            ):
                destination = compute_new_raw_target(
                    "wikilink", link.raw_target, link.fragment, resolved
                )
                source = apply_link_replacement(
                    source, "wikilink", link.raw_target, destination
                )
            continue
        if link.link_type not in {
            "markdown",
            "reference",
        } or (link.raw_target.startswith("/") and resolved == link.target_path):
            continue
        destination = "/" + quote(resolved, safe="/-._~")
        if link.fragment:
            destination += "#" + link.fragment
        source = apply_link_replacement(
            source, link.link_type, link.raw_target, destination
        )
    return source


def _combine_migration(
    left: _Log, right: _Log, target: str
) -> tuple[str | None, int, str]:
    """Insert every source entry into the target's dated sections."""
    eol_match = re.search(r"\r\n|\r|\n", target)
    eol = eol_match.group() if eol_match else "\n"
    insertions, _, count = _insertions(left, right, eol, dedupe=False)
    output = left.lines[:]
    for pos in sorted(insertions, reverse=True):
        if pos and output[pos - 1] and not output[pos - 1].endswith(("\n", "\r")):
            output[pos - 1] += eol
        if (
            pos
            and output[pos - 1].strip(" \t\r\n")
            and insertions[pos][0].startswith("## ")
        ):
            insertions[pos][0] = eol + insertions[pos][0]
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
    import posixpath

    sources = [
        s
        for s in sources
        if posixpath.dirname(s) and not designation(posixpath.dirname(s))
    ]
    runner = _LogMigrationRunner(doc, designation, apply, allow_protected)
    with okf_write_suppressed(), doc.write_scope():
        for index, source in enumerate(sources):
            try:
                runner.process(source)
            except _LogMigrationInterrupted as exc:
                exc.completed = tuple(runner.merged)
                exc.pending = tuple(sources[index:])
                raise
    return LogMigration(
        tuple(runner.merged),
        tuple(runner.kept),
        tuple(sorted(runner.headers)),
        tuple(sorted(runner.source_headers)),
        tuple(sorted(runner.unrewritten)),
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
        self.unrewritten: set[tuple[str, str]] = set()
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
        relocated = _relocate_links(src.content, source, target)
        self.unrewritten.update((source, note) for note in _relocation_notes(relocated))
        raw_target = self.doc._okf_author_snapshot(target)[1] if dst else None
        previous = self.simulated.get(target, raw_target)
        if previous is not None:
            source_log, _ = _parse(relocated)
            target_log, _ = _parse(previous)
            if source_log is not None and target_log is not None:
                for label, (href, _) in source_log.refs.items():
                    if label in target_log.refs and target_log.refs[label][0] != href:
                        self.unrewritten.add(
                            (
                                source,
                                f"reference {label!r} points to {href!r} but the target defines {target_log.refs[label][0]!r}; source links use the target definition",
                            )
                        )
        if previous is not None and strip_frontmatter_block(previous) != previous:
            self.headers.add(target)
        text, count, reason = _migration_text(previous, src.content, source, target)
        if text is None:
            raise InvalidRequestError(reason)
        if self.apply:
            self._write(
                source, target, text, src.etag, (dst.etag if dst else None, raw_target)
            )
        else:
            self.simulated[target] = text
        self.merged.append(LogMove(source, target, count))

    def _write(
        self,
        source: str,
        target: str,
        text: str,
        source_etag: str | None,
        target_state: tuple[str | None, str | None],
    ) -> None:
        from markdown_vault_mcp.exceptions import MarkdownMCPError

        target_etag, original = target_state
        try:
            with self.doc.exact_byte_writes():
                self.doc.write(
                    target,
                    text,
                    if_match=target_etag,
                    allow_overwrite=True,
                    create_only=target_etag is None,
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
        from markdown_vault_mcp.exceptions import MarkdownMCPError
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

    completed: tuple[LogMove, ...] = ()
    pending: tuple[str, ...] = ()
