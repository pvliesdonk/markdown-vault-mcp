"""Assess final archive format; see docs/design/reference/okf-v0.2.md."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import frontmatter
import yaml
from frontmatter.default_handlers import YAMLHandler
from markdown_it import MarkdownIt

from markdown_vault_mcp.okf import _DATE_ONLY_RE, _index_frontmatter_conforms
from markdown_vault_mcp.okf_export_paths import ArtifactLinks
from markdown_vault_mcp.okf_export_report import (
    Dependency,
    FormatFinding,
    OkfExportReport,
)
from markdown_vault_mcp.scanner import strip_frontmatter_block

if TYPE_CHECKING:
    from markdown_it.token import Token

_MARKDOWN = MarkdownIt("commonmark")


def _index_body_conforms(body: str) -> bool:
    tokens = _MARKDOWN.parse(body)
    index = 0
    while index < len(tokens):
        kind = tokens[index].type
        if kind == "heading_open":
            index += 3
        elif kind in {"bullet_list_open", "bullet_list_close"}:
            index += 1
        elif kind == "list_item_open":
            item = tokens[index : index + 5]
            if [t.type for t in item] != [
                "list_item_open",
                "paragraph_open",
                "inline",
                "paragraph_close",
                "list_item_close",
            ]:
                return False
            if not _index_entry_conforms(item[2].children or []):
                return False
            index += 5
        else:
            return False
    return True


def _index_entry_conforms(children: list[Token]) -> bool:
    if not children or children[0].type != "link_open":
        return False
    ends = [i for i, token in enumerate(children) if token.type == "link_close"]
    if not ends or not any(token.content.strip() for token in children[1 : ends[0]]):
        return False
    tail = children[ends[0] + 1 :]
    return not tail or (tail[0].type == "text" and tail[0].content.startswith(" - "))


def _log_date(value: str, previous: date | None) -> date | None:
    if not _DATE_ONLY_RE.fullmatch(value):
        return None
    try:
        current = date.fromisoformat(value)
    except ValueError:
        return None
    return current if previous is None or current <= previous else None


def _log_problem(body: str) -> str | None:
    tokens = _MARKDOWN.parse(body)
    previous: date | None = None
    for index, token in enumerate(tokens):
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            if token.level != 0 or token.type != "bullet_list_open" or previous is None:
                return "log_entry_shape"
        elif token.level == 0 and token.nesting == 1:
            if token.type == "heading_open":
                if token.tag == "h1" and previous is None:
                    continue
                if token.tag != "h2":
                    return "log_heading_shape"
                previous = _log_date(tokens[index + 1].content, previous)
                if previous is None:
                    return "log_heading_shape"
            else:
                return "log_entry_shape"
        elif token.level == 0 and token.nesting == 0:
            return "log_entry_shape"
    return None


def _inspect_concept(
    path: str, text: str, body: str, metadata: dict[str, Any], report: OkfExportReport
) -> None:
    handler = frontmatter.detect_format(text.lstrip(), frontmatter.handlers)
    if body == text or not isinstance(handler, YAMLHandler):
        report.format_findings.append(FormatFinding(path, "missing_yaml_frontmatter"))
    elif (
        not isinstance(note_type := metadata.get("type"), str) or not note_type.strip()
    ):
        report.format_findings.append(FormatFinding(path, "missing_or_invalid_type"))
    if any(
        not metadata.get(key) for key in ("title", "description", "resource", "tags")
    ):
        report.optional_metadata.append(path)


def _inspect_reserved(
    path: str, text: str, body: str, metadata: dict[str, Any], report: OkfExportReport
) -> None:
    if PurePosixPath(path).name == "index.md":
        if not _index_frontmatter_conforms(path, text, metadata):
            report.format_findings.append(FormatFinding(path, "index_frontmatter"))
        if not _index_body_conforms(body):
            report.format_findings.append(FormatFinding(path, "index_entry_shape"))
    elif problem := _log_problem(body):
        report.format_findings.append(FormatFinding(path, problem))


_ROLE_CHECKS = {
    "index.md": ["index_frontmatter", "index_entry_shape"],
    "log.md": ["log_date_headings", "log_entry_shape"],
    "concept": ["yaml_frontmatter", "type"],
}


def _role(path: str) -> str:
    name = PurePosixPath(path).name
    return name if name in _ROLE_CHECKS else "concept"


def _inspect(
    path: str, raw: bytes, report: OkfExportReport, links: ArtifactLinks
) -> None:
    role = _role(path)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        report.unchecked[path] = _ROLE_CHECKS[role].copy()
        return
    body = strip_frontmatter_block(text)
    report.broken_links.extend(
        Dependency(path, target) for target in links.unresolved(body, path)
    )
    if role == "log.md":
        _inspect_reserved(path, text, body, {}, report)
        return
    try:
        # Parsing directly preserves arbitrary YAML keys without Post's keyword expansion.
        metadata, _ = frontmatter.parse(text)
    except (yaml.YAMLError, ValueError):
        if role == "index.md":
            _inspect_reserved(path, text, body, {}, report)
        else:
            report.format_findings.append(FormatFinding(path, "malformed_frontmatter"))
        return
    if role == "index.md":
        _inspect_reserved(path, text, body, metadata, report)
    else:
        _inspect_concept(path, text, body, metadata, report)


def _audit_member(
    archive: zipfile.ZipFile, path: str, report: OkfExportReport, links: ArtifactLinks
) -> None:
    try:
        raw = archive.read(path)
    except (OSError, zipfile.BadZipFile, RuntimeError):
        report.unchecked[path] = _ROLE_CHECKS[_role(path)].copy()
        return
    try:
        _inspect(path, raw, report, links)
    except (RecursionError, OverflowError, MemoryError):
        report.unchecked[path] = _ROLE_CHECKS[_role(path)].copy()


def audit_export(
    data: bytes, report: OkfExportReport, extensions: frozenset[str]
) -> None:
    """Audit the actual ZIP members and set the three-state format verdict.

    Args:
        data: Final archive bytes.
        report: Export report to populate with artifact observations.
        extensions: Effective attachment extensions.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = set(archive.namelist())
        directories = {"", "."}
        for name in members:
            directories.update(str(parent) for parent in PurePosixPath(name).parents)
        links = ArtifactLinks(members, directories, extensions)
        for path in sorted(members):
            if not path.endswith(".md"):
                continue
            _audit_member(archive, path, report, links)
    report.verdict = (
        "incomplete"
        if report.unchecked
        else ("nonconformant" if report.format_findings else "conformant")
    )
