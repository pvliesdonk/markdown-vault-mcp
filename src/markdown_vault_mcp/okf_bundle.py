"""Build an OKF bundle from a policy-visible live inventory."""

from __future__ import annotations

import io
import logging
import os
import stat
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
from markdown_vault_mcp.okf import convert_wikilinks_to_markdown
from markdown_vault_mcp.okf_export_report import FormatFinding, OkfExportReport
from markdown_vault_mcp.utils.fs import open_directory_nofollow

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.bundle_inventory import BundleEntry
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
        report: Partial v0.2 format, admission and omission observations.
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
    if item.path.rsplit("/", 1)[-1] == config.content.conventions_file:
        reasons.append("conventions")
    root = (config.content.templates_folder or "").rstrip("/")
    if root and (item.path == root or item.path.startswith(root + "/")):
        reasons.append("templates")
    return reasons


def _observe_export(item: BundleEntry, report: OkfExportReport) -> None:
    if item.role != "concept_candidate":
        return
    if item.search_state == "non_admitted":
        report.local_policy.non_admitted.append(item.path)
    if item.parse_state in {"invalid_encoding", "malformed_metadata"}:
        report.format_findings.append(FormatFinding(item.path, item.parse_state))
    elif item.parse_state == "valid" and item.type_state in {"absent", "invalid"}:
        report.format_findings.append(
            FormatFinding(item.path, "missing_or_invalid_type")
        )
    elif item.parse_state == "not_inspected":
        report.uninspected[item.path] = item.reason or "not_inspected"


def build_okf_bundle(
    vault: Vault,
    config: ProjectConfig,
    *,
    folder: str = "",
    include_index_excluded: bool = False,
    include_hidden: bool = False,
) -> OkfBundleResult:
    """Export the selected inventory with partial OKF v0.2 observations.

    Args:
        vault: Live vault supplying the inventory and link graph.
        config: Vault root and convention and template configuration.
        folder: Vault-relative scope root, or empty for the entire vault.
        include_index_excluded: Include paths omitted from the search index.
        include_hidden: Include dot-paths other than internal state.

    Returns:
        Archive bytes, counts, and a partial report; no conformance verdict.
    """
    report = OkfExportReport()
    buf = io.BytesIO()
    notes = attachments = excluded = 0
    cursor = None
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        while True:
            page = vault.reader.inspect_bundle_tree(
                BundleTreeRequest(root=folder, entry_limit=1000, cursor=cursor)
            )
            if page.state != "inspected" or page.enumeration not in {
                "complete",
                "entry_limit",
            }:
                report.enumeration_incomplete = True
            for item in page.entries:
                if item.role == "directory":
                    continue
                reasons = _omission_reasons(
                    item,
                    config,
                    include_index_excluded=include_index_excluded,
                    include_hidden=include_hidden,
                )
                if reasons:
                    excluded += 1
                    for reason in reasons:
                        report.omitted[reason] = report.omitted.get(reason, 0) + 1
                    continue
                if item.state in {"unavailable", "missing"}:
                    report.uninspected[item.path] = item.reason or item.state
                    continue
                try:
                    raw = _read_safe(config.source_dir, item.path)
                except OSError:
                    report.uninspected[item.path] = "read_unavailable"
                    continue
                if item.role == "supporting":
                    _write(zf, item.path, raw)
                    attachments += 1
                else:
                    _observe_export(item, report)
                    try:
                        content = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        _write(zf, item.path, raw)
                    else:
                        if item.search_state == "searchable":
                            content, _, _ = convert_wikilinks_to_markdown(
                                content, vault.graph.get_outlinks(item.path)
                            )
                        _write(zf, item.path, content.encode("utf-8"))
                    notes += 1
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
    logger.info(
        "okf_bundle_built folder=%s notes=%d attachments=%d excluded=%d",
        folder or "(root)",
        notes,
        attachments,
        excluded,
    )
    return OkfBundleResult(buf.getvalue(), notes, attachments, excluded, report)


def _write(zf: zipfile.ZipFile, path: str, data: bytes) -> None:
    """Write one archive entry with a fixed timestamp for reproducible bytes."""
    info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)
