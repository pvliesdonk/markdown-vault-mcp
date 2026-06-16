# Types

All data types returned by the `Collection` API are importable from the top-level `markdown_vault_mcp` package.

```
from markdown_vault_mcp import NoteContent, SearchResult, NoteContext
```

## Document Types

## `NoteContent(path, title, folder, content, frontmatter, modified_at, etag=None)`

Full content of a document, returned by :meth:`~markdown_vault_mcp.collection.Collection.read`.

Attributes:

| Name          | Type             | Description                                                   |
| ------------- | ---------------- | ------------------------------------------------------------- |
| `path`        | `str`            | Relative path from the vault root (e.g. Journal/note.md).     |
| `title`       | `str`            | Document title derived from the first H1 heading or filename. |
| `folder`      | `str`            | Parent folder path (empty string for root-level documents).   |
| `content`     | `str`            | Raw markdown body including frontmatter.                      |
| `frontmatter` | `dict[str, Any]` | Parsed YAML frontmatter as a dict.                            |
| `modified_at` | `float`          | Last-modified time as a Unix timestamp float.                 |
| `etag`        | \`str            | None\`                                                        |

## `NoteInfo(path, title, folder, frontmatter, modified_at, kind='note')`

Summary info for a document, returned by :meth:`~markdown_vault_mcp.collection.Collection.list`.

Attributes:

| Name          | Type             | Description                                                                     |
| ------------- | ---------------- | ------------------------------------------------------------------------------- |
| `path`        | `str`            | Relative path from the vault root.                                              |
| `title`       | `str`            | Document title.                                                                 |
| `folder`      | `str`            | Parent folder path.                                                             |
| `frontmatter` | `dict[str, Any]` | Parsed YAML frontmatter.                                                        |
| `modified_at` | `float`          | Last-modified time as a Unix timestamp float.                                   |
| `kind`        | `str`            | Always "note" for markdown documents; distinguishes from :class:AttachmentInfo. |

## `ParsedNote(path, frontmatter, title, chunks, content_hash, modified_at, links=list())`

A parsed markdown document with extracted structure.

Attributes:

| Name           | Type             | Description                                                   |
| -------------- | ---------------- | ------------------------------------------------------------- |
| `path`         | `str`            | Relative path from the vault root.                            |
| `frontmatter`  | `dict[str, Any]` | Parsed YAML frontmatter as a dict.                            |
| `title`        | `str`            | Document title derived from the first H1 heading or filename. |
| `chunks`       | `list[Chunk]`    | Ordered list of content chunks split by heading.              |
| `content_hash` | `str`            | SHA-256 hash of the raw file content for change detection.    |
| `modified_at`  | `float`          | Last-modified time as a Unix timestamp float.                 |
| `links`        | `list[LinkInfo]` | All links extracted from the document body.                   |

## `Chunk(heading, heading_level, content, start_line)`

A chunk of a document, typically a section under a heading.

Attributes:

| Name            | Type  | Description                                                |
| --------------- | ----- | ---------------------------------------------------------- |
| `heading`       | \`str | None\`                                                     |
| `heading_level` | `int` | Markdown heading level (1-6); 0 for intro chunks.          |
| `content`       | `str` | Plain text content of this chunk.                          |
| `start_line`    | `int` | 1-based line number where this chunk begins in the source. |

## Search & Link Types

## `SearchResult(path, title, folder, heading, content, score, search_type, frontmatter)`

A search result from :meth:`~markdown_vault_mcp.collection.Collection.search`.

Attributes:

| Name          | Type                                       | Description                                                                                                                                                                                                                                                                                                           |
| ------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`        | `str`                                      | Relative path of the document containing this chunk.                                                                                                                                                                                                                                                                  |
| `title`       | `str`                                      | Document title.                                                                                                                                                                                                                                                                                                       |
| `folder`      | `str`                                      | Parent folder path.                                                                                                                                                                                                                                                                                                   |
| `heading`     | \`str                                      | None\`                                                                                                                                                                                                                                                                                                                |
| `content`     | `str`                                      | Matched chunk text — a query-relevant snippet by default (approximately snippet_words words plus optional leading/trailing ellipsis markers, centred on matched terms). Pass snippet_words=0 to search for the full chunk verbatim, or recover the full chunk after seeing a snippet via read(path, section=heading). |
| `score`       | `float`                                    | Relevance score. Higher is better; not comparable across search types.                                                                                                                                                                                                                                                |
| `search_type` | `Literal['keyword', 'semantic', 'hybrid']` | "keyword" (BM25), "semantic" (cosine similarity), or "hybrid" (chunk appeared in both keyword and semantic channels).                                                                                                                                                                                                 |
| `frontmatter` | `dict[str, Any]`                           | Parsed YAML frontmatter of the parent document.                                                                                                                                                                                                                                                                       |

## `FTSResult(path, title, folder, heading, content, score, chunk_count=1)`

A raw search result from the FTS5 index layer.

Attributes:

| Name          | Type    | Description                                                                                                                         |
| ------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `path`        | `str`   | Relative path of the document containing this chunk.                                                                                |
| `title`       | `str`   | Document title.                                                                                                                     |
| `folder`      | `str`   | Parent folder path.                                                                                                                 |
| `heading`     | \`str   | None\`                                                                                                                              |
| `content`     | `str`   | Matched chunk text — full chunk by default; truncated to a tokenizer-aware snippet when snippet_words is passed to the search call. |
| `score`       | `float` | BM25 relevance score (higher is better).                                                                                            |
| `chunk_count` | `int`   | Total number of chunks belonging to the parent document.                                                                            |

## `BacklinkInfo(source_path, source_title, link_text, link_type, fragment=None, raw_target='')`

A document that links to a given path, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_backlinks`.

