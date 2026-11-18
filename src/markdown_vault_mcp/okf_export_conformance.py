"""Assess OKF v0.2 format against the final archive, independently of inventory."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from pathlib import PurePosixPath

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

_MARKDOWN = MarkdownIt("commonmark")


def _index_body_conforms(body: str) -> bool:
    tokens = _MARKDOWN.parse(body)
    in_heading = False
    in_item = False
    for token in tokens:
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
            children = token.children or []
            if not in_item or not children or children[0].type != "link_open":
                return False
            if not any(child.type == "link_close" for child in children):
                return False
        elif token.type in {"fence", "code_block", "html_block", "ordered_list_open"}:
            return False
    return True


def _log_body_conforms(body: str) -> bool:
    tokens = _MARKDOWN.parse(body)
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        value = tokens[index + 1].content
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
    return True


def _inspect(path: str, raw: bytes, report: OkfExportReport, members: set[str]) -> None:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        report.format_findings.append(FormatFinding(path, "invalid_encoding"))
        report.unchecked[path] = ["frontmatter", "reserved_structure"]
        return
    reserved = is_okf_reserved_file(path)
    body = strip_frontmatter_block(text)
    has_block = body != text
    try:
        post = parse_frontmatter(text)
    except (yaml.YAMLError, ValueError):
        report.format_findings.append(FormatFinding(path, "malformed_frontmatter"))
        if reserved:
            report.unchecked[path] = ["reserved_structure"]
        return
    metadata = dict(post.metadata)
    if reserved:
        if PurePosixPath(path).name == "index.md":
            if not _index_frontmatter_conforms(path, text, metadata):
                report.format_findings.append(FormatFinding(path, "index_frontmatter"))
            if not _index_body_conforms(body):
                report.format_findings.append(FormatFinding(path, "index_entry_shape"))
        elif not _log_body_conforms(body):
            report.format_findings.append(FormatFinding(path, "log_heading_shape"))
    else:
        handler = frontmatter.detect_format(text.lstrip(), frontmatter.handlers)
        if not has_block or not isinstance(handler, YAMLHandler):
            report.format_findings.append(
                FormatFinding(path, "missing_yaml_frontmatter")
            )
        elif (
            not isinstance(note_type := metadata.get("type"), str)
            or not note_type.strip()
        ):
            report.format_findings.append(
                FormatFinding(path, "missing_or_invalid_type")
            )
        if not metadata.get("title") or not metadata.get("description"):
            report.optional_metadata.append(path)
    for link in extract_links(body, path):
        if link.target_path not in members:
            report.broken_links.append(Dependency(path, link.target_path))


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
                report.unchecked[path] = ["frontmatter", "reserved_structure"]
                continue
            _inspect(path, raw, report, members)
    report.verdict = (
        "incomplete"
        if report.unchecked
        else ("nonconformant" if report.format_findings else "conformant")
    )
