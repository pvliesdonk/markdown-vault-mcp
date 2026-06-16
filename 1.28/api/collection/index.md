# Collection

The `Collection` class is the primary public API for the library. MCP tools, CLI commands, and direct integrations all go through this class.

## Quick Start

```
from pathlib import Path
from markdown_vault_mcp import Collection

# Basic read-only collection
collection = Collection(source_dir=Path("/path/to/vault"))
stats = collection.build_index()
print(f"Indexed {stats.documents_indexed} documents")

# Search
results = collection.search("query text", limit=10)
for r in results:
    print(f"{r.path}: {r.title} (score: {r.score:.2f})")

# Read a document
note = collection.read("Journal/note.md")
print(note.content)
```

## API Reference

## `Collection(*, source_dir, index_path=None, embeddings_path=None, embedding_provider=None, read_only=True, state_path=None, indexed_frontmatter_fields=None, required_frontmatter=None, chunk_strategy='heading', on_write=None, git_strategy=None, git_pull_interval_s=0, exclude_patterns=None, attachment_extensions=None, max_attachment_size_mb=10.0, chunks_per_doc=2, snippet_words=200, length_downweight_alpha=0.25, max_chunk_words=400)`

Facade over FTS5 index, vector index, and change tracker.

Instantiate once per collection root. Call :meth:`build_index` (or let lazy initialisation handle it) before querying.

Parameters:

| Name                         | Type                | Description                                                                                    | Default                                                                                                                                                  |
| ---------------------------- | ------------------- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source_dir`                 | `Path`              | Root directory of the markdown collection.                                                     | *required*                                                                                                                                               |
| `index_path`                 | \`Path              | None\`                                                                                         | Path to the SQLite index file. None (default) uses an in-memory database that is discarded when the object is collected.                                 |
| `embeddings_path`            | \`Path              | None\`                                                                                         | Base path for the {path}.npy and {path}.json sidecar files. None (default) means semantic search is disabled.                                            |
| `embedding_provider`         | \`EmbeddingProvider | None\`                                                                                         | Provider used to generate embeddings. Required when embeddings_path is set.                                                                              |
| `read_only`                  | `bool`              | When True (default), write operations raise :exc:~markdown_vault_mcp.exceptions.ReadOnlyError. | `True`                                                                                                                                                   |
| `state_path`                 | \`Path              | None\`                                                                                         | Path to the hash-state JSON file used by :class:~markdown_vault_mcp.tracker.ChangeTracker. Defaults to {source_dir}/.markdown_vault_mcp/state.json.      |
| `indexed_frontmatter_fields` | \`list[str]         | None\`                                                                                         | Frontmatter keys whose values are promoted to the document_tags table for structured filtering.                                                          |
| `required_frontmatter`       | \`list[str]         | None\`                                                                                         | If provided, documents missing any listed field are excluded from the index entirely.                                                                    |
| `chunk_strategy`             | \`str               | ChunkStrategy\`                                                                                | "heading" (default), "whole", or a custom :class:~markdown_vault_mcp.scanner.ChunkStrategy instance.                                                     |
| `on_write`                   | \`WriteCallback     | None\`                                                                                         | Optional callback invoked after every successful write operation. Signature: Callable\[\[Path, str, Literal["write","edit","delete","rename"]\], None\]. |
| `git_strategy`               | \`GitWriteStrategy  | None\`                                                                                         | Optional git strategy used for background git tasks (e.g. periodic fetch + ff-only updates). Started via :meth:start.                                    |
| `git_pull_interval_s`        | `int`               | Interval in seconds for periodic pulls. 0 disables the pull loop.                              | `0`                                                                                                                                                      |

### `pause_writes()`

Block all write operations until the context exits.

Write operations are queued (blocked on the lock) rather than being rejected. Reads and search remain unblocked at the Python level.

### `sync_from_remote_before_index()`

One-time git fetch + ff-only update before build_index().

Intended to run during server startup before the initial index build. No reindex is triggered here because build_index() will scan the updated working tree.

### `start()`

Start background tasks for this Collection (e.g. git pull loop).

### `stop()`

Stop background tasks (e.g. git pull loop) without closing the collection.

Safe to call multiple times. A no-op if no pull loop was started. The SQLite connection and write callback remain open; only the pull loop thread is signalled to stop.

### `close()`

Release resources held by the collection.

Flushes deferred embeddings and pending write callbacks, then closes the SQLite connection and git strategy.

### `search(query, *, limit=10, mode='keyword', filters=None, folder=None, chunks_per_doc=None, snippet_words=None)`

Search the collection.

Parameters:

| Name             | Type                                       | Description                                                                                                | Default                                                                                                      |
| ---------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `query`          | `str`                                      | Search string.                                                                                             | *required*                                                                                                   |
| `limit`          | `int`                                      | Maximum number of results to return.                                                                       | `10`                                                                                                         |
| `mode`           | `Literal['keyword', 'semantic', 'hybrid']` | "keyword" for BM25 FTS5, "semantic" for cosine similarity, or "hybrid" for Reciprocal Rank Fusion of both. | `'keyword'`                                                                                                  |
| `filters`        | \`dict[str, str]                           | None\`                                                                                                     | Dict of {frontmatter_key: value} pairs (AND semantics). Only works for fields in indexed_frontmatter_fields. |
| `folder`         | \`str                                      | None\`                                                                                                     | If provided, restrict results to documents in this folder (and its sub-folders).                             |
| `chunks_per_doc` | \`int                                      | None\`                                                                                                     | Maximum number of chunks to return per document. None uses the server default configured at startup.         |
| `snippet_words`  | \`int                                      | None\`                                                                                                     | Width of the snippet window in words. 0 returns the full chunk. None uses the server default.                |

Returns:

| Type                 | Description                                                      |
| -------------------- | ---------------------------------------------------------------- |
| `list[SearchResult]` | List of :class:~markdown_vault_mcp.types.SearchResult ordered by |
| `list[SearchResult]` | relevance.                                                       |

Raises:

| Type         | Description                                                                                   |
| ------------ | --------------------------------------------------------------------------------------------- |
| `ValueError` | If mode is "semantic" or "hybrid" but no embedding provider or embeddings path is configured. |

### `read(path, *, section=None)`

Read the full content of a document from disk.

Parameters:

| Name      | Type  | Description                                      | Default                                                                                                                                                                                                                                                                       |
| --------- | ----- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`    | `str` | Relative document path (e.g. "Journal/note.md"). | *required*                                                                                                                                                                                                                                                                    |
| `section` | \`str | None\`                                           | When provided, return only the section whose heading matches section exactly (case-sensitive). Pass the heading value from a search result unchanged for guaranteed match. None (the default) returns the whole document. Raises :exc:ValueError if the section is not found. |

Returns:

| Name | Type          | Description |
| ---- | ------------- | ----------- |
| `A`  | \`NoteContent | None\`      |
|      | \`NoteContent | None\`      |

### `list(*, folder=None, pattern=None, include_attachments=False)`

List documents (and optionally attachments) in the collection.

Parameters:

| Name                  | Type   | Description                                                                                                                                                                    | Default                                                                                            |
| --------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| `folder`              | \`str  | None\`                                                                                                                                                                         | If provided, only return documents in this folder (and sub-folders).                               |
| `pattern`             | \`str  | None\`                                                                                                                                                                         | Unix glob matched against the relative path using :func:fnmatch.fnmatch. Example: "Journal/\*.md". |
| `include_attachments` | `bool` | When True, also return non-.md files that match the attachment allowlist. Each :class:~markdown_vault_mcp.types.AttachmentInfo entry includes kind="attachment" and mime_type. | `False`                                                                                            |

Returns:

| Name         | Type             | Description        |
| ------------ | ---------------- | ------------------ |
|              | \`list\[NoteInfo | AttachmentInfo\]\` |
| `optionally` | \`list\[NoteInfo | AttachmentInfo\]\` |
|              | \`list\[NoteInfo | AttachmentInfo\]\` |

### `build_index(*, force=False)`

Scan source_dir and build the FTS index.

If the index already contains documents and *force* is `False`, this is a no-op. `force=True` drops all existing data and rebuilds from scratch.

Parameters:

| Name    | Type   | Description                                            | Default |
| ------- | ------ | ------------------------------------------------------ | ------- |
| `force` | `bool` | When True, drop and rebuild the index unconditionally. | `False` |

Returns:

| Type         | Description                                                             |
| ------------ | ----------------------------------------------------------------------- |
| `IndexStats` | class:~markdown_vault_mcp.types.IndexStats describing what was indexed. |

### `reindex()`

Incrementally update the index based on file changes.

Returns:

| Type            | Description                                                          |
| --------------- | -------------------------------------------------------------------- |
| `ReindexResult` | class:~markdown_vault_mcp.types.ReindexResult with counts of changes |
| `ReindexResult` | applied.                                                             |

### `build_embeddings(*, force=False)`

Build the vector index from all chunks currently in the FTS index.

Parameters:

| Name    | Type   | Description                                                                  | Default |
| ------- | ------ | ---------------------------------------------------------------------------- | ------- |
| `force` | `bool` | If True, rebuild from scratch even if a vector index already exists on disk. | `False` |

Returns:

| Type  | Description                      |
| ----- | -------------------------------- |
| `int` | Total number of chunks embedded. |

Raises:

| Type         | Description                                                 |
| ------------ | ----------------------------------------------------------- |
| `ValueError` | If embedding_provider or embeddings_path is not configured. |

### `embeddings_status()`

Return status information about the vector index.

Returns:

| Type   | Description                                 |
| ------ | ------------------------------------------- |
| `dict` | Dict with keys provider, chunk_count, path, |
| `dict` | available.                                  |

### `list_folders()`

Return all distinct folder values across the indexed collection.

Returns:

| Type        | Description                                                 |
| ----------- | ----------------------------------------------------------- |
| `list[str]` | Sorted list of folder strings ("" for the collection root). |

### `list_tags(field='tags')`

Return all distinct values indexed for a given frontmatter field.

If *field* was not in `indexed_frontmatter_fields`, returns `[]`.

Parameters:

| Name    | Type  | Description                                 | Default  |
| ------- | ----- | ------------------------------------------- | -------- |
| `field` | `str` | Frontmatter key to query (default: "tags"). | `'tags'` |

Returns:

| Type        | Description                            |
| ----------- | -------------------------------------- |
| `list[str]` | Sorted list of distinct value strings. |

### `get_toc(path)`

Return table of contents for a document.

Queries the FTS sections table for headings and prepends the document title as a synthetic H1 entry.

Parameters:

| Name   | Type  | Description                                            | Default    |
| ------ | ----- | ------------------------------------------------------ | ---------- |
| `path` | `str` | Relative path to the document (e.g. "notes/intro.md"). | *required* |

Returns:

| Type                   | Description                                             |
| ---------------------- | ------------------------------------------------------- |
| `list[dict[str, Any]]` | List of {"heading": str, "level": int} dicts ordered by |
| `list[dict[str, Any]]` | position, with the document title prepended as level 1. |

Raises:

| Type         | Description                              |
| ------------ | ---------------------------------------- |
| `ValueError` | If no document exists at the given path. |

### `get_backlinks(path)`

Return all documents that link to the given document.

Parameters:

| Name   | Type  | Description                                                   | Default    |
| ------ | ----- | ------------------------------------------------------------- | ---------- |
| `path` | `str` | Relative path of the target document (e.g. "notes/topic.md"). | *required* |

Returns:

| Type                 | Description                                                   |
| -------------------- | ------------------------------------------------------------- |
| `list[BacklinkInfo]` | List of :class:~markdown_vault_mcp.types.BacklinkInfo objects |
| `list[BacklinkInfo]` | for each document that contains a link pointing to path.      |

Raises:

| Type         | Description                              |
| ------------ | ---------------------------------------- |
| `ValueError` | If no document exists at the given path. |

### `get_outlinks(path)`

Return all links from the given document to other documents.

The `exists` field on each :class:`~markdown_vault_mcp.types.OutlinkInfo` indicates whether the target document is currently indexed.

Parameters:

| Name   | Type  | Description                                                   | Default    |
| ------ | ----- | ------------------------------------------------------------- | ---------- |
| `path` | `str` | Relative path of the source document (e.g. "notes/topic.md"). | *required* |

Returns:

| Type                | Description                                                      |
| ------------------- | ---------------------------------------------------------------- |
| `list[OutlinkInfo]` | List of :class:~markdown_vault_mcp.types.OutlinkInfo objects for |
| `list[OutlinkInfo]` | each link originating from path.                                 |

Raises:

| Type         | Description                              |
| ------------ | ---------------------------------------- |
| `ValueError` | If no document exists at the given path. |

### `get_broken_links(*, folder=None)`

Return all links whose target does not exist in the collection.

Parameters:

| Name     | Type  | Description | Default                                                                                      |
| -------- | ----- | ----------- | -------------------------------------------------------------------------------------------- |
| `folder` | \`str | None\`      | If provided, restrict to source documents in this folder (exact match or sub-folder prefix). |

Returns:

| Type                   | Description                                                      |
| ---------------------- | ---------------------------------------------------------------- |
| `list[BrokenLinkInfo]` | List of :class:~markdown_vault_mcp.types.BrokenLinkInfo objects. |

### `get_similar(path, *, limit=10)`

Return the most semantically similar chunks from other documents.

Uses the stored embedding vectors for `path` (averaged across chunks) to compute cosine similarity against all other documents. No re-embedding is needed. Results are at chunk granularity — the same document may appear multiple times if it has many chunks.

Parameters:

| Name    | Type  | Description                              | Default    |
| ------- | ----- | ---------------------------------------- | ---------- |
| `path`  | `str` | Relative path of the reference document. | *required* |
| `limit` | `int` | Maximum number of results to return.     | `10`       |

Returns:

| Type                 | Description                                                   |
| -------------------- | ------------------------------------------------------------- |
| `list[SearchResult]` | List of :class:~markdown_vault_mcp.types.SearchResult objects |
| `list[SearchResult]` | ordered by descending similarity. Returns [] when embeddings  |
| `list[SearchResult]` | are not configured or the document has no stored vectors.     |

Raises:

| Type         | Description                              |
| ------------ | ---------------------------------------- |
| `ValueError` | If no document exists at the given path. |

### `get_recent(*, limit=20, folder=None)`

Return the most recently modified documents.

Parameters:

| Name     | Type  | Description                            | Default                                                                               |
| -------- | ----- | -------------------------------------- | ------------------------------------------------------------------------------------- |
| `limit`  | `int` | Maximum number of documents to return. | `20`                                                                                  |
| `folder` | \`str | None\`                                 | If provided, restrict to documents in this folder (exact match or sub-folder prefix). |

Returns:

| Type             | Description                                               |
| ---------------- | --------------------------------------------------------- |
| `list[NoteInfo]` | List of :class:~markdown_vault_mcp.types.NoteInfo objects |
| `list[NoteInfo]` | ordered by modification time (most recent first).         |

### `get_context(path, *, similar_limit=5, link_limit=10)`

Return a consolidated context dossier for a document.

Combines backlinks, outlinks, similar notes, folder peers, and indexed frontmatter tags into a single response, saving the caller multiple round trips.

Parameters:

| Name            | Type  | Description                                            | Default    |
| --------------- | ----- | ------------------------------------------------------ | ---------- |
| `path`          | `str` | Relative path of the document (e.g. "notes/topic.md"). | *required* |
| `similar_limit` | `int` | Maximum number of similar notes to include.            | `5`        |
| `link_limit`    | `int` | Maximum number of backlinks and outlinks to include.   | `10`       |

Returns:

| Name | Type          | Description                                         |
| ---- | ------------- | --------------------------------------------------- |
| `A`  | `NoteContext` | class:~markdown_vault_mcp.types.NoteContext object. |

Raises:

| Type         | Description                              |
| ------------ | ---------------------------------------- |
| `ValueError` | If no document exists at the given path. |

### `get_orphan_notes()`

Return all documents with no inbound or outbound links.

A document is an orphan if it has zero outlinks and is not referenced by any other document's links.

Returns:

| Type             | Description                                                |
| ---------------- | ---------------------------------------------------------- |
| `list[NoteInfo]` | List of :class:~markdown_vault_mcp.types.NoteInfo objects, |
| `list[NoteInfo]` | ordered by path.                                           |

### `get_most_linked(*, limit=10)`

Return the documents with the most inbound links.

Parameters:

| Name    | Type  | Description                                      | Default |
| ------- | ----- | ------------------------------------------------ | ------- |
| `limit` | `int` | Maximum number of results to return. Default 10. | `10`    |

Returns:

| Type                   | Description                                                     |
| ---------------------- | --------------------------------------------------------------- |
| `list[MostLinkedNote]` | List of :class:~markdown_vault_mcp.types.MostLinkedNote ordered |
| `list[MostLinkedNote]` | by backlink_count descending.                                   |

### `get_connection_path(source, target, max_depth=10)`

Return the shortest undirected path between two notes.

Treats the link graph as undirected — a link in either direction counts as a connection. Uses BFS with a configurable depth cap.

Parameters:

| Name        | Type  | Description                                                       | Default    |
| ----------- | ----- | ----------------------------------------------------------------- | ---------- |
| `source`    | `str` | Vault-relative path of the starting note.                         | *required* |
| `target`    | `str` | Vault-relative path of the destination note.                      | *required* |
| `max_depth` | `int` | Maximum path length in edges. Clamped to [1, 10]. Defaults to 10. | `10`       |

Returns:

| Type        | Description |
| ----------- | ----------- |
| \`list[str] | None\`      |
| \`list[str] | None\`      |

Raises:

| Type         | Description                                    |
| ------------ | ---------------------------------------------- |
| `ValueError` | If source or target is not found in the index. |

### `get_history(path=None, since=None, until=None, limit=20)`

Return commits that touched a note or the whole vault.

When *path* is `None`, queries the full vault history. Returns an empty list for vaults whose source directory is not inside a git repository.

Parameters:

| Name    | Type  | Description                                                               | Default                                                                                                                                                                                                                              |
| ------- | ----- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `path`  | \`str | None\`                                                                    | Vault-relative path of the note to filter on (e.g. "notes/alpha.md"). Must end with .md. None returns vault-wide history.                                                                                                            |
| `since` | \`str | None\`                                                                    | ISO 8601 datetime string or git date expression (e.g. "1 week ago"). Passed as --since to git log. None disables the filter.                                                                                                         |
| `until` | \`str | None\`                                                                    | ISO 8601 datetime string or git date expression, passed as --until to git log. None disables the filter. Both since and until boundaries are inclusive: a commit whose author date equals either endpoint is included in the result. |
| `limit` | `int` | Maximum number of commits to return. Clamped to [1, 100]. Defaults to 20. | `20`                                                                                                                                                                                                                                 |

Returns:

| Type                 | Description                                                   |
| -------------------- | ------------------------------------------------------------- |
| `list[HistoryEntry]` | List of :class:~markdown_vault_mcp.types.HistoryEntry ordered |
| `list[HistoryEntry]` | newest-first. Empty list when the vault has no git history or |
| `list[HistoryEntry]` | the note has no commits in the given range.                   |

Raises:

| Type         | Description                                    |
| ------------ | ---------------------------------------------- |
| `ValueError` | If path is provided but fails path validation. |

### `get_diff(path, since_sha=None, since_timestamp=None, per_commit=False, limit=None)`

Return the diff of a note between a reference point and HEAD.

Exactly one of *since_sha* or *since_timestamp* must be supplied.

Parameters:

| Name              | Type   | Description                                                                                                                                                                           | Default                                                                                                                                                                                                                                                        |
| ----------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`            | `str`  | Vault-relative path of the note to diff. Must end with .md.                                                                                                                           | *required*                                                                                                                                                                                                                                                     |
| `since_sha`       | \`str  | None\`                                                                                                                                                                                | A commit SHA (full or abbreviated, at least 4 hex digits) to diff from. Mutually exclusive with since_timestamp.                                                                                                                                               |
| `since_timestamp` | \`str  | None\`                                                                                                                                                                                | ISO 8601 datetime string resolved to the most recent commit at or before that point via git rev-list (boundary inclusive). Mutually exclusive with since_sha.                                                                                                  |
| `per_commit`      | `bool` | When False (default), return a single unified diff string from the reference point to HEAD. When True, return one :class:~markdown_vault_mcp.types.CommitDiff per intervening commit. | `False`                                                                                                                                                                                                                                                        |
| `limit`           | \`int  | None\`                                                                                                                                                                                | When per_commit is True, cap the number of intervening commits returned to the limit most recent ones. Clamped to [1, 100]. None (the default) means unbounded (still bounded by the underlying since..HEAD range). Silently ignored when per_commit is False. |

Returns:

| Type  | Description        |
| ----- | ------------------ |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |

Raises:

| Type         | Description                                                                                                                                        |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ValueError` | If exactly one of since_sha / since_timestamp is not supplied, since_sha contains invalid characters, or the resolved ref is not found in history. |

