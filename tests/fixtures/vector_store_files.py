"""Snapshots of committed built-in vectors for persistence assertions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def stored_vector_files(root: Path) -> dict[str, bytes]:
    """Snapshot vector files, excluding live locks and SQLite connection files.

    Args:
        root: Directory containing the test's ``vectors`` store.

    Returns:
        File names and bytes that must survive a refused rebuild.
    """
    return {
        path.name: path.read_bytes()
        for path in root.glob("vectors*")
        if path.is_file() and not path.name.endswith((".lock", "-wal", "-shm"))
    }
