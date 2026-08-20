"""Document CRUD, attachment, and path-validation manager.

Handles all read/write/edit/append/delete/rename operations, attachment I/O,
path validation, and backlink updates — all with dependency injection
and no back-reference to :class:`Vault`.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import shutil
import sqlite3
import tempfile
import threading
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import frontmatter as fm
import yaml

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    ReadOnlyError,
)
from markdown_vault_mcp.hashing import compute_etag, compute_file_hash
from markdown_vault_mcp.scanner import (
    extract_section,
    list_section_headings,
    parse_note,
)
from markdown_vault_mcp.types import (
    AttachmentContent,
    DeleteResult,
    EditResult,
    MoveFolderResult,
    NoteContent,
    RenameResult,
    SubtreeToc,
    TocEntry,
    WriteOperation,
    WriteResult,
)
from markdown_vault_mcp.utils import (
    effective_attachment_extensions,
    is_path_excluded,
    validate_path,
)
from markdown_vault_mcp.utils.links import (
    apply_link_replacement as _apply_link_replacement,
)
from markdown_vault_mcp.utils.links import (
    compute_new_raw_target as _compute_new_raw_target,
)
from markdown_vault_mcp.utils.text import (
    build_position_map as _build_position_map,
)
from markdown_vault_mcp.utils.text import (
    find_closest_match as _find_closest_match,
)
from markdown_vault_mcp.utils.text import (
    normalize_text as _normalize_text,
)
from markdown_vault_mcp.utils.text import (
    read_text_utf8 as _read_text_utf8,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.scanner import ChunkStrategy

logger = logging.getLogger(__name__)


_UMASK_LOCK = threading.Lock()
_umask: int | None = None


def _process_umask() -> int:
    """Return the process umask, read once and cached.

    ``os.umask`` is set-and-restore with no read-only accessor; we read it
    once under a lock so concurrent writers serialise the set-and-restore
    and never observe a transient mask. The umask is set by the parent
    (shell / systemd) before Python starts and is effectively immutable
    for the service lifetime, so a one-time read is correct.
    """
    global _umask
    if _umask is not None:
        return _umask
    with _UMASK_LOCK:
        # The lock serialises the set-and-restore so no writer in this
        # module observes a transient mask. A second writer that lost the
        # outer cache check re-reads and re-sets the same value (the umask
        # is process-global), so no inner guard is needed.
        mask = os.umask(0o077)
        os.umask(mask)
        _umask = mask
    return _umask


def _new_file_mode() -> int:
    """Mode for a freshly-created file, matching a plain ``open(path, "w")``."""
    return 0o666 & ~_process_umask()


class DocumentManager:
    """Manages document CRUD, attachments, path validation, and backlinks.

    All file I/O, FTS index mutations, and write-callback dispatch for
    individual documents flow through this class.

    Args:
        fts: The FTS index for upsert/delete operations.
        source_dir: Absolute path to the vault root directory.
        write_lock: Shared re-entrant lock serialising write operations.
        chunk_strategy: Strategy for splitting documents into chunks.
        read_only: When ``True`` (default), write operations raise
            :exc:`~markdown_vault_mcp.exceptions.ReadOnlyError`. Stays
            ``True`` for the same reason ``Vault``'s does: it is the
            library-tier fail-safe, not the operator default the server
            flipped in #1113.
        exclude_patterns: Glob patterns for paths to exclude.
        attachment_extensions: Allowlist of attachment file extensions.
        max_note_read_bytes: Maximum bytes returned by full-document reads.
            ``0`` disables the limit (default ``262144``, i.e. 256 KB).
        on_write_callback: Fires after a successful write to enqueue a
            git commit.  Signature: ``(abs_path, content, operation)``.
        mark_paths_dirty: Routes FTS-affecting write operations through
            the single-owner :class:`IndexWriter` (#559).  Called with an
            iterable of vault-relative paths after each successful
            mutation; the writer drains the resulting dirty set via a
            ``ProcessDirtyPaths`` job.  ``None`` (default) leaves the
            DocumentManager FTS-side-effect-free, which is the contract
            used by the isolation tests.
        title_field: Frontmatter key consulted first when resolving document
            titles.  Must match the value the index managers use, or
            ``read()`` titles would diverge from search titles.
    """

    def __init__(
        self,
        fts: FTSIndex,
        source_dir: Path,
        *,
        write_lock: threading.RLock,
        chunk_strategy: ChunkStrategy,
        read_only: bool = True,
        exclude_patterns: list[str] | None = None,
        attachment_extensions: list[str] | None = None,
        max_note_read_bytes: int = 262144,
        on_write_callback: Callable[[Path, str, WriteOperation], None] | None = None,
        mark_paths_dirty: Callable[[Iterable[str]], None] | None = None,
        title_field: str = "title",
        okf_write_enrich: Callable[[str, WriteOperation], str] | None = None,
    ) -> None:
        self._fts = fts
        self._source_dir = source_dir
        self._file_write_lock = write_lock
        self._chunk_strategy = chunk_strategy
        self._read_only = read_only
        self._exclude_patterns = exclude_patterns
        self._attachment_extensions = attachment_extensions
        self._max_note_read_bytes = max_note_read_bytes
        self._on_write_callback = on_write_callback or (lambda *_a: None)
        self._mark_paths_dirty = mark_paths_dirty
        self._title_field = title_field
        # OKF enforced-write hook (#964): transforms the final note text
        # (stamp `generated`, clear `verified`) before it lands. None when
        # OKF_WRITE is off — zero overhead on the write path.
        self._okf_write_enrich = okf_write_enrich

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _check_writable(self) -> None:
        """Raise ReadOnlyError if the vault is configured as read-only.

        Raises:
            ReadOnlyError: If ``read_only=True``.
        """
        if self._read_only:
            raise ReadOnlyError(
                "Vault is read-only; write operations are not permitted."
            )

    def ensure_writable(self) -> None:
        """Raise :exc:`ReadOnlyError` if the vault is read-only.

        Public entry point for collaborators (e.g. the OKF migration
        manager) that need to reject up front, before any per-note write is
        reached — a whole-vault transform over an empty read-only vault
        would otherwise silently no-op.
        """
        self._check_writable()

    def _effective_attachment_extensions(self) -> frozenset[str]:
        """Return the effective set of allowed attachment extensions.

        Returns:
            Frozenset of lower-case extension strings (without leading dot).
            The special value ``frozenset(["*"])`` means all non-.md files.
        """
        return effective_attachment_extensions(self._attachment_extensions)

    def _is_attachment(self, path: str) -> bool:
        """Return True if *path* is an allowed non-.md attachment.

        Args:
            path: Relative path to check.

        Returns:
            ``True`` when the extension is in the allowlist and is not ``.md``.
        """
        if path.endswith(".md"):
            return False
        suffix = Path(path).suffix.lstrip(".").lower()
        exts = self._effective_attachment_extensions()
        return "*" in exts or suffix in exts

    def _is_path_excluded(self, path: str) -> bool:
        """Check whether *path* matches any configured exclude pattern.

        Args:
            path: Relative POSIX path string.

        Returns:
            ``True`` if the path matches any pattern in
            ``self._exclude_patterns``.
        """
        return is_path_excluded(path, self._exclude_patterns)

    def _validate_path(self, path: str) -> Path:
        """Resolve a relative path and validate it is inside source_dir.

        Args:
            path: Relative document path.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path escapes the source directory or does
                not end with ``.md``.
        """
        return validate_path(path, self._source_dir)

    def _validate_attachment_path(self, path: str) -> Path:
        """Resolve and validate a non-.md attachment path.

        Args:
            path: Relative attachment path.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path escapes the source directory, ends with
                ``.md``, or has an extension not in the attachment allowlist.
        """
        if path.endswith(".md"):
            raise ValueError(
                f"Path ends with '.md' — use the note read/write methods "
                f"instead: {path}"
            )
        exts = self._effective_attachment_extensions()
        suffix = Path(path).suffix.lstrip(".").lower()
        if "*" not in exts and suffix not in exts:
            allowed_str = ", ".join(f".{e}" for e in sorted(exts))
            raise ValueError(
                f"Extension '.{suffix}' is not in the attachment allowlist. "
                f"Allowed: {allowed_str}. "
                "Set MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS=* to allow "
                "all non-.md files."
            )
        abs_path = (self._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._source_dir.resolve()):
            raise ValueError(f"Path traversal detected: {path}")
        return abs_path

    def _validate_dir_path(self, path: str) -> Path:
        """Resolve a relative folder path and validate it is inside the vault.

        Unlike :meth:`_validate_path`, this does not require a ``.md`` suffix —
        it is for directory prefixes used by :meth:`move_folder` and
        :meth:`_subtree_toc`.

        Args:
            path: Relative folder path (e.g. ``"drafts"`` or ``"a/b"``).

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path is empty or escapes the source directory.
        """
        if not path or path in (".", "/"):
            raise ValueError(f"Invalid folder path: {path!r}")
        abs_path = (self._source_dir / path).resolve()
        source_root = self._source_dir.resolve()
        if abs_path == source_root or not abs_path.is_relative_to(source_root):
            raise ValueError(f"Path traversal detected: {path}")
        return abs_path

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def read(self, path: str, *, section: str | None = None) -> NoteContent | None:
        """Read a document or a single section from disk.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).
            section: When provided, return the whole section whose heading
                matches *section* — its body and any sub-sections, up to the
                next heading at the same or higher level (case-sensitive;
                internal whitespace is collapsed before comparison). ``None``
                returns the whole document.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent`, or ``None`` in
            whole-document mode if the file does not exist or exists but cannot
            be parsed (invalid UTF-8, an I/O error, or malformed YAML
            frontmatter — a warning is logged in those cases). When ``section``
            is provided, the returned ``NoteContent.frontmatter`` is an empty
            dict ``{}`` because section reads do not synthesise per-section
            frontmatter. Call ``read(path)`` without ``section=`` to get the
            full document's frontmatter.

        Raises:
            ValueError: When *section* is provided and is empty / whitespace,
                or when the document does not contain a section with that
                heading. (Path-not-found also raises in section mode rather
                than returning ``None``, since "no document" implies "no
                section".)
        """
        if section is not None:
            if not section.strip():
                raise ValueError("section must be a non-empty heading or None")
            return self._read_section(path, section.strip())

        abs_path = (self._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._source_dir.resolve()):
            return None
        if not abs_path.is_file():
            return None

        # Enforce MAX_NOTE_READ_BYTES (.md whole-document reads only — section=
        # reads short-circuit with an early return above; non-.md paths fall
        # through to parse_note() and return None on UnicodeDecodeError, same
        # as historical behaviour, so the cap stays scoped to its env-var name).
        is_md = path.lower().endswith(".md")
        if is_md and self._max_note_read_bytes > 0:
            try:
                size_bytes = abs_path.stat().st_size
            except OSError:
                # File deleted/inaccessible between is_file() and stat() —
                # match the surrounding parse_note OSError handling at the
                # bottom of this method (return None, don't raise).
                return None
            if size_bytes > self._max_note_read_bytes:
                raise ValueError(
                    f"Document {path!r} is {size_bytes} bytes "
                    f"({size_bytes / 1024:.1f} KB), exceeds "
                    f"MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES "
                    f"({self._max_note_read_bytes} bytes / "
                    f"{self._max_note_read_bytes / 1024:.0f} KB). "
                    f"Use read({path!r}, section=...) for partial reads "
                    f"(see search() output's heading field), or increase "
                    f"MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES if you need the "
                    f"full document in context."
                )

        # Guard both on-disk reads (parse_note's, and the content read below)
        # in one try: a file removed/truncated/made-unparseable on disk between
        # the size check and here — or between the two reads — degrades to None
        # rather than leaking the raw exception (#742, #745). yaml.YAMLError
        # covers frontmatter that became malformed after indexing.
        # (parse_note decodes without universal-newline translation; the content
        # is read separately via _read_text_utf8, which applies it, so the two
        # reads are intentionally not collapsed — see #745.)
        try:
            note = parse_note(
                abs_path,
                self._source_dir,
                self._chunk_strategy,
                title_field=self._title_field,
            )
            raw_content = _read_text_utf8(abs_path)
        except (UnicodeDecodeError, OSError, yaml.YAMLError) as exc:
            logger.warning("read(%s): could not parse file — %s", path, exc)
            return None

        etag = note.content_hash
        folder = str(Path(path).parent)
        if folder == ".":
            folder = ""

        return NoteContent(
            path=note.path,
            title=note.title,
            folder=folder,
            content=raw_content,
            frontmatter=note.frontmatter,
            modified_at=note.modified_at,
            etag=etag,
        )

    def _read_section(self, path: str, heading: str) -> NoteContent:
        """Return a NoteContent containing the full named section.

        The section is reconstructed from the document on disk: its span runs
        from just after the heading line to the next heading at the same or
        higher level (or end of document), so the *whole* section is returned
        even when the chunker fragmented it into several rows — an over-budget
        body split on paragraphs, or a parent split at its sub-headings (#741).
        Sub-sections under the requested heading are included.

        Args:
            path: Relative document path.
            heading: Heading string to match (internal whitespace collapsed;
                case-sensitive). When a document contains multiple sections
                with the same heading text, the first occurrence is returned.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent` with the
            section's content.  ``frontmatter`` is always ``{}`` because
            section reads do not synthesise per-section frontmatter; call
            :meth:`read` without ``section=`` to get the full document's
            frontmatter.

        Raises:
            ValueError: If the document is not indexed, its file cannot be
                read or decoded, its frontmatter cannot be parsed, or the
                heading is not found.
        """
        doc_row = self._fts.get_note(path)
        if doc_row is None:
            raise ValueError(
                f"Section '{heading}' not found in document {path}: "
                "document is not indexed or does not exist"
            )

        abs_path = self._validate_path(path)
        # The file decoded and parsed cleanly when it was indexed, but it may
        # have changed on disk since (the stale-index window). Map read failures
        # (UnicodeDecodeError/OSError) and malformed-frontmatter failures
        # (yaml.YAMLError) to a user-facing ValueError instead of leaking the
        # raw exception into the MCP error middleware.
        try:
            text = _read_text_utf8(abs_path)
        except (UnicodeDecodeError, OSError) as exc:
            raise ValueError(
                f"Section '{heading}' not found in document {path}: "
                "document file is not readable"
            ) from exc

        try:
            content = extract_section(text, heading)
        except yaml.YAMLError as exc:
            raise ValueError(
                f"Section '{heading}' not found in document {path}: "
                "document frontmatter is not parseable"
            ) from exc

        if content is None:
            # extract_section already parsed the frontmatter successfully above,
            # so list_section_headings (same parse) cannot raise here. Dedupe
            # so a repeated heading is suggested once and does not crowd the cap.
            available = list(dict.fromkeys(list_section_headings(text)))[:10]
            if available:
                suggestion = " — available headings include: " + ", ".join(
                    repr(h) for h in available
                )
            else:
                suggestion = " (document has no headings)"
            raise ValueError(
                f"Section '{heading}' not found in document {path}{suggestion}"
            )

        folder = str(Path(path).parent)
        if folder == ".":
            folder = ""

        return NoteContent(
            path=path,
            title=doc_row["title"],
            folder=folder,
            content=content,
            frontmatter={},  # section reads do not synthesise frontmatter
            modified_at=doc_row["modified_at"],
            etag="",  # ETag is whole-file; not meaningful for a section
        )

    def attachment_size(self, path: str) -> int:
        """Return the on-disk byte size of an attachment without reading it.

        Args:
            path: Relative attachment path.

        Returns:
            The file size in bytes (from ``stat``).

        Raises:
            ValueError: If the path escapes the source directory, has an
                extension not in the allowlist, or the file does not exist.
        """
        abs_path = self._validate_attachment_path(path)
        try:
            if not abs_path.is_file():
                raise ValueError(f"Attachment not found: {path}")
            return abs_path.stat().st_size
        except OSError as exc:
            raise ValueError(f"Attachment not found: {path}") from exc

    def read_attachment(self, path: str) -> AttachmentContent:
        """Read the binary content of a non-.md attachment.

        Args:
            path: Relative attachment path (e.g. ``"assets/diagram.pdf"``).

        Returns:
            :class:`~markdown_vault_mcp.types.AttachmentContent` with
            base64-encoded content and MIME type.

        Raises:
            ValueError: If the path escapes the source directory, has an
                extension not in the allowlist, or the file does not exist.
        """
        abs_path = self._validate_attachment_path(path)
        if not abs_path.is_file():
            raise ValueError(f"Attachment not found: {path}")

        stat = abs_path.stat()
        size_bytes = stat.st_size

        mime_type, _ = mimetypes.guess_type(path)
        raw = abs_path.read_bytes()
        content_base64 = base64.b64encode(raw).decode("ascii")
        etag = compute_etag(raw)
        return AttachmentContent(
            path=path,
            mime_type=mime_type,
            size_bytes=size_bytes,
            content_base64=content_base64,
            modified_at=stat.st_mtime,
            etag=etag,
        )

    def get_toc(
        self,
        path: str,
        *,
        max_level: int | None = None,
        max_notes: int = 200,
    ) -> list[TocEntry] | SubtreeToc:
        """Return a table of contents for a note or a folder subtree.

        When *path* ends in ``.md`` the result is a single note's flat
        outline: a :class:`~markdown_vault_mcp.types.TocEntry` list with the
        document title prepended as a synthetic H1. Otherwise *path* is treated
        as a folder prefix and the result is a
        :class:`~markdown_vault_mcp.types.SubtreeToc` aggregating the subtree.

        Args:
            path: Note path (``"a/b.md"``) or folder prefix (``"a/b"``).
            max_level: If set, drop headings with ``level`` above this; must be
                ``>= 1``. The synthetic H1 title is always present regardless of
                ``max_level`` (it is prepended after the level filter).
            max_notes: Folder mode only — cap on distinct notes (default 200);
                must be ``>= 1``.

        Returns:
            Note mode: ``list[TocEntry]``. Folder mode: :class:`~markdown_vault_mcp.types.SubtreeToc`.

        Raises:
            ValueError: If ``max_notes < 1`` or ``max_level < 1``; note mode, if
                no document exists at *path* or if the path escapes the vault;
                folder mode, if *path* is empty, the vault root
                (```.```/```/```), or escaping the vault.
        """
        if max_notes < 1:
            raise ValueError(f"max_notes must be >= 1, got {max_notes!r}")
        if max_level is not None and max_level < 1:
            raise ValueError(f"max_level must be >= 1, got {max_level!r}")
        if path.endswith(".md"):
            return self._note_toc(path, max_level=max_level)
        return self._subtree_toc(path, max_level=max_level, max_notes=max_notes)

    @staticmethod
    def _prepend_title_h1(title: str, headings: list[TocEntry]) -> list[TocEntry]:
        """Prepend the document title as a synthetic H1, dropping any real H1 whose text matches the title."""
        toc: list[TocEntry] = [TocEntry(heading=title, level=1)]
        toc.extend(h for h in headings if not (h.level == 1 and h.heading == title))
        return toc

    def _note_toc(self, path: str, *, max_level: int | None) -> list[TocEntry]:
        """Return a single note's flat outline, title prepended as a synthetic H1."""
        self._validate_path(path)
        row = self._fts.get_note(path)
        if row is None:
            raise ValueError(f"Document not found: {path}")
        title: str = row["title"]
        headings = self._fts.get_toc(path, max_level=max_level)
        return self._prepend_title_h1(title, headings)

    def _subtree_toc(
        self, path: str, *, max_level: int | None, max_notes: int
    ) -> SubtreeToc:
        """Return the nested-per-note TOC for every note under a folder prefix."""
        prefix = path.rstrip("/")
        self._validate_dir_path(prefix)
        notes_raw, truncated = self._fts.get_subtree_toc(
            prefix, max_level=max_level, max_notes=max_notes
        )
        # replace() copies path/title and swaps in the H1-prepended headings,
        # so a future SubtreeNote field is preserved automatically.
        notes = [
            replace(note, headings=self._prepend_title_h1(note.title, note.headings))
            for note in notes_raw
        ]
        return SubtreeToc(path=prefix, notes=notes, truncated=truncated)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def _check_if_match(self, abs_path: Path, path: str, if_match: str | None) -> None:
        """Enforce an optional etag precondition on *abs_path*.

        Args:
            abs_path: Absolute path of the file the etag refers to.
            path: Vault-relative path, used in the error.
            if_match: Etag from a previous read, or ``None`` to skip the
                check. A file that does not exist counts as a mismatch.

        Raises:
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash (or the file is missing).
        """
        if if_match is None:
            return
        if not abs_path.is_file():
            raise ConcurrentModificationError(
                path,
                expected=if_match,
                actual="(file does not exist)",
            )
        current_hash = compute_file_hash(abs_path)
        if current_hash != if_match:
            raise ConcurrentModificationError(
                path, expected=if_match, actual=current_hash
            )

    def _atomic_write(self, abs_path: Path, data: str | bytes) -> None:
        """Write *data* to *abs_path* atomically via a sibling tempfile.

        The temp file lands with :meth:`Path.replace` semantics; permission
        bits are preserved when the target already exists. A freshly-created
        target is chmod'd to ``0o666 & ~umask`` after the replace so fresh
        writes honour the process umask (matching a plain ``open``) instead
        of the ``0o600`` ``tempfile`` hardcodes. On failure the temp file is
        removed and the original is left untouched.

        Args:
            abs_path: Absolute destination path.
            data: Text (written UTF-8) or raw bytes.
        """
        existed = abs_path.is_file()
        if isinstance(data, str):
            with tempfile.NamedTemporaryFile(
                dir=abs_path.parent,
                mode="w",
                encoding="utf-8",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(data)
                tmp_name = tmp.name
        else:
            with tempfile.NamedTemporaryFile(
                dir=abs_path.parent,
                mode="wb",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(data)
                tmp_name = tmp.name
        if existed:
            shutil.copymode(abs_path, tmp_name)
        try:
            Path(tmp_name).replace(abs_path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        if not existed:
            abs_path.chmod(_new_file_mode())

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
        if_match: str | None = None,
    ) -> WriteResult:
        """Create or overwrite a document.

        Creates intermediate directories as needed.  If *frontmatter* is
        provided, it is serialised as a YAML header at the top of the file.

        Args:
            path: Relative document path.
            content: Markdown body (excluding frontmatter).
            frontmatter: Optional frontmatter dict serialised as YAML header.
            if_match: Optional etag from a previous :meth:`read` call.
                When provided, the write is only performed if the current
                file hash matches this value, preventing overwrites of
                concurrent modifications. Supplying *if_match* for a file
                that does not yet exist raises
                :exc:`~markdown_vault_mcp.exceptions.ConcurrentModificationError`.
                Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash (or the file does not exist).
            ValueError: If *path* escapes the source directory.
        """
        self._check_writable()
        with self._file_write_lock:
            abs_path = self._validate_path(path)
            self._check_if_match(abs_path, path, if_match)
            created = not abs_path.is_file()

            abs_path.parent.mkdir(parents=True, exist_ok=True)

            if frontmatter is not None:
                post = fm.Post(content, **frontmatter)
                file_content = fm.dumps(post)
            else:
                file_content = content

            self._finalize_content_write(abs_path, path, file_content, "write")
            result = WriteResult(path=path, created=created)

        return result

    def _finalize_content_write(
        self,
        abs_path: Path,
        path: str,
        content: str,
        operation: WriteOperation,
    ) -> None:
        """Run the shared tail of every content write.

        Enriches *content* through the OKF enforced-write hook (when wired),
        writes it atomically, marks the path dirty for the index writer, and
        fires the write callback.  Must be called with ``_file_write_lock``
        held.

        Args:
            abs_path: Absolute path of the target file.
            path: Vault-relative path of the target file.
            content: Full file content to persist.
            operation: The write operation reported to the enricher and the
                write callback.
        """
        if self._okf_write_enrich is not None:
            content = self._okf_write_enrich(content, operation)
        self._atomic_write(abs_path, content)
        if self._mark_paths_dirty is not None:
            self._mark_paths_dirty([path])
        self._on_write_callback(abs_path, content, operation)

    def append(
        self,
        path: str,
        content: str,
        if_match: str | None = None,
        *,
        create_if_missing: bool = False,
    ) -> WriteResult:
        """Append *content* to the end of a document (#980).

        Unlike :meth:`edit`, no prior read is required: the existing file
        content is never part of the request.  When the file does not end
        with a newline, one is inserted before *content* so the appended
        text starts on its own line; otherwise *content* is written
        directly after the existing content.  Like every content write,
        the file is persisted in the vault's normalized form (UTF-8, no
        BOM, ``\\n`` line endings), so a CRLF file is normalized on its
        first append.

        Args:
            path: Relative document path.
            content: Text to append.  Must be non-empty.
            if_match: Optional etag from a previous :meth:`read` call.
                When provided, the append is only performed if the current
                file hash matches this value.  Pass ``None`` (default) to
                append unconditionally.
            create_if_missing: When ``True``, a missing document is created
                with *content* as its body instead of raising
                :exc:`~markdown_vault_mcp.exceptions.DocumentNotFoundError`.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult` — ``created`` is
            ``True`` only when *create_if_missing* created a new file.

        Raises:
            ReadOnlyError: If the vault is read-only.
            DocumentNotFoundError: If the file does not exist and
                *create_if_missing* is ``False``.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash (or the file is missing).
            ValueError: If *content* is empty or *path* escapes the source
                directory.
        """
        self._check_writable()
        if not content:
            raise ValueError("content must not be empty")

        with self._file_write_lock:
            abs_path = self._validate_path(path)
            existed = abs_path.is_file()
            if not existed and not create_if_missing:
                raise DocumentNotFoundError(f"Document not found: {path}")
            self._check_if_match(abs_path, path, if_match)

            if existed:
                file_content = _read_text_utf8(abs_path)
                needs_newline = bool(file_content) and not file_content.endswith("\n")
                new_content = file_content + ("\n" if needs_newline else "") + content
            else:
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                new_content = content

            # An append is a content edit for the enforced-write layer and
            # the git commit callback alike.
            self._finalize_content_write(abs_path, path, new_content, "edit")
            result = WriteResult(path=path, created=not existed)

        return result

    def write_attachment(
        self,
        path: str,
        content: bytes,
        if_match: str | None = None,
    ) -> WriteResult:
        """Create or overwrite a non-.md attachment.

        Args:
            path: Relative attachment path (e.g. ``"assets/diagram.pdf"``).
            content: Raw bytes to write.
            if_match: Optional etag from a previous :meth:`read_attachment`
                call. When provided, the write is only performed if the
                current file hash matches this value, preventing overwrites
                of concurrent modifications. Pass ``None`` (default) to skip
                the check.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash, or *if_match* is supplied
                for a file that does not yet exist.
            ValueError: If the path escapes the source directory or has an
                extension not in the allowlist.
        """
        self._check_writable()
        with self._file_write_lock:
            abs_path = self._validate_attachment_path(path)
            self._check_if_match(abs_path, path, if_match)
            created = not abs_path.is_file()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(abs_path, content)
            result = WriteResult(path=path, created=created)
            self._on_write_callback(abs_path, "", "write")

        return result

    def edit(
        self,
        path: str,
        old_text: str | None = None,
        new_text: str = "",
        if_match: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> EditResult:
        """Patch a section of a document.

        Supports three modes:

        - **Exact match** (``old_text`` only): verifies *old_text* exists
          exactly once in the full file content (including frontmatter),
          replaces it with *new_text*.
        - **Line-range** (``line_start``/``line_end`` only): replaces the
          specified line range with *new_text*.
        - **Scoped match** (both): searches for *old_text* only within the
          specified line range, allowing disambiguation of repeated text.

        When exact match fails, a normalized comparison is attempted
        (Unicode NFC, dash/quote normalization, whitespace collapsing).
        If a unique normalized match is found, it is used and
        ``match_type="normalized"`` is returned.

        Args:
            path: Relative document path.
            old_text: Text to replace. Required for exact-match and
                scoped-match modes.  Must appear exactly once (in the
                file or in the line range).
            new_text: Replacement text. When using line-range mode with an
                empty string (``""``), the selected lines are replaced with a
                single blank line. To delete lines entirely, pass the literal
                content of those lines as *old_text* (scoped-match mode) and
                supply an empty *new_text*, which removes that text span
                without inserting a blank line.
            if_match: Optional etag from a previous :meth:`read` call.
                When provided, the edit is only performed if the current
                file hash matches this value, preventing edits based on
                stale content. Pass ``None`` (default) to skip the check.
            line_start: First line to replace (1-based, inclusive).
                Must be provided together with *line_end*.
            line_end: Last line to replace (1-based, inclusive).
                Must be provided together with *line_start*.

        Returns:
            :class:`~markdown_vault_mcp.types.EditResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            DocumentNotFoundError: If the file does not exist.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            EditConflictError: If *old_text* is not found or appears
                more than once.
            ValueError: If parameter combination is invalid, or line
                numbers are out of range.
        """
        self._check_writable()

        # --- Parameter validation ---
        if old_text is not None and not old_text:
            raise ValueError("old_text must not be empty")
        has_lines = line_start is not None or line_end is not None
        if old_text is None and not has_lines:
            raise ValueError("Must provide old_text, line_start/line_end, or both")
        if (line_start is None) != (line_end is None):
            raise ValueError("Must provide both line_start and line_end, not just one")
        if line_start is not None and line_end is not None:
            if line_start < 1:
                raise ValueError("line_start must be >= 1 (lines are 1-based)")
            if line_start > line_end:
                raise ValueError(
                    f"line_start ({line_start}) must be <= line_end ({line_end})"
                )

        with self._file_write_lock:
            abs_path = self._validate_path(path)
            if not abs_path.is_file():
                raise DocumentNotFoundError(f"Document not found: {path}")

            self._check_if_match(abs_path, path, if_match)

            file_content = _read_text_utf8(abs_path)

            if has_lines:
                assert line_start is not None and line_end is not None
                new_content, match_type = self._edit_with_lines(
                    file_content,
                    old_text,
                    new_text,
                    line_start,
                    line_end,
                    path,
                )
            else:
                assert old_text is not None
                new_content, match_type = self._edit_with_text(
                    file_content, old_text, new_text, path
                )

            self._finalize_content_write(abs_path, path, new_content, "edit")

        return EditResult(path=path, replacements=1, match_type=match_type)

    def _edit_with_lines(
        self,
        file_content: str,
        old_text: str | None,
        new_text: str,
        line_start: int,
        line_end: int,
        path: str,
    ) -> tuple[str, str]:
        """Handle line-range and scoped-match edit modes.

        Returns:
            Tuple of (new_file_content, match_type).
        """
        lines = file_content.split("\n")
        total_lines = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
        if line_end > total_lines:
            raise ValueError(
                f"line_end ({line_end}) out of range (file has {total_lines} lines)"
            )

        start_idx = line_start - 1
        end_idx = line_end

        if old_text is not None:
            scope = "\n".join(lines[start_idx:end_idx])
            context_desc = f"lines {line_start}-{line_end} of {path}"
            new_scope, match_type = self._match_and_replace(
                scope, old_text, new_text, path, context_desc=context_desc
            )
            lines[start_idx:end_idx] = new_scope.split("\n")
        else:
            match_type = "exact"
            replacement_lines = new_text.rstrip("\n").split("\n") if new_text else [""]
            lines[start_idx:end_idx] = replacement_lines

        return "\n".join(lines), match_type

    def _edit_with_text(
        self,
        file_content: str,
        old_text: str,
        new_text: str,
        path: str,
    ) -> tuple[str, str]:
        """Handle exact-match edit mode (with normalized fallback).

        Returns:
            Tuple of (new_file_content, match_type).
        """
        return self._match_and_replace(file_content, old_text, new_text, path)

    def _match_and_replace(
        self,
        content: str,
        old_text: str,
        new_text: str,
        path: str,
        context_desc: str | None = None,
    ) -> tuple[str, str]:
        """Try exact match, then normalized match, then raise with diagnostics.

        Args:
            content: The text to search within (full file or line-range scope).
            old_text: Text to find and replace.
            new_text: Replacement text.
            path: Vault-relative file path, used in error messages.
            context_desc: Optional human-readable context for error messages.

        Returns:
            Tuple of (new_content, match_type).
        """
        location = context_desc or path
        count = content.count(old_text)

        if count == 1:
            return content.replace(old_text, new_text, 1), "exact"

        if count > 1:
            raise EditConflictError(
                f"old_text appears {count} times in {location}; "
                f"must appear exactly once"
            )

        # count == 0: try normalized matching.
        normalized_content = _normalize_text(content)
        normalized_old = _normalize_text(old_text)
        norm_count = normalized_content.count(normalized_old)

        if norm_count == 1:
            pos_map = _build_position_map(content, normalized_content)
            norm_start = normalized_content.index(normalized_old)
            norm_end = norm_start + len(normalized_old)
            orig_start = pos_map[norm_start]
            orig_end = pos_map[norm_end]
            new_content = content[:orig_start] + new_text + content[orig_end:]
            return new_content, "normalized"

        if norm_count > 1:
            raise EditConflictError(
                f"old_text appears {norm_count} times in {location} after "
                f"normalization; must appear exactly once"
            )

        # norm_count == 0: raise with diagnostics.
        diag = _find_closest_match(old_text, content)
        raise EditConflictError(f"old_text not found in {location}", **diag)

    def delete(self, path: str, if_match: str | None = None) -> DeleteResult:
        """Delete a document or attachment.

        Removes the file from disk.  For ``.md`` documents, also removes all
        FTS and embedding index entries.  For attachments, only the file is
        deleted (no index update).

        Args:
            path: Relative document or attachment path.
            if_match: Optional etag from a previous :meth:`read` or
                :meth:`read_attachment` call. When provided, the deletion is
                only performed if the current file hash matches this value.
                Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.DeleteResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            DocumentNotFoundError: If the file does not exist.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            ValueError: If the path escapes the source directory, or (for
                non-.md paths) has an extension not in the attachment
                allowlist.
        """
        self._check_writable()
        with self._file_write_lock:
            if path.endswith(".md"):
                abs_path = self._validate_path(path)
                if not abs_path.is_file():
                    raise DocumentNotFoundError(f"Document not found: {path}")
                self._check_if_match(abs_path, path, if_match)
                abs_path.unlink()
                if self._mark_paths_dirty is not None:
                    self._mark_paths_dirty([path])
            else:
                abs_path = self._validate_attachment_path(path)
                if not abs_path.is_file():
                    raise DocumentNotFoundError(f"Attachment not found: {path}")
                self._check_if_match(abs_path, path, if_match)
                abs_path.unlink()

            self._on_write_callback(abs_path, "", "delete")

        return DeleteResult(path=path)

    def rename(
        self,
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        *,
        update_links: bool = False,
    ) -> RenameResult:
        """Rename or move a document or attachment.

        Renames the file on disk.  For ``.md`` documents, also updates FTS
        and embedding index entries.  For attachments, only the file is moved
        (no index update).  Creates intermediate directories for *new_path*
        as needed.

        When *update_links* is ``True`` and *old_path* is a ``.md`` document,
        every document that links to *old_path* is also updated so its links
        point to *new_path*.

        Args:
            old_path: Current relative document or attachment path.
            new_path: Target relative document or attachment path.
            if_match: Optional etag from a previous :meth:`read` or
                :meth:`read_attachment` call for *old_path*. When provided,
                the rename is only performed if the current file hash matches
                this value. Pass ``None`` (default) to skip the check.
            update_links: When ``True``, find all documents that link to
                *old_path* and rewrite their link targets to point to
                *new_path*. Only applies to ``.md`` documents.

        Returns:
            :class:`~markdown_vault_mcp.types.RenameResult` with
            *updated_links* counting source documents successfully updated.

        Raises:
            ReadOnlyError: If the vault is read-only.
            DocumentNotFoundError: If *old_path* does not exist.
            DocumentExistsError: If *new_path* already exists.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current hash of *old_path*.
            ValueError: If either path escapes the source directory, or (for
                non-.md paths) has an extension not in the attachment
                allowlist.
        """
        self._check_writable()
        updated_links = 0
        backlink_callbacks: list[tuple[Path, str]] = []

        with self._file_write_lock:
            if old_path.endswith(".md"):
                old_abs = self._validate_path(old_path)
                new_abs = self._validate_path(new_path)

                if not old_abs.is_file():
                    raise DocumentNotFoundError(f"Document not found: {old_path}")
                if new_abs.is_file():
                    raise DocumentExistsError(f"Target already exists: {new_path}")
                self._check_if_match(old_abs, old_path, if_match)

                backlinks = self._fts.get_backlinks(old_path) if update_links else []

                new_abs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_abs), str(new_abs))

                note = parse_note(
                    new_abs,
                    self._source_dir,
                    self._chunk_strategy,
                    title_field=self._title_field,
                )

                callback_content = _read_text_utf8(new_abs)

                backlink_callbacks, backlink_paths = self._update_backlinks(
                    old_path, new_path, backlinks
                )
                updated_links = len(backlink_callbacks)

                if self._mark_paths_dirty is not None:
                    dirty: list[str] = [old_path, note.path]
                    dirty.extend(backlink_paths)
                    self._mark_paths_dirty(dirty)
            else:
                old_abs = self._validate_attachment_path(old_path)
                new_abs = self._validate_attachment_path(new_path)

                if not old_abs.is_file():
                    raise DocumentNotFoundError(f"Attachment not found: {old_path}")
                if new_abs.is_file():
                    raise DocumentExistsError(f"Target already exists: {new_path}")
                self._check_if_match(old_abs, old_path, if_match)

                new_abs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_abs), str(new_abs))

                callback_content = ""

            self._on_write_callback(new_abs, callback_content, "rename")
            for src_abs, src_content in backlink_callbacks:
                self._on_write_callback(src_abs, src_content, "edit")

        return RenameResult(
            old_path=old_path,
            new_path=new_path,
            updated_links=updated_links,
        )

    def _rewrite_one_source(
        self,
        source_abs: Path,
        rewrites: list[tuple[str, str, str | None, str, str]],
        source_path: str,
    ) -> str:
        """Rewrite all link targets in a single source file in one pass.

        Reads *source_abs*, applies every ``(link_type, raw_target, fragment,
        target_old, target_new)`` rewrite using the source's *current* vault
        path (which, for a moved source, is its new path), and writes the
        result back atomically.

        Args:
            source_abs: Absolute path of the source file to rewrite (its
                current on-disk location).
            rewrites: One tuple per link to rewrite:
                ``(link_type, raw_target, fragment, target_old, target_new)``.
            source_path: Vault-relative path of the source at its current
                location — used for correct relative-path computation.

        Returns:
            The rewritten file content.
        """
        content = _read_text_utf8(source_abs)
        for link_type, raw_target, fragment, target_old, target_new in rewrites:
            new_raw = _compute_new_raw_target(
                link_type,
                raw_target,
                fragment,
                target_new,
                source_path=source_path,
                old_path=target_old,
            )
            content = _apply_link_replacement(content, link_type, raw_target, new_raw)
        self._atomic_write(source_abs, content)
        return content

    def _rewrite_link_sources(
        self,
        by_source: dict[str, list[tuple[str, dict[str, Any]]]],
        target_map: dict[str, str],
        *,
        op_name: str,
    ) -> tuple[list[tuple[Path, str]], list[str], list[str]]:
        """Rewrite link targets in each grouped source file (best effort).

        Shared engine behind :meth:`rename` (single target) and
        :meth:`move_folder` (many targets). A source that cannot be
        rewritten is logged and reported without aborting the operation.

        This method must be called while ``_file_write_lock`` is held. It
        does **not** fire write callbacks or mark paths dirty itself — the
        caller is responsible for both.

        Args:
            by_source: Mapping of source path (at its *current* on-disk
                location) to ``(target_old, backlink_row)`` pairs to rewrite
                within that source.
            target_map: Mapping of old target path to new target path; must
                cover every ``target_old`` in *by_source*.
            op_name: Caller name used as the log-message prefix.

        Returns:
            Tuple ``(callbacks, dirty_paths, failed_sources)``:

            * ``callbacks`` — ``(abs_path, new_content)`` pairs for every
              source document that was successfully rewritten.
            * ``dirty_paths`` — vault-relative paths of those same sources,
              for the caller to feed to ``mark_paths_dirty``.
            * ``failed_sources`` — sources that were skipped or failed.
        """
        pending_callbacks: list[tuple[Path, str]] = []
        dirty_paths: list[str] = []
        failed_sources: list[str] = []
        for source_path, items in by_source.items():
            try:
                source_abs = self._validate_path(source_path)
                if not source_abs.is_file():
                    logger.warning(
                        "%s: skipping %s — file not found",
                        op_name,
                        source_path,
                    )
                    failed_sources.append(source_path)
                    continue
                rewrites = [
                    (
                        row["link_type"],
                        row["raw_target"],
                        row["fragment"],
                        target_old,
                        target_map[target_old],
                    )
                    for target_old, row in items
                ]
                content = self._rewrite_one_source(source_abs, rewrites, source_path)
                pending_callbacks.append((source_abs, content))
                dirty_paths.append(source_path)
            except (OSError, UnicodeDecodeError, ValueError, sqlite3.Error) as exc:
                logger.warning("%s: failed to update %s: %s", op_name, source_path, exc)
                failed_sources.append(source_path)
            except Exception as exc:
                logger.warning(
                    "%s: unexpected error updating %s: %s",
                    op_name,
                    source_path,
                    exc,
                    exc_info=True,
                )
                failed_sources.append(source_path)
        return pending_callbacks, dirty_paths, failed_sources

    def _update_backlinks(
        self,
        old_path: str,
        new_path: str,
        backlinks: list[dict[str, Any]],
    ) -> tuple[list[tuple[Path, str]], list[str]]:
        """Rewrite source files that link to *old_path* to point to *new_path*.

        Called by :meth:`rename` after the file has already been moved on
        disk.  Each source file is read, all of its links to *old_path*
        are rewritten in a single pass, then written back.

        This method must be called while ``_file_write_lock`` is held.  It
        does **not** fire write callbacks or mark paths dirty itself —
        :meth:`rename` is responsible for both, using the returned
        ``(callbacks, dirty_paths)`` pair.

        Args:
            old_path: Vault-relative path that was renamed (the old location).
            new_path: Vault-relative path after the rename (the new location).
            backlinks: Rows returned by :meth:`FTSIndex.get_backlinks` before
                the rename.

        Returns:
            Tuple ``(callbacks, dirty_paths)``:

            * ``callbacks`` — list of ``(abs_path, new_content)`` pairs for
              every source document that was successfully rewritten.
            * ``dirty_paths`` — vault-relative paths of those same source
              documents, for the caller to feed to
              ``mark_paths_dirty``.
        """
        if not backlinks:
            return [], []

        by_source: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for row in backlinks:
            by_source[row["source_path"]].append((old_path, row))

        if old_path in by_source:
            by_source[new_path] = by_source.pop(old_path)

        pending_callbacks, dirty_paths, _failed = self._rewrite_link_sources(
            by_source, {old_path: new_path}, op_name="_update_backlinks"
        )
        return pending_callbacks, dirty_paths

    def _plan_folder_move(
        self, old_abs: Path, old_dir: str, new_dir: str
    ) -> tuple[list[tuple[Path, Path]], dict[str, str], list[tuple[Path, str]]]:
        """Enumerate the subtree under *old_abs* and gate on collisions.

        Pure planning: walks the filesystem (not the index — attachments and
        other files are not indexed), classifies each file, and fails before
        anything moves if a destination already exists.

        Args:
            old_abs: Absolute path of the source folder.
            old_dir: Vault-relative source prefix, as passed by the caller
                (used verbatim in error messages).
            new_dir: Vault-relative target prefix.

        Returns:
            Tuple ``(moves, md_map, attachment_moves)``:

            * ``moves`` — ``(src_abs, dst_abs)`` for every file to move.
            * ``md_map`` — old→new vault-relative path map (``.md`` only).
            * ``attachment_moves`` — ``(dst_abs, new_rel_path)`` for
              allowlisted attachments.

        Raises:
            DocumentNotFoundError: If the folder contains no files.
            DocumentExistsError: If any destination file already exists.
        """
        old_rel = old_dir.strip("/")
        new_rel = new_dir.strip("/")
        attachment_exts = self._effective_attachment_extensions()
        moves: list[tuple[Path, Path]] = []  # (src_abs, dst_abs)
        md_map: dict[str, str] = {}  # old_rel_path -> new_rel_path (.md only)
        attachment_moves: list[tuple[Path, str]] = []  # (dst_abs, new_rel)
        for src_abs in sorted(old_abs.rglob("*")):
            if not src_abs.is_file():
                continue
            rel_within = src_abs.relative_to(old_abs).as_posix()
            old_path = f"{old_rel}/{rel_within}"
            new_path = f"{new_rel}/{rel_within}"
            dst_abs = (self._source_dir / new_path).resolve()
            moves.append((src_abs, dst_abs))
            if old_path.endswith(".md"):
                md_map[old_path] = new_path
            else:
                suffix = src_abs.suffix.lstrip(".").lower()
                if "*" in attachment_exts or suffix in attachment_exts:
                    attachment_moves.append((dst_abs, new_path))

        if not moves:
            raise DocumentNotFoundError(f"Folder is empty: {old_dir}")

        # Atomic collision gate — fail before moving anything.
        for _src_abs, dst_abs in moves:
            if dst_abs.exists():
                rel = dst_abs.relative_to(self._source_dir.resolve()).as_posix()
                raise DocumentExistsError(f"Target already exists: {rel}")

        return moves, md_map, attachment_moves

    def _dispatch_move_callbacks(
        self,
        md_map: dict[str, str],
        attachment_moves: list[tuple[Path, str]],
        pending_callbacks: list[tuple[Path, str]],
    ) -> None:
        """Fire write callbacks for a completed folder move.

        Emits ``rename`` for every moved note and allowlisted attachment,
        and ``edit`` for every rewritten source. A source inside the moved
        subtree already gets a ``rename`` callback (it is in *md_map*), so
        its redundant ``edit`` is skipped to avoid a duplicate git commit.

        Args:
            md_map: Old→new vault-relative path map for moved notes.
            attachment_moves: ``(dst_abs, new_rel_path)`` for moved
                allowlisted attachments.
            pending_callbacks: ``(abs_path, new_content)`` for rewritten
                link sources.
        """
        moved_note_paths = set(md_map.values())
        source_root = self._source_dir.resolve()
        for _old_path, new_path in md_map.items():
            new_note_abs = (self._source_dir / new_path).resolve()
            self._on_write_callback(
                new_note_abs, _read_text_utf8(new_note_abs), "rename"
            )
        for dst_abs, _new_rel in attachment_moves:
            self._on_write_callback(dst_abs, "", "rename")
        for src_abs, src_content in pending_callbacks:
            src_rel = src_abs.relative_to(source_root).as_posix()
            if src_rel in moved_note_paths:
                continue
            self._on_write_callback(src_abs, src_content, "edit")

    def move_folder(self, old_dir: str, new_dir: str) -> MoveFolderResult:
        """Move an entire folder subtree to a new prefix, rewriting links.

        Moves every file under *old_dir* (``.md`` notes, allowlisted
        attachments, and any other files) to the matching path under
        *new_dir*, preserving relative structure. All links across the vault
        that point into the moved subtree — including links *between*
        documents inside it — are rewritten in a single pass.

        The move is atomic at the gate: if any destination file path already
        exists, the operation raises before moving anything. Link rewrites are
        best-effort: a source that cannot be rewritten is logged and reported
        in :attr:`MoveFolderResult.failed_links` without aborting the move.

        Args:
            old_dir: Relative source folder prefix (e.g. ``"drafts"``).
            new_dir: Relative target folder prefix (e.g. ``"archive/2026"``).

        Returns:
            :class:`~markdown_vault_mcp.types.MoveFolderResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            DocumentNotFoundError: If *old_dir* is missing, not a directory,
                or empty.
            DocumentExistsError: If any destination file already exists.
            ValueError: If either path escapes the vault, is the vault root,
                or one path is nested inside the other.
            OSError: If the OS raises during the move phase (e.g. a permission
                error, full disk, or concurrent file removal). The pre-move
                collision gate prevents destination clashes, but an OS error
                mid-move can leave the subtree partially moved with the index
                unchanged; a subsequent reindex reconciles the index with the
                on-disk state.
        """
        self._check_writable()

        with self._file_write_lock:
            old_abs = self._validate_dir_path(old_dir)
            new_abs = self._validate_dir_path(new_dir)

            if not old_abs.is_dir():
                raise DocumentNotFoundError(f"Folder not found: {old_dir}")

            # Reject nesting in either direction (would corrupt the prefix map).
            if old_abs == new_abs:
                raise ValueError("old_dir and new_dir are the same folder")
            if new_abs.is_relative_to(old_abs) or old_abs.is_relative_to(new_abs):
                raise ValueError("move_folder: old_dir and new_dir must not be nested")

            # 1+2. Enumerate the subtree, build the old->new path map, and
            #      gate on destination collisions before moving anything.
            moves, md_map, attachment_moves = self._plan_folder_move(
                old_abs, old_dir, new_dir
            )

            # 3. Capture backlinks for every moved note BEFORE the move, while
            #    the index still reflects old paths. Annotate each row with its
            #    target's old/new path so a multi-target rewrite is possible.
            annotated: list[tuple[str, dict[str, Any]]] = []  # (target_old, row)
            for target_old in md_map:
                for row in self._fts.get_backlinks(target_old):
                    annotated.append((target_old, row))

            # 4. Move every file.
            for src_abs, dst_abs in moves:
                dst_abs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src_abs), str(dst_abs))

            # 5. Single-pass link rewrite. Group annotated rows by source,
            #    remapping a source that moved to its new path so it is read at
            #    its new on-disk location.
            by_source: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
            for target_old, row in annotated:
                src_old = row["source_path"]
                src_new = md_map.get(src_old, src_old)  # remap if inside subtree
                by_source[src_new].append((target_old, row))

            pending_callbacks, dirty_paths, failed_links = self._rewrite_link_sources(
                by_source, md_map, op_name="move_folder"
            )

            # 6. Index: purge old note paths, reparse new note paths, plus the
            #    rewritten sources. A single mark_paths_dirty batch makes the
            #    writer run resolve_vault_wikilinks() once.
            if self._mark_paths_dirty is not None:
                move_dirty: list[str] = []
                move_dirty.extend(md_map.keys())  # old paths -> purged
                move_dirty.extend(md_map.values())  # new paths -> reparsed
                move_dirty.extend(dirty_paths)  # rewritten sources
                self._mark_paths_dirty(move_dirty)

            # 7. Callbacks: rename for every moved note + allowlisted attachment,
            #    edit for every rewritten source (minus intra-subtree dupes).
            self._dispatch_move_callbacks(md_map, attachment_moves, pending_callbacks)

            # 8. Remove the now-empty source tree. A leftover file (e.g. from a
            #    concurrent write) is logged rather than silently swallowed.
            try:
                shutil.rmtree(old_abs)
            except OSError as exc:
                logger.warning(
                    "move_folder: failed to remove source tree %s: %s",
                    old_dir,
                    exc,
                )

        return MoveFolderResult(
            old_dir=old_dir,
            new_dir=new_dir,
            files_moved=len(moves),
            updated_links=len(pending_callbacks),
            failed_links=failed_links,
        )
