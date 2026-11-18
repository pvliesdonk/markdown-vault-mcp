"""Relocate Markdown body destinations for a subtree export."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import quote, urlsplit

from markdown_vault_mcp.okf_export_report import Dependency
from markdown_vault_mcp.scanner import (
    _RE_FENCED_CODE,
    _RE_INLINE_CODE,
    _blank_spans,
    _definition_destination_end,
    _iter_definition_matches,
    _paragraph_regions,
)
from markdown_vault_mcp.utils.links import (
    decode_markdown_destination,
    iter_inline_links,
)


def relocate_body(
    body: str, source: str, scope: str, members: set[str], external_base: str | None
) -> tuple[str, list[Dependency]]:
    """Relocate destinations and report references outside the selected files.

    Args:
        body: Markdown body, without frontmatter.
        source: Vault-relative source path.
        scope: Vault-relative selected root.
        members: Selected vault-relative file paths.
        external_base: Optional URL prefix for outside references.

    Returns:
        Rewritten body and unresolved dependencies.

    Raises:
        ValueError: When an outside reference would point at another member.
    """
    if not scope:
        return body, []
    masked = body
    for pattern in (_RE_FENCED_CODE, _RE_INLINE_CODE):
        masked = pattern.sub(lambda m: re.sub(r"[^\r\n]", " ", m.group()), masked)
    changes: list[tuple[int, int, str]] = []
    dependencies: list[Dependency] = []
    bundle_source = source[len(scope) + 1 :]
    bundle_members = {p[len(scope) + 1 :] for p in members}
    spans: list[tuple[int, int]] = []
    definition_spans = []
    for label, tail, start, end in _iter_definition_matches(masked):
        if label.startswith("^"):
            continue
        target_start = masked.find(tail, start)
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
    for start, end in sorted(spans):
        raw = body[start:end]
        decoded = decode_markdown_destination(raw)
        parsed = urlsplit(decoded)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        path = parsed.path
        target = posixpath.normpath(
            path.lstrip("/")
            if path.startswith("/")
            else posixpath.join(posixpath.dirname(source), path)
        )
        suffix = ("?" + parsed.query if parsed.query else "") + (
            "#" + parsed.fragment if parsed.fragment else ""
        )
        if target in members:
            if not path.startswith("/"):
                continue
            replacement = "/" + quote(target[len(scope) + 1 :], safe="/") + suffix
        else:
            dependencies.append(Dependency(source, raw))
            if external_base:
                replacement = (
                    external_base.rstrip("/")
                    + "/"
                    + quote(target.lstrip("/"), safe="/")
                    + suffix
                )
            else:
                apparent = posixpath.normpath(
                    path.lstrip("/")
                    if path.startswith("/")
                    else posixpath.join(posixpath.dirname(bundle_source), path)
                )
                if apparent in bundle_members:
                    raise ValueError(f"Export collision in {source}: {raw}")
                continue
        if raw.startswith("<") and raw.endswith(">"):
            replacement = "<" + replacement + ">"
        changes.append((start, end, replacement))
    for start, end, replacement in reversed(changes):
        body = body[:start] + replacement + body[end:]
    return body, dependencies
