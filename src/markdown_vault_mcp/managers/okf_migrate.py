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

Generated reserved files have no new frontmatter; existing index headers are preserved.

These are write tools gated on read-only mode only, not on a future
``OKF_WRITE`` flag (design §7): they are deliberate migrations, not ongoing
enforcement. The index-dependent transforms refresh prior queued writes
before reading their inputs (#1464); a refresh error refuses the transform.

Every write they issue goes straight to
:class:`~markdown_vault_mcp.managers.document.DocumentManager`, below the
writer facet that runs ``log.md`` / ``index.md`` maintenance, so a mechanical
rewrite triggers none, and a library caller of ``vault.writer.okf_*`` gets the
same rewrite the tool does (#1401).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from markdown_vault_mcp._okf_write import okf_write_suppressed
from markdown_vault_mcp.exceptions import DocumentUnreadableError, InvalidRequestError
from markdown_vault_mcp.okf import (
    OKF_ROOT_INDEX_TITLE,
    OkfConvertResult,
    OkfIndexResult,
    OkfLogResult,
    build_index_markdown,
    build_log_markdown,
    convert_wikilinks_to_markdown,
    is_okf_reserved_file,
)
from markdown_vault_mcp.utils import normalize_folder

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.managers.git_query import GitQueryManager
    from markdown_vault_mcp.managers.link import LinkManager
    from markdown_vault_mcp.managers.search import SearchManager

logger = logging.getLogger(__name__)


@dataclass
class _IndexTraversal:
    """Shared state for one generated listing's folded subtree."""

    root: str
    notes: Sequence[object]
    skipped: dict[str, str]
    allow_protected: bool
    designated: Callable[[str], bool] | None
    sections: dict[str, list[tuple[str, str, str | None]]] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)


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

    def convert_links(
        self, *, folder: str | None = None, allow_protected: bool = False
    ) -> OkfConvertResult:
        """Rewrite resolvable wikilinks as root-absolute markdown links.

        Notes inside a git submodule are left out, since a write there would
        never be committed (#1564).

        Args:
            folder: Restrict to this folder subtree; ``None`` covers the
                whole vault, ``""`` the bundle root only.  Folded through
                :func:`~markdown_vault_mcp.utils.normalize_folder`.

            allow_protected: Allow this library call to change protected paths.

        Returns:
            An :class:`~markdown_vault_mcp.okf.OkfConvertResult`.

        Raises:
            InvalidRequestError: If protected paths were skipped; eligible writes remain.
            ReadOnlyError: If the vault is read-only.
            SubmodulePathError: If *folder* lies inside a git submodule.
            DocumentUnreadableError: If a note it rewrites exists but cannot be
                read (#1608). Notes converted before it keep their changes.
            IndexUnavailableError: If the index is not built.
            TimeoutError: If the queued index refresh exceeds its wait budget.
        """
        self._doc_mgr.ensure_writable()
        self._doc_mgr.ensure_index_current()
        self._require_built()
        folder = normalize_folder(folder)
        if folder:
            self._doc_mgr.refuse_folder_in_submodule(folder, "folder")
        files_changed = 0
        converted = 0
        skipped = 0
        scanned = 0
        in_submodules = 0
        protected: dict[str, str] = {}
        # Every write a transform issues is mechanical: it must not re-stamp
        # provenance or clear a human attestation, whichever entry point
        # called it (#1401).
        with okf_write_suppressed():
            for note in self._search_mgr.list(folder=folder, include_attachments=False):
                if self._doc_mgr.in_submodule(note.path):
                    in_submodules += 1
                    continue
                scanned += 1
                outlinks = self._link_mgr.get_outlinks(note.path)
                if not any(link.link_type == "wikilink" for link in outlinks):
                    continue
                note_content = self._doc_mgr.read(note.path)
                if note_content is None:  # pragma: no cover - deleted mid-scan
                    continue
                new_content, n_conv, n_skip = convert_wikilinks_to_markdown(
                    note_content.content, outlinks
                )
                converted += n_conv
                skipped += n_skip
                if new_content != note_content.content:
                    reason = self._doc_mgr.protection_reason(
                        note.path, allow_protected=allow_protected
                    )
                    if reason:
                        protected[note.path] = reason
                        continue
                    self._doc_mgr.write(
                        note.path,
                        new_content,
                        allow_overwrite=True,
                        allow_protected=allow_protected,
                    )
                    files_changed += 1
        if in_submodules:
            logger.info(
                "okf_convert_links_submodule_notes_skipped notes=%d", in_submodules
            )
        if protected:
            raise InvalidRequestError(
                f"Converted {files_changed} notes; did not convert {protected!r}. "
                "Ask the operator to convert them or change the setting."
            )
        return OkfConvertResult(
            files_changed=files_changed,
            links_converted=converted,
            links_skipped=skipped,
            notes_scanned=scanned,
        )

    def generate_index(
        self,
        *,
        folder: str = "",
        allow_protected: bool = False,
        designated: Callable[[str], bool] | None = None,
    ) -> OkfIndexResult:
        """Generate a reserved ``index.md`` listing for *folder* from the TOC.

        The default migration creates missing indexes recursively and leaves
        existing descendant indexes untouched. With a designation predicate,
        undesignated folders without indexes are folded into sections; existing
        indexes remain boundaries. Existing frontmatter is preserved, including
        the root ``okf_version`` declaration. Submodules are never written.

        Args:
            folder: Vault-relative folder (``""`` for the bundle root).
                Folded through
                :func:`~markdown_vault_mcp.utils.normalize_folder`, so the
                path arithmetic below is never handed a spelling with
                surrounding slashes.
            designated: Optional predicate restricting automatically created
                descendant indexes; omitted for the usual migration behavior.

            allow_protected: Allow this library call to change protected paths.

        Returns:
            An :class:`~markdown_vault_mcp.okf.OkfIndexResult`.

        Raises:
            InvalidRequestError: If protected paths were skipped; eligible writes remain.
            ReadOnlyError: If the vault is read-only.
            SubmodulePathError: If *folder* lies inside a git submodule.
            DocumentUnreadableError: If the folder's ``index.md`` exists but
                cannot be read; it is left untouched (#1608).
            IndexUnavailableError: If the index is not built.
            TimeoutError: If the queued index refresh exceeds its wait budget.
        """
        skipped: dict[str, str] = {}
        result = self._generate_index(folder, skipped, allow_protected, designated)
        written = [p for p in (result.path, *result.created) if p not in skipped]
        if skipped:
            raise InvalidRequestError(
                f"Generated indexes {written!r}; did not write {skipped!r}. "
                "Ask the operator to generate them or change the setting."
            )
        return result

    def _generate_index(
        self,
        folder: str,
        skipped: dict[str, str],
        allow_protected: bool,
        designated: Callable[[str], bool] | None,
    ) -> OkfIndexResult:
        """Generate eligible indexes and collect protected paths."""
        self._doc_mgr.ensure_writable()
        self._doc_mgr.ensure_index_current()
        self._require_built()
        folder = normalize_folder(folder) or ""
        self._doc_mgr.refuse_folder_in_submodule(folder, "folder")
        traversal = _IndexTraversal(
            folder,
            self._search_mgr.list(folder=folder or None),
            skipped,
            allow_protected,
            designated,
        )
        self._collect_index_entries(folder, traversal)
        sections = traversal.sections
        entries = sections.pop("", [])

        index_path = f"{folder}/index.md" if folder else "index.md"
        with self._doc_mgr.write_scope():
            existing = self._doc_mgr.read(index_path)
            heading = folder.rsplit("/", 1)[-1] if folder else OKF_ROOT_INDEX_TITLE
            body = build_index_markdown(heading, entries, sections=sections)
            reason = self._doc_mgr.protection_reason(
                index_path, allow_protected=allow_protected, allow_designated=True
            )
            preserved = False
            if reason:
                skipped[index_path] = reason
            else:
                with okf_write_suppressed():
                    preserved = self._doc_mgr.replace_body(
                        index_path,
                        existing.content if existing is not None else None,
                        body,
                        allow_protected=allow_protected,
                        allow_designated=True,
                    )
        return OkfIndexResult(
            path=index_path,
            entries=len(entries) + sum(len(items) for items in sections.values()),
            frontmatter_preserved=preserved,
            created=tuple(traversal.created),
        )

    def _collect_index_entries(self, folder: str, state: _IndexTraversal) -> None:
        """Collect a folder and fold unindexed descendants into sections."""
        from markdown_vault_mcp.types import NoteInfo

        prefix = f"{folder}/" if folder else ""
        section = (
            folder[len(state.root) + 1 :]
            if state.root and folder != state.root
            else folder
            if not state.root
            else ""
        )
        entries = state.sections.setdefault(section, [])
        subfolders: set[str] = set()
        for note in state.notes:
            if not isinstance(note, NoteInfo) or not note.path.startswith(prefix):
                continue
            rel = note.path[len(prefix) :]
            if "/" in rel:
                subfolders.add(rel.split("/", 1)[0])
            elif not is_okf_reserved_file(rel):
                raw = note.frontmatter.get("description")
                desc = raw.strip() if isinstance(raw, str) and raw.strip() else None
                entries.append((note.title, f"/{note.path}", desc))
        for sub in sorted(subfolders):
            child = prefix + sub
            has_index = self._has_index(child)
            if self._doc_mgr.in_submodule(child) and not has_index:
                continue
            if has_index or state.designated is None or state.designated(child):
                if not has_index:
                    result = self._generate_index(
                        child, state.skipped, state.allow_protected, state.designated
                    )
                    state.created.extend(
                        ([result.path] if result.path not in state.skipped else [])
                        + list(result.created)
                    )
                if f"{child}/index.md" not in state.skipped:
                    entries.append((f"{sub}/", f"/{child}/index.md", None))
            else:
                self._collect_index_entries(child, state)

    def _has_index(self, folder: str) -> bool:
        """Whether *folder* has an ``index.md``; an unreadable one counts.

        An index that exists but cannot be read is a file the listing may
        point at, and one ``generate_index`` must not overwrite (#1608).
        """
        try:
            return self._doc_mgr.read(f"{folder}/index.md") is not None
        except DocumentUnreadableError:
            return True

    def seed_log(
        self, *, folder: str = "", limit: int = 100, allow_protected: bool = False
    ) -> OkfLogResult:
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
                Folded through
                :func:`~markdown_vault_mcp.utils.normalize_folder`.
            limit: Maximum commits to read (clamped to 100 by the git layer).

            allow_protected: Allow this library call to change protected paths.

        Returns:
            An :class:`~markdown_vault_mcp.okf.OkfLogResult`.

        Raises:
            InvalidRequestError: If the destination is protected.
            ReadOnlyError: If the vault is read-only.
            SubmodulePathError: If *folder* lies inside a git submodule.
            DocumentUnreadableError: If ``log.md`` exists but cannot be read;
                it is left untouched (#1608).
            FileExistsError: If ``log.md`` already exists in *folder*.
        """
        self._doc_mgr.ensure_writable()
        folder = normalize_folder(folder) or ""
        self._doc_mgr.refuse_folder_in_submodule(folder, "folder")
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
        with okf_write_suppressed():
            self._doc_mgr.write(log_path, body, allow_protected=allow_protected)
        return OkfLogResult(path=log_path, commits=commits, dates=dates)
