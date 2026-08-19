"""Filesystem traversal helpers."""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from pathlib import Path

logger = logging.getLogger(__name__)

# pathlib's Path.glob / Path.rglob do not recurse into symlinked
# subdirectories by default — the behavior was unspecified pre-3.13 and an
# explicit recurse_symlinks=False default in 3.13+. Pass recurse_symlinks=True
# on 3.13+ to enable symlink-farm vault layouts (issue #508). The kwarg does
# not exist on 3.11/3.12 where pathlib's symlink behavior is buggy and
# inconsistent; users with symlink farms on those versions need to upgrade.
#
# Warning: vault symlinks must not form cycles. A self-referential link
# (e.g. ``vault/loop -> vault/``) hangs the scan with no cycle detection.
GLOB_SYMLINK_KWARGS: dict[str, Any] = (
    {"recurse_symlinks": True} if sys.version_info >= (3, 13) else {}
)

# ``os.walk`` equivalent of ``GLOB_SYMLINK_KWARGS``: follow symlinked
# subdirectories only on the same versions where the glob path does, so the
# pruning walk in :func:`iter_markdown_files` discovers exactly the tree the
# raw ``glob("**/*.md", **GLOB_SYMLINK_KWARGS)`` it replaces would (issue #508).
_WALK_FOLLOWLINKS = sys.version_info >= (3, 13)


