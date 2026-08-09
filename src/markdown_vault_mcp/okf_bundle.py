"""OKF bundle export (#963): build a conformant bundle zip from live vault state.

Phase 4 of ``docs/design/okf.md`` §7, and the export half of the migration
story that :mod:`markdown_vault_mcp.managers.okf_migrate` began. The build
**never mutates the vault**: it reads the notes and attachments in scope,
rewrites resolvable wikilinks to OKF's recommended root-absolute markdown links
(reusing :func:`~markdown_vault_mcp.okf.convert_wikilinks_to_markdown` and the
resolved outlink graph, so links are preserved edge-for-edge), excludes
convention and template files, keeps the reserved navigation files
(``index.md`` / ``log.md``), includes non-conformant notes as-is, and returns a
deterministic zip.

It is served through pvl-core's ``create_download_link`` via an ``okf-bundle``
download ref (:class:`~markdown_vault_mcp._transfer_sink.VaultTransferSink`), not
a bespoke tool; residual conformance gaps are reported separately by
``okf_validate``, so the bundle itself carries no gap payload.
"""

from __future__ import annotations

import base64
import io
import logging
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

from markdown_vault_mcp.okf import convert_wikilinks_to_markdown
from markdown_vault_mcp.types import NoteInfo
from markdown_vault_mcp.utils.text import read_text_utf8

if TYPE_CHECKING:
    from pathlib import Path

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
        excluded: Number of in-scope files skipped (convention/template files).
    """

    data: bytes
    notes: int
    attachments: int
    excluded: int


def _is_excluded(path: str, conventions_file: str | None, templates_root: str) -> bool:
    """Return whether *path* is a convention or template file (excluded from export).

    Convention files (the per-folder ``_conventions.md``) are authoring guidance,
    and the template folder holds note scaffolds — neither is bundle content. The
    reserved ``index.md`` / ``log.md`` are navigation and are kept.
    """
    name = path.rsplit("/", 1)[-1]
    if conventions_file is not None and name == conventions_file:
        return True
    return bool(
        templates_root
        and (path == templates_root or path.startswith(f"{templates_root}/"))
    )


def _safe_abs(source_dir: Path, path: str) -> Path:
    """Resolve a vault-relative path under *source_dir*, rejecting any escape.

    ``list_documents`` already yields safe relative paths; this is defence in
    depth before a raw filesystem read.
    """
    resolved = (source_dir / path).resolve()
    if not resolved.is_relative_to(source_dir.resolve()):
        raise ValueError(f"Path escapes the vault: {path}")
    return resolved


def build_okf_bundle(
    vault: Vault, config: ProjectConfig, *, folder: str = ""
) -> OkfBundleResult:
    """Build an OKF bundle zip of *folder* (or the whole vault) from live state.

    Args:
        vault: The live vault — its reader enumerates and reads notes/attachments
            and its graph supplies the resolved outlinks for wikilink rewriting.
        config: The project config — supplies the vault root and the
            convention-file / template-folder names to exclude.
        folder: Vault-relative folder to scope the bundle to; ``""`` is the whole
            vault.

    Returns:
        An :class:`OkfBundleResult` with the zip bytes and per-kind counts.

    Notes:
        Notes are read raw from disk (``read_text_utf8``) rather than through the
        note-read cap, so a large note exports in full. The build never writes to
        the vault.
    """
    conventions_file = config.content.conventions_file
    templates_folder = config.content.templates_folder
    templates_root = templates_folder.rstrip("/") if templates_folder else ""
    source_dir = config.source_dir

    items = sorted(
        vault.reader.list_documents(folder=folder or None, include_attachments=True),
        key=lambda item: item.path,
    )

    buf = io.BytesIO()
    notes = attachments = excluded = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            path = item.path
            if _is_excluded(path, conventions_file, templates_root):
                excluded += 1
                continue
            if isinstance(item, NoteInfo):
                content = read_text_utf8(_safe_abs(source_dir, path))
                new_content, _, _ = convert_wikilinks_to_markdown(
                    content, vault.graph.get_outlinks(path)
                )
                _write(zf, path, new_content.encode("utf-8"))
                notes += 1
            else:  # AttachmentInfo — list_documents yields only these two kinds
                att = vault.reader.read_attachment(path)
                _write(zf, path, base64.b64decode(att.content_base64))
                attachments += 1

    logger.info(
        "okf_bundle_built folder=%s notes=%d attachments=%d excluded=%d",
        folder or "(root)",
        notes,
        attachments,
        excluded,
    )
    return OkfBundleResult(buf.getvalue(), notes, attachments, excluded)


def _write(zf: zipfile.ZipFile, path: str, data: bytes) -> None:
    """Write one archive entry with a fixed timestamp for reproducible bytes."""
    info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)
