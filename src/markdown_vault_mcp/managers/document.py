"""Document CRUD, attachment, and path-validation manager.

Handles all read/write/edit/delete/rename operations, attachment I/O,
path validation, and backlink updates — all with dependency injection
and no back-reference to :class:`Vault`.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import frontmatter as fm

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    ReadOnlyError,
)
from markdown_vault_mcp.hashing import compute_etag, compute_file_hash
from markdown_vault_mcp.scanner import parse_note
from markdown_vault_mcp.types import (
    AttachmentContent,
    DeleteResult,
    EditResult,
    NoteContent,
    RenameResult,
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

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Iterable

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.scanner import ChunkStrategy

logger = logging.getLogger(__name__)


class DocumentManager:
    """Manages document CRUD, attachments, path validation, and backlinks.

    All file I/O, FTS index mutations, and write-callback dispatch for
    individual documents flow through this class.

    Args:
        fts: The FTS index for upsert/delete operations.
        source_dir: Absolute path to the vault root directory.
        write_lock: Shared re-entrant lock serialising write operations.
        chunk_strategy: Strategy for splitting documents into chunks.
        read_only: When ``True``, write operations raise
            :exc:`~markdown_vault_mcp.exceptions.ReadOnlyError`.
        exclude_patterns: Glob patterns for paths to exclude.
        attachment_extensions: Allowlist of attachment file extensions.
        max_attachment_size_mb: Maximum attachment size in megabytes.
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
        max_attachment_size_mb: float = 1.0,
        max_note_read_bytes: int = 262144,
        on_write_callback: Callable[[Path, str, WriteOperation], None] | None = None,
        mark_paths_dirty: Callable[[Iterable[str]], None] | None = None,
    ) -> None:
        self._fts = fts
        self._source_dir = source_dir
        self._file_write_lock = write_lock
        self._chunk_strategy = chunk_strategy
        self._read_only = read_only
        self._exclude_patterns = exclude_patterns
        self._attachment_extensions = attachment_extensions
        self._max_attachment_size_mb = max_attachment_size_mb
        self._max_note_read_bytes = max_note_read_bytes
        self._on_write_callback = on_write_callback or (lambda *_a: None)
        self._mark_paths_dirty = mark_paths_dirty

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

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def read(self, path: str, *, section: str | None = None) -> NoteContent | None:
        """Read a document or a single section from disk.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).
            section: When provided, return only the chunk whose heading
                matches *section* (case-sensitive; internal whitespace is
                collapsed before comparison). ``None`` returns the whole
                document (today's behaviour).

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent`, or ``None`` if
            the file does not exist (whole-document mode). When ``section`` is
            provided, the returned ``NoteContent.frontmatter`` is an empty dict
            ``{}`` because section reads do not synthesise per-section frontmatter.
            Call ``read(path)`` without ``section=`` to get the full document's
            frontmatter.

        Raises:
            ValueError: When *section* is provided and is empty / whitespace,
                or when the document does not contain a chunk with that
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

        try:
            note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
        except (UnicodeDecodeError, OSError) as exc:
            logger.warning("read(%s): could not parse file — %s", path, exc)
            return None

        raw_content = abs_path.read_text(encoding="utf-8")
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
        """Return a NoteContent containing only the named section's chunk.

        Args:
            path: Relative document path.
            heading: Exact heading string to match in the sections table.
                When a document contains multiple sections with the same
                heading text (rare in practice), the first occurrence by
                ``start_line`` is returned.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent` with the
            section's content.  ``frontmatter`` is always ``{}`` because
            section reads do not synthesise per-section frontmatter; call
            :meth:`read` without ``section=`` to get the full document's
            frontmatter.

        Raises:
            ValueError: If the document is not indexed or the heading is
                not found.
        """
        doc_row = self._fts.get_note(path)
        if doc_row is None:
            raise ValueError(
                f"Section '{heading}' not found in document {path}: "
                "document is not indexed or does not exist"
            )
        section_row = self._fts.get_section(path, heading)
        if section_row is None:
            # Miss path only — fires a second SELECT over the same rows
            # get_section already fetched. Acceptable because the miss
            # path is by definition rare (LLM caller already gave us a
            # bad heading) and consolidating the queries would couple
            # get_section's return shape to error-message rendering.
            available = self._fts.list_section_headings(path, limit=10)
            if available:
                suggestion = " — available headings include: " + ", ".join(
                    repr(h) for h in available
                )
            else:
                suggestion = " (document has no indexed headings)"
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
            content=section_row["content"],
            frontmatter={},  # section reads do not synthesise frontmatter
            modified_at=doc_row["modified_at"],
            etag="",  # ETag is whole-file; not meaningful for a section
        )

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
            ValueError: If the file exceeds the configured size limit.
        """
        abs_path = self._validate_attachment_path(path)
        if not abs_path.is_file():
            raise ValueError(f"Attachment not found: {path}")

        stat = abs_path.stat()
        size_bytes = stat.st_size
        if self._max_attachment_size_mb > 0:
            limit_bytes = int(self._max_attachment_size_mb * 1024 * 1024)
            if size_bytes > limit_bytes:
                raise ValueError(
                    f"Attachment {path!r} is {size_bytes} bytes "
                    f"({size_bytes / 1024 / 1024:.1f} MB), exceeds "
                    f"MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB "
                    f"({self._max_attachment_size_mb} MB). "
                    f"Increase MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB if "
                    f"you need the bytes in context."
                )

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

    def get_toc(self, path: str) -> list[dict[str, Any]]:
        """Return table of contents for a document.

        Queries the FTS sections table for headings and prepends the document
        title as a synthetic H1 entry.

        Args:
            path: Relative path to the document (e.g. ``"notes/intro.md"``).

        Returns:
            List of ``{"heading": str, "level": int}`` dicts ordered by
            position, with the document title prepended as level 1.

        Raises:
            ValueError: If no document exists at the given path.
        """
        self._validate_path(path)

        row = self._fts.get_note(path)
        if row is None:
            raise ValueError(f"Document not found: {path}")

        title: str = row["title"]
        headings = self._fts.get_toc(path)

        toc: list[dict[str, Any]] = [{"heading": title, "level": 1}]
        toc.extend(
            h for h in headings if not (h["level"] == 1 and h["heading"] == title)
        )
        return toc

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

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
            if if_match is not None:
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
            created = not abs_path.is_file()

            abs_path.parent.mkdir(parents=True, exist_ok=True)

            if frontmatter is not None:
                post = fm.Post(content, **frontmatter)
                file_content = fm.dumps(post)
            else:
                file_content = content

            with tempfile.NamedTemporaryFile(
                dir=abs_path.parent,
                mode="w",
                encoding="utf-8",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(file_content)
                tmp_name = tmp.name
            if abs_path.is_file():
                shutil.copymode(abs_path, tmp_name)
            try:
                Path(tmp_name).replace(abs_path)
            except Exception:
                Path(tmp_name).unlink(missing_ok=True)
                raise

            if self._mark_paths_dirty is not None:
                self._mark_paths_dirty([path])

            result = WriteResult(path=path, created=created)

        self._on_write_callback(abs_path, file_content, "write")
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
            ValueError: If the path escapes the source directory, has an
                extension not in the allowlist, or the content exceeds the
                size limit.
        """
        self._check_writable()
        with self._file_write_lock:
            abs_path = self._validate_attachment_path(path)
            if if_match is not None:
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
            if self._max_attachment_size_mb > 0:
                limit_bytes = int(self._max_attachment_size_mb * 1024 * 1024)
                if len(content) > limit_bytes:
                    size_bytes = len(content)
                    raise ValueError(
                        f"Attachment {path!r} is {size_bytes} bytes "
                        f"({size_bytes / 1024 / 1024:.1f} MB), exceeds "
                        f"MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB "
                        f"({self._max_attachment_size_mb} MB). "
                        f"Increase MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB "
                        f"if you need the bytes in context."
                    )
            created = not abs_path.is_file()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=abs_path.parent,
                mode="wb",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(content)
                tmp_name = tmp.name
            if abs_path.is_file():
                shutil.copymode(abs_path, tmp_name)
            try:
                Path(tmp_name).replace(abs_path)
            except Exception:
                Path(tmp_name).unlink(missing_ok=True)
                raise
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

            if if_match is not None:
                current_hash = compute_file_hash(abs_path)
                if current_hash != if_match:
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual=current_hash
                    )

            file_content = abs_path.read_text(encoding="utf-8")

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

            with tempfile.NamedTemporaryFile(
                dir=abs_path.parent,
                mode="w",
                encoding="utf-8",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(new_content)
                tmp_name = tmp.name
            shutil.copymode(abs_path, tmp_name)
            try:
                Path(tmp_name).replace(abs_path)
            except Exception:
                Path(tmp_name).unlink(missing_ok=True)
                raise

            if self._mark_paths_dirty is not None:
                self._mark_paths_dirty([path])

        self._on_write_callback(abs_path, new_content, "edit")
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
                if if_match is not None:
                    current_hash = compute_file_hash(abs_path)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            path, expected=if_match, actual=current_hash
                        )
                abs_path.unlink()
                if self._mark_paths_dirty is not None:
                    self._mark_paths_dirty([path])
            else:
                abs_path = self._validate_attachment_path(path)
                if not abs_path.is_file():
                    raise DocumentNotFoundError(f"Attachment not found: {path}")
                if if_match is not None:
                    current_hash = compute_file_hash(abs_path)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            path, expected=if_match, actual=current_hash
                        )
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
                if if_match is not None:
                    current_hash = compute_file_hash(old_abs)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            old_path,
                            expected=if_match,
                            actual=current_hash,
                        )

                backlinks = self._fts.get_backlinks(old_path) if update_links else []

                new_abs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_abs), str(new_abs))

                note = parse_note(new_abs, self._source_dir, self._chunk_strategy)

                callback_content = new_abs.read_text(encoding="utf-8")

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
                if if_match is not None:
                    current_hash = compute_file_hash(old_abs)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            old_path,
                            expected=if_match,
                            actual=current_hash,
                        )

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

        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in backlinks:
            by_source[row["source_path"]].append(row)

        if old_path in by_source:
            by_source[new_path] = by_source.pop(old_path)

        pending_callbacks: list[tuple[Path, str]] = []
        dirty_paths: list[str] = []
        for source_path, rows in by_source.items():
            try:
                source_abs = self._validate_path(source_path)
                if not source_abs.is_file():
                    logger.warning(
                        "_update_backlinks: skipping %s — file not found",
                        source_path,
                    )
                    continue
                content = source_abs.read_text(encoding="utf-8")
                for row in rows:
                    new_raw = _compute_new_raw_target(
                        row["link_type"],
                        row["raw_target"],
                        row["fragment"],
                        new_path,
                        source_path=source_path,
                        old_path=old_path,
                    )
                    content = _apply_link_replacement(
                        content,
                        row["link_type"],
                        row["raw_target"],
                        new_raw,
                    )
                with tempfile.NamedTemporaryFile(
                    dir=source_abs.parent,
                    mode="w",
                    encoding="utf-8",
                    suffix=".tmp",
                    delete=False,
                ) as tmp:
                    tmp.write(content)
                    tmp_name = tmp.name
                shutil.copymode(source_abs, tmp_name)
                try:
                    Path(tmp_name).replace(source_abs)
                except Exception:
                    Path(tmp_name).unlink(missing_ok=True)
                    raise
                pending_callbacks.append((source_abs, content))
                dirty_paths.append(source_path)
            except (
                OSError,
                UnicodeDecodeError,
                ValueError,
                sqlite3.Error,
            ) as exc:
                logger.warning(
                    "_update_backlinks: failed to update %s: %s",
                    source_path,
                    exc,
                )
            except Exception as exc:
                logger.warning(
                    "_update_backlinks: unexpected error updating %s: %s",
                    source_path,
                    exc,
                    exc_info=True,
                )
        return pending_callbacks, dirty_paths
