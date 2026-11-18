"""Build an OKF bundle from a policy-visible live inventory."""

from __future__ import annotations

import io
import logging
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
from markdown_vault_mcp.okf import convert_wikilinks_to_markdown
from markdown_vault_mcp.okf_export_conformance import audit_export
from markdown_vault_mcp.okf_export_paths import ExportPaths, convert_body_wikilinks
from markdown_vault_mcp.okf_export_report import OkfExportReport
from markdown_vault_mcp.scanner import strip_frontmatter_block
from markdown_vault_mcp.utils.content_kind import effective_attachment_extensions
from markdown_vault_mcp.utils.fs import open_directory_nofollow

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.bundle_inventory import BundleEntry, BundleInspection
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.vault import Vault

logger = logging.getLogger(__name__)

# A fixed zip entry timestamp so re-exporting an unchanged vault yields identical
# bytes (the DOS epoch zipfile clamps to). Export fidelity, not wall-clock.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class OkfBundleResult:
    """Outcome of a bundle build: the zip bytes plus what went into it.

    Attributes:
        data: The zip archive bytes.
        notes: Number of notes written into the bundle.
        attachments: Number of attachments written into the bundle.
        excluded: Number of files omitted by selection policy, counted once.
        report: Artifact format verdict, admission and omission observations.
    """

    data: bytes
    notes: int
    attachments: int
    excluded: int
    report: OkfExportReport


def _read_safe(source: Path, path: str) -> bytes:
    """Read one regular file without following any path component."""
    parts = path.split("/")
    with open_directory_nofollow(source, parts[:-1]) as parent:
        fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("Regular file required")
            return stream.read()


def _omission_reasons(
    item: BundleEntry,
    config: ProjectConfig,
    *,
    include_index_excluded: bool,
    include_hidden: bool,
) -> list[str]:
    reasons = []
    if item.visibility == "index_excluded" and not include_index_excluded:
        reasons.append("index_excluded")
    if item.hidden_path and not include_hidden:
        reasons.append("hidden")
    # Reserved names retain navigation/history roles even when conventions.
    if config.content.conventions_file == item.path.rsplit("/", 1)[-1]:
        reasons.append("conventions")
    root = (config.content.templates_folder or "").rstrip("/")
    if root and (item.path == root or item.path.startswith(root + "/")):
        reasons.append("templates")
    return reasons


def _observe_export(item: BundleEntry, report: OkfExportReport) -> None:
    if item.parse_state == "not_inspected":
        report.uninspected[item.path] = item.reason or "not_inspected"
    if item.role != "concept_candidate":
        return
    if item.search_state == "non_admitted":
        report.local_policy.non_admitted.append(item.path)
    elif item.search_state != "searchable":
        report.local_policy.unknown.append(item.path)


def _scope(folder: str) -> str:
    """Normalize redundant POSIX separators and dot components."""
    scope = PurePosixPath(folder)
    return str(scope) if scope.parts else ""


def _require_complete(page: BundleInspection, folder: str) -> None:
    """Refuse a page that could omit unobservable descendants."""
    if page.state != "inspected" or page.enumeration not in {
        "complete",
        "entry_limit",
    }:
        failed = next(
            (
                entry
                for entry in page.entries
                if entry.role == "directory" and entry.state == "unavailable"
            ),
            None,
        )
        path = failed.path if failed else folder or "(root)"
        reason = (
            (failed.reason or failed.state)
            if failed
            else (page.state if page.state != "inspected" else page.enumeration)
        )
        location = f"at {path}" if failed else "(path not reported)"
        raise ValueError(
            f"Cannot export scope {folder or '(root)'}: inventory unavailable {location}: {reason}. "
            "Check permissions, traversal limits and state paths."
        )


