"""Manager for document CRUD and attachment operations."""

from __future__ import annotations

import base64
import fnmatch
import logging
import mimetypes
import os.path as osp
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import frontmatter as fm

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
)
from markdown_vault_mcp.hashing import compute_etag, compute_file_hash
from markdown_vault_mcp.scanner import parse_note
from markdown_vault_mcp.types import (
    AttachmentContent,
    DeleteResult,
    EditResult,
    NoteContent,
    RenameResult,
    WriteResult,
)
from markdown_vault_mcp.utils.links import (
    apply_link_replacement,
    compute_new_raw_target,
)
from markdown_vault_mcp.utils.text import (
    build_position_map,
    find_closest_match,
    normalize_text,
)

if TYPE_CHECKING:
    from markdown_vault_mcp.collection import Collection

logger = logging.getLogger(__name__)

# Default set of allowed attachment extensions (without leading dot, lower-case).
_DEFAULT_ATTACHMENT_EXTENSIONS: frozenset[str] = frozenset(
    [
        # Documents
        "pdf",
        "docx",
        "xlsx",
        "pptx",
        "odt",
        "ods",
        "odp",
        # Images
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "svg",
        "bmp",
        "tiff",
        # Archives
        "zip",
        "tar",
        "gz",
        # Audio / Video
        "mp3",
        "mp4",
        "wav",
        "ogg",
        # Text and data
        "txt",
        "csv",
        "tsv",
        "json",
        "yaml",
        "toml",
        "xml",
        "html",
        "css",
        "js",
        "ts",
    ]
)