Attributes:

| Name           | Type                                           | Description                                                                                    |
| -------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `source_path`  | `str`                                          | Relative path of the document containing the link.                                             |
| `source_title` | `str`                                          | Title of the linking document.                                                                 |
| `link_text`    | `str`                                          | Display text of the link.                                                                      |
| `link_type`    | `Literal['markdown', 'wikilink', 'reference']` | Link syntax: "markdown" ([text](path)), "wikilink" (\[[path]\]), or "reference" ([text][ref]). |
| `fragment`     | \`str                                          | None\`                                                                                         |
| `raw_target`   | `str`                                          | The unresolved link target exactly as written in the source.                                   |

## `OutlinkInfo(target_path, link_text, link_type, fragment=None, raw_target='', exists=False)`

A link from a document to another path, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_outlinks`.

Attributes:

| Name          | Type                                           | Description                                           |
| ------------- | ---------------------------------------------- | ----------------------------------------------------- |
| `target_path` | `str`                                          | Resolved relative path of the link target.            |
| `link_text`   | `str`                                          | Display text of the link.                             |
| `link_type`   | `Literal['markdown', 'wikilink', 'reference']` | Link syntax: "markdown", "wikilink", or "reference".  |
| `fragment`    | \`str                                          | None\`                                                |
| `raw_target`  | `str`                                          | The unresolved link target exactly as written.        |
| `exists`      | `bool`                                         | True if the target document exists in the collection. |

## `BrokenLinkInfo(source_path, source_title, target_path, link_text, link_type, fragment=None, raw_target='')`

A link whose target does not exist, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_broken_links`.

Attributes:

| Name           | Type                                           | Description                                               |
| -------------- | ---------------------------------------------- | --------------------------------------------------------- |
| `source_path`  | `str`                                          | Relative path of the document containing the broken link. |
| `source_title` | `str`                                          | Title of the linking document.                            |
| `target_path`  | `str`                                          | Resolved path the link points to (does not exist).        |
| `link_text`    | `str`                                          | Display text of the link.                                 |
| `link_type`    | `Literal['markdown', 'wikilink', 'reference']` | Link syntax: "markdown", "wikilink", or "reference".      |
| `fragment`     | \`str                                          | None\`                                                    |
| `raw_target`   | `str`                                          | The unresolved link target exactly as written.            |

## `LinkInfo(target_path, link_text, link_type, fragment=None, raw_target='')`

A link extracted from a markdown document.

Attributes:

| Name          | Type                                           | Description                                                                                    |
| ------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `target_path` | `str`                                          | Resolved relative path of the link target.                                                     |
| `link_text`   | `str`                                          | Display text of the link.                                                                      |
| `link_type`   | `Literal['markdown', 'wikilink', 'reference']` | Link syntax: "markdown" ([text](path)), "wikilink" (\[[path]\]), or "reference" ([text][ref]). |
| `fragment`    | \`str                                          | None\`                                                                                         |
| `raw_target`  | `str`                                          | The unresolved link target exactly as written in the source.                                   |

## `SimilarItem(path, title, score)`

A compact similar-note entry in :attr:`NoteContext.similar`.

A subset of :class:`SearchResult` — path, title, and score only. Use :meth:`~markdown_vault_mcp.collection.Collection.get_similar` directly when you need the full chunk content.

Attributes:

| Name    | Type    | Description                                 |
| ------- | ------- | ------------------------------------------- |
| `path`  | `str`   | Relative path from the vault root.          |
| `title` | `str`   | Document title.                             |
| `score` | `float` | Cosine similarity score (higher is better). |

## `NoteContext(path, title, folder, frontmatter, modified_at, backlinks, outlinks, similar, folder_notes, tags)`

Consolidated context for a document, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_context`.

