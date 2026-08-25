# MCP Tools

markdown-vault-mcp exposes MCP tools across several categories. Write tools are available unless `MARKDOWN_VAULT_MCP_READ_ONLY=true`.

!!! note "Index freshness on read tools (`wait_for_pending_writes` + `_meta.index_stale`)"
    Every read tool that queries the FTS index (`search`, `list_documents`, `list_folders`, `list_tags`, `stats`, `get_recent`, `get_backlinks`, `get_outlinks`, `get_broken_links`, `get_similar`, `get_toc`, `get_context`, `get_orphan_notes`, `get_most_linked`, and `get_connection_path`) accepts an optional **`wait_for_pending_writes`** (`bool`, default `false`) parameter and reports index freshness **out-of-band in the MCP response's `_meta.index_stale` field** rather than wrapping the payload in a `{stale, data}` envelope. The data payload is a **bare list/dict, identical whether the index is fresh or stale**; clients that do not care about drift ignore `_meta` entirely. Clients that need a fresh-read guarantee either inspect `result._meta.index_stale`, or pass `wait_for_pending_writes=true` to block until the writer drains (bounded by `MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S`, default 60&nbsp;s; on timeout it answers from the current index rather than raising). `index_stale` is `true` when the IndexWriter had pending or in-flight work. The relevant conditions are: the optional `wait_for_pending_writes` timed out; a write completed inside the read window; the writer was non-idle at response time. The same `_meta.index_stale` field rides on the index-querying MCP **resources** (`config://`, `stats://`, `folders://`, `tags://`, `recent://`, `toc://`, `similar://`), readable via the resource read's `_meta` (resources carry no `wait_for_pending_writes` parameter; they signal only).

!!! note "The `folder` argument"
    Every tool taking a `folder` argument reads it the same way. Omitting it (or passing `null`) applies no folder restriction. Passing the empty string `""` means **root-level documents only** on the read tools, and the bundle root on the `okf_*` write tools, since the root folder is spelled `""` in `list_folders` output and in each result's `folder` field. Any other value selects that folder and its sub-folders, and is matched after folding backslashes to forward slashes and stripping surrounding slashes, so `"Journal"`, `"Journal/"` and `"/Journal/"` all name the same folder.


<!-- DOMAIN-TOOLS-LIST-START -->

## Quick Reference

