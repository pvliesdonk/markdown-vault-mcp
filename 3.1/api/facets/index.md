# Facets

The read / write / graph / index operations live on four cohesive facets, reached through the `reader` / `writer` / `graph` / `index` accessors on the [`Vault`](https://pvliesdonk.github.io/markdown-vault-mcp/3.1/api/vault/index.md) composition root.

```
from pathlib import Path
from markdown_vault_mcp import Vault

vault = Vault(source_dir=Path("/path/to/vault"))
vault.index.build_index()

# Reader facet — search / read / list / metadata
results = vault.reader.search("query text", limit=10)
note = vault.reader.read("Journal/note.md")

# Writer facet — write / edit / delete / rename / attachments
vault.writer.write("Journal/new.md", "# New note")

# Graph facet — backlinks / outlinks / orphans / paths
backlinks = vault.graph.get_backlinks("Journal/note.md")

# Index facet — build / reindex / embeddings / readiness
vault.index.reindex()
```

## ReaderFacet

Search, read, listing, table-of-contents, similarity, context, history/diff, and attachment reads.

## `ReaderFacet(*, search_mgr, doc_mgr, git_query_mgr, require_built)`

Read-only queries over the shared managers.

Hold the managers the read methods delegate to.

Parameters:

| Name            | Type                 | Description                                                    | Default    |
| --------------- | -------------------- | -------------------------------------------------------------- | ---------- |
| `search_mgr`    | `SearchManager`      | Search / list / similarity / context / recent / stats queries. | *required* |
| `doc_mgr`       | `DocumentManager`    | Document and attachment reads, table-of-contents.              | *required* |
| `git_query_mgr` | `GitQueryManager`    | Git history / diff reads.                                      | *required* |
| `require_built` | `Callable[[], None]` | Index-readiness gate for the bucket-3 methods.                 | *required* |

### `search(query, *, limit=10, mode='keyword', filters=None, folder=None, chunks_per_file=None, snippet_words=None)`

Search the vault.

Parameters:

| Name              | Type                                       | Description                                                                                                | Default                                                                                                      |
| ----------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `query`           | `str`                                      | Search string.                                                                                             | *required*                                                                                                   |
| `limit`           | `int`                                      | Maximum number of files (not chunks) to return.                                                            | `10`                                                                                                         |
| `mode`            | `Literal['keyword', 'semantic', 'hybrid']` | "keyword" for BM25 FTS5, "semantic" for cosine similarity, or "hybrid" for Reciprocal Rank Fusion of both. | `'keyword'`                                                                                                  |
| `filters`         | \`dict[str, str]                           | None\`                                                                                                     | Dict of {frontmatter_key: value} pairs (AND semantics). Only works for fields in indexed_frontmatter_fields. |
| `folder`          | \`str                                      | None\`                                                                                                     | If provided, restrict results to documents in this folder (and its sub-folders).                             |
| `chunks_per_file` | \`int                                      | None\`                                                                                                     | Maximum number of sections returned per file. None uses the server default configured at startup.            |
| `snippet_words`   | \`int                                      | None\`                                                                                                     | Width of the snippet window in words. 0 returns the full chunk. None uses the server default.                |

Returns:

| Type                  | Description                                                    |
| --------------------- | -------------------------------------------------------------- |
| `list[GroupedResult]` | List of :class:~markdown_vault_mcp.types.GroupedResult ordered |
| `list[GroupedResult]` | by descending file score (max of section scores). Each result  |
| `list[GroupedResult]` | wraps one document with up to chunks_per_file sections.        |

Raises:

| Type         | Description                                                                                   |
| ------------ | --------------------------------------------------------------------------------------------- |
| `ValueError` | If mode is "semantic" or "hybrid" but no embedding provider or embeddings path is configured. |

### `read(path, *, section=None)`

Read the full content of a document from disk.

Parameters:

| Name      | Type  | Description                                      | Default                                                                                                                                                                                                                                                                                                                   |
| --------- | ----- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`    | `str` | Relative document path (e.g. "Journal/note.md"). | *required*                                                                                                                                                                                                                                                                                                                |
| `section` | \`str | None\`                                           | When provided, return only the section whose heading matches section (case-sensitive; internal whitespace is collapsed before comparison). Pass the heading value from a search result unchanged for guaranteed match. None (the default) returns the whole document. Raises :exc:ValueError if the section is not found. |

Returns:

| Name | Type          | Description |
| ---- | ------------- | ----------- |
| `A`  | \`NoteContent | None\`      |
|      | \`NoteContent | None\`      |

### `get_metadata(path)`

Return indexed metadata (title/folder/frontmatter) without a read.

Unlike :meth:`read`, this hits only the index — no file I/O, no `max_note_read_bytes` cap — so it is the right call for consumers that need a label rather than the document body (e.g. graph node rendering).

Parameters:

| Name   | Type  | Description             | Default    |
| ------ | ----- | ----------------------- | ---------- |
| `path` | `str` | Relative document path. | *required* |

Returns:

| Name | Type           | Description |
| ---- | -------------- | ----------- |
| `A`  | \`DocumentMeta | None\`      |
|      | \`DocumentMeta | None\`      |

### `list_documents(*, folder=None, pattern=None, include_attachments=False)`

List documents (and optionally attachments) in the vault.

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

### `list_folders()`

Return all distinct folder values across the indexed vault.

Returns:

| Type        | Description                                            |
| ----------- | ------------------------------------------------------ |
| `list[str]` | Sorted list of folder strings ("" for the vault root). |

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

### `get_toc(path, *, max_level=None, max_notes=200)`

Return a table of contents for a note or a folder subtree.

Note paths (ending in `.md`) return a flat :class:`~markdown_vault_mcp.types.TocEntry` list with the title as a synthetic H1. Folder paths return a :class:`~markdown_vault_mcp.types.SubtreeToc`. The result depends on the FTS index, so cold-start callers must build the index first (bucket 3).

Parameters:

| Name        | Type  | Description                                      | Default                                           |
| ----------- | ----- | ------------------------------------------------ | ------------------------------------------------- |
| `path`      | `str` | Note path or folder prefix.                      | *required*                                        |
| `max_level` | \`int | None\`                                           | Drop headings with level above this (both modes). |
| `max_notes` | `int` | Folder mode cap on distinct notes (default 200). | `200`                                             |

Returns:

| Type             | Description  |
| ---------------- | ------------ |
| \`list[TocEntry] | SubtreeToc\` |
| \`list[TocEntry] | SubtreeToc\` |

Raises:

| Type                    | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `IndexUnavailableError` | If :meth:IndexFacet.build_index has not been called. |
| `ValueError`            | Note path with no document; invalid folder path.     |

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

### `get_similar(path, *, limit=10, chunks_per_file=None)`

Return semantically similar documents grouped by file.

See :meth:`SearchManager.get_similar` for details. Returns :class:`~markdown_vault_mcp.types.GroupedResult` objects ordered by descending file score; each result wraps one document with up to `chunks_per_file` sections.

Parameters:

| Name              | Type  | Description                              | Default                           |
| ----------------- | ----- | ---------------------------------------- | --------------------------------- |
| `path`            | `str` | Relative path of the reference document. | *required*                        |
| `limit`           | `int` | Maximum number of files to return.       | `10`                              |
| `chunks_per_file` | \`int | None\`                                   | Maximum sections per result file. |

Returns:

| Type                  | Description              |
| --------------------- | ------------------------ |
| `list[GroupedResult]` | List of grouped results. |

Raises:

| Type                    | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `IndexUnavailableError` | If :meth:IndexFacet.build_index has not been called. |

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

| Name | Type          | Description                                                 |
| ---- | ------------- | ----------------------------------------------------------- |
| `A`  | `NoteContext` | class:~markdown_vault_mcp.types.NoteContext object. Its     |
|      | `NoteContext` | similar field is a list of                                  |
|      | `NoteContext` | class:~markdown_vault_mcp.types.GroupedResult entries, each |
|      | `NoteContext` | with exactly one section (chunks_per_file=1) so the dossier |
|      | `NoteContext` | stays compact.                                              |

Raises:

| Type                    | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `IndexUnavailableError` | If :meth:IndexFacet.build_index has not been called. |
| `ValueError`            | If no document exists at the given path.             |

### `get_history(path=None, since=None, until=None, limit=20)`

Return commits that touched a note, attachment, or the whole vault.

When *path* is `None`, queries the full vault history. Returns an empty list for vaults whose source directory is not inside a git repository.

Parameters:

| Name    | Type  | Description                                                               | Default                                                                                                                                                                                                                                 |
| ------- | ----- | ------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`  | \`str | None\`                                                                    | A .md note or a configured attachment (e.g. assets/x.png); None returns vault-wide history.                                                                                                                                             |
| `since` | \`str | None\`                                                                    | ISO 8601 datetime string or git date expression (e.g. "1 week ago"). Passed as --since to git log. None disables the filter.                                                                                                            |
| `until` | \`str | None\`                                                                    | ISO 8601 datetime string or git date expression, passed as --until to git log. None disables the filter. Both since and until boundaries are inclusive: a commit whose committer date equals either endpoint is included in the result. |
| `limit` | `int` | Maximum number of commits to return. Clamped to [1, 100]. Defaults to 20. | `20`                                                                                                                                                                                                                                    |

Returns:

| Type                 | Description                                                    |
| -------------------- | -------------------------------------------------------------- |
| `list[HistoryEntry]` | List of :class:~markdown_vault_mcp.types.HistoryEntry ordered  |
| `list[HistoryEntry]` | newest-first. Empty list when the vault has no git history or  |
| `list[HistoryEntry]` | the note has no commits in the given range. The                |
| `list[HistoryEntry]` | paths_changed field on each entry is populated for vault-wide  |
| `list[HistoryEntry]` | queries (path=None); it is always empty for single-note        |
| `list[HistoryEntry]` | queries, since the path is already determined by the query     |
| `list[HistoryEntry]` | arguments — callers know which file the commit touched without |
| `list[HistoryEntry]` | needing it echoed back.                                        |

Raises:

| Type         | Description                                                                              |
| ------------ | ---------------------------------------------------------------------------------------- |
| `ValueError` | If path is provided but fails path validation (unsupported extension or path traversal). |

### `get_diff(path, since_sha=None, since_timestamp=None, per_commit=False, limit=None)`

Return the diff of a note or attachment between a ref and HEAD.

Exactly one of *since_sha* or *since_timestamp* must be supplied.

Parameters:

| Name              | Type   | Description                                                                                                                                                                           | Default                                                                                                                                                                                                                                                            |
| ----------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `path`            | `str`  | A .md note or a configured attachment (e.g. assets/x.png) to diff. Unsupported extensions raise ValueError.                                                                           | *required*                                                                                                                                                                                                                                                         |
| `since_sha`       | \`str  | None\`                                                                                                                                                                                | A commit SHA (full or abbreviated, at least 4 hex digits) to diff from. Mutually exclusive with since_timestamp.                                                                                                                                                   |
| `since_timestamp` | \`str  | None\`                                                                                                                                                                                | ISO 8601 datetime string, resolved via git rev-list --before=<ts> -1 HEAD to the most recent commit at or before that instant. Boundary is inclusive: a commit whose committer date equals since_timestamp IS the resolved ref. Mutually exclusive with since_sha. |
| `per_commit`      | `bool` | When False (default), return a single unified diff string from the reference point to HEAD. When True, return one :class:~markdown_vault_mcp.types.CommitDiff per intervening commit. | `False`                                                                                                                                                                                                                                                            |
| `limit`           | \`int  | None\`                                                                                                                                                                                | When per_commit is True, cap the number of intervening commits returned to the limit most recent ones. Clamped to [1, 100]. None (the default) means unbounded (still bounded by the underlying since..HEAD range). Silently ignored when per_commit is False.     |

Returns:

| Type  | Description        |
| ----- | ------------------ |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |

Raises:

| Type         | Description                                                                                                                                                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ValueError` | If exactly one of since_sha / since_timestamp is not supplied, since_sha contains invalid characters, the resolved ref is not found in history, or path has an extension that is neither .md nor a configured attachment type. |

### `stats()`

Return vault-wide statistics.

Delegates to :meth:`SearchManager.stats`.

Returns:

| Type         | Description                                          |
| ------------ | ---------------------------------------------------- |
| `VaultStats` | class:~markdown_vault_mcp.types.VaultStats snapshot. |

### `attachment_size(path)`

Return an attachment's on-disk byte size without reading it.

Delegates to :meth:`DocumentManager.attachment_size`.

### `read_attachment(path)`

Read the binary content of a non-.md attachment.

Delegates to :meth:`DocumentManager.read_attachment`.

## WriterFacet

Create, edit, delete, rename, and attachment writes.

## `WriterFacet(doc_mgr)`

Document-mutation operations, backed by :class:`DocumentManager`.

Hold the document manager the write operations delegate to.

Parameters:

| Name      | Type              | Description                                          | Default    |
| --------- | ----------------- | ---------------------------------------------------- | ---------- |
| `doc_mgr` | `DocumentManager` | The shared :class:DocumentManager owned by the root. | *required* |

### `write(path, content, frontmatter=None, if_match=None)`

Create or overwrite a document.

Creates intermediate directories as needed. If *frontmatter* is provided, it is serialised as a YAML header at the top of the file.

Parameters:

| Name          | Type             | Description                                     | Default                                                                                                                                                                                                                                      |
| ------------- | ---------------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`        | `str`            | Relative document path (e.g. "notes/topic.md"). | *required*                                                                                                                                                                                                                                   |
| `content`     | `str`            | Markdown body (excluding frontmatter).          | *required*                                                                                                                                                                                                                                   |
| `frontmatter` | \`dict[str, Any] | None\`                                          | Optional frontmatter dict serialised as a YAML header.                                                                                                                                                                                       |
| `if_match`    | \`str            | None\`                                          | Optional etag from a previous :meth:ReaderFacet.read call. When provided, the write is only performed if the current file hash matches this value, preventing overwrites of concurrent modifications. Pass None (default) to skip the check. |

Returns:

| Type          | Description                                  |
| ------------- | -------------------------------------------- |
| `WriteResult` | class:~markdown_vault_mcp.types.WriteResult. |

Raises:

| Type                          | Description                                                                                                                   |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `ReadOnlyError`               | If the vault is read-only.                                                                                                    |
| `ConcurrentModificationError` | If if_match is provided and does not match the current file hash, or if_match is supplied for a file that does not yet exist. |
| `ValueError`                  | If path escapes the source directory.                                                                                         |

### `edit(path, old_text=None, new_text='', if_match=None, line_start=None, line_end=None)`

Patch a section of a document.

Replaces the first occurrence of *old_text* with *new_text*, or replaces the line range \[*line_start*, *line_end*\] when line numbers are given instead.

Parameters:

| Name         | Type  | Description                                         | Default                                                                                         |
| ------------ | ----- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `path`       | `str` | Relative document path.                             | *required*                                                                                      |
| `old_text`   | \`str | None\`                                              | Exact text to replace (must occur exactly once). Mutually exclusive with line_start / line_end. |
| `new_text`   | `str` | Replacement text (may be empty to delete old_text). | `''`                                                                                            |
| `if_match`   | \`str | None\`                                              | Optional etag for optimistic concurrency; see :meth:WriterFacet.write.                          |
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
| `ReadOnlyError`               | If the vault is read-only.                          |
| `ConcurrentModificationError` | If if_match is provided and does not match.         |
| `DocumentNotFoundError`       | If the file does not exist.                         |
| `ValueError`                  | If path escapes the source directory.               |

### `delete(path, if_match=None)`

Delete a document or attachment.

Removes the file from disk and purges its entries from the FTS and vector indices.

Parameters:

| Name       | Type  | Description                                            | Default                                                                |
| ---------- | ----- | ------------------------------------------------------ | ---------------------------------------------------------------------- |
| `path`     | `str` | Relative path of the document or attachment to remove. | *required*                                                             |
| `if_match` | \`str | None\`                                                 | Optional etag for optimistic concurrency; see :meth:WriterFacet.write. |

Returns:

| Type           | Description                                   |
| -------------- | --------------------------------------------- |
| `DeleteResult` | class:~markdown_vault_mcp.types.DeleteResult. |

Raises:

| Type                          | Description                                 |
| ----------------------------- | ------------------------------------------- |
| `ReadOnlyError`               | If the vault is read-only.                  |
| `ConcurrentModificationError` | If if_match is provided and does not match. |
| `DocumentNotFoundError`       | If path does not exist.                     |

### `rename(old_path, new_path, if_match=None, *, update_links=False)`

Rename or move a document or attachment.

Moves the file on disk and updates the FTS / vector indices. When *update_links* is `True`, all wikilinks and markdown links in other documents that pointed to *old_path* are rewritten to *new_path*.

Parameters:

| Name           | Type   | Description                                                                                    | Default                                                                |
| -------------- | ------ | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `old_path`     | `str`  | Current relative path of the document or attachment.                                           | *required*                                                             |
| `new_path`     | `str`  | Desired relative path after the move.                                                          | *required*                                                             |
| `if_match`     | \`str  | None\`                                                                                         | Optional etag for optimistic concurrency; see :meth:WriterFacet.write. |
| `update_links` | `bool` | When True, rewrite internal links across the vault to reflect the new path. Defaults to False. | `False`                                                                |

Returns:

| Type           | Description                                   |
| -------------- | --------------------------------------------- |
| `RenameResult` | class:~markdown_vault_mcp.types.RenameResult. |

Raises:

| Type                          | Description                                           |
| ----------------------------- | ----------------------------------------------------- |
| `ReadOnlyError`               | If the vault is read-only.                            |
| `ConcurrentModificationError` | If if_match is provided and does not match.           |
| `DocumentNotFoundError`       | If old_path does not exist.                           |
| `DocumentExistsError`         | If new_path already exists.                           |
| `ValueError`                  | If old_path or new_path escapes the source directory. |

### `move_folder(old_dir, new_dir)`

Move a folder subtree to a new prefix, rewriting links vault-wide.

Moves every file under *old_dir* (notes, attachments, and other files) to the matching path under *new_dir* and rewrites all links across the vault that point into the moved subtree, including links between documents inside it.

The move is atomic at the gate (a destination-file collision aborts before anything moves); link rewrites are best-effort.

Parameters:

| Name      | Type  | Description                                          | Default    |
| --------- | ----- | ---------------------------------------------------- | ---------- |
| `old_dir` | `str` | Relative source folder prefix (e.g. "drafts").       | *required* |
| `new_dir` | `str` | Relative target folder prefix (e.g. "archive/2026"). | *required* |

Returns:

| Type               | Description                                       |
| ------------------ | ------------------------------------------------- |
| `MoveFolderResult` | class:~markdown_vault_mcp.types.MoveFolderResult. |

Raises:

| Type                    | Description                                                                                                                                                                                                                                         |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ReadOnlyError`         | If the vault is read-only.                                                                                                                                                                                                                          |
| `DocumentNotFoundError` | If old_dir is missing, not a directory, or empty.                                                                                                                                                                                                   |
| `DocumentExistsError`   | If any destination file already exists.                                                                                                                                                                                                             |
| `ValueError`            | If either path escapes the vault or the two paths are nested.                                                                                                                                                                                       |
| `OSError`               | If the OS raises during the move phase (e.g. a permission error or full disk). The collision gate prevents pre-existing destination clashes, but a mid-move OS error leaves the subtree partially moved with the index unchanged; reindex recovers. |

### `write_attachment(path, content, if_match=None)`

Create or overwrite a non-.md attachment.

Delegates to :meth:`DocumentManager.write_attachment`.

Returns:

| Type          | Description                                  |
| ------------- | -------------------------------------------- |
| `WriteResult` | class:~markdown_vault_mcp.types.WriteResult. |

Raises:

| Type                          | Description                                                                                                                   |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `ReadOnlyError`               | If the vault is read-only.                                                                                                    |
| `ConcurrentModificationError` | If if_match is provided and does not match the current file hash, or if_match is supplied for a file that does not yet exist. |
| `ValueError`                  | If the path escapes the source directory or has an extension not in the allowlist.                                            |

## GraphFacet

Backlinks, outlinks, broken links, orphans, most-linked notes, and connection paths.

## `GraphFacet(*, link_mgr, require_built)`

Link-graph queries, backed by :class:`LinkManager`.

Hold the link manager and the index-readiness gate.

Parameters:

| Name            | Type                 | Description                                                                                              | Default    |
| --------------- | -------------------- | -------------------------------------------------------------------------------------------------------- | ---------- |
| `link_mgr`      | `LinkManager`        | The shared :class:LinkManager owned by the root.                                                         | *required* |
| `require_built` | `Callable[[], None]` | Raises :exc:IndexUnavailableError if the index has not been built; called by the bucket-3 query methods. | *required* |

### `get_backlinks(path, *, limit=None)`

Return all documents that link to the given document.

Parameters:

| Name    | Type  | Description                                                   | Default                                                              |
| ------- | ----- | ------------------------------------------------------------- | -------------------------------------------------------------------- |
| `path`  | `str` | Relative path of the target document (e.g. "notes/topic.md"). | *required*                                                           |
| `limit` | \`int | None\`                                                        | Maximum number of results to return. None (default) means unlimited. |

Returns:

| Type                 | Description                                                   |
| -------------------- | ------------------------------------------------------------- |
| `list[BacklinkInfo]` | List of :class:~markdown_vault_mcp.types.BacklinkInfo objects |
| `list[BacklinkInfo]` | for each document that contains a link pointing to path.      |

Raises:

| Type                    | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `IndexUnavailableError` | If :meth:IndexFacet.build_index has not been called. |
| `ValueError`            | If no document exists at the given path.             |

### `get_outlinks(path, *, limit=None)`

Return all links from the given document to other documents.

The `exists` field on each :class:`~markdown_vault_mcp.types.OutlinkInfo` indicates whether the target document is currently indexed.

Parameters:

| Name    | Type  | Description                                                   | Default                                                              |
| ------- | ----- | ------------------------------------------------------------- | -------------------------------------------------------------------- |
| `path`  | `str` | Relative path of the source document (e.g. "notes/topic.md"). | *required*                                                           |
| `limit` | \`int | None\`                                                        | Maximum number of results to return. None (default) means unlimited. |

Returns:

| Type                | Description                                                      |
| ------------------- | ---------------------------------------------------------------- |
| `list[OutlinkInfo]` | List of :class:~markdown_vault_mcp.types.OutlinkInfo objects for |
| `list[OutlinkInfo]` | each link originating from path.                                 |

Raises:

| Type                    | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `IndexUnavailableError` | If :meth:IndexFacet.build_index has not been called. |
| `ValueError`            | If no document exists at the given path.             |

### `get_broken_links(*, folder=None)`

Return all links whose target does not exist in the vault.

Parameters:

| Name     | Type  | Description | Default                                                                                      |
| -------- | ----- | ----------- | -------------------------------------------------------------------------------------------- |
| `folder` | \`str | None\`      | If provided, restrict to source documents in this folder (exact match or sub-folder prefix). |

Returns:

| Type                   | Description                                                      |
| ---------------------- | ---------------------------------------------------------------- |
| `list[BrokenLinkInfo]` | List of :class:~markdown_vault_mcp.types.BrokenLinkInfo objects. |

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

| Type                    | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `IndexUnavailableError` | If :meth:IndexFacet.build_index has not been called. |
| `ValueError`            | If source or target is not found in the index.       |

## IndexFacet

Index build / reindex / embeddings (sync + async), readiness, and writer status.

## `IndexFacet(*, coordinator, index_mgr)`

Index build, readiness, writer-status, and embeddings-status operations.

Delegates 1:1 to the :class:`IndexWriteCoordinator` (build / readiness / writer status) and to :class:`IndexManager` (:meth:`IndexFacet.embeddings_status`).

Hold the collaborators the index operations delegate to.

Parameters:

| Name          | Type                    | Description                                                                                              | Default    |
| ------------- | ----------------------- | -------------------------------------------------------------------------------------------------------- | ---------- |
| `coordinator` | `IndexWriteCoordinator` | The shared :class:IndexWriteCoordinator owned by the root. Only its public operations are surfaced here. | *required* |
| `index_mgr`   | `IndexManager`          | The shared :class:IndexManager, queried by :meth:IndexFacet.embeddings_status.                           | *required* |

### `is_queryable()`

Return True when the FTS index is queryable (precondition snapshot).

A captured build error does NOT demote queryability: it is diagnostic state surfaced via :meth:`IndexFacet.get_index_status`, not a gate.

### `start_background_build_index()`

Spawn a daemon thread that runs :meth:`IndexFacet.build_index` to completion.

.. deprecated:: 1.28 Superseded by :meth:`IndexFacet.build_index_async`. Retained for legacy tests.

### `should_use_background_build()`

Return True iff the lifespan should route to the background build.

.. deprecated:: 1.28 Retained for legacy tests; the lifespan no longer branches on it.

### `is_drained()`

Return True iff the IndexWriter has no pending or in-flight work.

Reflects the moment of call only; pair with :meth:`IndexFacet.write_generation` to detect a complete write cycle inside a read window.

### `write_generation()`

Return the writer's monotonic completion counter.

Increments once per completed job. Pair with :meth:`IndexFacet.is_drained` to detect a write cycle inside a read window.

### `wait_for_drain(timeout=None)`

Block until :meth:`IndexFacet.is_drained`; `True` if drained, `False` on timeout.

### `get_index_status()`

Return a non-blocking twelve-key snapshot of build + writer state.

Keys: `status` (`"queryable"` | `"building"` | `"failed"`), `documents_indexed`, `documents_indexed_error`, `error`, `last_reindex_error`, `last_build_embeddings_error`, plus `queue_depth`, `in_flight`, `dirty_paths`, `dirty_embeddings`, `write_generation` merged from the writer, and `skipped_files` — a list of `{path, category, detail}` dicts for files dropped from the index for a surfaced deterministic reason (parse / encoding / missing-frontmatter / internal-error), read from tracker state (#775, #802). A captured build error appears in `error` as diagnostic context without demoting a `queryable` status; `documents_indexed_error` carries a SQLite read failure (`documents_indexed` stays `0`) (#583).

### `wait_until_queryable(timeout=None)`

Block until the FTS index is queryable, or raise.

A captured build error does NOT block here; it surfaces as `IndexUnavailableError(reason="build_failed")` and is also readable via :meth:`IndexFacet.get_index_status`. Library bucket-3/4 methods use the root's `_require_built` instead, which raises immediately.

Raises:

| Type                    | Description                                                                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `IndexUnavailableError` | timeout expired (reason="timeout"), a build ran and failed (reason="build_failed"), or no build was ever scheduled (reason="never_built"). |

### `build_index(*, force=False)`

Scan source_dir and build the FTS index.

Warm restarts (existing populated index, `force=False`) are an O(1) no-op keyed on FTS state. `force=True` drops and rebuilds; config changes require `force=True` to apply (see issue #525).

Returns:

| Type         | Description                                                             |
| ------------ | ----------------------------------------------------------------------- |
| `IndexStats` | class:~markdown_vault_mcp.types.IndexStats describing what was indexed. |

### `reindex()`

Incrementally update the index based on file changes.

Returns:

| Type            | Description                                                        |
| --------------- | ------------------------------------------------------------------ |
| `ReindexResult` | class:~markdown_vault_mcp.types.ReindexResult with counts applied. |

Raises:

| Type                    | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `IndexUnavailableError` | If :meth:IndexFacet.build_index has not been called. |

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

| Type                           | Description                                                                |
| ------------------------------ | -------------------------------------------------------------------------- |
| `IndexUnavailableError`        | If :meth:IndexFacet.build_index has not been called.                       |
| `EmbeddingsNotConfiguredError` | If embedding_provider or embeddings_path is unset (a ValueError subclass). |

### `build_index_async(*, force=False)`

Submit a full FTS index build and return the Future.

Caller may `.result()` to wait or fire-and-forget. Warm-restart short-circuit returns an already-resolved Future without queuing a job, mirroring :meth:`IndexFacet.build_index`.

Parameters:

| Name    | Type   | Description                                            | Default |
| ------- | ------ | ------------------------------------------------------ | ------- |
| `force` | `bool` | When True, drop and rebuild the index unconditionally. | `False` |

Returns:

| Type                 | Description                                               |
| -------------------- | --------------------------------------------------------- |
| `Future[IndexStats]` | concurrent.futures.Future carrying the :class:IndexStats. |

### `reindex_async()`

Submit an incremental FTS reindex and return the Future.

Does not require :meth:`IndexFacet.build_index` first — the writer's FIFO queue orders any earlier :class:`BuildIndex` before this job. Writer-thread failures are surfaced via :meth:`IndexFacet.get_index_status` (#561).

### `build_embeddings_async(*, force=False)`

Submit a vector index build and return the Future.

Does not require :meth:`IndexFacet.build_index` first — FIFO ordering runs any earlier :class:`BuildIndex` first. Writer-thread failures are surfaced via :meth:`IndexFacet.get_index_status` (#561).

### `embeddings_status()`

Return status information about the vector index.

Returns:

| Type             | Description                                 |
| ---------------- | ------------------------------------------- |
| `dict[str, Any]` | Dict with keys provider, chunk_count, path, |
| `dict[str, Any]` | available.                                  |

### `skipped_files()`

Return files dropped from the index for a surfaced reason (#775).

Delegates to :meth:`IndexManager.skipped_files`; also merged, as plain dicts, into :meth:`IndexFacet.get_index_status`'s `skipped_files` key.

Returns:

| Type                | Description                                                       |
| ------------------- | ----------------------------------------------------------------- |
| `list[SkippedFile]` | Path-sorted list of :class:~markdown_vault_mcp.types.SkippedFile. |
