"""The sidecar files released versions and edge builds left beside a vector store (#1505).

v5.0.0 and v5.1.0 wrote ``{base}.json`` and ``{base}.npy``; an edge build of
``main`` before #1505 wrote ``{base}.json`` naming ``{base}.m{seq}.npy``. No
store writes, renames or deletes these files: a downgrade still finds them,
and an upgrade reads them through a read-only donor. A store that writes its
own files reports them as unused once it has committed, so an operator knows
what may be deleted.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from markdown_vault_mcp.utils.fs import file_bytes, path_exists

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def numbered_files(base: Path, prefix: str) -> dict[int, Path]:
    """Return the ``{base}{prefix}{seq}.npy`` files beside *base*, by ``seq``.

    Names derive with ``Path.with_suffix``, so a base with an extension
    (``emb.npy``) matches ``emb{prefix}{seq}.npy`` (#819).

    Args:
        base: The store's base path.
        prefix: The suffix before the number: ``.v2.m`` for the numpy
            store's own matrices, ``.m`` for an edge build's.

    Returns:
        Each existing file by its number.
    """
    stem, suffix = base.with_suffix(prefix).name, ".npy"
    found: dict[int, Path] = {}
    for path in base.parent.glob(f"{stem}*{suffix}"):
        seq = path.name[len(stem) : -len(suffix)]
        if seq.isdigit():
            found[int(seq)] = path
    return found


def legacy_paths(base: Path) -> tuple[Path, ...]:
    """Return the released and edge-build sidecar files beside *base*.

    Args:
        base: The store's base path.

    Returns:
        ``{base}.json`` and ``{base}.npy``, whether or not they exist, and
        every ``{base}.m{seq}.npy`` that exists.
    """
    return (
        base.with_suffix(".json"),
        base.with_suffix(".npy"),
        *numbered_files(base, ".m").values(),
    )


def report_unused_sidecar(base: Path) -> None:
    """Log the released or edge-build files a committed store left beside *base*.

    One INFO line, ``embeddings_legacy_sidecar_unused``, with their summed
    size; none when there are none. Informational: a refused stat skips the
    file rather than failing the open.

    Args:
        base: The store's base path.
    """
    present = 0
    found = False
    for path in legacy_paths(base):
        with contextlib.suppress(OSError):
            if path_exists(path):
                present += file_bytes(path)
                found = True
    if found:
        logger.info(
            "embeddings_legacy_sidecar_unused path=%s bytes=%d", str(base), present
        )