Attributes:

| Name           | Type                   | Description                                                    |
| -------------- | ---------------------- | -------------------------------------------------------------- |
| `path`         | `str`                  | Relative path from the vault root.                             |
| `title`        | `str`                  | Document title.                                                |
| `folder`       | `str`                  | Parent folder path.                                            |
| `frontmatter`  | `dict[str, Any]`       | Parsed YAML frontmatter.                                       |
| `modified_at`  | `float`                | Last-modified time as a Unix timestamp float.                  |
| `backlinks`    | `list[BacklinkInfo]`   | Documents that link to this document.                          |
| `outlinks`     | `list[OutlinkInfo]`    | Links from this document with existence flags.                 |
| `similar`      | `list[SimilarItem]`    | Up to similar_limit semantically similar notes (compact form). |
| `folder_notes` | `list[str]`            | Paths of other notes in the same folder (up to 20).            |
| `tags`         | `dict[str, list[str]]` | Tag values for each indexed frontmatter field.                 |

## `MostLinkedNote(path, title, backlink_count)`

A document with its inbound backlink count, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_most_linked`.

Attributes:

| Name             | Type  | Description                                           |
| ---------------- | ----- | ----------------------------------------------------- |
| `path`           | `str` | Relative path from the vault root.                    |
| `title`          | `str` | Document title.                                       |
| `backlink_count` | `int` | Number of other documents that link to this document. |

## Operation Results

## `WriteResult(path, created)`

Result of a write operation.

Attributes:

| Name      | Type   | Description                                                   |
| --------- | ------ | ------------------------------------------------------------- |
| `path`    | `str`  | Relative path of the document that was written.               |
| `created` | `bool` | True if the document was newly created; False if overwritten. |

## `EditResult(path, replacements, match_type='exact')`

Result of an edit operation.

Attributes:

| Name           | Type  | Description                                                                                            |
| -------------- | ----- | ------------------------------------------------------------------------------------------------------ |
| `path`         | `str` | Relative path of the document that was edited.                                                         |
| `replacements` | `int` | Number of text replacements made (always 1 for exact match).                                           |
| `match_type`   | `str` | How the replacement was found: "exact" (verbatim match) or "normalized" (whitespace-normalised match). |

## `DeleteResult(path)`

Result of a delete operation.

Attributes:

| Name   | Type  | Description                                     |
| ------ | ----- | ----------------------------------------------- |
| `path` | `str` | Relative path of the document that was deleted. |

## `RenameResult(old_path, new_path, updated_links=0)`

Result of a rename operation.

Attributes:

| Name            | Type  | Description                                                 |
| --------------- | ----- | ----------------------------------------------------------- |
| `old_path`      | `str` | Original relative path.                                     |
| `new_path`      | `str` | New relative path after the rename.                         |
| `updated_links` | `int` | Number of backlinks in other documents that were rewritten. |

## `IndexStats(documents_indexed, chunks_indexed, skipped)`

Statistics from :meth:`~markdown_vault_mcp.collection.Collection.build_index`.

Attributes:

| Name                | Type  | Description                                      |
| ------------------- | ----- | ------------------------------------------------ |
| `documents_indexed` | `int` | Number of documents successfully indexed.        |
| `chunks_indexed`    | `int` | Total number of chunks indexed.                  |
| `skipped`           | `int` | Number of documents skipped due to parse errors. |

## `ReindexResult(added, modified, deleted, unchanged)`

Result of :meth:`~markdown_vault_mcp.collection.Collection.reindex`.

Attributes:

| Name        | Type  | Description                                  |
| ----------- | ----- | -------------------------------------------- |
| `added`     | `int` | Documents added since the last index.        |
| `modified`  | `int` | Documents that changed since the last index. |
| `deleted`   | `int` | Documents removed since the last index.      |
| `unchanged` | `int` | Documents with no changes.                   |

## `CollectionStats(document_count, chunk_count, folder_count, semantic_search_available, indexed_frontmatter_fields=list(), attachment_extensions=list(), link_count=0, broken_link_count=0, orphan_count=0)`

Collection-wide statistics, returned by :meth:`~markdown_vault_mcp.collection.Collection.stats`.

Attributes:

| Name                         | Type        | Description                                            |
| ---------------------------- | ----------- | ------------------------------------------------------ |
| `document_count`             | `int`       | Number of indexed markdown documents.                  |
| `chunk_count`                | `int`       | Total number of indexed sections (chunks).             |
| `folder_count`               | `int`       | Number of distinct folder paths.                       |
| `semantic_search_available`  | `bool`      | True if a vector index is loaded and ready.            |
| `indexed_frontmatter_fields` | `list[str]` | Frontmatter fields configured for tag indexing.        |
| `attachment_extensions`      | `list[str]` | File extensions recognised as attachments.             |
| `link_count`                 | `int`       | Total number of links extracted from all documents.    |
| `broken_link_count`          | `int`       | Number of links whose target does not exist.           |
| `orphan_count`               | `int`       | Number of documents with no inbound or outbound links. |

## `ChangeSet(added, modified, deleted, unchanged)`

Documents that changed since the last index build.

Attributes:

| Name        | Type        | Description                                                   |
| ----------- | ----------- | ------------------------------------------------------------- |
| `added`     | `list[str]` | Paths of newly discovered documents.                          |
| `modified`  | `list[str]` | Paths of documents whose content changed.                     |
| `deleted`   | `list[str]` | Paths of documents that no longer exist on disk.              |
| `unchanged` | `int`       | Count of documents with no changes (not listed individually). |

## Attachment Types

## `AttachmentContent(path, mime_type, size_bytes, content_base64, modified_at, etag=None)`

Full content of an attachment, returned by :meth:`~markdown_vault_mcp.collection.Collection.read` for non-.md files.

Attributes:

| Name             | Type    | Description                                   |
| ---------------- | ------- | --------------------------------------------- |
| `path`           | `str`   | Relative path from the vault root.            |
| `mime_type`      | \`str   | None\`                                        |
| `size_bytes`     | `int`   | File size in bytes.                           |
| `content_base64` | `str`   | Base64-encoded file content.                  |
| `modified_at`    | `float` | Last-modified time as a Unix timestamp float. |
| `etag`           | \`str   | None\`                                        |

## `AttachmentInfo(path, folder, mime_type, size_bytes, modified_at, kind='attachment')`

Summary info for an attachment, returned by :meth:`~markdown_vault_mcp.collection.Collection.list` when `include_attachments=True`.

Attributes:

| Name          | Type    | Description                                              |
| ------------- | ------- | -------------------------------------------------------- |
| `path`        | `str`   | Relative path from the vault root.                       |
| `folder`      | `str`   | Parent folder path.                                      |
| `mime_type`   | \`str   | None\`                                                   |
| `size_bytes`  | `int`   | File size in bytes.                                      |
| `modified_at` | `float` | Last-modified time as a Unix timestamp float.            |
| `kind`        | `str`   | Always "attachment"; distinguishes from :class:NoteInfo. |

## Git Types

## `HistoryEntry(sha, short_sha, timestamp, author, message, paths_changed)`

A commit that touched a note or the vault, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_history`.

