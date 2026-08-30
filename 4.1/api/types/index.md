# Types

All data types returned by the `Vault` API are importable from the `markdown_vault_mcp.types` module.

```
from markdown_vault_mcp.types import NoteContent, GroupedResult, SectionHit, NoteContext
```

## Document Types

## `NoteContent(path, title, folder, content, frontmatter, modified_at, etag=None)`

Full content of a document, returned by :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.read`.

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

## `NoteInfo(path, title, folder, frontmatter, modified_at, kind='note', content_chars=0)`

Summary info for a document, returned by :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.list_documents`.

Attributes:

| Name            | Type             | Description                                                                                                                                                                                                                          |
| --------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `path`          | `str`            | Relative path from the vault root.                                                                                                                                                                                                   |
| `title`         | `str`            | Document title.                                                                                                                                                                                                                      |
| `folder`        | `str`            | Parent folder path.                                                                                                                                                                                                                  |
| `frontmatter`   | `dict[str, Any]` | Parsed YAML frontmatter.                                                                                                                                                                                                             |
| `modified_at`   | `float`          | Last-modified time as a Unix timestamp float.                                                                                                                                                                                        |
| `kind`          | `str`            | Always "note" for markdown documents; distinguishes from :class:AttachmentInfo.                                                                                                                                                      |
| `content_chars` | `int`            | Character count of the note body, frontmatter excluded, so a caller can budget batches without reading the note (#1039). 0 for a row indexed before this field existed, and for rows from queries that select a narrower column set. |

## `ParsedNote(path, frontmatter, title, chunks, content_hash, modified_at, links=list(), content_chars=0)`

A parsed markdown document with extracted structure.

Attributes:

| Name            | Type             | Description                                                                                                                                                                                                                                                                    |
| --------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `path`          | `str`            | Relative path from the vault root.                                                                                                                                                                                                                                             |
| `frontmatter`   | `dict[str, Any]` | Parsed YAML frontmatter as a dict.                                                                                                                                                                                                                                             |
| `title`         | `str`            | Document title derived from the first H1 heading or filename.                                                                                                                                                                                                                  |
| `chunks`        | `list[Chunk]`    | Ordered list of content chunks split by heading.                                                                                                                                                                                                                               |
| `content_hash`  | `str`            | SHA-256 hash of the raw file content for change detection.                                                                                                                                                                                                                     |
| `modified_at`   | `float`          | Last-modified time as a Unix timestamp float.                                                                                                                                                                                                                                  |
| `links`         | `list[LinkInfo]` | All links extracted from the document body.                                                                                                                                                                                                                                    |
| `content_chars` | `int`            | Character count of the document body as parsed: frontmatter removed and surrounding whitespace normalised by the frontmatter parser. Measured before chunking, because chunk_overlap_words duplicates text across chunks and would inflate any count summed from :attr:chunks. |

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

A search result from :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.search`.

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

## `SectionHit(heading, content, score)`

One section's contribution to a :class:`GroupedResult`.

Attributes:

| Name      | Type    | Description                                                                                      |
| --------- | ------- | ------------------------------------------------------------------------------------------------ |
| `heading` | \`str   | None\`                                                                                           |
| `content` | `str`   | Matched snippet — query-relevant window by default, or full chunk if snippet_words=0 was passed. |
| `score`   | `float` | Chunk-level relevance score after length-downweight. Not comparable across search modes.         |

## `GroupedResult(path, title, folder, score, search_type, frontmatter, sections)`

A file-grouped search result.

Replaces the flat per-chunk :class:`SearchResult` across `search`, `get_similar`, and `get_context.similar`. See issue #469.

Attributes:

| Name          | Type                                       | Description                                                                                                                                                                                                                                                                                 |
| ------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`        | `str`                                      | Relative path of the document.                                                                                                                                                                                                                                                              |
| `title`       | `str`                                      | Document title.                                                                                                                                                                                                                                                                             |
| `folder`      | `str`                                      | Parent folder path.                                                                                                                                                                                                                                                                         |
| `score`       | `float`                                    | File-level score = max(section.score for section in sections).                                                                                                                                                                                                                              |
| `search_type` | `Literal['keyword', 'semantic', 'hybrid']` | "keyword", "semantic", or "hybrid".                                                                                                                                                                                                                                                         |
| `frontmatter` | `dict[str, Any]`                           | Parsed YAML frontmatter.                                                                                                                                                                                                                                                                    |
| `sections`    | `list[SectionHit]`                         | Up to the per-file cap best-matching sections, sorted by (score DESC, start_line ASC, section_id ASC) so ties surface in document order — the section_id key gives a fully deterministic order even when chunks share a start_line (e.g. word-split fragments of one oversize source line). |

## `FTSResult(path, title, folder, heading, content, score, chunk_count=1, start_line=0, section_id=0)`

A raw search result from the FTS5 index layer.

Attributes:

| Name          | Type    | Description                                                                                                                                                                                                                                                                                 |
| ------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`        | `str`   | Relative path of the document containing this chunk.                                                                                                                                                                                                                                        |
| `title`       | `str`   | Document title.                                                                                                                                                                                                                                                                             |
| `folder`      | `str`   | Parent folder path.                                                                                                                                                                                                                                                                         |
| `heading`     | \`str   | None\`                                                                                                                                                                                                                                                                                      |
| `content`     | `str`   | Matched chunk text — full chunk by default; truncated to a tokenizer-aware snippet when snippet_words is passed to the search call.                                                                                                                                                         |
| `score`       | `float` | BM25 relevance score (higher is better).                                                                                                                                                                                                                                                    |
| `chunk_count` | `int`   | Total number of chunks belonging to the parent document.                                                                                                                                                                                                                                    |
| `start_line`  | `int`   | Line number of the chunk's first line in the source document. Defaults to 0 for the document intro chunk and as a fallback when the underlying section row cannot be resolved.                                                                                                              |
| `section_id`  | `int`   | sections table rowid of the matched chunk, used as the final deterministic tie-break when chunks share both score and start_line (e.g. word-split fragments of one oversize source line). Defaults to 0 when the section row cannot be resolved (legacy index) or for non-keyword channels. |

## `BacklinkInfo(source_path, source_title, link_text, link_type, fragment=None, raw_target='')`

A document that links to a given path, returned by :meth:`~markdown_vault_mcp.facets.graph.GraphFacet.get_backlinks`.

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

A link from a document to another path, returned by :meth:`~markdown_vault_mcp.facets.graph.GraphFacet.get_outlinks`.

Attributes:

| Name          | Type                                           | Description                                          |
| ------------- | ---------------------------------------------- | ---------------------------------------------------- |
| `target_path` | `str`                                          | Resolved relative path of the link target.           |
| `link_text`   | `str`                                          | Display text of the link.                            |
| `link_type`   | `Literal['markdown', 'wikilink', 'reference']` | Link syntax: "markdown", "wikilink", or "reference". |
| `fragment`    | \`str                                          | None\`                                               |
| `raw_target`  | `str`                                          | The unresolved link target exactly as written.       |
| `exists`      | `bool`                                         | True if the target document exists in the vault.     |

## `BrokenLinkInfo(source_path, source_title, target_path, link_text, link_type, fragment=None, raw_target='')`

A link whose target does not exist, returned by :meth:`~markdown_vault_mcp.facets.graph.GraphFacet.get_broken_links`.

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

## `NoteContext(path, title, folder, frontmatter, modified_at, backlinks, outlinks, similar, folder_notes, tags)`

Consolidated context for a document, returned by :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.get_context`.

Attributes:

| Name           | Type                   | Description                                                                                                                                                    |
| -------------- | ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`         | `str`                  | Relative path from the vault root.                                                                                                                             |
| `title`        | `str`                  | Document title.                                                                                                                                                |
| `folder`       | `str`                  | Parent folder path.                                                                                                                                            |
| `frontmatter`  | `dict[str, Any]`       | Parsed YAML frontmatter.                                                                                                                                       |
| `modified_at`  | `float`                | Last-modified time as a Unix timestamp float.                                                                                                                  |
| `backlinks`    | `list[BacklinkInfo]`   | Documents that link to this document.                                                                                                                          |
| `outlinks`     | `list[OutlinkInfo]`    | Links from this document with existence flags.                                                                                                                 |
| `similar`      | `list[GroupedResult]`  | Up to similar_limit semantically similar notes, field-collapsed. Each entry is a :class:GroupedResult with exactly one section (chunks_per_file=1 by default). |
| `folder_notes` | `list[str]`            | Paths of other notes in the same folder (up to 20).                                                                                                            |
| `tags`         | `dict[str, list[str]]` | Tag values for each indexed frontmatter field.                                                                                                                 |

## `MostLinkedNote(path, title, folder, backlink_count)`

A document with its inbound backlink count, returned by :meth:`~markdown_vault_mcp.facets.graph.GraphFacet.get_most_linked`.

Attributes:

| Name             | Type  | Description                                                 |
| ---------------- | ----- | ----------------------------------------------------------- |
| `path`           | `str` | Relative path from the vault root.                          |
| `title`          | `str` | Document title.                                             |
| `folder`         | `str` | Parent folder path (empty string for root-level documents). |
| `backlink_count` | `int` | Number of other documents that link to this document.       |

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

Statistics from :meth:`~markdown_vault_mcp.facets.index.IndexFacet.build_index`.

Attributes:

| Name                | Type  | Description                                      |
| ------------------- | ----- | ------------------------------------------------ |
| `documents_indexed` | `int` | Number of documents successfully indexed.        |
| `chunks_indexed`    | `int` | Total number of chunks indexed.                  |
| `skipped`           | `int` | Number of documents skipped due to parse errors. |

## `ReindexResult(added, modified, deleted, unchanged, skipped=0)`

Result of :meth:`~markdown_vault_mcp.facets.index.IndexFacet.reindex`.

Attributes:

| Name        | Type  | Description                                                                                                                                                                                                             |
| ----------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `added`     | `int` | Documents added since the last index.                                                                                                                                                                                   |
| `modified`  | `int` | Documents that changed since the last index.                                                                                                                                                                            |
| `deleted`   | `int` | Documents removed since the last index.                                                                                                                                                                                 |
| `unchanged` | `int` | Documents with no changes.                                                                                                                                                                                              |
| `skipped`   | `int` | Files present on disk that were deliberately not indexed (missing required frontmatter, matching an exclude pattern, or unparseable), whether newly skipped this scan or unchanged since they were last skipped (#665). |

## `VaultStats(document_count, chunk_count, folder_count, semantic_search_available, indexed_frontmatter_fields=list(), attachment_extensions=list(), link_count=0, broken_link_count=0, orphan_count=0)`

Vault-wide statistics, returned by :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.stats`.

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

## `ChangeSet(added, modified, deleted, unchanged, skipped_unchanged=0)`

Documents that changed since the last index build.

Attributes:

| Name                | Type        | Description                                                                                                                                                         |
| ------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `added`             | `list[str]` | Paths of newly discovered documents.                                                                                                                                |
| `modified`          | `list[str]` | Paths of documents whose content changed.                                                                                                                           |
| `deleted`           | `list[str]` | Paths of documents that no longer exist on disk.                                                                                                                    |
| `unchanged`         | `int`       | Count of documents with no changes (not listed individually).                                                                                                       |
| `skipped_unchanged` | `int`       | Count of files previously recorded as skipped (never indexed) whose content has not changed since; they appear in no other bucket and need no re-evaluation (#665). |

## Attachment Types

## `AttachmentContent(path, mime_type, size_bytes, content_base64, modified_at, etag=None)`

Full content of an attachment, returned by :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.read_attachment` for non-.md files.

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

Summary info for an attachment, returned by :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.list_documents` when `include_attachments=True`.

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

A commit that touched a note or the vault, returned by :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.get_history`.

Attributes:

| Name            | Type        | Description                                                                                                                                                                                                                                               |
| --------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sha`           | `str`       | Full 40-character commit SHA.                                                                                                                                                                                                                             |
| `short_sha`     | `str`       | Abbreviated 7-character SHA.                                                                                                                                                                                                                              |
| `timestamp`     | `str`       | ISO 8601 commit timestamp.                                                                                                                                                                                                                                |
| `author`        | `str`       | Commit author name and email.                                                                                                                                                                                                                             |
| `message`       | `str`       | First line of the commit message.                                                                                                                                                                                                                         |
| `paths_changed` | `list[str]` | Files touched by the commit. Populated for vault-wide queries (path=None). Always empty for single-note queries, since the path is already determined by the query arguments — callers know which file the commit touched without needing it echoed back. |

## `CommitDiff(sha, short_sha, timestamp, message, diff)`

A per-commit diff entry, returned by :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.get_diff` when `per_commit=True`.

Attributes:

| Name        | Type  | Description                       |
| ----------- | ----- | --------------------------------- |
| `sha`       | `str` | Full 40-character commit SHA.     |
| `short_sha` | `str` | Abbreviated 7-character SHA.      |
| `timestamp` | `str` | ISO 8601 commit timestamp.        |
| `message`   | `str` | First line of the commit message. |
| `diff`      | `str` | Unified diff text for the commit. |

## Callbacks

**`WriteOperation`**

Type alias for the kind of write operation reported to callbacks. `WriteCallback` and the `op` argument below both reference it.

```
WriteOperation = Literal["write", "edit", "delete", "rename"]
```

**`WriteCallback`**

Type alias for the `on_write` callback passed to `Vault`. Called after each successful write operation.

```
WriteCallback = Callable[[Path, str, WriteOperation], None]
```

Arguments received by the callback:

| Argument  | Type             | Description                                                        |
| --------- | ---------------- | ------------------------------------------------------------------ |
| `path`    | `Path`           | Absolute path of the modified file                                 |
| `content` | `str`            | New file content (empty string for binary attachments and deletes) |
| `op`      | `WriteOperation` | Which write operation fired (see `WriteOperation` above)           |
