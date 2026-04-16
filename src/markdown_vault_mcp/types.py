"""Data types for markdown-vault-mcp."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class Chunk:
    """A chunk of a document, typically a section under a heading."""

    heading: str | None
    heading_level: int
    content: str
    start_line: int


@dataclass
class LinkInfo:
    """A link extracted from a markdown document."""

    target_path: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""


@dataclass
class ParsedNote:
    """A parsed markdown document."""

    path: str
    frontmatter: dict[str, Any]
    title: str
    chunks: list[Chunk]
    content_hash: str
    modified_at: float
    links: list[LinkInfo] = field(default_factory=list)


@dataclass
class SearchResult:
    """A search result from the Collection API."""

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    search_type: Literal["keyword", "semantic"]
    frontmatter: dict[str, Any]


@dataclass
class FTSResult:
    """A raw search result from the FTS5 index layer."""

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float


@dataclass
class NoteContent:
    """Full content of a document, returned by read()."""

    path: str
    title: str
    folder: str
    content: str
    frontmatter: dict[str, Any]
    modified_at: float
    etag: str | None = None


@dataclass
class NoteInfo:
    """Summary info for a document, returned by list()."""

    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    kind: str = "note"


@dataclass
class WriteResult:
    """Result of a write operation."""

    path: str
    created: bool


@dataclass
class EditResult:
    """Result of an edit operation."""

    path: str
    replacements: int
    match_type: str = "exact"


@dataclass
class DeleteResult:
    """Result of a delete operation."""

    path: str


@dataclass
class RenameResult:
    """Result of a rename operation."""

    old_path: str
    new_path: str
    updated_links: int = 0


@dataclass
class IndexStats:
    """Statistics from build_index()."""

    documents_indexed: int
    chunks_indexed: int
    skipped: int


@dataclass
class ReindexResult:
    """Result of an incremental reindex."""

    added: int
    modified: int
    deleted: int
    unchanged: int


@dataclass
class AttachmentContent:
    """Full content of an attachment, returned by read() for non-.md files."""

    path: str
    mime_type: str | None
    size_bytes: int
    content_base64: str
    modified_at: float
    etag: str | None = None


@dataclass
class AttachmentInfo:
    """Summary info for an attachment, returned by list(include_attachments=True)."""

    path: str
    folder: str
    mime_type: str | None
    size_bytes: int
    modified_at: float
    kind: str = "attachment"


@dataclass
class CollectionStats:
    """Collection-wide statistics."""

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
    """Documents that changed since last index."""

    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: int


@dataclass
class BacklinkInfo:
    """A document that links to a given path."""

    source_path: str
    source_title: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""


@dataclass
class OutlinkInfo:
    """A link from a document to another path."""

    target_path: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""
    exists: bool = False


@dataclass
class BrokenLinkInfo:
    """A link whose target does not exist in the collection."""

    source_path: str
    source_title: str
    target_path: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""


@dataclass
class MostLinkedNote:
    """A document with its inbound backlink count, returned by get_most_linked()."""

    path: str
    title: str
    backlink_count: int


@dataclass
class SimilarItem:
    """Shape of each entry in :attr:`NoteContext.similar`.

    A compact subset of :class:`SearchResult` — path, title, and score only.
    Use :meth:`~markdown_vault_mcp.collection.Collection.get_similar` directly
    when you need the full chunk content.
    """

    path: str
    title: str
    score: float


@dataclass
class NoteContext:
    """Consolidated context for a document, returned by get_context()."""

    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    backlinks: list[BacklinkInfo]
    outlinks: list[OutlinkInfo]
    similar: list[SimilarItem]
    folder_notes: list[str]
    tags: dict[str, list[str]]


@dataclass
class HistoryEntry:
    """A commit that touched a note or the vault, returned by get_history()."""

    sha: str
    short_sha: str
    timestamp: str
    author: str
    message: str
    paths_changed: list[str]


@dataclass
class CommitDiff:
    """A per-commit diff entry, returned by get_diff() when per_commit=True."""

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
