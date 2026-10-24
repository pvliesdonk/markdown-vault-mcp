"""Move a corrupt built-in store's files aside instead of rebuilding over them (#1505).

Each file is renamed to ``<name>.corrupt-<YYYYMMDDTHHMMSSZ>``, every file of
one store with the same suffix, and ``-2``, ``-3`` and so on appended when
any of those names is taken. Nothing is deleted: the operator inspects or
deletes the copies. A rename that is refused undoes the ones already made
and is reported as unavailable, so a store that cannot be moved is never
rebuilt over.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from markdown_vault_mcp.exceptions import StoreUnavailableError
from markdown_vault_mcp.utils.fs import path_exists

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _suffix(paths: list[Path], stamp: str) -> str:
    """Return the first ``corrupt-<stamp>[-n]`` suffix free for every path."""
    suffix, n = f"corrupt-{stamp}", 1
    while any(path_exists(p.with_name(f"{p.name}.{suffix}")) for p in paths):
        n += 1
        suffix = f"corrupt-{stamp}-{n}"
    return suffix


def _undo(done: list[tuple[Path, Path]]) -> None:
    """Rename moved files back; a refusal is logged, the rest still tried."""
    for source, target in reversed(done):
        try:
            target.rename(source)
        except OSError:
            logger.warning(
                "vector_store_move_aside_undo_failed path=%s", target, exc_info=True
            )


def move_aside(
    paths: Iterable[Path],
    *,
    backend: str,
    location: str,
    clock: Callable[[], datetime] | None = None,
) -> list[Path]:
    """Rename each existing file in *paths* aside, all under one suffix.

    Args:
        paths: The store's files; those that do not exist are skipped.
        backend: The store's backend name, for the error.
        location: The store's location, for the log line and the error.
        clock: Supplies the time the suffix is stamped with; UTC now by
            default.

    Returns:
        The files' new paths, in the order given; empty when none existed.

    Raises:
        StoreUnavailableError: If a file cannot be checked or renamed; the
            renames already made are undone.
    """
    try:
        present = [p for p in paths if path_exists(p)]
        if not present:
            return []
        stamp = (clock or _now)().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = _suffix(present, stamp)
    except OSError as exc:
        raise StoreUnavailableError(
            f"Vector store at {location} cannot be moved aside: {exc}",
            backend=backend,
            location=location,
        ) from exc
    done: list[tuple[Path, Path]] = []
    try:
        for source in present:
            target = source.with_name(f"{source.name}.{suffix}")
            source.rename(target)
            done.append((source, target))
    except OSError as exc:
        _undo(done)
        raise StoreUnavailableError(
            f"Vector store at {location} is corrupt and cannot be moved aside: {exc}",
            backend=backend,
            location=location,
        ) from exc
    moved = [target for _, target in done]
    logger.warning(
        "vector_store_moved_aside location=%s reason=corrupt paths=%s",
        location,
        ",".join(str(p) for p in moved),
    )
    return moved
