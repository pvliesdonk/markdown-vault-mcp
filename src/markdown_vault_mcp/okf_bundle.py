"""Build an OKF bundle from a Git commit or policy-visible live inventory."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import stat
import zipfile
from contextlib import closing, nullcontext
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from markdown_vault_mcp.bundle_inventory import BundleTreeRequest, _parsed
from markdown_vault_mcp.fts_index import _pick_wikilink_candidate
from markdown_vault_mcp.git.query import _LFS_POINTER_PREFIX
from markdown_vault_mcp.managers.git_query import (
    GitSnapshotUnavailableError,
    git_export_snapshot,
    git_snapshot_changes,
)
from markdown_vault_mcp.okf import convert_wikilinks_to_markdown
from markdown_vault_mcp.okf_export_conformance import audit_export
from markdown_vault_mcp.okf_export_paths import ExportPaths, convert_body_wikilinks
from markdown_vault_mcp.okf_export_report import OkfExportReport
from markdown_vault_mcp.scanner import (
    extract_links,
    parse_frontmatter,
    strip_frontmatter_block,
)
from markdown_vault_mcp.types import OutlinkInfo
from markdown_vault_mcp.utils.content_kind import effective_attachment_extensions
from markdown_vault_mcp.utils.fs import open_directory_nofollow
from markdown_vault_mcp.utils.text import decode_utf8

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from markdown_vault_mcp.bundle_inventory import BundleEntry, BundleInspection
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.managers.git_query import GitExportSnapshot
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
    paths: ExportPaths,
    report: OkfExportReport,
    resolver: Callable[[str, str], list[OutlinkInfo]] | None,
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
    if resolver is not None:
        outlinks = resolver(body, item.path)
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


@dataclass
class _SnapshotLinks:
    members: set[str]
    names: dict[str, list[str]]
    aliases: dict[str, list[str]]


def _snapshot_link_index(notes: Iterable[tuple[str, bytes]]) -> _SnapshotLinks:
    aliases: dict[str, list[str]] = {}
    names: dict[str, list[str]] = {}
    members: set[str] = set()
    for path, raw in notes:
        members.add(path)
        names.setdefault(path.rpartition("/")[2], []).append(path)
        try:
            metadata = parse_frontmatter(decode_utf8(raw)).metadata
        except (UnicodeError, ValueError):
            continue
        value = metadata.get("aliases") or metadata.get("alias")
        values = (
            [value]
            if isinstance(value, str)
            else value
            if isinstance(value, list)
            else []
        )
        for alias in values:
            if isinstance(alias, (str, int, float)) and str(alias).strip():
                aliases.setdefault(str(alias).strip().lower(), []).append(path)
    return _SnapshotLinks(members, names, aliases)


def _snapshot_outlinks(
    body: str,
    source: str,
    index: _SnapshotLinks,
    extensions: frozenset[str],
) -> list[OutlinkInfo]:
    result = []
    for link in extract_links(body, source, attachment_extensions=extensions):
        if link.link_type != "wikilink":
            continue
        target = link.target_path
        if (
            not link.raw_target.startswith(("./", "../"))
            and target not in index.members
        ):
            choices = [
                path
                for path in index.names.get(target.rpartition("/")[2], [])
                if path.endswith("/" + target)
            ]
            if not choices:
                choices = index.aliases.get(
                    link.raw_target.split("#", 1)[0].lower(), []
                )
            if choices:
                target = _pick_wikilink_candidate(choices, source)
        result.append(
            OutlinkInfo(
                target,
                link.link_text,
                "wikilink",
                link.fragment,
                link.raw_target,
                target in index.members,
            )
        )
    return result


def _snapshot_searchable(entry: BundleEntry, raw: bytes) -> bool:
    if entry.visibility != "visible":
        return False
    if entry.reason == "note_limit":
        return _parsed(entry.path, entry.role, raw).parse_state == "valid"
    return entry.parse_state == "valid"


# The public API keeps its established explicit options.
def build_okf_bundle(  # noqa: PLR0913, RUF100
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
        vault: Vault supplying the source and, without Git, the live link graph.
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
            file or directory, the external URL prefix is invalid, or the Git
            snapshot cannot be read.
        OSError: If a selected file cannot be read.
    """
    snapshot = git_export_snapshot(vault.source_dir)
    with closing(snapshot) if snapshot is not None else nullcontext():
        return _build_okf_bundle(
            vault,
            config,
            snapshot,
            (folder, include_index_excluded, include_hidden, external_base),
        )


