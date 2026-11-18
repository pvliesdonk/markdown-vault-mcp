"""Assess final archive format; see docs/design/reference/okf-v0.2.md."""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from datetime import date
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import frontmatter
import yaml
from frontmatter.default_handlers import YAMLHandler
from markdown_it import MarkdownIt

from markdown_vault_mcp.okf import _index_frontmatter_conforms, is_okf_reserved_file
from markdown_vault_mcp.okf_export_report import (
    Dependency,
    FormatFinding,
    OkfExportReport,
)
from markdown_vault_mcp.scanner import (
    extract_links,
    parse_frontmatter,
    strip_frontmatter_block,
)

if TYPE_CHECKING:
    from markdown_it.token import Token

_MARKDOWN = MarkdownIt("commonmark")


def _index_body_conforms(body: str) -> bool:
    in_heading = False
    in_item = False
    for token in _MARKDOWN.parse(body):
        if token.type == "heading_open":
            in_heading = True
        elif token.type == "heading_close":
            in_heading = False
        elif token.type == "list_item_open":
            if in_item:
                return False
            in_item = True
        elif token.type == "list_item_close":
            in_item = False
        elif token.type == "inline" and not in_heading:
            if not in_item or not _index_entry_conforms(token.children or []):
                return False
        elif token.type not in {
            "bullet_list_open",
            "bullet_list_close",
            "paragraph_open",
            "paragraph_close",
            "inline",
        }:
            return False
    return True


def _index_entry_conforms(children: list[Token]) -> bool:
    if not children or children[0].type != "link_open":
        return False
    ends = [i for i, token in enumerate(children) if token.type == "link_close"]
    if not ends or ends[0] < 2:
        return False
    tail = children[ends[0] + 1 :]
    return not tail or (tail[0].type == "text" and tail[0].content.startswith(" - "))


def _log_body_conforms(body: str) -> bool:
    tokens = _MARKDOWN.parse(body)
    previous: date | None = None
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        value = tokens[index + 1].content
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return False
        try:
            current = date.fromisoformat(value)
        except ValueError:
            return False
        if previous is not None and current > previous:
            return False
        previous = current
    return True


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
    elif not _log_body_conforms(body):
        report.format_findings.append(FormatFinding(path, "log_heading_shape"))


def _checks_for(path: str) -> list[str]:
    name = PurePosixPath(path).name
    if name == "index.md":
        return ["index_frontmatter", "index_entry_shape"]
    if name == "log.md":
        return ["log_date_headings"]
    return ["yaml_frontmatter", "type"]


def _inspect(path: str, raw: bytes, report: OkfExportReport, members: set[str]) -> None:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        report.format_findings.append(FormatFinding(path, "invalid_encoding"))
        report.unchecked[path] = _checks_for(path)
        return
    reserved = is_okf_reserved_file(path)
    body = strip_frontmatter_block(text)
    if PurePosixPath(path).name == "log.md":
        _inspect_reserved(path, text, body, {}, report)
        _find_broken_links(path, body, members, report)
        return
    try:
        post = parse_frontmatter(text)
    except (yaml.YAMLError, ValueError):
        report.format_findings.append(FormatFinding(path, "malformed_frontmatter"))
        if reserved:
            if path == "index.md":
                report.unchecked[path] = ["index_frontmatter"]
            else:
                report.format_findings.append(FormatFinding(path, "index_frontmatter"))
            if not _index_body_conforms(body):
                report.format_findings.append(FormatFinding(path, "index_entry_shape"))
        _find_broken_links(path, body, members, report)
        return
    metadata = dict(post.metadata)
    if reserved:
        _inspect_reserved(path, text, body, metadata, report)
    else:
        _inspect_concept(path, text, body, metadata, report)
    _find_broken_links(path, body, members, report)


def _local_destination(path: str, destination: str) -> str | None:
    try:
        parts = urlsplit(destination)
    except ValueError:
        return None
    if parts.scheme or parts.netloc or not parts.path:
        return None
    target = unquote(parts.path)
    base = "" if target.startswith("/") else posixpath.dirname(path)
    return posixpath.normpath(posixpath.join(base, target.lstrip("/")))


def _markdown_destinations(body: str) -> list[str]:
    destinations = []
    for token in _MARKDOWN.parse(body):
        for child in token.children or []:
            if child.type == "link_open":
                destination = child.attrGet("href")
            elif child.type == "image":
                destination = child.attrGet("src")
            else:
                continue
            if isinstance(destination, str):
                destinations.append(destination)
    return destinations


def _find_broken_links(
    path: str, body: str, members: set[str], report: OkfExportReport
) -> None:
    targets = [_local_destination(path, dest) for dest in _markdown_destinations(body)]
    targets.extend(
        link.target_path
        for link in extract_links(body, path)
        if link.link_type == "wikilink"
    )
    for target in dict.fromkeys(targets):
        if (
            target
            and target not in members
            and not any(name.startswith(target.rstrip("/") + "/") for name in members)
        ):
            report.broken_links.append(Dependency(path, target))


def audit_export(data: bytes, report: OkfExportReport) -> None:
    """Audit the actual ZIP members and set the three-state format verdict.

    Args:
        data: Final archive bytes.
        report: Export report to populate with artifact observations.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = set(archive.namelist())
        for path in sorted(members):
            if not path.endswith(".md"):
                continue
            try:
                raw = archive.read(path)
            except (OSError, zipfile.BadZipFile, RuntimeError):
                report.unchecked[path] = _checks_for(path)
                continue
            _inspect(path, raw, report, members)
    report.verdict = (
        "incomplete"
        if report.unchecked
        else ("nonconformant" if report.format_findings else "conformant")
    )