Attributes:

| Name            | Type        | Description                                     |
| --------------- | ----------- | ----------------------------------------------- |
| `sha`           | `str`       | Full 40-character commit SHA.                   |
| `short_sha`     | `str`       | Abbreviated 7-character SHA.                    |
| `timestamp`     | `str`       | ISO 8601 commit timestamp.                      |
| `author`        | `str`       | Commit author name and email.                   |
| `message`       | `str`       | First line of the commit message.               |
| `paths_changed` | `list[str]` | Relative paths of files changed in this commit. |

## `CommitDiff(sha, short_sha, timestamp, message, diff)`

A per-commit diff entry, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_diff` when `per_commit=True`.

Attributes:

| Name        | Type  | Description                       |
| ----------- | ----- | --------------------------------- |
| `sha`       | `str` | Full 40-character commit SHA.     |
| `short_sha` | `str` | Abbreviated 7-character SHA.      |
| `timestamp` | `str` | ISO 8601 commit timestamp.        |
| `message`   | `str` | First line of the commit message. |
| `diff`      | `str` | Unified diff text for the commit. |

## Callbacks

**`WriteCallback`**

Type alias for the `on_write` callback passed to `Collection`. Called after each successful write operation (write, edit, delete, rename).

```
WriteCallback = Callable[[Path, str, Literal["write", "edit", "delete", "rename"]], None]
```

Arguments received by the callback:

| Argument  | Type           | Description                                                        |
| --------- | -------------- | ------------------------------------------------------------------ |
| `path`    | `Path`         | Absolute path of the modified file                                 |
| `content` | `str`          | New file content (empty string for binary attachments and deletes) |
| `op`      | `Literal[...]` | Operation type: `"write"`, `"edit"`, `"delete"`, or `"rename"`     |