# The established export workflow shares one pinned snapshot and policy.
def _build_okf_bundle(  # noqa: C901, PLR0912, PLR0915, RUF100
    vault: Vault,
    config: ProjectConfig,
    snapshot: GitExportSnapshot | None,
    options: tuple[str, bool, bool, str | None],
) -> OkfBundleResult:
    folder, include_index_excluded, include_hidden, external_base = options
    folder = _scope(folder)
    if snapshot is not None:
        for path, (mode, _) in snapshot.entries.items():
            if mode == "160000" and (folder == path or folder.startswith(path + "/")):
                raise GitSnapshotUnavailableError(
                    f"Git submodule {path} is unavailable; run git submodule update --init and retry."
                )
    report = OkfExportReport()
    if snapshot is not None:
        report.snapshot.commit = snapshot.commit
    buf = io.BytesIO()
    notes = attachments = excluded = 0
    cursor = None
    selected = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        while True:
            page = vault.reader.inspect_bundle_tree(
                BundleTreeRequest(
                    root=folder,
                    entry_limit=1000,
                    cursor=cursor,
                    snapshot=snapshot,
                )
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
                    if item.reason == "submodule":
                        raise GitSnapshotUnavailableError(
                            f"Git submodule {item.path} is unavailable; run git submodule update --init and retry."
                        )
                    raise OSError(
                        f"Cannot export {item.path}: {item.reason or item.state}"
                    )
                selected.append(item)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        root_entries: list[BundleEntry] | None = None

        def root_inventory() -> list[BundleEntry]:
            nonlocal root_entries
            if root_entries is None:
                root_entries = []
                cursor = None
                while True:
                    page = vault.reader.inspect_bundle_tree(
                        BundleTreeRequest(
                            root="",
                            entry_limit=1000,
                            cursor=cursor,
                            snapshot=snapshot,
                        )
                    )
                    _require_complete(page, "")
                    root_entries.extend(page.entries)
                    if page.next_cursor is None:
                        break
                    cursor = page.next_cursor
            return root_entries

        def inventory() -> set[str]:
            return {
                item.path
                for item in root_inventory()
                if item.role not in {"directory", "unknown"}
                and item.state not in {"unavailable", "missing"}
            }

        extensions = effective_attachment_extensions(config.attachment_extensions)
        paths = ExportPaths(
            folder,
            {item.path for item in selected},
            external_base,
            inventory,
            extensions,
        )
        snapshot_index: _SnapshotLinks | None = None

        def snapshot_links(body: str, source: str) -> list[OutlinkInfo]:
            nonlocal snapshot_index
            if "[[" not in body:
                return []
            if snapshot_index is None:
                if snapshot is None:
                    raise ValueError("Snapshot links require a Git snapshot")
                snapshot_notes = (
                    (entry.path, raw)
                    for entry in root_inventory()
                    if entry.path.endswith(".md") and entry.visibility == "visible"
                    if entry.parse_state == "valid" or entry.reason == "note_limit"
                    for raw in (snapshot.read(entry.path),)
                    if _snapshot_searchable(entry, raw)
                )
                snapshot_index = _snapshot_link_index(snapshot_notes)
            return _snapshot_outlinks(body, source, snapshot_index, extensions)

        def graph_links(_body: str, source: str) -> list[OutlinkInfo]:
            return vault.graph.get_outlinks(source)

        digests: dict[str, bytes] = {}
        for item in selected:
            raw = (
                snapshot.read(item.path)
                if snapshot
                else _read_safe(vault.source_dir, item.path)
            )
            if snapshot is None:
                digests[item.path] = hashlib.sha256(raw).digest()
            elif raw.startswith(_LFS_POINTER_PREFIX.encode()):
                raise GitSnapshotUnavailableError(
                    "Git LFS pointer cannot be exported; use a commit containing the file bytes."
                )
            name = item.path[len(folder) + 1 :] if folder else item.path
            if item.role == "supporting":
                _write(zf, name, raw)
                attachments += 1
            else:
                _observe_export(item, report)
                searchable = (
                    _snapshot_searchable(item, raw)
                    if snapshot is not None
                    else item.search_state == "searchable"
                )
                resolver = (
                    (snapshot_links if snapshot else graph_links)
                    if searchable
                    else None
                )
                _write(
                    zf, name, _note_bytes(raw, (item, name), paths, report, resolver)
                )
                notes += 1
    if snapshot is not None:
        changed, flagged = git_snapshot_changes(vault.source_dir, snapshot.commit)
        for prefix, (child, commit) in snapshot.children.items():
            if folder and not (
                prefix == folder
                or prefix.startswith(folder + "/")
                or folder.startswith(prefix + "/")
            ):
                continue
            try:
                with open_directory_nofollow(
                    vault.source_dir, tuple(prefix.split("/"))
                ):
                    child_changed, child_flagged = git_snapshot_changes(child, commit)
            except (OSError, ValueError) as exc:
                raise GitSnapshotUnavailableError(
                    f"Git submodule {prefix} is unavailable; run git submodule update --init and retry."
                ) from exc
            changed.update(prefix + "/" + path for path in child_changed)
            flagged.update(
                {prefix + "/" + path: absent for path, absent in child_flagged.items()}
            )
        changed.update({path + "/" for path in changed if path in snapshot.children})
        page = vault.reader.inspect_bundle_tree(
            BundleTreeRequest(
                root=folder,
                entry_limit=1,
                snapshot=replace(snapshot, changed=changed, flagged=flagged),
            )
        )
        _require_complete(page, folder)
        report.snapshot.uncommitted_paths.extend(page.uncommitted)
    for path, digest in digests.items():
        try:
            current = hashlib.sha256(_read_safe(vault.source_dir, path)).digest()
        except OSError:
            report.snapshot.inconsistent_paths.append(path)
        else:
            if current != digest:
                report.snapshot.inconsistent_paths.append(path)
    logger.info(
        "okf_bundle_built folder=%s notes=%d attachments=%d excluded=%d",
        folder or "(root)",
        notes,
        attachments,
        excluded,
    )
    data = buf.getvalue()
    audit_export(data, report, extensions)
    return OkfBundleResult(data, notes, attachments, excluded, report)


def _write(zf: zipfile.ZipFile, path: str, data: bytes) -> None:
    """Write one archive entry with a fixed timestamp for reproducible bytes."""
    info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)
