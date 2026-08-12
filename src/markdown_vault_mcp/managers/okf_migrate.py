"""OKF migration transforms (#963): in-place mechanical vault conversions.

Phase 4 of `docs/design/okf.md` §7. Three one-shot transforms an LLM should
not perform note-by-note, each built on the existing write path
(:meth:`DocumentManager.write` — atomic write, read-only gate, index
dirtying, and the git-commit callback all come for free):

- :meth:`convert_links` rewrites resolvable wikilinks as OKF's recommended
  bundle-root-absolute markdown links, reusing the *already-resolved*
  outlink targets so the link graph is preserved edge-for-edge.
- :meth:`generate_index` (re)writes a reserved ``index.md`` progressive-
  disclosure listing from the table of contents, preserving any existing
  frontmatter (notably the root ``okf_version`` declaration).
- :meth:`seed_log` seeds a reserved ``log.md`` change history from git.

These are write tools gated on read-only mode only, not on a future
``OKF_WRITE`` flag (design §7): they are deliberate migrations, not ongoing
enforcement.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from markdown_vault_mcp.okf import (
    OKF_RESERVED_FILENAMES,
    OkfConvertResult,
    OkfIndexResult,
    OkfLogResult,
    build_index_markdown,
    build_log_markdown,
    convert_wikilinks_to_markdown,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.managers.git_query import GitQueryManager
    from markdown_vault_mcp.managers.link import LinkManager
    from markdown_vault_mcp.managers.search import SearchManager

logger = logging.getLogger(__name__)


class OkfMigrationManager:
    """Orchestrate the OKF migration transforms over existing managers."""

    def __init__(
        self,
        *,
        doc_mgr: DocumentManager,
        link_mgr: LinkManager,
        search_mgr: SearchManager,
        git_query_mgr: GitQueryManager,
        require_built: Callable[[], None],
    ) -> None:
        """Hold the collaborators the transforms delegate to.

        Args:
            doc_mgr: Reads (raw content, TOC, reserved-file existence) and
                writes (the shared write path); supplies the read-only gate
                and git-commit callback.
            link_mgr: Resolved outlinks for the link converter.
            search_mgr: Note listing and per-note metadata (descriptions).
            git_query_mgr: Vault-wide history for the log seeder.
            require_built: Index-readiness gate; the link/TOC-dependent
                transforms call it first.
        """
        self._doc_mgr = doc_mgr
        self._link_mgr = link_mgr
        self._search_mgr = search_mgr
        self._git_query_mgr = git_query_mgr
        self._require_built = require_built

    def convert_links(self, *, folder: str | None = None) -> OkfConvertResult:
        """Rewrite resolvable wikilinks as root-absolute markdown links.

        Args:
            folder: Restrict to this folder subtree; ``None`` covers the
                whole vault.

        Returns:
            An :class:`~markdown_vault_mcp.okf.OkfConvertResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            IndexUnavailableError: If the index is not built.
        """
        self._doc_mgr.ensure_writable()
        self._require_built()
        files_changed = 0
        converted = 0
        skipped = 0
        scanned = 0
        for note in self._search_mgr.list(folder=folder, include_attachments=False):
            scanned += 1
            outlinks = self._link_mgr.get_outlinks(note.path)
            if not any(link.link_type == "wikilink" for link in outlinks):
                continue
            note_content = self._doc_mgr.read(note.path)
            if note_content is None:  # pragma: no cover - listed-then-deleted race
                continue
            new_content, n_conv, n_skip = convert_wikilinks_to_markdown(
                note_content.content, outlinks
            )
            converted += n_conv
            skipped += n_skip
            if new_content != note_content.content:
                self._doc_mgr.write(note.path, new_content)
                files_changed += 1
        return OkfConvertResult(
            files_changed=files_changed,
            links_converted=converted,
            links_skipped=skipped,
            notes_scanned=scanned,
        )

    def generate_index(self, *, folder: str = "") -> OkfIndexResult:
        """Generate a reserved ``index.md`` listing for *folder* from the TOC.

        Progressive disclosure (OKF spec): the listing carries only the
        folder's *immediate* notes plus a pointer to each immediate
        subfolder's own ``index.md`` — it does not flatten the whole
        subtree, so each level defers depth to the level below. Existing
        frontmatter is preserved (the root ``index.md``'s ``okf_version``
        declaration must survive regeneration).

        Args:
            folder: Vault-relative folder (``""`` for the bundle root).

        Returns:
            An :class:`~markdown_vault_mcp.okf.OkfIndexResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            IndexUnavailableError: If the index is not built.
        """
        self._doc_mgr.ensure_writable()
        self._require_built()
        from markdown_vault_mcp.types import NoteInfo

        prefix = f"{folder}/" if folder else ""
        note_entries: list[tuple[str, str, str | None]] = []
        subfolders: set[str] = set()
        for note in self._search_mgr.list(folder=folder or None):
            if not isinstance(note, NoteInfo):  # pragma: no cover - mypy narrowing
                continue
            rel = note.path[len(prefix) :]
            if "/" in rel:
                # A deeper note: record its immediate subfolder, don't list it.
                subfolders.add(rel.split("/", 1)[0])
                continue
            if rel in OKF_RESERVED_FILENAMES:
                continue
            raw_desc = note.frontmatter.get("description")
            description = (
                raw_desc.strip()
                if isinstance(raw_desc, str) and raw_desc.strip()
                else None
            )
            note_entries.append((note.title, f"/{note.path}", description))

        # Immediate notes first, then a pointer into each subfolder's index.
        entries = note_entries + [
            (f"{sub}/", f"/{prefix}{sub}/index.md", None) for sub in sorted(subfolders)
        ]

        index_path = f"{folder}/index.md" if folder else "index.md"
        existing = self._doc_mgr.read(index_path)
        existing_fm = existing.frontmatter if existing is not None else None
        preserved = bool(existing_fm)
        heading = folder.rsplit("/", 1)[-1] if folder else "Index"
        body = build_index_markdown(heading, entries)
        self._doc_mgr.write(
            index_path, body, frontmatter=existing_fm if preserved else None
        )
        return OkfIndexResult(
            path=index_path, entries=len(entries), frontmatter_preserved=preserved
        )

    def seed_log(self, *, folder: str = "", limit: int = 100) -> OkfLogResult:
        """Seed a reserved ``log.md`` for *folder* from git history.

        Refuses to overwrite an existing ``log.md`` — a change history is
        hand-maintained after seeding, so clobbering it would destroy real
        content.

        *folder* both chooses where ``log.md`` is written and scopes its
        content: a folder seeds only the commits that touched that subtree,
        while the bundle root (``folder=""``) seeds the whole bundle's history
        (#974).

        Args:
            folder: Vault-relative folder (``""`` for the bundle root).
            limit: Maximum commits to read (clamped to 100 by the git layer).

        Returns:
            An :class:`~markdown_vault_mcp.okf.OkfLogResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            FileExistsError: If ``log.md`` already exists in *folder*.
        """
        self._doc_mgr.ensure_writable()
        log_path = f"{folder}/log.md" if folder else "log.md"
        if self._doc_mgr.read(log_path) is not None:
            raise FileExistsError(
                f"{log_path} already exists; seeding would overwrite the "
                "change history. Remove or rename it first."
            )
        # A folder scopes history to its subtree; the bundle root (folder="")
        # falls back to whole-vault history via path=None.
        history = self._git_query_mgr.get_history(path=folder or None, limit=limit)
        body, commits, dates = build_log_markdown(history)
        self._doc_mgr.write(log_path, body)
        return OkfLogResult(path=log_path, commits=commits, dates=dates)
