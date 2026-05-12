"""Data types for markdown-vault-mcp."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class Chunk:
    """A chunk of a document, typically a section under a heading.

    Attributes:
        heading: Section heading text, or ``None`` for the document intro.
        heading_level: Markdown heading level (1-6); 0 for intro chunks.
        content: Plain text content of this chunk.
        start_line: 1-based line number where this chunk begins in the source.
    """

    heading: str | None
    heading_level: int
    content: str
    start_line: int


@dataclass
class LinkInfo:
    """A link extracted from a markdown document.

    Attributes:
        target_path: Resolved relative path of the link target.
        link_text: Display text of the link.
        link_type: Link syntax: ``"markdown"`` (``[text](path)``), ``"wikilink"`` (``[[path]]``), or ``"reference"`` (``[text][ref]``).
        fragment: Heading anchor (``#section``) if present in the link target.
        raw_target: The unresolved link target exactly as written in the source.
    """

    target_path: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""


@dataclass
class ParsedNote:
    """A parsed markdown document with extracted structure.

    Attributes:
        path: Relative path from the vault root.
        frontmatter: Parsed YAML frontmatter as a dict.
        title: Document title derived from the first H1 heading or filename.
        chunks: Ordered list of content chunks split by heading.
        content_hash: SHA-256 hash of the raw file content for change detection.
        modified_at: Last-modified time as a Unix timestamp float.
        links: All links extracted from the document body.
    """

    path: str
    frontmatter: dict[str, Any]
    title: str
    chunks: list[Chunk]
    content_hash: str
    modified_at: float
    links: list[LinkInfo] = field(default_factory=list)


@dataclass
class SearchResult:
    """A search result from :meth:`~markdown_vault_mcp.collection.Collection.search`.

    Attributes:
        path: Relative path of the document containing this chunk.
        title: Document title.
        folder: Parent folder path.
        heading: Section heading this chunk falls under, or ``None`` for the intro.
        content: Matched chunk text — a query-relevant snippet by default
            (approximately ``snippet_words`` words plus optional leading/trailing
            ellipsis markers, centred on matched terms). Pass
            ``snippet_words=0`` to ``search`` for the full chunk verbatim,
            or recover the full chunk after seeing a snippet via
            ``read(path, section=heading)``.
        score: Relevance score. Higher is better; not comparable across search types.
        search_type: ``"keyword"`` (BM25), ``"semantic"`` (cosine similarity),
            or ``"hybrid"`` (chunk appeared in both keyword and semantic channels).
        frontmatter: Parsed YAML frontmatter of the parent document.
    """

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    search_type: Literal["keyword", "semantic", "hybrid"]
    frontmatter: dict[str, Any]


@dataclass
class SectionHit:
    """One section's contribution to a :class:`GroupedResult`.

    Attributes:
        heading: Section heading text, or ``None`` for the document intro.
        content: Matched snippet — query-relevant window by default, or full
            chunk if ``snippet_words=0`` was passed.
        score: Chunk-level relevance score after length-downweight.  Not
            comparable across search modes.
    """

    heading: str | None
    content: str
    score: float


@dataclass
class GroupedResult:
    """A file-grouped search result.

    Replaces the flat per-chunk :class:`SearchResult` across ``search``,
    ``get_similar``, and ``get_context.similar``.  See issue #469.

    Attributes:
        path: Relative path of the document.
        title: Document title.
        folder: Parent folder path.
        score: File-level score = ``max(section.score for section in sections)``.
        search_type: ``"keyword"``, ``"semantic"``, or ``"hybrid"``.
        frontmatter: Parsed YAML frontmatter.
        sections: Up to the per-file cap best-matching sections, sorted by
            ``(score DESC, start_line ASC)`` so ties surface in document order.
    """

    path: str
    title: str
    folder: str
    score: float
    search_type: Literal["keyword", "semantic", "hybrid"]
    frontmatter: dict[str, Any]
    sections: list[SectionHit]


@dataclass
class FTSResult:
    """A raw search result from the FTS5 index layer.

    Attributes:
        path: Relative path of the document containing this chunk.
        title: Document title.
        folder: Parent folder path.
        heading: Section heading this chunk falls under, or ``None``.
        content: Matched chunk text — full chunk by default; truncated to a
            tokenizer-aware snippet when ``snippet_words`` is passed to the
            search call.
        score: BM25 relevance score (higher is better).
        chunk_count: Total number of chunks belonging to the parent document.
        start_line: Line number of the chunk's first line in the source
            document.  Defaults to ``0`` for the document intro chunk and as
            a fallback when the underlying section row cannot be resolved.
    """

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    chunk_count: int = 1
    start_line: int = 0


@dataclass
class NoteContent:
    """Full content of a document, returned by :meth:`~markdown_vault_mcp.collection.Collection.read`.

    Attributes:
        path: Relative path from the vault root (e.g. ``Journal/note.md``).
        title: Document title derived from the first H1 heading or filename.
        folder: Parent folder path (empty string for root-level documents).
        content: Raw markdown body including frontmatter.
        frontmatter: Parsed YAML frontmatter as a dict.
        modified_at: Last-modified time as a Unix timestamp float.
        etag: Opaque hash of file content for optimistic concurrency checks.
    """

    path: str
    title: str
    folder: str
    content: str
    frontmatter: dict[str, Any]
    modified_at: float
    etag: str | None = None


@dataclass
class NoteInfo:
    """Summary info for a document, returned by :meth:`~markdown_vault_mcp.collection.Collection.list`.

    Attributes:
        path: Relative path from the vault root.
        title: Document title.
        folder: Parent folder path.
        frontmatter: Parsed YAML frontmatter.
        modified_at: Last-modified time as a Unix timestamp float.
        kind: Always ``"note"`` for markdown documents; distinguishes from :class:`AttachmentInfo`.
    """

    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    kind: str = "note"


@dataclass
class WriteResult:
    """Result of a write operation.

    Attributes:
        path: Relative path of the document that was written.
        created: ``True`` if the document was newly created; ``False`` if overwritten.
    """

    path: str
    created: bool


@dataclass
class EditResult:
    """Result of an edit operation.

    Attributes:
        path: Relative path of the document that was edited.
        replacements: Number of text replacements made (always 1 for exact match).
        match_type: How the replacement was found: ``"exact"`` (verbatim match) or ``"normalized"`` (whitespace-normalised match).
    """

    path: str
    replacements: int
    match_type: str = "exact"


@dataclass
class DeleteResult:
    """Result of a delete operation.

    Attributes:
        path: Relative path of the document that was deleted.
    """

    path: str


@dataclass
class RenameResult:
    """Result of a rename operation.

    Attributes:
        old_path: Original relative path.
        new_path: New relative path after the rename.
        updated_links: Number of backlinks in other documents that were rewritten.
    """

    old_path: str
    new_path: str
    updated_links: int = 0


@dataclass
class IndexStats:
    """Statistics from :meth:`~markdown_vault_mcp.collection.Collection.build_index`.

    Attributes:
        documents_indexed: Number of documents successfully indexed.
        chunks_indexed: Total number of chunks indexed.
        skipped: Number of documents skipped due to parse errors.
    """

    documents_indexed: int
    chunks_indexed: int
    skipped: int


@dataclass
class ReindexResult:
    """Result of :meth:`~markdown_vault_mcp.collection.Collection.reindex`.

    Attributes:
        added: Documents added since the last index.
        modified: Documents that changed since the last index.
        deleted: Documents removed since the last index.
        unchanged: Documents with no changes.
    """

    added: int
    modified: int
    deleted: int
    unchanged: int


@dataclass
class AttachmentContent:
    """Full content of an attachment, returned by :meth:`~markdown_vault_mcp.collection.Collection.read` for non-.md files.

    Attributes:
        path: Relative path from the vault root.
        mime_type: Detected MIME type, or ``None`` if unknown.
        size_bytes: File size in bytes.
        content_base64: Base64-encoded file content.
        modified_at: Last-modified time as a Unix timestamp float.
        etag: Opaque hash for optimistic concurrency checks.
    """

    path: str
    mime_type: str | None
    size_bytes: int
    content_base64: str
    modified_at: float
    etag: str | None = None


@dataclass
class AttachmentInfo:
    """Summary info for an attachment, returned by :meth:`~markdown_vault_mcp.collection.Collection.list` when ``include_attachments=True``.

    Attributes:
        path: Relative path from the vault root.
        folder: Parent folder path.
        mime_type: Detected MIME type, or ``None`` if unknown.
        size_bytes: File size in bytes.
        modified_at: Last-modified time as a Unix timestamp float.
        kind: Always ``"attachment"``; distinguishes from :class:`NoteInfo`.
    """

    path: str
    folder: str
    mime_type: str | None
    size_bytes: int
    modified_at: float
    kind: str = "attachment"


@dataclass
class CollectionStats:
    """Collection-wide statistics, returned by :meth:`~markdown_vault_mcp.collection.Collection.stats`.

    Attributes:
        document_count: Number of indexed markdown documents.
        chunk_count: Total number of indexed sections (chunks).
        folder_count: Number of distinct folder paths.
        semantic_search_available: ``True`` if a vector index is loaded and ready.
        indexed_frontmatter_fields: Frontmatter fields configured for tag indexing.
        attachment_extensions: File extensions recognised as attachments.
        link_count: Total number of links extracted from all documents.
        broken_link_count: Number of links whose target does not exist.
        orphan_count: Number of documents with no inbound or outbound links.
    """

    document_count: int
    chunk_count: int
    folder_count: int
    semantic_search_available: bool
    indexed_frontmatter_fields: list[str] = field(default_factory=list)
    attachment_extensions: list[str] = field(default_factory=list)
    link_count: int = 0
    broken_link_count: int = 0
    orphan_count: int = 0


@dataclass
class ChangeSet:
    """Documents that changed since the last index build.

    Attributes:
        added: Paths of newly discovered documents.
        modified: Paths of documents whose content changed.
        deleted: Paths of documents that no longer exist on disk.
        unchanged: Count of documents with no changes (not listed individually).
    """

    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: int


@dataclass
class BacklinkInfo:
    """A document that links to a given path, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_backlinks`.

    Attributes:
        source_path: Relative path of the document containing the link.
        source_title: Title of the linking document.
        link_text: Display text of the link.
        link_type: Link syntax: ``"markdown"`` (``[text](path)``), ``"wikilink"`` (``[[path]]``), or ``"reference"`` (``[text][ref]``).
        fragment: Heading anchor (``#section``) if present in the link target.
        raw_target: The unresolved link target exactly as written in the source.
    """

    source_path: str
    source_title: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""


@dataclass
class OutlinkInfo:
    """A link from a document to another path, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_outlinks`.

    Attributes:
        target_path: Resolved relative path of the link target.
        link_text: Display text of the link.
        link_type: Link syntax: ``"markdown"``, ``"wikilink"``, or ``"reference"``.
        fragment: Heading anchor if present.
        raw_target: The unresolved link target exactly as written.
        exists: ``True`` if the target document exists in the collection.
    """

    target_path: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""
    exists: bool = False


@dataclass
class BrokenLinkInfo:
    """A link whose target does not exist, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_broken_links`.

    Attributes:
        source_path: Relative path of the document containing the broken link.
        source_title: Title of the linking document.
        target_path: Resolved path the link points to (does not exist).
        link_text: Display text of the link.
        link_type: Link syntax: ``"markdown"``, ``"wikilink"``, or ``"reference"``.
        fragment: Heading anchor if present.
        raw_target: The unresolved link target exactly as written.
    """

    source_path: str
    source_title: str
    target_path: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""


@dataclass
class MostLinkedNote:
    """A document with its inbound backlink count, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_most_linked`.

    Attributes:
        path: Relative path from the vault root.
        title: Document title.
        folder: Parent folder path (empty string for root-level documents).
        backlink_count: Number of other documents that link to this document.
    """

    path: str
    title: str
    folder: str
    backlink_count: int


@dataclass
class NoteContext:
    """Consolidated context for a document, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_context`.

    Attributes:
        path: Relative path from the vault root.
        title: Document title.
        folder: Parent folder path.
        frontmatter: Parsed YAML frontmatter.
        modified_at: Last-modified time as a Unix timestamp float.
        backlinks: Documents that link to this document.
        outlinks: Links from this document with existence flags.
        similar: Up to ``similar_limit`` semantically similar notes,
            field-collapsed.  Each entry is a :class:`GroupedResult` with
            exactly one section (chunks_per_file=1 by default).
        folder_notes: Paths of other notes in the same folder (up to 20).
        tags: Tag values for each indexed frontmatter field.
    """

    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    backlinks: list[BacklinkInfo]
    outlinks: list[OutlinkInfo]
    similar: list[GroupedResult]
    folder_notes: list[str]
    tags: dict[str, list[str]]


@dataclass
class HistoryEntry:
    """A commit that touched a note or the vault, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_history`.

    Attributes:
        sha: Full 40-character commit SHA.
        short_sha: Abbreviated 7-character SHA.
        timestamp: ISO 8601 commit timestamp.
        author: Commit author name and email.
        message: First line of the commit message.
        paths_changed: Files touched by the commit.  Populated for vault-wide
            queries (``path=None``).  Always empty for single-note queries,
            since the path is already determined by the query arguments —
            callers know which file the commit touched without needing it
            echoed back.
    """

    sha: str
    short_sha: str
    timestamp: str
    author: str
    message: str
    paths_changed: list[str]


@dataclass
class CommitDiff:
    """A per-commit diff entry, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_diff` when ``per_commit=True``.

    Attributes:
        sha: Full 40-character commit SHA.
        short_sha: Abbreviated 7-character SHA.
        timestamp: ISO 8601 commit timestamp.
        message: First line of the commit message.
        diff: Unified diff text for the commit.
    """

    sha: str
    short_sha: str
    timestamp: str
    message: str
    diff: str


WriteCallback = Callable[
    [Path, str, Literal["write", "edit", "delete", "rename"]], None
]

# Default set of allowed attachment extensions (without leading dot, lower-case).
# .md is always excluded — it is always handled as a markdown note.
DEFAULT_ATTACHMENT_EXTENSIONS: frozenset[str] = frozenset(
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