### `stats()`

Return collection-wide statistics.

Returns:

| Type              | Description                                               |
| ----------------- | --------------------------------------------------------- |
| `CollectionStats` | class:~markdown_vault_mcp.types.CollectionStats snapshot. |

### `read_attachment(path)`

Read the binary content of a non-.md attachment.

Delegates to :meth:`DocumentManager.read_attachment`.

### `write_attachment(path, content, if_match=None)`

Create or overwrite a non-.md attachment.

Delegates to :meth:`DocumentManager.write_attachment`.

### `write(path, content, frontmatter=None, if_match=None)`

Create or overwrite a document.

Creates intermediate directories as needed. If *frontmatter* is provided, it is serialised as a YAML header at the top of the file.

Parameters:

| Name          | Type   | Description                                     | Default                                                                                                                                                                                                                          |
| ------------- | ------ | ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`        | `str`  | Relative document path (e.g. "notes/topic.md"). | *required*                                                                                                                                                                                                                       |
| `content`     | `str`  | Markdown body (excluding frontmatter).          | *required*                                                                                                                                                                                                                       |
| `frontmatter` | \`dict | None\`                                          | Optional frontmatter dict serialised as a YAML header.                                                                                                                                                                           |
| `if_match`    | \`str  | None\`                                          | Optional etag from a previous :meth:read call. When provided, the write is only performed if the current file hash matches this value, preventing overwrites of concurrent modifications. Pass None (default) to skip the check. |

Returns:

| Type          | Description                                  |
| ------------- | -------------------------------------------- |
| `WriteResult` | class:~markdown_vault_mcp.types.WriteResult. |

Raises:

| Type                          | Description                                                       |
| ----------------------------- | ----------------------------------------------------------------- |
| `ReadOnlyError`               | If the collection is read-only.                                   |
| `ConcurrentModificationError` | If if_match is provided and does not match the current file hash. |
| `ValueError`                  | If path escapes the source directory.                             |

### `edit(path, old_text=None, new_text='', if_match=None, line_start=None, line_end=None)`

Patch a section of a document.

Replaces the first occurrence of *old_text* with *new_text*, or replaces the line range \[*line_start*, *line_end*\] when line numbers are given instead.

Parameters:

| Name         | Type  | Description                                         | Default                                                                                         |
| ------------ | ----- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `path`       | `str` | Relative document path.                             | *required*                                                                                      |
| `old_text`   | \`str | None\`                                              | Exact text to replace (must occur exactly once). Mutually exclusive with line_start / line_end. |
| `new_text`   | `str` | Replacement text (may be empty to delete old_text). | `''`                                                                                            |
| `if_match`   | \`str | None\`                                              | Optional etag for optimistic concurrency; see :meth:write.                                      |
| `line_start` | \`int | None\`                                              | 1-based start line for line-range mode.                                                         |
| `line_end`   | \`int | None\`                                              | 1-based end line (inclusive) for line-range mode.                                               |

Returns:

| Type         | Description                                 |
| ------------ | ------------------------------------------- |
| `EditResult` | class:~markdown_vault_mcp.types.EditResult. |

Raises:

| Type                          | Description                                         |
| ----------------------------- | --------------------------------------------------- |
| `EditConflictError`           | If old_text is not found or appears more than once. |
| `ReadOnlyError`               | If the collection is read-only.                     |
| `ConcurrentModificationError` | If if_match is provided and does not match.         |
| `ValueError`                  | If path escapes the source directory.               |

### `delete(path, if_match=None)`

Delete a document or attachment.

Removes the file from disk and purges its entries from the FTS and vector indices.

Parameters:

| Name       | Type  | Description                                            | Default                                                    |
| ---------- | ----- | ------------------------------------------------------ | ---------------------------------------------------------- |
| `path`     | `str` | Relative path of the document or attachment to remove. | *required*                                                 |
| `if_match` | \`str | None\`                                                 | Optional etag for optimistic concurrency; see :meth:write. |

Returns:

| Type           | Description                                   |
| -------------- | --------------------------------------------- |
| `DeleteResult` | class:~markdown_vault_mcp.types.DeleteResult. |

Raises:

| Type                          | Description                                 |
| ----------------------------- | ------------------------------------------- |
| `ReadOnlyError`               | If the collection is read-only.             |
| `ConcurrentModificationError` | If if_match is provided and does not match. |
| `DocumentNotFoundError`       | If path does not exist.                     |

### `rename(old_path, new_path, if_match=None, *, update_links=False)`

Rename or move a document or attachment.

Moves the file on disk and updates the FTS / vector indices. When *update_links* is `True`, all wikilinks and markdown links in other documents that pointed to *old_path* are rewritten to *new_path*.

Parameters:

| Name           | Type   | Description                                                                                    | Default                                                    |
| -------------- | ------ | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `old_path`     | `str`  | Current relative path of the document or attachment.                                           | *required*                                                 |
| `new_path`     | `str`  | Desired relative path after the move.                                                          | *required*                                                 |
| `if_match`     | \`str  | None\`                                                                                         | Optional etag for optimistic concurrency; see :meth:write. |
| `update_links` | `bool` | When True, rewrite internal links across the vault to reflect the new path. Defaults to False. | `False`                                                    |

Returns:

| Type           | Description                                   |
| -------------- | --------------------------------------------- |
| `RenameResult` | class:~markdown_vault_mcp.types.RenameResult. |

Raises:

| Type                          | Description                                           |
| ----------------------------- | ----------------------------------------------------- |
| `ReadOnlyError`               | If the collection is read-only.                       |
| `ConcurrentModificationError` | If if_match is provided and does not match.           |
| `DocumentNotFoundError`       | If old_path does not exist.                           |
| `ValueError`                  | If old_path or new_path escapes the source directory. |