def _note_bytes(
    raw: bytes,
    selection: tuple[BundleEntry, str],
    vault: Vault,
    paths: ExportPaths,
    report: OkfExportReport,
) -> bytes:
    """Convert graph links and relocate body and frontmatter paths."""
    item, name = selection
    if paths.scope and (b"\x00" in raw or raw.startswith((b"\xff\xfe", b"\xfe\xff"))):
        report.uninspected.setdefault(item.path, "body_encoding")
        return raw
    try:
        content = raw.decode("utf-8", "surrogateescape" if paths.scope else "strict")
    except UnicodeDecodeError:
        return raw
    body = strip_frontmatter_block(content.removeprefix("\ufeff"))
    prefix = content[: len(content) - len(body)]
    if item.search_state == "searchable":
        outlinks = vault.graph.get_outlinks(item.path)
        if paths.scope:
            body = convert_body_wikilinks(body, outlinks)
        else:
            body, _, _ = convert_wikilinks_to_markdown(body, outlinks)
    prefix = paths.relocate_frontmatter(prefix, item.path, report)
    body, dependencies = paths.relocate(body, item.path)
    report.dependencies.extend(dependencies)
    result = (prefix + body).encode("utf-8", "surrogateescape")
    if result != raw:
        report.transformed.append(name)
    return result


def build_okf_bundle(
    vault: Vault,
    config: ProjectConfig,
    *,
    folder: str = "",
    include_index_excluded: bool = False,
    include_hidden: bool = False,
    external_base: str | None = None,
) -> OkfBundleResult:
    """Export the selected inventory and assess the final OKF v0.2 artifact.

    Args:
        vault: Live vault supplying the inventory and link graph.
        config: Convention and template configuration.
        folder: Vault-relative scope root, or empty for the entire vault.
        include_index_excluded: Include paths omitted from the search index.
        include_hidden: Include dot-paths other than internal state.
        external_base: Absolute HTTP(S) URL prefix without a query or fragment
            for body and frontmatter destinations outside the selection.

    Returns:
        Archive bytes, counts, and an artifact conformance report.

    Raises:
        ValueError: If the scope is invalid or inventory cannot enumerate it,
            including failures under otherwise omitted hidden paths, or if an
            outside body or frontmatter destination collides with an exported
            file or directory, or the external URL prefix is invalid.
        OSError: If a selected file cannot be read.
    """
    folder = _scope(folder)
    report = OkfExportReport()
    buf = io.BytesIO()
    notes = attachments = excluded = 0
    cursor = None
    selected = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        while True:
            page = vault.reader.inspect_bundle_tree(
                BundleTreeRequest(root=folder, entry_limit=1000, cursor=cursor)
            )
            _require_complete(page, folder)
            for item in page.entries:
                if item.role == "directory":
                    continue
                reasons = _omission_reasons(
                    item,
                    config,
                    include_index_excluded=include_index_excluded,
                    include_hidden=include_hidden,
                )
                for reason in reasons:
                    report.omitted[reason] = report.omitted.get(reason, 0) + 1
                if reasons:
                    excluded += 1
                    continue
                if item.state in {"unavailable", "missing"}:
                    raise OSError(
                        f"Cannot export {item.path}: {item.reason or item.state}"
                    )
                selected.append(item)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        def inventory() -> set[str]:
            cursor = None
            candidates: set[str] = set()
            while True:
                page = vault.reader.inspect_bundle_tree(
                    BundleTreeRequest(root="", entry_limit=1000, cursor=cursor)
                )
                _require_complete(page, "")
                candidates.update(
                    item.path
                    for item in page.entries
                    if item.role not in {"directory", "unknown"}
                    and item.state not in {"unavailable", "missing"}
                )
                if page.next_cursor is None:
                    return candidates
                cursor = page.next_cursor

        paths = ExportPaths(
            folder,
            {item.path for item in selected},
            external_base,
            inventory,
            effective_attachment_extensions(config.attachment_extensions),
        )
        for item in selected:
            raw = _read_safe(vault.source_dir, item.path)
            name = item.path[len(folder) + 1 :] if folder else item.path
            if item.role == "supporting":
                _write(zf, name, raw)
                attachments += 1
            else:
                _observe_export(item, report)
                _write(zf, name, _note_bytes(raw, (item, name), vault, paths, report))
                notes += 1
    logger.info(
        "okf_bundle_built folder=%s notes=%d attachments=%d excluded=%d",
        folder or "(root)",
        notes,
        attachments,
        excluded,
    )
    data = buf.getvalue()
    audit_export(data, report)
    return OkfBundleResult(data, notes, attachments, excluded, report)


def _write(zf: zipfile.ZipFile, path: str, data: bytes) -> None:
    """Write one archive entry with a fixed timestamp for reproducible bytes."""
    info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)