| Tool | Title | Category | Description |
|------|-------|----------|-------------|
| [`search`](#search) | Search Vault | Read | Hybrid full-text + semantic search with optional frontmatter filters |
| [`read`](#read) | Read Note | Read | Read a document or attachment by relative path |
| [`list_documents`](#list_documents) | List Documents | Read | List indexed documents and optionally attachments |
| [`list_folders`](#list_folders) | List Folders | Read | List all folder paths in the vault |
| [`list_tags`](#list_tags) | List Tags | Read | List all unique frontmatter tag values |
| [`stats`](#stats) | Vault Stats | Read | Get vault statistics and capabilities |
| [`embeddings_status`](#embeddings_status) | Embeddings Status | Read | Check embedding provider and vector index status |
| [`get_index_status`](#get_index_status) | Index Status | Read | Check background FTS build state (queryable / building / failed) |
| [`get_backlinks`](#get_backlinks) | Backlinks | Read | Find all documents that link to a given document |
| [`get_outlinks`](#get_outlinks) | Outlinks | Read | Find all links from a document, with existence check |
| [`get_broken_links`](#get_broken_links) | Broken Links | Read | Find all links pointing to non-existent documents |
| [`get_similar`](#get_similar) | Similar Notes | Read | Find semantically similar notes by document path |
| [`get_toc`](#get_toc) | Table of Contents | Read | Heading outline for a note or a folder subtree |
| [`get_recent`](#get_recent) | Recent Notes | Read | Get the most recently modified notes |
| [`get_context`](#get_context) | Note Context | Read | Get a consolidated context dossier for a note |
| [`get_conventions`](#get_conventions) | Folder Conventions | Read | Get the authoring conventions that apply to a note or folder |
| [`get_orphan_notes`](#get_orphan_notes) | Orphan Notes | Read | Find notes with no inbound or outbound links |
| [`get_most_linked`](#get_most_linked) | Most-Linked Notes | Read | Find the most-linked-to notes ranked by backlink count |
| [`get_connection_path`](#get_connection_path) | Connection Path | Read | Find the shortest path between two notes via link graph |
| [`okf_validate`](#okf_validate) | Validate OKF Bundle | Read | Audit the vault's Open Knowledge Format conformance |
| [`summarize`](#summarize) | Summarize Notes | AI | Summarize a note, a set of notes, or a subtree with an LLM (needs `OPENAI_API_KEY` or an OpenAI-compatible base URL) |
| [`get_job_result`](#get_job_result) | Get Job Result | AI | Retrieve the outcome of a background job started by a long-running tool |
| [`get_history`](#get_history) | Note History | Read (git) | List commits that touched a note, attachment, folder, or the whole vault |
| [`get_diff`](#get_diff) | Note Diff | Read (git) | Return a diff of a note or attachment between two points in history |
| [`reindex`](#reindex) | Reindex Vault | Admin | Incremental reindex (`force=true` re-parses everything); inline result when fast, job promotion when slow |
| [`build_embeddings`](#build_embeddings) | Build Embeddings | Admin | Build or rebuild vector embeddings; inline result when fast, job promotion when slow |
| [`write`](#write) | Write Note | Write | Create or overwrite a document or attachment |
| [`edit`](#edit) | Edit Note | Write | Replace a unique text span in a document |
| [`append`](#append) | Append to Note | Write | Append text to the end of a note without reading it first |
| [`delete`](#delete) | Delete Note | Write | Delete a document or attachment |
| [`rename`](#rename) | Rename Note | Write | Rename/move a document or attachment |
| [`move_folder`](#move_folder) | Move Folder | Write | Move an entire folder subtree and rewrite vault links |
| [`okf_convert_links`](#okf_convert_links) | OKF: Convert Wikilinks | Write | Rewrite wikilinks as OKF root-absolute markdown links |
| [`okf_generate_index`](#okf_generate_index) | OKF: Generate index.md | Write | Generate a reserved `index.md` listing from the TOC |
| [`okf_seed_log`](#okf_seed_log) | OKF: Seed log.md | Write | Seed a reserved `log.md` change history from git |
| [`okf_verify`](#okf_verify) | OKF: Verify Note | Write | Attest a note as human-reviewed (elicitation-gated; requires `OKF_WRITE`) |
| [`fetch`](#fetch) | Fetch to Vault | Write | Download from URL and save to vault |
| [`git_sync`](#git_sync) | Sync with Git | Write (git) | Force an immediate git pull / push / both, bypassing the periodic loops |
| [`create_download_link`](#create_download_link) | Create Download Link | Transfer | Mint a one-time capability URL to download a vault file or an OKF bundle archive (HTTP/SSE only) |
| [`create_upload_link`](#create_upload_link) | Create Upload Link | Transfer | Mint a one-time capability URL to upload bytes to a fixed vault path (HTTP/SSE only) |
| [`browse_vault`](#browse_vault) | Browse Vault | Apps | Open the vault explorer SPA |
| [`show_context`](#show_context) | Context Card | Apps | Open the Context Card for a note |

---

## Search & Discovery

### `search`

Find documents matching a query using full-text or semantic search.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | Natural language or keyword query string |
| `limit` | int | `10` | Maximum results to return |
| `mode` | string | `"keyword"` | `"keyword"` (FTS5/BM25), `"semantic"` (vector similarity), or `"hybrid"` (reciprocal rank fusion) |
| `folder` | string | `null` | Restrict to documents under this folder path |
| `filters` | object | `null` | Filter by indexed frontmatter field values (such as `{"tags": "pacing"}`), ANDed. On an OKF bundle, `status` (`stable` also matches notes without one), `stale` (`true`/`false`), and `trust_tier` carry OKF semantics; `type` filters normally |
| `chunks_per_file` | int | server default (`2`) | Maximum number of matching sections returned per file. Overrides `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` for this call. `0` is rejected. |
| `snippet_words` | int | server default (`200`) | Approximate word budget for each section's `content` field. `0` returns the full chunk. Overrides `MARKDOWN_VAULT_MCP_SNIPPET_WORDS` for this call. |

**Returns:** List of grouped result dicts ranked by relevance, one entry per file with up to `chunks_per_file` best-matching sections. Each entry contains: `path`, `title`, `folder`, `score` (max section score), `search_type`, `frontmatter`, and `sections` (a list of `{heading, content, score}` dicts sorted by score then document order). On an OKF (Open Knowledge Format) bundle (see `MARKDOWN_VAULT_MCP_OKF_MODE` in [Configuration](../configuration.md)), each entry also carries an `okf` dict with the note's `type`, lifecycle `status`, `stale` flag, `trust_tier`, and `sources_count`. On such a bundle, ranking also downweights `deprecated` notes (more) and stale notes (less) so current content surfaces first, and demotes the reserved navigation files `index.md` / `log.md` below real notes. This ranking adjustment applies only when a bundle is detected; on any other vault the result order is unchanged.

!!! note "Grouped result shape"
    Each file appears at most once in results, with up to `chunks_per_file` sections nested under `sections`. The top-level `score` is the maximum of the section scores (MaxP aggregation). Iterate `sections` to drill into individual matches.

!!! note "Snippet content and full-chunk recovery"
    By default, each section's `content` is a snippet of approximately 200 words centered on the query terms (not the full chunk). Pass `snippet_words=0` to receive the complete chunk. To read the full section after receiving a search result, call `read(path=result["path"], section=result["sections"][0]["heading"])`; this returns the entire section (body plus any sub-sections) reconstructed from the document.

!!! tip "Choosing a search mode"
    - Use `mode="hybrid"` when semantic search is available, combining keyword precision with semantic understanding
    - Use `mode="keyword"` for exact term matches
    - Use `mode="semantic"` for meaning-based similarity
    - Check `stats` to see if `semantic_search_available` is true

    Keyword and hybrid modes accept FTS5 operators (`AND`, `OR`, `NEAR`, `"exact phrase"`, `prefix*`). A natural-language query whose terms contain characters FTS5 reserves (a hyphenated slug such as `vault-mcp`, or a colon) is matched literally rather than failing, so plain queries do not need escaping.

    An empty or whitespace-only `query` returns an empty list in `semantic` and `hybrid` modes. Such a query carries no signal, and answering it locally avoids a round-trip that embedding providers reject with a raw HTTP 400.

**Example usage:**

```json
{
  "query": "character development techniques",
  "mode": "hybrid",
  "limit": 5,
  "filters": {"tags": "craft"}
}
```

### `read`

Read the full content of a document or attachment by path. When combined with search, the optional `section` parameter lets you retrieve a single section in full (its body and any sub-sections) without loading the entire document.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative path to the document or attachment (such as `"Journal/note.md"` or `"assets/diagram.pdf"`) |
| `section` | string | `null` | Optional heading selecting one section. The whole section is returned (every paragraph, list, and sub-section from the heading up to the next heading at the same or higher level), reconstructed from the document on disk, so a section longer than the chunk budget comes back intact rather than truncated to its first chunk. Pass the `heading` field from a `search` result. Matching collapses internal whitespace on both sides: `"1.3.  Reducing..."` (two spaces) matches a stored `"1.3. Reducing..."` (one space) and vice versa. On miss, the error lists the document's actual headings so callers can recover. Raises an error if the heading is not found or is empty. |

!!! tip "Recovering the full section after search"
    When `search` returns a snippet result, pass `result["heading"]` as the `section` parameter to recover the complete section: `read(path=result["path"], section=result["heading"])`. If the document has no sub-headings (preamble content), omit `section` to read the whole document.

!!! note "Heading matching tolerates whitespace differences"
    The `section` lookup compares heading strings after collapsing all whitespace runs to single spaces (and stripping leading/trailing whitespace). This handles the common case where an LLM caller infers a heading from a rendered TOC that normalises whitespace differently from the source markdown. Markdown emphasis (`**bold**`, `_italic_`) and case still matter; pass the heading as it would appear in the document source.

**Context cost:** every byte returned counts against the LLM's context
budget. Reads above `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` (default
256 KB for `.md`) or `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` (default
1 MB for binaries) raise an error; use `section=result["heading"]` for
partial markdown reads (see the tip above).

**Returns:**

=== "Markdown document"

    ```json
    {
      "path": "Journal/note.md",
      "title": "My Note",
      "folder": "Journal",
      "content": "---\ntitle: My Note\ntags: [journal]\n---\n\nThe note body...",
      "frontmatter": {"title": "My Note", "tags": ["journal"]},
      "modified_at": 1741564800.0
    }
    ```

    On an OKF bundle, whole-document reads also carry an `okf` dict (`type`, `status`, `stale`, `trust_tier`, and the note's full `sources` list). Section reads omit it — they carry no frontmatter to derive it from.

=== "Attachment"

    ```json
    {
      "path": "assets/diagram.pdf",
      "mime_type": "application/pdf",
      "size_bytes": 12345,
      "content_base64": "<base64 string>",
      "modified_at": 1741564800.0
    }
    ```

### `list_documents`

List documents (and optionally attachments) in the vault.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `folder` | string | `null` | Return only documents in this folder |
| `pattern` | string | `null` | Unix glob matched against relative paths (such as `"Journal/*.md"`) |
| `include_attachments` | bool | `false` | When true, also returns non-`.md` files that match the configured allowlist |
| `filters` | object | `null` | Frontmatter equality filters, ANDed (any key; list fields match by membership). On an OKF bundle, `status` / `stale` / `trust_tier` carry OKF semantics: `{"stale": "true"}` or `{"status": "deprecated"}` builds a triage listing. Any filter excludes attachments |

**Returns:** List of info dicts. Every entry has a `kind` field (`"note"` or `"attachment"`). Body content is not included; call `read` for full text.

### `list_folders`

List all folder paths that contain documents. Use this to discover valid folder names for filtering `search` or `list_documents`. The root folder (top-level documents) is represented as the empty string `""`.

**Returns:** Sorted list of folder paths, such as `["", "Journal", "Projects"]`.

### `list_tags`

List all distinct values for a frontmatter field across the vault.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `field` | string | `"tags"` | Frontmatter field name to enumerate. Must match a field in `indexed_frontmatter_fields` (check `stats`) |

**Returns:** Sorted list of distinct string values, such as `["craft", "pacing", "worldbuilding"]`.

### `stats`

Get an overview of the vault's size, capabilities, and configuration. Call this at the start of a session to understand what the vault contains and what search modes are available.

**Returns:**

```json
{
  "document_count": 42,
  "chunk_count": 156,
  "folder_count": 5,
  "semantic_search_available": true,
  "indexed_frontmatter_fields": ["tags", "cluster"],
  "attachment_extensions": ["pdf", "png", "jpg"]
}
```

On an OKF bundle (see `MARKDOWN_VAULT_MCP_OKF_MODE` in [Configuration](../configuration.md)), the response also carries an `okf` section: the configured mode, the declared spec version, a per-`type` histogram plus an untyped count, `status` and trust-tier breakdowns, and the stale-note count.

### `embeddings_status`

Check the embedding provider configuration and vector index status. Use this to diagnose why semantic search is unavailable.

**Returns:**

```json
{
  "available": true,
  "provider": "OllamaProvider",
  "chunk_count": 156,
  "path": "/data/state/embeddings/embeddings"
}
```

### `get_index_status`

Returns background-build state of the FTS index. Use this when
`initialize` returned but bucket-3/4 calls block longer than expected
or surface `IndexUnavailableError` (with `reason` of `"never_built"`,
`"build_failed"`, `"timeout"`, `"broken"`, or `"busy"`). The `status` field
distinguishes "still building" from "build failed," and the `error`
field carries the diagnostic message from the last failed background
build attempt.

**Returns:**
- `status`: `"queryable"`, `"building"`, or `"failed"`.
- `documents_indexed`: count of documents committed to the FTS index
  right now (rises during `"building"`). `0` both for an empty index
  and when the count could not be read; check `documents_indexed_error`
  to tell them apart.
- `documents_indexed_error`: `null` on a normal read; the SQLite error
  message when the document count could not be read (such as a locked or
  closed database), in which case `documents_indexed` is `0`.
- `error`: `null` unless the background build raised; otherwise the
  exception message.
- `skipped_files`: list of files dropped from the index for a surfaced
  deterministic reason. Each entry is `{"path", "category", "detail"}`, with
  `category` one of `parse_error`, `encoding_error`, `missing_frontmatter`, or
  `internal_error` (an unexpected indexer error rather than a content problem).
  Empty when nothing was skipped. This tells a parse-dropped note apart from
  one that simply has not synced yet, without reading container logs.
  Exclude-pattern matches and transient I/O skips are intentionally omitted.

**Tags:** read-only.

---

### `okf_validate`

Audit the vault's [OKF (Open Knowledge Format)](https://github.com/GoogleCloudPlatform/knowledge-catalog) conformance. Reports degrees, not a verdict: during a migration to OKF this is the progress meter. The audit reads the vault from disk, so it works before the index is built and before the vault declares `okf_version` (run it first to decide whether to declare). Paths matching the vault's effective exclude patterns are skipped, which doubles as the whitelist for known-nonconforming zones. The tool is hidden when `MARKDOWN_VAULT_MCP_OKF_MODE` is `off`.

No parameters.

**Returns:** Report object with the detection state (`mode`, `declared_version`, `active`), the progress ratio (`total_notes`, `conformant_notes`), `root_index_missing` (bool), and per-rule findings that each carry `count` and up to 20 `examples` paths. Conformance findings: `missing_type`, `unparseable_frontmatter`, `misplaced_okf_version`. Advisory: `unknown_status`, `log_heading_shape`. Informational: `wikilink_files`, `missing_recommended`. Reserved files (`index.md`, `log.md`) are exempt from the `type` rule.

## Index Management

!!! note "Cold-start blocking"
    Calls to `reindex` and `build_embeddings` during a cold-start background FTS build block via the tool-layer `needs_queryable` decorator. If the build takes longer than `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S` (default 60&nbsp;s), the tool returns `IndexUnavailableError(reason="timeout")`. The same exception fires with `reason="build_failed"` if a scheduled background build ran and failed; read `get_index_status`'s `error` field for the captured diagnostic. The decorator also remaps a SQLite `OperationalError` from the handler call to `IndexUnavailableError(reason="broken")` (corruption / I/O failure / unknown codes) or `reason="busy"` (SQLITE_BUSY/LOCKED, lock contention); inspect the exception's `__cause__` for the underlying SQLite error. Poll `get_index_status` to observe build state without blocking.

### `reindex`

Incrementally update the full-text search index to reflect file changes made outside this server. Only changed files are processed; unchanged documents are skipped, and files deliberately excluded from the index (missing required frontmatter, exclude-pattern matches, unparseable content) are remembered across scans so they are not re-parsed or re-reported until their content changes (#665).

If semantic search is configured, the reindex job re-embeds the changed documents on the writer thread. Poll `get_index_status` and watch the `dirty_embeddings` counter to observe embedding convergence.

!!! note "Boot reconciliation"
    The server lifespan automatically queues one incremental reindex at every startup (#665), so files added, modified, or deleted while no server was running are reconciled without a manual `reindex` call. Reads served before that job completes report `index_stale: true` in `_meta`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force` | bool | `false` | When true, drops every indexed document and re-parses the whole vault instead of applying the hash-detected delta. The index is not queryable while the rebuild runs |

Change detection is hash-based, so an unchanged file is never re-parsed, and the rows derived from it (links, chunks, tags) persist as first extracted. A server upgrade that changes extraction needs the index rebuilt, and the server does that for itself: each build records the version of its parse pipeline, and a start that finds an older version rebuilds once before serving (#1124). `force=true` is the manual form of the same repair, for an index you have reason to distrust; it is not routine maintenance. An ordinary reindex re-embeds the documents it touches, but a forced rebuild does not, so follow one with [`build_embeddings`](#build_embeddings) (without `force`) when semantic search is configured.

**Returns:** The tool is dual-mode. A fast reindex (the common case, since work scales with the drift, not the vault) completes inline with `"status": "completed"` and its real counts: `added`, `modified`, `deleted`, `unchanged`, and `skipped`, plus `full_rebuild` (true only for a `force=true` run, whose counts report every document under `added` because the rebuild dropped and re-added them all). A reindex still running at the jobs soft deadline (`JOBS_SOFT_DEADLINE_S`, default 25 s) continues on the single-owner :class:`IndexWriter` thread (#559) and returns `{"status": "working", "job_id": ...}` immediately; fetch the outcome with [`get_job_result`](#get_job_result). A failure within the deadline raises immediately; after promotion it is reported through `get_job_result` (and mirrored in `get_index_status`'s `last_reindex_error`). `get_index_status` remains the non-blocking observability view (`queue_depth`, `in_flight`, `dirty_paths`, `dirty_embeddings`), covering boot-time and file-watcher work no client call initiated.

### `build_embeddings`

Build vector embeddings to enable semantic and hybrid search. This can be slow for large vaults.

Without `force`, an existing vector index is **converged** to the FTS chunk set (#665). The operation embeds missing documents, re-embeds those whose indexed content changed, and drops vectors for deleted or excluded documents. A document whose source file still exists but is missing from the search index (such as one that failed to parse) keeps its vectors until the index sees it again (#1130). Work scales with the size of the drift, not the size of the vault; an already-converged index does no embedding work.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force` | bool | `false` | When true, discards existing embeddings and rebuilds from scratch. Use only if the embedding model has changed |

**Returns:** The tool is dual-mode. A fast convergence (small drift) completes inline with `"status": "completed"` and `chunks_embedded` (the total number of chunks embedded). A build still running at the jobs soft deadline (typical for a `force=true` rebuild of a large vault) continues on the single-owner :class:`IndexWriter` thread (#559) and returns `{"status": "working", "job_id": ...}` immediately; fetch the outcome with [`get_job_result`](#get_job_result). A failure within the deadline raises immediately (a missing embedding provider now surfaces here instead of landing only in `get_index_status`); after promotion it is reported through `get_job_result`.

!!! note "When to use"
    Normally never: the server queues a `build_embeddings` job at every startup, which converges the vector index to whatever the boot reconciliation reindex found (#665). Manual calls are needed in three cases: embedding a vault for the first time without restarting; retrying after a provider outage; or rebuilding from scratch after an embedding-model change (pass `force=true`).

---

## Write Operations

!!! info "Write tools are hidden when `MARKDOWN_VAULT_MCP_READ_ONLY=true`"
    They are registered by default. Set the variable to `true` for a
    search-only vault; through 3.1 that was the default, so a server
    upgrading from 3.x without setting it gains these tools.

    Several of them carry a second gate that this flag does not lift:
    `git_sync` needs managed git mode, `create_upload_link` needs an HTTP
    transport with `BASE_URL`, and the `okf_*` tools need OKF semantics
    active. Each is noted on its own entry below.

### `write`

Create or overwrite a document or attachment.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path. Extension determines handling (`.md` = note, else attachment) |
| `content` | string | Full markdown body for `.md` files (excluding frontmatter). Ignored for attachments |
| `frontmatter` | object | Optional YAML frontmatter dict for `.md` files. Ignored for attachments |
| `content_base64` | string | Base64-encoded binary content for attachment files. Required when path is not `.md` |

**Context cost:** the `content` parameter (text) is bounded only by the
LLM's own output budget. The `content_base64` parameter (binary) inflates
by ~33%.

**Returns:** `{"path": "Journal/note.md", "created": true}`

For `.md` files, the response may also include a `conventions` list: the
[folder conventions](#get_conventions) that apply to the target folder
(root-first `{folder, path, content}` entries; omitted when none apply).
Clients should verify the written note complies and issue a corrective `edit`
if it does not.

!!! warning
    `write` replaces the entire file. Use `edit` for targeted changes to existing documents.

### `edit`

Make a targeted text replacement in an existing document. Supports three modes:

- **Exact match** (`old_text` only): must appear exactly once in the document.
- **Line-range** (`line_start` + `line_end`, no `old_text`): replaces the specified lines. Pass `if_match` for safety.
- **Scoped match** (`old_text` + `line_start`/`line_end`): searches for `old_text` within the specified line range only.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Relative path to the document |
| `old_text` | string | Conditional | Text to replace. Required unless using line-range mode |
| `new_text` | string | Yes | Replacement text |
| `if_match` | string | No | Etag from `read` for optimistic concurrency |
| `line_start` | integer | Conditional | First line to replace (1-based, inclusive). Required with `line_end` |
| `line_end` | integer | Conditional | Last line to replace (1-based, inclusive). Required with `line_start` |

**Returns:** `{"path": "Journal/note.md", "replacements": 1, "match_type": "exact"}`

`match_type` is `"exact"` when the text matched byte-for-byte, or `"normalized"` when it matched after Unicode/whitespace normalization. The response may also include a `conventions` list; see [`write`](#write).

!!! tip "Usage pattern"
    Always call `read` first to get the exact current text and line numbers. For small edits, use `old_text` (exact match). For large block replacements, use `line_start`/`line_end` with the line numbers shown by `read`. Frontmatter can be edited; `old_text` may span the YAML block.

!!! info "Normalized matching"
    When exact match fails, the tool automatically tries a normalized comparison. Normalization covers Unicode NFC, whitespace collapsing, and smart quote conversion (en-dash/em-dash to hyphen). If a unique match is found, it proceeds and returns `match_type: "normalized"`.

!!! warning "Diagnostic errors"
    When no match is found, the error message reports the closest matching line number and the character position of the first difference, along with short snippets showing what was expected vs. what was found. This helps identify the exact mismatch.

### `append`

Append text to the end of an existing `.md` note without reading it first.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Relative path to the document |
| `content` | string | Yes | Text to append (must be non-empty) |
| `if_match` | string | No | Etag from `read` for optimistic concurrency |
| `create_if_missing` | bool | No | When `true`, a missing note is created with `content` as its body. Default `false`, so a typo in `path` fails loudly rather than silently creating a new note |

**Returns:** `{"path": "Journal/2026.md", "created": false}`

`created` is `true` only when `create_if_missing` created a new note. The response may also include a `conventions` list; see [`write`](#write).

!!! tip "Cheaper than `edit` for additive changes"
    Unlike `edit`, no prior `read` is needed, so the existing note content never enters the LLM context. This makes it ideal for log entries, journal additions, and checklist items. A newline is inserted between the existing content and the appended text when the file does not already end with one; include leading blank lines or heading markers in `content` yourself for a separating paragraph or section.

### `delete`

Permanently delete a document or attachment. For `.md` documents, also removes from all search indices.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document or attachment to delete |

**Returns:** `{"path": "Journal/old-note.md"}`

!!! danger
    This is irreversible unless git history exists. Confirm the path with the user before calling.

### `rename`

Rename a document or attachment, or move it to a different folder. Parent directories for the new path are created automatically.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `old_path` | string | Current relative path |
| `new_path` | string | Target relative path. Fails if `new_path` already exists |

**Returns:** `{"old_path": "drafts/idea.md", "new_path": "projects/idea.md"}`

### `move_folder`

Move an entire folder subtree to a new prefix and rewrite all vault links that point into the moved subtree. This is the folder-level analogue of `rename`.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `old_dir` | string | Current folder path (relative, no trailing slash, such as `"drafts/2024"`) |
| `new_dir` | string | Target folder path (relative, such as `"archive/2024"`) |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `old_dir` | string | Source folder path (echoed back) |
| `new_dir` | string | Destination folder path (echoed back) |
| `files_moved` | integer | Number of files moved on disk |
| `updated_links` | integer | Number of source documents whose links were rewritten |
| `failed_links` | array of strings | Paths of documents whose link rewrite failed (best-effort) |

**Atomicity:** before any file is moved, every destination path is checked. If any single destination file already exists the call raises and **nothing is moved**. Merging into a pre-existing target directory is allowed as long as no per-file collision exists.

**Link rewrites:** after the move, all vault links pointing into the subtree (markdown links and wikilinks, including links between documents inside the moved subtree and backlinks from outside) are rewritten in a single pass. Rewriting is best-effort; a document that cannot be rewritten is listed in `failed_links` and does not abort the move.

**Index:** updated immediately after the call; no `reindex` needed.

!!! warning
    Link rewrites are not rolled back if the process is interrupted after the move phase begins. The move phase itself is not OS-failure-atomic: an OS error during file moves (permission error, full disk, concurrent removal) can leave the subtree partially moved with the index unchanged; run `reindex` to recover. Use `rename` for single-file moves where full atomicity is required.

### OKF migration transforms

Three one-shot [OKF (Open Knowledge Format)](https://github.com/GoogleCloudPlatform/knowledge-catalog) migration tools for moving a vault toward the bundle conventions. They are write tools (hidden in read-only mode and when `MARKDOWN_VAULT_MCP_OKF_MODE` is `off`), and they run through the normal write path, so a git-backed vault commits each change. Run each one once when moving a vault into the format.

#### `okf_convert_links`

Rewrite `[[wikilinks]]` as the bundle-root-absolute markdown links OKF recommends (`[text](/path/note.md)`) across the vault or one folder. Only links whose target is indexed are converted, so the link graph is preserved edge-for-edge; unresolvable wikilinks are left untouched and counted as skipped. Re-running is safe (already-converted markdown links are not touched).

| Parameter | Type | Description |
|-----------|------|-------------|
| `folder` | string | Restrict to this folder subtree; omit to convert the whole vault |

**Returns:** `files_changed`, `links_converted`, `links_skipped`, `notes_scanned` (all integers).

#### `okf_generate_index`

Generate (or overwrite) a folder's reserved `index.md` as a progressive-disclosure listing: `- [title](/path.md) - description` for each note directly in the folder, plus a pointer into each immediate subfolder's own `index.md`. Descriptions are drawn from frontmatter. The listing is one level deep (it does not flatten the subtree). Existing frontmatter is preserved, so regenerating the bundle-root `index.md` keeps its `okf_version` declaration. Reserved files are omitted.

| Parameter | Type | Description |
|-----------|------|-------------|
| `folder` | string | Folder to index; omit for the bundle root |

**Returns:** `path` (string), `entries` (integer), `frontmatter_preserved` (bool).

#### `okf_seed_log`

Seed a folder's reserved `log.md` change history from the vault's git commit history, newest-first `## YYYY-MM-DD` sections, one bullet per commit. The `folder` argument both places the log and scopes its content: a folder seeds only the commits that touched that subtree, while the bundle root seeds the whole vault's history. Refuses to overwrite an existing `log.md` (a change history is hand-maintained after seeding).

| Parameter | Type | Description |
|-----------|------|-------------|
| `folder` | string | Folder to write `log.md` into and scope history to; omit for the bundle root (whole-vault history) |

**Returns:** `path` (string), `commits` (integer), `dates` (integer, distinct-day count).

#### `okf_verify`

Attest a note as human-reviewed by appending a `{by: human:<subject>, at: <date>}` entry to its `verified` frontmatter list, promoting the note's trust tier to `human-reviewed`. Part of the [enforced write layer](../guides/okf.md#the-enforced-write-layer): registered only when `MARKDOWN_VAULT_MCP_OKF_WRITE` is enabled. The append itself does not clear `verified`; only content-changing writes do that.

How the review is confirmed depends on [`MARKDOWN_VAULT_MCP_OKF_VERIFY`](../configuration.md):

- **`elicit`** (default): the tool issues an MCP elicitation asking you to confirm you reviewed the note, and writes the entry only on an affirmative reply. It fails closed (if the client cannot elicit or you decline, it errors and writes nothing), so a model cannot self-attest on your behalf. The subject recorded is your authenticated identity when present, else `local`.
- **`trust-auth`**: attributes to the authenticated caller with no confirmation, and errors when the server runs with no auth. Only safe when the sole caller is a human-driven UI.
- **`off`**: the tool is hidden entirely.

Whichever mode, `human-reviewed` means a human deliberately confirmed the review, not that the note is provably correct.

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Vault-relative path of the note to verify |

**Returns:** `path` (string), `verifier` (string, the `human:<subject>` actor recorded), `verified_count` (integer, entries after the append).

### `fetch`

Download a file from a URL and save it to the vault as a note or attachment. Designed for MCP-to-MCP file transfer when content is too large for the LLM context window.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | Source URL to download. Only `http`/`https` schemes allowed; the host is resolved and blocked unless every address is publicly routable (private, loopback, link-local, CGNAT/shared, and reserved ranges are all refused); the validated IP is pinned for the connection; ambient `HTTP(S)_PROXY`/`.netrc` settings are ignored. Redirects are followed, and each hop repeats every check above (SSRF protection) |
| `path` | string | required | Destination path in vault. Extension determines handling: `.md` for notes, anything else for attachments |
| `frontmatter` | object | `null` | Optional YAML frontmatter dict for `.md` files. Ignored for attachments |
| `if_match` | string | `null` | Optional etag from a previous `read` call for optimistic concurrency |
| `timeout_s` | float | `30.0` | Download timeout in seconds |

**Context cost:** zero. The file is downloaded server-side. Reference
the saved file by `path` for downstream tools rather than `read()`-ing it
back into context.

**Returns:** `{"path": "notes/report.md", "created": true, "content_length": 4096, "content_type": "text/markdown", "final_url": "https://example.com/report.md"}`

For `.md` destinations, the response may also include a `conventions` list; see [`write`](#write).

The download itself runs through `fastmcp-pvl-core`'s hardened `fetch_url`
primitive, so the SSRF protections above are shared, audited code rather
than a local copy.

!!! warning "Redirects are followed (changed in 4.0)"

    Through 3.1 this tool refused every redirect: a `301` or `302` produced
    an error rather than content. It now follows the chain, so the bytes may
    come from a host other than the one you asked for. Every hop re-runs the
    full address check, so a redirect cannot reach an internal target — but
    if you rely on `fetch` to reject indirection, that guarantee is gone.

    `final_url` reports where the bytes came from: equal to `url` when
    nothing redirected, otherwise the last hop. Check it when the source
    host matters. It keeps its query string (a redirect target's query is
    often load-bearing), so do not log it verbatim.

    A chain longer than the transport's redirect limit fails with a
    "too many redirects" error rather than saving anything.

### `git_sync`

Force an immediate `git pull` / `git push` / both, bypassing the periodic
pull interval and write-idle push delay. Returns a structured payload
with the local HEAD SHA, branch, and per-leg results so an LLM agent can
confirm "your changes are now on the remote" or recover from a divergent
history before continuing the conversation.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `direction` | `"pull"` \| `"push"` \| `"both"` | `"both"` | Which legs to run. `"both"` runs pull first, then push; if pull fails (`pull.applied=false`) the push leg is skipped and `push` stays `null` so a callable can inspect `pull.reason` before retrying. |
| `dry_run` | bool | `false` | When `true`, the pull leg runs `git fetch` and reports what *would* happen (`would_apply: bool`, projected `to_sha`) without moving HEAD. The push leg has no safe local probe for "would the remote accept this," so a dry-run push is a no-op that returns `applied=false` with `reason="dry_run_unsupported"`. |

**Returns:** Dict with the following fields:

- `direction` (str): the requested direction, echoed back.
- `head_sha` (str): local HEAD SHA after the operation. Differs from
  the pre-call HEAD when the pull leg advanced the branch.
- `branch` (str): current branch name (or `"HEAD"` on detached HEAD).
- `pull` (dict | null): payload from the pull leg, or `null` when
  `direction="push"`. Fields: `applied`, `fast_forward`,
  `commits_pulled`, `from_sha`, `to_sha`; optional `reason`,
  `conflict_files`; `would_apply` (only in `dry_run` mode).
  `commits_pulled` is reliable on the fast-forward path. On
  `reason="rebased"` and `reason="conflicts_resolved_with_siblings"` it
  is `0` even when HEAD advanced (the rebase replays local commits *on
  top of* the upstream rather than fast-forwarding); inspect
  `from_sha != to_sha` to detect the actual change.
- `push` (dict | null): payload from the push leg, or `null` when
  `direction="pull"` or when the pull leg failed in
  `direction="both"`. Fields: `applied`, `commits_pushed`,
  `remote_sha_before`, `remote_sha_after`; optional `reason`, `hint`.
- `dry_run` (bool): present only when `dry_run=true` was passed.

**Examples:**

Successful both-direction sync (clean fast-forward + clean push):

```json
{
  "direction": "both",
  "head_sha": "abc1234",
  "branch": "main",
  "pull": {
    "applied": true,
    "fast_forward": true,
    "commits_pulled": 3,
    "from_sha": "9999999",
    "to_sha": "abc1234"
  },
  "push": {
    "applied": true,
    "commits_pushed": 5,
    "remote_sha_before": "8888888",
    "remote_sha_after": "abc1234"
  }
}
```

Pull with conflict (Syncthing-style sibling resolution per
[#232](https://github.com/pvliesdonk/markdown-vault-mcp/issues/232)):

```json
{
  "direction": "pull",
  "head_sha": "abc1234",
  "branch": "main",
  "pull": {
    "applied": true,
    "fast_forward": false,
    "commits_pulled": 0,
    "from_sha": "9999999",
    "to_sha": "abc1234",
    "reason": "conflicts_resolved_with_siblings",
    "conflict_files": ["Notes/2026-05-09.conflict-mcp-20260511-114203.md"]
  },
  "push": null
}
```

The pull *succeeded* (`applied=true`): HEAD now points at the remote tip
and the local edits that conflicted with the remote were preserved as
`.conflict-mcp-<timestamp>.md` siblings on the same path. The remote
version wins on the canonical path; the LLM should read the listed
each sibling and propose how to merge the local content back in.
`commits_pulled` is `0` on this path because the rebase replays local
commits *on top of* the remote (the remote commits are reconciled, not
"pulled forward" in the linear-history sense).

Push rejected as non-fast-forward:

```json
{
  "direction": "push",
  "head_sha": "abc1234",
  "branch": "main",
  "push": {
    "applied": false,
    "commits_pushed": 0,
    "remote_sha_before": "9999999",
    "remote_sha_after": "9999999",
    "reason": "non_fast_forward",
    "hint": "Remote has commits the local clone has not seen.  Run git_sync(direction='pull') to reconcile (fast-forward when possible, Syncthing-style siblings on real conflict), then retry git_sync(direction='push')."
  }
}
```

**`pull.reason` values** (set on every non-fast-forward outcome and on
failures; `null` for clean fast-forwards and dry-runs):

| Reason | Meaning | `applied` |
|--------|---------|-----------|
| `"fetch_failed"` | `git fetch origin` exited non-zero (network / auth / proxy). HEAD did not move. | `false` |
| `"no_remote"` | No remote-tracking ref (`origin/<branch>`, or `origin/HEAD` for a detached checkout) could be resolved on the local clone. | `false` |
| `"rebased"` | Local and remote diverged but `git rebase origin/<branch>` replayed local commits cleanly. `conflict_files` empty. | `true` |
| `"conflicts_resolved_with_siblings"` | Rebase hit real conflicts; resolved by accepting upstream and writing local versions as `.conflict-mcp-*` siblings (#232). `conflict_files` populated. | `true` |
| `"conflict_resolution_failed"` | The conflict-resolution loop could not produce a recoverable working tree; rebase was aborted. HEAD did not move. | `false` |
| `"non_fast_forward_with_conflicts"` | Rare catastrophic fallback when even the conflict-resolution path could not stabilise the working tree. HEAD did not move. | `false` |

**`push.reason` values** (`null` on success including the
already-up-to-date no-op):

| Reason | Meaning | `applied` |
|--------|---------|-----------|
| `"dry_run_unsupported"` | Caller passed `dry_run=true`. Git has no safe local probe for "would the remote accept this," so the push leg is a deliberate no-op. | `false` |
| `"no_remote"` | No remote-tracking ref could be resolved (no `origin/<branch>` and no `origin/HEAD`). Push not attempted. | `false` |
| `"non_fast_forward"` | Remote rejected the push because the local branch is not a strict descendant of the remote tip. `hint` points at `git_sync(direction='pull')` to reconcile first. | `false` |
| `"push_failed"` | `git push origin` exited non-zero for any other reason (network, auth, server-side hook). `hint` carries the truncated stderr. | `false` |

**Context cost:** small (structured dict only, no file bytes).

**Tag:** `{write, git-managed}`. Hidden when
`MARKDOWN_VAULT_MCP_READ_ONLY=true` or when the deployment is not in
managed git mode (`MARKDOWN_VAULT_MCP_GIT_REPO_URL` not set).

**Errors:**

- `ValueError`: raised at call time when the underlying strategy is
  not a managed `GitWriteStrategy` (that is, `MARKDOWN_VAULT_MCP_GIT_REPO_URL`
  is unset). The visibility tag normally hides the tool in that case;
  this error guards the path where a client invokes the tool by name
  despite it not being advertised.

!!! note "Requirements"
    Only available in managed git mode. Set
    `MARKDOWN_VAULT_MCP_GIT_REPO_URL` and a working
    `MARKDOWN_VAULT_MCP_GIT_TOKEN` (with the
    `MARKDOWN_VAULT_MCP_GIT_USERNAME` appropriate for your provider;
    see the [Git Integration guide](../guides/git-integration.md#provider-username-reference)).

---

## Link Graph

!!! note "Cold-start blocking"
    Calls to `get_backlinks`, `get_outlinks`, `get_similar`, `get_context`, and `get_connection_path` during a cold-start background FTS build block via the tool-layer `needs_queryable` decorator. If the build takes longer than `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S` (default 60&nbsp;s), the tool returns `IndexUnavailableError(reason="timeout")`. The same exception fires with `reason="build_failed"` if a scheduled background build ran and failed; read `get_index_status`'s `error` field for the captured diagnostic. The decorator also remaps a SQLite `OperationalError` from the handler call to `IndexUnavailableError(reason="broken")` (corruption / I/O failure / unknown codes) or `reason="busy"` (SQLITE_BUSY/LOCKED, lock contention); inspect the exception's `__cause__` for the underlying SQLite error. Poll `get_index_status` to observe build state without blocking.

### `get_backlinks`

Find all documents that link to a given document.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative path to the target document |
| `limit` | int | `null` | Maximum number of backlinks to return. Omitted (the default) returns all. |
| `wait_for_pending_writes` | bool | `false` | Block until the IndexWriter drains before answering, then report freshness via `_meta.index_stale` (see the *Index freshness on read tools* note at the top of this page). |

**Returns:** List of documents containing links to the given path. Each entry has `source_path`, `source_title`, `link_text`, `link_type`, `fragment`, and `raw_target` fields. Index freshness is reported in `_meta.index_stale` (see the freshness note at the top of this page).

### `get_outlinks`

Find all links from a document, with existence check.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative path to the source document |
| `limit` | int | `null` | Maximum number of outlinks to return. Omitted (the default) returns all. |
| `wait_for_pending_writes` | bool | `false` | Block until the IndexWriter drains before answering, then report freshness via `_meta.index_stale` (see the *Index freshness on read tools* note at the top of this page). |

**Returns:** List of link targets with an `exists` field indicating whether the target document is in the vault. Each entry has `target_path`, `link_text`, `link_type`, `fragment`, `raw_target`, and `exists` fields. Index freshness is reported in `_meta.index_stale` (see the freshness note at the top of this page).

### `get_broken_links`

Find all links across the vault pointing to non-existent documents.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `folder` | string | `null` | Optional folder filter; only checks links from documents in this folder |

**Returns:** List of entries with `source_path`, `source_title`, `target_path`, `link_text`, `link_type`, `fragment`, and `raw_target` fields.

### `get_similar`

Find semantically similar notes by document path. Requires embeddings to be built.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative path to the document |
| `limit` | int | `10` | Maximum files to return |
| `chunks_per_file` | int | server default (`2`) | Maximum number of matching sections returned per file. Overrides `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` for this call. `0` is rejected. |
| `folder` | string | `null` | Restrict results to this folder (exact match or sub-folder prefix), such as `3-Resources` |
| `filters` | object | `null` | Frontmatter equality filters, ANDed, such as `{"type": "resource"}`. List-valued fields match by membership. On an OKF bundle, `status` / `stale` / `trust_tier` carry OKF semantics |
| `wait_for_pending_writes` | bool | `false` | Block until the IndexWriter drains before answering, then report freshness via `_meta.index_stale` (see the *Index freshness on read tools* note at the top of this page). |

**Returns:** List of grouped similar-document dicts ranked by cosine similarity, one entry per file with up to `chunks_per_file` best-matching sections. Each entry contains: `path`, `title`, `folder`, `score` (max section score), `search_type` (`"semantic"`), `frontmatter`, and `sections` (a list of `{heading, content, score}` dicts sorted by score then document order). Index freshness is reported in `_meta.index_stale` (see the freshness note at the top of this page).

!!! note "Grouped result shape"
    Returns one entry per file with up to `chunks_per_file` best-matching sections. Default is 2 sections per file; pass `chunks_per_file=1` for compact dossiers.

!!! note "Filter semantics"
    `folder` and `filters` are applied *after* the vector search (the vector store carries no structured metadata), against each candidate's full frontmatter. Unlike `search`'s keyword-mode filters, they are not limited to `MARKDOWN_VAULT_MCP_INDEXED_FIELDS`: any frontmatter key works. The candidate pool is automatically widened when filtering so narrow filters do not starve the result list.

### `get_toc`

Heading outline for a single note or an entire folder subtree. Mirrors the [`toc://vault/{path}`](../resources.md#tocvaultpath) resource, adding `max_level` and `max_notes` controls. Dispatch is by suffix: paths ending in `.md` are treated as notes; all other paths are treated as folder prefixes.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Note path (such as `"a/b.md"`) or folder prefix (such as `"a/b"`) |
| `max_level` | int | `null` | Drop headings deeper than this level (such as `2` to keep H1 and H2). The synthetic H1 title always survives. `null` returns all levels. |
| `max_notes` | int | `200` | Folder mode only. Cap on distinct notes. When more notes match, the first `max_notes` (sorted by path) are returned and `truncated` is `true`. |
| `wait_for_pending_writes` | bool | `false` | Block until the IndexWriter drains before answering, then report freshness via `_meta.index_stale` (see the *Index freshness on read tools* note at the top of this page). |

**Returns:**

- **Note mode** (`path` ends in `.md`): flat ordered `list` of `{heading (str), level (int)}`. The document title is included as a synthetic H1 entry.
- **Folder mode** (`path` is a folder prefix): `{path (str), notes (list), truncated (bool)}` where each entry in `notes` is `{path, title, headings}` and `headings` is a list of `{heading, level}` for that note. An empty or nonexistent folder returns an empty `notes` list with `truncated: false`.

Index freshness is reported in `_meta.index_stale` (see the freshness note at the top of this page).

### `get_recent`

Get the most recently modified notes.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | `20` | Maximum results to return |
| `folder` | string | `null` | Optional folder filter; only returns notes from this folder (such as `"Journal"`) |

**Returns:** List of notes with Unix timestamps (`modified_at` as float), sorted by modification time (newest first).

### `get_context`

Get a consolidated context dossier for a note. Combines backlinks, outlinks, similar notes, folder peers, tags, and modification time into a single response.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative path to the document |
| `similar_limit` | int | `5` | Max similar files to include. Pass `0` to skip the similarity lookup (such as when `stats` shows `semantic_search_available=false`) |
| `link_limit` | int | `10` | Max backlinks and outlinks to include each |
| `wait_for_pending_writes` | bool | `false` | Block until the IndexWriter drains before answering, then report freshness via `_meta.index_stale` (see the *Index freshness on read tools* note at the top of this page). |

**Returns:** Object with `path`, `title`, `folder`, `frontmatter`, `modified_at`, `backlinks`, `outlinks`, `similar`, `folder_notes`, and `tags` fields. The `similar` list contains grouped result dicts, one entry per file with up to `chunks_per_file` best-matching sections (default 1 for `get_context` to keep dossiers compact). May also include a `conventions` list: the [folder conventions](#get_conventions) that apply to the note's folder. On an OKF bundle it also carries the note's `okf` annotation (`type`, `status`, `stale`, `trust_tier`, `sources_count`). Index freshness is reported in `_meta.index_stale` (see the freshness note at the top of this page).

!!! note "Grouped similar shape"
    Each `similar` entry contains `path`, `title`, `folder`, `score`, `search_type`, `frontmatter`, and `sections` (a list of `{heading, content, score}` dicts). `get_context` defaults to one section per file for compact dossiers; `search` and `get_similar` default to 2.

### `get_conventions`

Get the vault owner's authoring conventions that apply to a note or folder.

Vaults may carry per-folder convention files (default `_conventions.md`,
configurable via
[`MARKDOWN_VAULT_MCP_CONVENTIONS_FILE`](../configuration.md)) whose free-form
markdown describes how notes in that folder should be authored, such as
*"reference material: keep notes self-contained; do not link out to project
or journal notes."* Conventions accumulate down the tree: a vault-root file
applies everywhere and nested files add to it. The server transports the text
verbatim; it never interprets it.

Convention files are excluded from the search index (they never appear in
`search`, `list_documents`, or `get_similar` results) but remain readable via
`read` and editable via `write`/`edit`. This tool reads directly from disk,
so it works even while the index is still building.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | `""` | Relative note path (resolves to its parent folder) or folder path. `""` returns vault-root conventions plus the folder listing |

**Returns:** Object with:

- `path`: the queried path.
- `conventions`: applicable entries, root-first, each `{folder, path, content}` (`folder` is `""` for the vault root).
- `convention_folders`: every folder carrying a convention file. Included only in discovery mode (`path=""`), since it requires a vault-wide folder walk.

!!! tip "Write-time enforcement"
    The `write`, `edit`, and `fetch` tools echo applicable conventions in
    their responses, so a client can self-check compliance right after
    writing. Call `get_conventions` *before* writing to get the rules up
    front.

### `get_orphan_notes`

Find all notes with no inbound or outbound links (isolated documents that may need cross-referencing).

**Returns:** List of `NoteInfo` objects (`path`, `title`, `folder`, `frontmatter`, `modified_at`, `kind`), ordered by path. Returns ALL orphans with no limit; check `stats.orphan_count` before calling on large vaults.

### `get_most_linked`

Find the most-linked-to notes in the vault, ranked by backlink count.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | `10` | Maximum results to return |

**Returns:** List of `{"path": "...", "backlink_count": N}` entries.

### `get_connection_path`

Find the shortest path between two notes via BFS on the undirected link graph (max 10 hops).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source` | string | required | Relative path to the starting document |
| `target` | string | required | Relative path to the target document |
| `max_depth` | int | `10` | Maximum hops to search (clamped to [1, 10]) |
| `wait_for_pending_writes` | bool | `false` | Block until the IndexWriter drains before answering, then report freshness via `_meta.index_stale` (see the *Index freshness on read tools* note at the top of this page). |

**Returns:** Object with `found` (bool), `path` (ordered list of document paths from source to target), and `hops` (number of edges, or `-1` if not found). Index freshness is reported in `_meta.index_stale` (see the *Index freshness on read tools* admonition at the top of this page).

### `get_history`

List commits that touched a note, attachment, or folder (or the whole vault) within an optional time window, up to a maximum count. Only available for git-backed vaults.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | `null` | Relative vault path. A `.md` note or a configured attachment extension (png, pdf, svg, …) scopes to that single file (such as `"notes/alpha.md"`, `"assets/diagram.png"`); an existing folder scopes to its subtree (such as `"guides"`, returning commits that touched `guides/**`). Omit for vault-wide history. A non-directory path with an unsupported extension is rejected. |
| `since` | string | `null` | ISO 8601 datetime string (`"2026-04-01T00:00:00"`) or git date expression (`"1 week ago"`). Passed as `--since` to `git log`. Inclusive at the boundary. |
| `until` | string | `null` | ISO 8601 datetime string or git date expression, passed as `--until` to `git log`. Combined with `since` to bound a window. Inclusive at the boundary. |
| `limit` | int | `20` | Maximum number of commits to return. Capped at 100. |

**Returns:** Object with `commits` (list of commit entries, newest-first) and `total` (count; always equals `len(commits)` and does NOT indicate how many commits exist beyond the `limit` cap). The envelope keeps the structured payload self-describing on the wire instead of relying on FastMCP's auto-wrapping `result` key. Each entry in `commits` contains:

| Field | Type | Description |
|-------|------|-------------|
| `sha` | string | Full 40-character commit SHA |
| `short_sha` | string | 7-character abbreviated SHA |
| `timestamp` | string | ISO 8601 author timestamp |
| `author` | string | Author name and email |
| `message` | string | First line of the commit message |
| `paths_changed` | list[string] | Files touched by the commit. Populated for vault-wide queries (`path=null`) and folder queries (the subtree files the commit touched); always empty for single-note queries, since the path is already determined by the query arguments (callers know which file the commit touched without needing it echoed back). |

**Raises:** `ToolError` if `path` is invalid or uses an unsupported extension.

### `get_diff`

Return the diff of a specific note or attachment between a reference point and `HEAD`. Only available for git-backed vaults.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative vault path (such as `"notes/alpha.md"` or `"assets/diagram.png"`). May be a `.md` note or a configured attachment extension (png, pdf, svg, …). An unsupported extension is rejected. |
| `since_sha` | string | `null` | A commit SHA (full or abbreviated, at least 4 hex digits) to diff from. Mutually exclusive with `since_timestamp`. |
| `since_timestamp` | string | `null` | ISO 8601 datetime string, resolved via `git rev-list --before=<ts> -1 HEAD` to the most recent commit at or before that instant. Boundary is **inclusive**: a commit whose committer date equals `since_timestamp` IS the resolved ref. Mutually exclusive with `since_sha`. |
| `per_commit` | bool | `false` | When `false`, return a single unified diff. When `true`, return one diff per intervening commit, newest-first. |
| `limit` | int | `null` | Applies only when `per_commit=true`. Caps the number of commits returned to the `limit` most recent ones. Clamped to `[1, 100]`. `null` = unbounded (still bounded by the `since..HEAD` range). Silently ignored when `per_commit=false`. |

Exactly one of `since_sha` / `since_timestamp` must be supplied.

**Returns:**

- `per_commit=false`: object with `diff` (string), the unified diff from reference to HEAD. For a **binary attachment**, this is a `git diff --stat` size/rename summary (such as `assets/x.png | Bin 1234 -> 5678 bytes`); for a **text attachment** (`.svg`, `.csv`, …) or `.md` note, it is a full unified patch. May include `[diff truncated: N bytes omitted]` if output exceeds 50 KB.
- `per_commit=true`: object with `commits` (list of per-commit entries, newest-first, each containing `sha`, `short_sha`, `timestamp`, `message`, and `diff`) and `total` (count; always equals `len(commits)` and does NOT indicate how many commits exist beyond the `limit` cap). The envelope keeps the structured payload self-describing on the wire instead of relying on FastMCP's auto-wrapping `result` key.

**Raises:** `ToolError` if parameters are invalid, the reference commit is not found, or the path uses an unsupported extension.

---

## AI Summarization

### `summarize`

Summarize a note, a set of notes, or a folder subtree with a language model. In the default `synthesis` mode the result is one cohesive summary that synthesizes across all the notes and **references the individual source notes by path**, so each point can be traced back to its origin. In `per_note` mode it returns a separate summary for each note instead.

The tool is only registered when a summarization backend is configured: an `OPENAI_API_KEY`, or an explicit OpenAI-compatible base URL for local endpoints that need no key. Otherwise it does not appear in the tool listing. Any OpenAI-compatible endpoint works: OpenAI, a local Ollama, the Anthropic compatibility endpoint, vLLM, and others.

Inputs larger than one model request are handled map-reduce style. Notes are packed into batches of at most `SUMMARIZE_MAX_INPUT_CHARS` characters and each batch is summarized on its own; a final pass combines the partial summaries into one result. Large folders issue several model calls and take proportionally longer. Coverage per call is capped at the note limit (`SUMMARIZE_MAX_NOTES`, also the ceiling for the per-call `max_notes` parameter); the response reports exactly how many notes made it in (`notes_included`) and how many were dropped (`notes_omitted`). When notes were dropped, the response carries a `hint` telling the caller that full coverage needs separate calls on subfolders or smaller path sets. The live configured limit is substituted into the tool description and into the server instructions at startup, so a calling model can plan those splits before its first call.

**Slow summaries do not block.** The tool is dual-mode: an MCP client that speaks background tasks runs it as a protocol-native task, and for any other client the call runs in the foreground up to the jobs soft deadline (`JOBS_SOFT_DEADLINE_S`, default 25 s). A summary that finishes within the deadline returns inline with `"status": "completed"` and the fields below. If it is still running when the deadline elapses, the tool returns `{"status": "working", "job_id": ...}` immediately and keeps generating in the background; fetch the result with [`get_job_result`](#get_job_result) using that `job_id`. Each individual backend call is itself bounded by `SUMMARIZE_TIMEOUT` (default 120 s); on timeout the summary fails with a clear message that says how to retry rather than a vague client-side hang.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `paths` | array of string | required | Note paths (`"notes/topic.md"`) and/or folder prefixes (`"notes/project"`). Folders expand to every note in the subtree (capped by the server's summarize limits). Duplicates are de-duplicated. |
| `focus` | string | `null` | Optional free-text instruction that steers the summary, such as `"extract action items"` or `"focus on decisions and their rationale"`. Omit for a general-purpose summary. |
| `mode` | `"synthesis"` \| `"per_note"` | `"synthesis"` | `synthesis` for one cross-note summary that references sources; `per_note` for one summary per note. |
| `max_notes` | int | server limit | Per-call note limit. Values above the server's configured cap are clamped to it; values below it narrow the work. |

**Returns:** When the summary completes within the soft deadline, a dict with `"status": "completed"` plus:

- `summary` (string): the generated summary text.
- `sources` (list of `{path, title}`): the notes that were summarised, always populated so individual notes are attributable even when the prose does not name every one.
- `mode` (string): the mode used.
- `truncated` (bool): `true` when content was lost to a cap. This covers the server's note limit and the per-request character budget, which can cut a single note as well as a partial summary during the combine step.
- `notes_included` (int): notes whose content reached the model.
- `notes_omitted` (int): matched notes dropped by the note limit. When non-zero, the summary does not cover the whole selection.
- `notes_limit` (int): the note limit in effect for this call.
- `hint` (string or null): recovery guidance when notes were omitted; `null` when the selection was fully covered.

When the work is promoted to a background job, a dict with `"status": "working"`, a `job_id` string, a `poll_with` field naming the polling tool, a `retry_after_s` hint, and a `message`. Call [`get_job_result`](#get_job_result) with the `job_id` to fetch the result.

**Errors:** raises if `paths` is empty, `mode` is invalid, no readable notes were found, or the backend call fails within the soft deadline. A backend failure after promotion is reported through `get_job_result` instead.

!!! warning "Note content goes to the configured backend"
    The referenced notes are sent to the summarization backend the operator configured, which may be a remote provider or a local endpoint. Do not summarize notes whose content must not be shared with that backend. The [`summarize-subtree` prompt](../prompts.md#summarize-subtree) is the client-side alternative that summarizes with the client's own model; see its docs for how the two routes relate.

!!! note "Dependency"
    Requires the `openai` SDK and an OpenAI-compatible backend (an `OPENAI_API_KEY`, or a base URL such as a local Ollama). Install with `pip install 'markdown-vault-mcp[summarize]'` (or `[all]`). Configure the endpoint, model, and limits via the `MARKDOWN_VAULT_MCP_SUMMARIZE_*` env vars. See [Configuration](../configuration.md).

---

### `get_job_result`

Retrieve the outcome of a background job started by a long-running tool on this server: a [`summarize`](#summarize), [`reindex`](#reindex), or [`build_embeddings`](#build_embeddings) call promoted past the jobs soft deadline. When such a call returns `{"status": "working", "job_id": ...}`, pass that `job_id` here to fetch the result, polling every few seconds while it is still running.

The tool is provided by the shared jobs subsystem (`fastmcp-pvl-core`), so its name, payload, and lifecycle vocabulary are uniform across the `*-mcp` server family, and it is always registered (the index-maintenance tools produce job handles regardless of whether a summarize backend is configured). Retrieval is scoped to the calling subject: another caller's `job_id` answers exactly like an unknown one. Job records expire `JOBS_RESULT_TTL_S` seconds after creation (default one hour) and a promoted job does not survive a server restart, so fetch results soon after completion.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `job_id` | string | required | The `job_id` from a `{"status": "working"}` answer of a long-running tool. |

**Returns:** Dict with `job_id`, `status`, `result`, and `error`, where `status` is one of:

- `"working"`: still running; the dict adds `running_for_s` and a `retry_after_s` polling hint. Poll again shortly.
- `"completed"`: the work is done; `result` carries the tool's full result object (for `summarize`: `summary`, `sources`, `mode`, `truncated`, `notes_included`, `notes_omitted`, `notes_limit`, `hint`).
- `"failed"`: the work failed; see `error` for the reason (often a backend timeout; narrow the request and retry).

**Errors:** raises for an unknown, expired, or foreign `job_id`.

---

## One-Time Transfer Links

Transfer tools mint short-lived capability URLs so large files can move between the vault and a browser or another service without inflating the LLM context window. The unguessable token in the URL is the authorization; no separate `Authorization` header is needed.

!!! note "HTTP/SSE transport only"
    Transfer tools require a running HTTP or SSE server with `MARKDOWN_VAULT_MCP_BASE_URL` set. They are not available on stdio transport.

!!! warning "Write tool visibility"
    `create_download_link` is available in read-only mode. `create_upload_link` is a write tool and is hidden when `MARKDOWN_VAULT_MCP_READ_ONLY=true`.

### `create_download_link`

Mint a one-time capability URL to download vault content. The `ref` says what to serve. It is either a vault-relative path to a note or attachment that exists at link-creation time, or an OKF-bundle reference for a generated bundle archive. The URL works exactly once; a successful download settles the token. A failed or interrupted download does not settle it, so a retry stays possible until the TTL expires.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref` | string | required | What to download. A vault-relative path to an existing note or attachment, or an OKF-bundle reference (see below) |
| `ttl_s` | number | server default (`MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S`) | Token lifetime in seconds. Clamped to `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S`. Omit to use the server default |

**Returns:**

```json
{
  "url": "https://mcp.example.com/transfer/...",
  "expires_in_s": 3600
}
```

**Example usage:**

```json
{"ref": "assets/diagram.pdf", "ttl_s": 600}
```

Then in a terminal:

```bash
curl "https://mcp.example.com/transfer/<token>" -o diagram.pdf
```

**OKF bundle export.** Pass an OKF-bundle reference as `ref` to download a conformant bundle archive of the vault (or a folder subtree) instead of a single file:

| `ref` value | Serves |
|-------------|--------|
| `okf-bundle` | A zip of the whole vault |
| `okf-bundle:<folder>` | A zip scoped to that folder subtree |

The export reads from the live vault and never changes it. Wikilinks become the root-absolute markdown links OKF recommends. Convention files (`_conventions.md`) and the template folder are left out, while the reserved `index.md` and `log.md` stay in. Non-conformant notes appear as they are. Run `okf_validate` for the residual conformance gaps; the archive itself carries no gap report. Bundle export is unavailable when `MARKDOWN_VAULT_MCP_OKF_MODE` is `off`.

!!! note "Read-lazy"
    A file `ref` is read from disk at fetch time, not at link-creation time, so the downloader receives the version current at fetch time. A bundle `ref` is generated at fetch time from the vault's current state.

### `create_upload_link`

Mint a one-time capability URL to upload bytes to a fixed, pre-validated destination path in the vault. The destination path is decided at link creation; the uploader sends raw bytes via `POST` (or `PUT` as an alias). The upload commits via the normal write path (traversal and extension validation, size cap, index update, and git-commit callback).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref` | string | required | Destination path in the vault. Validated for path traversal and allowed extension at link-creation time. May name a new or existing path; an existing file is overwritten on upload |
| `ttl_s` | number | server default (`MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S`) | Token lifetime in seconds. Clamped to `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S`. Omit to use the server default |

**Returns:**

```json
{
  "url": "https://mcp.example.com/transfer/...",
  "expires_in_s": 3600
}
```

**Example usage:**

```json
{"ref": "assets/uploaded-diagram.pdf"}
```

Then in a terminal:

```bash
curl -X POST --data-binary @local-diagram.pdf \
     "https://mcp.example.com/transfer/<token>"
```

!!! note "Raw body, not multipart"
    The upload endpoint expects the raw file bytes as the request body. Do not use `multipart/form-data`; send the content directly (curl's `--data-binary` flag does this correctly).

!!! note "One-time"
    The token is consumed on the first successful upload. A transient failure (network error, size limit exceeded) does not consume the token; retry is permitted until the TTL expires.

---

## MCP Apps

These tools power the browser-based vault explorer views. See the [MCP Apps guide](../guides/mcp-apps.md) for details.

### `browse_vault`

Open the vault explorer SPA. Optionally focus on a specific note and view.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | `null` | Note path to focus on |
| `view` | string | `null` | View to open: `context`, `graph`, `browse`, or `note` |

**Returns:** For Apps-capable clients, opens the interactive SPA. For other clients, returns a text summary.

### `show_context`

Open the Context Card view for a specific note, showing backlinks, outlinks, similar notes, tags, and folder peers.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document |

**Returns:** For Apps-capable clients, opens the Context Card. For other clients, returns the context dossier as text.
<!-- DOMAIN-TOOLS-LIST-END -->