def _dir_prune_rules(
    exclude_patterns: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Split exclude patterns into directory-prune rules of the two known shapes.

    Only the two pattern shapes the codebase actually uses can safely prune a
    directory before descending. Any other shape is ignored here (the per-file
    :func:`~markdown_vault_mcp.utils.is_path_excluded` filter remains the sole
    correctness layer for it), so an unrecognised pattern never prunes.

    The recognised shapes, matched against the ``fnmatch`` semantics the file
    filter uses (``*`` crosses ``/``, so ``**`` and ``*`` are equivalent):

    - ``PREFIX/**`` with no wildcard in ``PREFIX`` (e.g. ``go/**``,
      ``.claude/plugins/**``). Every path under directory ``PREFIX`` is
      excluded, so a directory equal to or nested under ``PREFIX`` is pruned.
    - ``**/NAME/**`` with ``NAME`` a single plain path segment (e.g.
      ``**/node_modules/**``). Every path with ``NAME`` as a non-leading
      component is excluded, so a directory that has ``NAME`` as a component
      other than its first is pruned. A *top-level* directory named ``NAME``
      is deliberately not pruned. ``**/NAME/**`` requires a ``/`` before
      ``NAME``, so ``NAME/file.md`` at the root is not excluded by the file
      filter and must still be descended.

    Args:
        exclude_patterns: Raw exclude glob patterns.

    Returns:
        Tuple ``(anchored_prefixes, anydepth_names)``. ``anchored_prefixes`` are
        the ``PREFIX`` strings; ``anydepth_names`` are the ``NAME`` segments.
    """
    anchored: list[str] = []
    anydepth: list[str] = []
    for pattern in exclude_patterns:
        if not pattern.endswith("/**"):
            continue
        body = pattern[:-3]
        if body.startswith("**/"):
            name = body[3:]
            if name and not _has_glob_meta(name) and "/" not in name:
                anydepth.append(name)
            continue
        if body and not _has_glob_meta(body):
            anchored.append(body)
    return anchored, anydepth


def _has_glob_meta(text: str) -> bool:
    """Return whether *text* contains an fnmatch wildcard metacharacter."""
    return any(ch in text for ch in "*?[")


def _should_prune_dir(
    rel_posix: str, anchored: Sequence[str], anydepth: Sequence[str]
) -> bool:
    """Return whether every file under directory *rel_posix* is excluded.

    Pure optimization predicate: a ``True`` result means the per-file filter
    would reject every descendant, so the directory can be skipped without
    descending. Derived from the same patterns the file filter uses, so it can
    never prune a directory that holds a non-excluded file.

    Args:
        rel_posix: Directory path relative to the vault root, POSIX-style, with
            no trailing slash (the empty string for the root itself).
        anchored: Anchored ``PREFIX`` strings from :func:`_dir_prune_rules`.
        anydepth: Any-depth ``NAME`` segments from :func:`_dir_prune_rules`.

    Returns:
        ``True`` if the directory can be pruned.
    """
    if not rel_posix:
        # The vault root is never fully excluded; its children decide.
        return False
    for prefix in anchored:
        if rel_posix == prefix or rel_posix.startswith(prefix + "/"):
            return True
    if anydepth:
        # A child path rel_posix/<x> contains "/NAME/" for every <x> exactly
        # when NAME appears as a component other than the first — i.e. anywhere
        # after the first slash.
        components = rel_posix.split("/")
        for name in anydepth:
            if name in components[1:]:
                return True
    return False


def iter_markdown_files(
    source_dir: Path,
    exclude_patterns: Sequence[str] | None = None,
    *,
    on_error: Callable[[OSError], None] | None = None,
) -> Iterator[Path]:
    """Yield ``*.md`` files under *source_dir*, pruning excluded subtrees.

    Drop-in replacement for ``source_dir.glob("**/*.md", **GLOB_SYMLINK_KWARGS)``
    that walks with :func:`os.walk` and prunes directories the exclude patterns
    fully cover *before* descending into them, instead of walking the whole tree
    and discarding matches afterwards. On a large tree with excludes such as
    ``$HOME`` with ``node_modules``/``.venv``/``go`` subtrees excluded, this
    avoids descending millions of throwaway nodes.

    Pruning is a pure optimization. It only skips a directory when the exclude
    patterns guarantee every file beneath it is excluded (see
    :func:`_should_prune_dir`); callers must still apply the per-file
    :func:`~markdown_vault_mcp.utils.is_path_excluded` filter as the correctness
    layer, because unrecognised pattern shapes are not pruned and a pruned
    directory's own path is not otherwise filtered here.

    Symlink handling matches :data:`GLOB_SYMLINK_KWARGS`: symlinked
    subdirectories are followed only on Python 3.13+. An unreadable directory
    (permission denied, broken link) is skipped rather than aborting the walk,
    but — unlike ``glob``'s silent suppression — it is logged at WARNING so a
    dropped subtree is observable instead of vanishing without a trace (#835).

    Yields paths in ``os.walk`` order (arbitrary within a directory); callers
    that need a stable order must sort, exactly as they did around the glob.

    Args:
        source_dir: Root directory of the markdown vault.
        exclude_patterns: Exclude glob patterns. Used only to derive directory
            prune rules; ``None`` or empty prunes nothing (the walk still runs
            and yields every ``*.md`` file).
        on_error: Optional callback invoked after an unreadable-directory error
            is logged. The walk continues after the callback returns.

    Yields:
        Absolute paths to ``*.md`` files, anchored at the unresolved
        *source_dir* (so relative paths computed against *source_dir* match
        even when it is itself a symlink).
    """
    anchored, anydepth = _dir_prune_rules(exclude_patterns or ())
    source_str = os.fspath(source_dir)

    def _on_walk_error(exc: OSError) -> None:
        # os.walk drops the offending directory and continues; surface it so a
        # permission-denied or broken-link subtree does not silently disappear
        # from discovery/indexing (#835).
        logger.warning("markdown_walk_dir_unreadable path=%s: %s", exc.filename, exc)
        if on_error is not None:
            on_error(exc)

    for dirpath, dirnames, filenames in os.walk(
        source_str, onerror=_on_walk_error, followlinks=_WALK_FOLLOWLINKS
    ):
        rel_dir = os.path.relpath(dirpath, source_str)
        rel_prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        if anchored or anydepth:
            kept: list[str] = []
            for name in dirnames:
                child_rel = f"{rel_prefix}/{name}" if rel_prefix else name
                if not _should_prune_dir(child_rel, anchored, anydepth):
                    kept.append(name)
            # Mutate in place so os.walk does not descend the pruned dirs.
            dirnames[:] = kept
        base = source_dir / rel_prefix if rel_prefix else source_dir
        for filename in filenames:
            if filename.endswith(".md"):
                yield base / filename
