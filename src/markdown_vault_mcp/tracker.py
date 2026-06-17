"""Hash-based change detection for markdown-vault-mcp."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp.hashing import compute_file_hash
from markdown_vault_mcp.types import ChangeSet, ParsedNote
from markdown_vault_mcp.utils import is_path_excluded
from markdown_vault_mcp.utils.fs import GLOB_SYMLINK_KWARGS

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Current on-disk state file format version. Version 2 splits the flat
# path → digest map into separate "indexed" and "skipped" maps (#665).
_STATE_VERSION = 2


class ChangeTracker:
    """Detects file additions, modifications, and deletions using SHA256 hashes.

    State is persisted as a JSON file with two maps of relative document path
    to last-seen SHA256 hex digest: ``indexed`` (files that made it into the
    index) and ``skipped`` (files seen on disk but deliberately not indexed,
    e.g. missing required frontmatter). A skipped file is only re-reported as
    added when its content changes, so it can be re-evaluated. Legacy state
    files (a flat path-to-hash object) load fine; all entries are treated as
    indexed. On the first run (no state file), every file on disk is treated
    as newly added.

    Example::

        tracker = ChangeTracker(Path("/data/vault/.markdown_vault_mcp/state.json"))
        changes = tracker.detect_changes(Path("/data/vault"))
        # process changes ...
        tracker.update_state(notes, skipped=newly_skipped)
    """

    def __init__(self, state_path: Path) -> None:
        """Initialise the tracker.

        Args:
            state_path: Path to the JSON state file. The file need not exist
                yet; the parent directory is created on first write.
        """
        self._state_path = state_path
        # Skipped entries from the last detect_changes() that are still on
        # disk with unchanged content; carried forward by update_state().
        self._skipped_carry: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_changes(
        self,
        source_dir: Path,
        glob_pattern: str = "**/*.md",
        exclude_patterns: Sequence[str] | None = None,
    ) -> ChangeSet:
        """Compare files on disk against stored state and return the delta.

        Algorithm:

        1. Load existing state (empty maps if state file does not exist).
        2. Scan *source_dir* for all files matching *glob_pattern*.
        3. Compute SHA256 of each file's raw bytes.
        4. Categorise each path:

           - present on disk but absent from state → **added**
           - in indexed state and hash differs → **modified**
           - in indexed state and hash matches → **unchanged** (counted only)
           - in skipped state and hash matches → **skipped_unchanged**
             (counted only; not re-reported)
           - in skipped state and hash differs → **added** (re-evaluated;
             it may now qualify for indexing)
           - in indexed state but absent from disk → **deleted**
           - in skipped state but absent from disk → dropped silently (it
             was never indexed, so nothing needs deleting)
           - matches *exclude_patterns* → **filtered before hashing**: absent
             from every result list, including **deleted**. A path that was
             previously indexed and is now excluded is *withheld* from
             ``deleted`` (it is excluded, not removed) so the reindex
             stale-purge (#255) — which loads vectors before purging — remains
             its sole remover; reporting it as deleted would leak its
             embedding (#257).

        5. Return a :class:`~markdown_vault_mcp.types.ChangeSet`.

        Args:
            source_dir: Root directory of the markdown vault.
            glob_pattern: Glob pattern used to discover files, relative to
                *source_dir*. Defaults to ``"**/*.md"``.
            exclude_patterns: Glob patterns (matched against the relative POSIX
                path via :func:`~markdown_vault_mcp.utils.is_path_excluded`,
                which uses :func:`fnmatch.fnmatch` — use a ``**`` suffix such as
                ``.obsidian/**`` to match a whole subtree). Matching files are
                skipped before hashing, so excluded subtrees are neither hashed
                nor reported as changes (#257). ``None`` excludes nothing.

        Returns:
            A :class:`~markdown_vault_mcp.types.ChangeSet` describing the delta.
        """
        indexed_state, skipped_state = self._load_state()

        # Build a mapping of relative path → sha256 for current disk contents.
        disk_state: dict[str, str] = {}
        for abs_path in sorted(source_dir.glob(glob_pattern, **GLOB_SYMLINK_KWARGS)):
            if not abs_path.is_file():
                continue
            try:
                rel_str = abs_path.relative_to(source_dir).as_posix()
            except ValueError:
                logger.warning("File outside source_dir, skipping: %s", abs_path)
                continue
            # Skip excluded subtrees before hashing — saves the I/O and keeps
            # them out of the ChangeSet (#257).
            if is_path_excluded(rel_str, exclude_patterns):
                continue
            try:
                content_hash = self._compute_hash(abs_path)
            except OSError as exc:
                logger.warning("Cannot read %s, skipping: %s", abs_path, exc)
                continue
            disk_state[rel_str] = content_hash

        added: list[str] = []
        modified: list[str] = []
        unchanged: int = 0
        skipped_unchanged: int = 0
        self._skipped_carry = {}

        for rel_path, current_hash in disk_state.items():
            if rel_path in indexed_state:
                if indexed_state[rel_path] != current_hash:
                    modified.append(rel_path)
                else:
                    unchanged += 1
            elif rel_path in skipped_state:
                if skipped_state[rel_path] != current_hash:
                    # Content changed since the skip — re-evaluate it.
                    added.append(rel_path)
                else:
                    skipped_unchanged += 1
                    self._skipped_carry[rel_path] = current_hash
            else:
                added.append(rel_path)

        # An excluded path that was previously indexed is filtered out of
        # disk_state above, so it would otherwise look "deleted". It is not
        # deleted — it is excluded — and the reindex stale-purge (#255) is the
        # designated remover (it lazy-loads vectors before purging). Reporting
        # it as deleted instead would route it through the delete loop, which
        # does not load vectors, leaking its embedding (#257).
        deleted: list[str] = [
            rel_path
            for rel_path in indexed_state
            if rel_path not in disk_state
            and not is_path_excluded(rel_path, exclude_patterns)
        ]

        logger.debug(
            "detect_changes: %d added, %d modified, %d deleted, %d unchanged, "
            "%d skipped-unchanged",
            len(added),
            len(modified),
            len(deleted),
            unchanged,
            skipped_unchanged,
        )

        return ChangeSet(
            added=added,
            modified=modified,
            deleted=deleted,
            unchanged=unchanged,
            skipped_unchanged=skipped_unchanged,
        )

    def update_state(
        self,
        notes: list[ParsedNote],
        skipped: dict[str, str] | None = None,
    ) -> None:
        """Persist the current hash state derived from *notes*.

        Overwrites the entire state file. The indexed map comes from *notes*;
        the skipped map merges the unchanged skipped entries observed by the
        preceding :meth:`detect_changes` call with *skipped*. Call this after
        successfully (re)indexing a set of documents to record their current
        content hashes.

        Args:
            notes: Parsed notes whose ``path`` and ``content_hash`` attributes
                form the new indexed state. Any indexed paths not in this list
                are dropped from state (treated as deleted on the next scan).
            skipped: Mapping of relative path to SHA256 hex digest for files
                seen during this scan but deliberately not indexed (missing
                required frontmatter, excluded, unparseable). Indexed paths
                always win: any path also present in *notes* is dropped from
                the skipped map.
        """
        new_indexed = {note.path: note.content_hash for note in notes}
        new_skipped = {
            path: content_hash
            for path, content_hash in {
                **self._skipped_carry,
                **(skipped or {}),
            }.items()
            if path not in new_indexed
        }
        self._save_state(new_indexed, new_skipped)
        # The carry is consumed by exactly one update_state call; clearing it
        # keeps a later call without a fresh detect_changes (e.g. a full
        # build_index) from merging stale skipped entries into its snapshot.
        self._skipped_carry = {}
        logger.debug(
            "update_state: wrote state for %d indexed, %d skipped document(s)",
            len(new_indexed),
            len(new_skipped),
        )

    def reset(self) -> None:
        """Delete the state file so the next scan treats all files as added.

        If the state file does not exist, this is a no-op.
        """
        self._skipped_carry = {}
        if self._state_path.exists():
            self._state_path.unlink()
            logger.debug("reset: deleted state file %s", self._state_path)
        else:
            logger.debug("reset: state file does not exist, nothing to delete")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> tuple[dict[str, str], dict[str, str]]:
        """Load the persisted state from disk.

        Supports two formats: the current versioned object
        (``{"version": 2, "indexed": {...}, "skipped": {...}}``) and the
        legacy flat mapping of path to digest, which is loaded with every
        entry treated as indexed.

        Returns:
            Tuple of ``(indexed, skipped)`` maps of relative document path to
            SHA256 hex digest. Both are empty when the state file does not
            exist or is malformed.
        """
        if not self._state_path.exists():
            logger.debug(
                "No state file at %s; treating all files as added", self._state_path
            )
            return {}, {}
        try:
            with self._state_path.open(encoding="utf-8") as fh:
                state = json.load(fh)
            if not isinstance(state, dict):
                logger.warning(
                    "State file %s is malformed (expected object); resetting",
                    self._state_path,
                )
                return {}, {}
            if state.get("version") == _STATE_VERSION:
                indexed = state.get("indexed", {})
                skipped = state.get("skipped", {})
                if not isinstance(indexed, dict) or not isinstance(skipped, dict):
                    logger.warning(
                        "State file %s is malformed (expected object maps); resetting",
                        self._state_path,
                    )
                    return {}, {}
                return indexed, skipped
            # Legacy flat format: every entry is an indexed path → digest.
            return state, {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Cannot read state file %s (%s); treating all files as added",
                self._state_path,
                exc,
            )
            return {}, {}

    def _save_state(self, indexed: dict[str, str], skipped: dict[str, str]) -> None:
        """Write the indexed and skipped maps to the state file as JSON.

        Creates parent directories if they do not exist.

        Args:
            indexed: Mapping of relative document path to SHA256 hex digest
                for documents present in the index.
            skipped: Mapping of relative document path to SHA256 hex digest
                for files seen on disk but deliberately not indexed.
        """
        state = {"version": _STATE_VERSION, "indexed": indexed, "skipped": skipped}
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._state_path.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
            Path(tmp_path).replace(self._state_path)
        except:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        logger.debug(
            "Saved state for %d indexed, %d skipped path(s) to %s",
            len(indexed),
            len(skipped),
            self._state_path,
        )

    def _compute_hash(self, path: Path) -> str:
        """Compute the SHA256 hex digest of *path* using chunked reads.

        Delegates to :func:`~markdown_vault_mcp.hashing.compute_file_hash`.

        Args:
            path: Absolute path to the file to hash.

        Returns:
            Lowercase hex-encoded SHA256 digest.

        Raises:
            OSError: If the file cannot be opened or read.
        """
        return compute_file_hash(path)