class DocumentManager:
    """Handles document reading, writing, editing, and attachments."""

    def __init__(self, collection: Collection) -> None:
        self._collection = collection

    def read(self, path: str) -> NoteContent | None:
        """Read the full content of a document from disk."""
        self._collection._ensure_initialized()

        abs_path = (self._collection._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._collection._source_dir.resolve()):
            return None
        if not abs_path.is_file():
            return None

        try:
            note = parse_note(
                abs_path, self._collection._source_dir, self._collection._chunk_strategy
            )
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

    def get_toc(self, path: str) -> list[dict[str, Any]]:
        """Return table of contents for a document."""
        self._collection._ensure_initialized()
        self.validate_path(path)

        row = self._collection._fts.get_note(path)
        if row is None:
            raise ValueError(f"Document not found: {path}")

        title: str = row["title"]
        headings = self._collection._fts.get_toc(path)

        # Prepend a synthetic H1 for the document title, filtering out any
        # real H1 that duplicates it (common when docs start with ``# Title``).
        toc: list[dict[str, Any]] = [{"heading": title, "level": 1}]
        toc.extend(
            h for h in headings if not (h["level"] == 1 and h["heading"] == title)
        )
        return toc

    def write_attachment(
        self, path: str, content: bytes, if_match: str | None = None
    ) -> WriteResult:
        """Create or overwrite a non-.md attachment."""
        self._collection._check_writable()
        with self._collection._write_lock:
            self._collection._ensure_initialized()
            abs_path = self.validate_attachment_path(path)
            if if_match is not None:
                if not abs_path.is_file():
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual="(file does not exist)"
                    )
                current_hash = compute_file_hash(abs_path)
                if current_hash != if_match:
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual=current_hash
                    )
            if self._collection._max_attachment_size_mb > 0:
                limit_bytes = int(
                    self._collection._max_attachment_size_mb * 1024 * 1024
                )
                if len(content) > limit_bytes:
                    raise ValueError(
                        f"Content ({len(content)} bytes) exceeds the limit of "
                        f"{self._collection._max_attachment_size_mb} MB ({limit_bytes} bytes). "
                        "Raise MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB or set "
                        "it to 0 to disable the limit."
                    )
            created = not abs_path.is_file()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=abs_path.parent, mode="wb", suffix=".tmp", delete=False
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

        self._collection._fire_write_callback(abs_path, "", "write")

        return result

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
        if_match: str | None = None,
    ) -> WriteResult:
        """Create or overwrite a document."""
        self._collection._check_writable()
        with self._collection._write_lock:
            self._collection._ensure_initialized()

            abs_path = self.validate_path(path)
            if if_match is not None:
                if not abs_path.is_file():
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual="(file does not exist)"
                    )
                current_hash = compute_file_hash(abs_path)
                if current_hash != if_match:
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual=current_hash
                    )
            created = not abs_path.is_file()

            # Create intermediate directories.
            abs_path.parent.mkdir(parents=True, exist_ok=True)

            # Build file content with optional frontmatter.
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

            # Update FTS index.
            note = parse_note(
                abs_path, self._collection._source_dir, self._collection._chunk_strategy
            )
            self._collection._fts.upsert_note(note)

            # Mark for deferred embedding update.
            self._collection._update_vector_index(note)

            result = WriteResult(path=path, created=created)

        # Fire git callback in background thread.
        self._collection._fire_write_callback(abs_path, file_content, "write")

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
        """Patch a section of a document."""
        self._collection._check_writable()

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

        with self._collection._write_lock:
            self._collection._ensure_initialized()

            abs_path = self.validate_path(path)
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
                    file_content, old_text, new_text, line_start, line_end, path
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

            # Update FTS index.
            note = parse_note(
                abs_path, self._collection._source_dir, self._collection._chunk_strategy
            )
            self._collection._fts.upsert_note(note)

            # Mark for deferred embedding update.
            self._collection._update_vector_index(note)

        # Fire git callback in background thread.
        self._collection._fire_write_callback(abs_path, new_content, "edit")

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
        """Handle line-range and scoped-match edit modes."""
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
        """Handle exact-match edit mode (with normalized fallback)."""
        return self._match_and_replace(file_content, old_text, new_text, path)

    def _match_and_replace(
        self,
        content: str,
        old_text: str,
        new_text: str,
        path: str,
        context_desc: str | None = None,
    ) -> tuple[str, str]:
        """Try exact match, then normalized match, then raise with diagnostics."""
        location = context_desc or path
        count = content.count(old_text)

        if count == 1:
            return content.replace(old_text, new_text, 1), "exact"

        if count > 1:
            raise EditConflictError(
                f"old_text appears {count} times in {location}; must appear exactly once"
            )

        normalized_content = normalize_text(content)
        normalized_old = normalize_text(old_text)
        norm_count = normalized_content.count(normalized_old)

        if norm_count == 1:
            pos_map = build_position_map(content, normalized_content)
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

        diag = find_closest_match(old_text, content)
        raise EditConflictError(f"old_text not found in {location}", **diag)

    def delete(self, path: str, if_match: str | None = None) -> DeleteResult:
        """Delete a document or attachment."""
        self._collection._check_writable()
        with self._collection._write_lock:
            self._collection._ensure_initialized()

            if path.endswith(".md"):
                abs_path = self.validate_path(path)
                if not abs_path.is_file():
                    raise DocumentNotFoundError(f"Document not found: {path}")
                if if_match is not None:
                    current_hash = compute_file_hash(abs_path)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            path, expected=if_match, actual=current_hash
                        )
                abs_path.unlink()
                self._collection._fts.delete_by_path(path)
                if (
                    self._collection._embeddings_path is not None
                    and self._collection._embedding_provider is not None
                ):
                    with self._collection._embedding_flush_lock:
                        self._collection._dirty_embeddings.add(path)
                    self._collection._schedule_embedding_flush()
            else:
                abs_path = self.validate_attachment_path(path)
                if not abs_path.is_file():
                    raise DocumentNotFoundError(f"Attachment not found: {path}")
                if if_match is not None:
                    current_hash = compute_file_hash(abs_path)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            path, expected=if_match, actual=current_hash
                        )
                abs_path.unlink()

        self._collection._fire_write_callback(abs_path, "", "delete")
        return DeleteResult(path=path)

    def rename(
        self,
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        *,
        update_links: bool = False,
    ) -> RenameResult:
        """Rename or move a document or attachment."""
        self._collection._check_writable()
        updated_links = 0
        backlink_callbacks: list[tuple[Path, str]] = []

        with self._collection._write_lock:
            self._collection._ensure_initialized()

            if old_path.endswith(".md"):
                old_abs = self.validate_path(old_path)
                new_abs = self.validate_path(new_path)

                if not old_abs.is_file():
                    raise DocumentNotFoundError(f"Document not found: {old_path}")
                if new_abs.is_file():
                    raise DocumentExistsError(f"Target already exists: {new_path}")
                if if_match is not None:
                    current_hash = compute_file_hash(old_abs)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            old_path, expected=if_match, actual=current_hash
                        )

                backlinks = (
                    self._collection._fts.get_backlinks(old_path)
                    if update_links
                    else []
                )

                new_abs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_abs), str(new_abs))

                self._collection._fts.delete_by_path(old_path)

                note = parse_note(
                    new_abs,
                    self._collection._source_dir,
                    self._collection._chunk_strategy,
                )
                self._collection._fts.upsert_note(note)

                if (
                    self._collection._embeddings_path is not None
                    and self._collection._embedding_provider is not None
                ):
                    with self._collection._embedding_flush_lock:
                        self._collection._dirty_embeddings.add(old_path)
                        self._collection._dirty_embeddings.add(note.path)
                    self._collection._schedule_embedding_flush()

                callback_content = new_abs.read_text(encoding="utf-8")
                backlink_callbacks = self._update_backlinks(
                    old_path, new_path, backlinks
                )
                updated_links = len(backlink_callbacks)
            else:
                old_abs = self.validate_attachment_path(old_path)
                new_abs = self.validate_attachment_path(new_path)

                if not old_abs.is_file():
                    raise DocumentNotFoundError(f"Attachment not found: {old_path}")
                if new_abs.is_file():
                    raise DocumentExistsError(f"Target already exists: {new_path}")
                if if_match is not None:
                    current_hash = compute_file_hash(old_abs)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            old_path, expected=if_match, actual=current_hash
                        )

                new_abs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_abs), str(new_abs))
                callback_content = ""

        self._collection._fire_write_callback(new_abs, callback_content, "rename")
        for src_abs, src_content in backlink_callbacks:
            self._collection._fire_write_callback(src_abs, src_content, "edit")

        return RenameResult(
            old_path=old_path, new_path=new_path, updated_links=updated_links
        )

    def _update_backlinks(
        self,
        old_path: str,
        new_path: str,
        backlinks: list[dict[str, Any]],
    ) -> list[tuple[Path, str]]:
        """Rewrite source files that link to old_path."""
        from collections import defaultdict

        if not backlinks:
            return []

        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in backlinks:
            by_source[row["source_path"]].append(row)

        if old_path in by_source:
            by_source[new_path] = by_source.pop(old_path)

        pending_callbacks: list[tuple[Path, str]] = []
        for source_path, rows in by_source.items():
            try:
                source_abs = self.validate_path(source_path)
                if not source_abs.is_file():
                    continue
                content = source_abs.read_text(encoding="utf-8")
                for row in rows:
                    new_raw = compute_new_raw_target(
                        row["link_type"],
                        row["raw_target"],
                        row["fragment"],
                        new_path,
                        source_path=source_path,
                        old_path=old_path,
                    )
                    content = apply_link_replacement(
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
                updated_note = parse_note(
                    source_abs,
                    self._collection._source_dir,
                    self._collection._chunk_strategy,
                )
                self._collection._fts.upsert_note(updated_note)
                self._collection._update_vector_index(updated_note)
                pending_callbacks.append((source_abs, content))
            except Exception:
                logger.warning(
                    "_update_backlinks: failed to update %s", source_path, exc_info=True
                )
        return pending_callbacks

    def read_attachment(self, path: str) -> AttachmentContent:
        """Read binary content of a non-.md attachment."""
        abs_path = self.validate_attachment_path(path)
        if not abs_path.is_file():
            raise ValueError(f"Attachment not found: {path}")

        stat = abs_path.stat()
        size_bytes = stat.st_size
        if self._collection._max_attachment_size_mb > 0:
            limit_bytes = int(self._collection._max_attachment_size_mb * 1024 * 1024)
            if size_bytes > limit_bytes:
                raise ValueError(f"Attachment {path!r} exceeds size limit.")

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

    def effective_attachment_extensions(self) -> frozenset[str]:
        """Return effective set of allowed attachment extensions."""
        if self._collection._attachment_extensions is None:
            return _DEFAULT_ATTACHMENT_EXTENSIONS
        return frozenset(self._collection._attachment_extensions)

    def is_attachment(self, path: str) -> bool:
        """Return True if path is an allowed non-.md attachment."""
        if path.endswith(".md"):
            return False
        suffix = Path(path).suffix.lstrip(".").lower()
        exts = self.effective_attachment_extensions()
        return "*" in exts or suffix in exts

    def is_path_excluded(self, path: str) -> bool:
        """Check whether *path* matches any configured exclude pattern."""
        exclude_patterns = self._collection._exclude_patterns
        if not exclude_patterns:
            return False
        return any(fnmatch.fnmatch(path, pat) for pat in exclude_patterns)

    def validate_path(self, path: str) -> Path:
        """Resolve and validate a .md document path."""
        if not path.endswith(".md"):
            raise ValueError(f"Path must end with '.md': {path}")
        abs_path = (self._collection._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._collection._source_dir.resolve()):
            raise ValueError(f"Path traversal detected: {path}")
        return abs_path

    def validate_attachment_path(self, path: str) -> Path:
        """Resolve and validate a non-.md attachment path."""
        if path.endswith(".md"):
            raise ValueError(
                f"Path ends with '.md' — use the note read/write methods instead: {path}"
            )
        exts = self.effective_attachment_extensions()
        suffix = Path(path).suffix.lstrip(".").lower()
        if "*" not in exts and suffix not in exts:
            allowed_str = ", ".join(f".{e}" for e in sorted(exts))
            raise ValueError(
                f"Extension '.{suffix}' is not in the attachment allowlist. "
                f"Allowed: {allowed_str}. "
                "Set MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS=* to allow all non-.md files."
            )
        abs_path = (self._collection._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._collection._source_dir.resolve()):
            raise ValueError(f"Path traversal detected: {path}")
        return abs_path


def _compute_new_raw_target(
    link_type: str,
    raw_target: str,
    fragment: str | None,
    new_path: str,
    source_path: str = "",
    old_path: str = "",
) -> str:
    """Compute the replacement raw_target string when a file is renamed."""
    if link_type == "wikilink":
        # Determine whether the original wikilink included the .md extension.
        old_path_part = raw_target.split("#")[0]
        if old_path_part.lower().endswith(".md"):
            new_path_part = new_path
        else:
            new_path_part = new_path[:-3]
        return new_path_part + ("#" + fragment if fragment else "")
    else:
        # markdown and reference links.
        raw_path_part = raw_target.split("#")[0]
        if source_path and old_path and raw_path_part != old_path:
            # Relative-to-source link: compute the correct new relative path.
            source_dir = str(Path(source_path).parent)
            new_rel = osp.relpath(new_path, source_dir)
            new_path_part = new_rel.replace("\\", "/")
        else:
            new_path_part = new_path
        return new_path_part + ("#" + fragment if fragment else "")


def _apply_link_replacement(
    content: str, link_type: str, old_raw: str, new_raw: str
) -> str:
    """Replace a single link target occurrence in file content."""
    if link_type == "markdown":
        return re.sub(
            r"(?<!!)(\[[^\]]*?\])\(" + re.escape(old_raw) + r"((?:\s[^)]*)?)\)",
            lambda m: m.group(1) + "(" + new_raw + m.group(2) + ")",
            content,
        )
    elif link_type == "reference":
        return re.sub(
            r"^(\[.*?\]:\s+)" + re.escape(old_raw) + r"([ \t].*|$)",
            lambda m: m.group(1) + new_raw + m.group(2),
            content,
            flags=re.MULTILINE,
        )
    elif link_type == "wikilink":
        return re.sub(
            r"\[\[" + re.escape(old_raw) + r"(\|[^\]]*)?\]\]",
            lambda m: "[[" + new_raw + (m.group(1) or "") + "]]",
            content,
        )
    return content
