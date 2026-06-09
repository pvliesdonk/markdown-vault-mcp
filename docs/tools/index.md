# MCP Tools

markdown-vault-mcp exposes MCP tools across several categories. Write tools are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

!!! note "Index freshness on read tools (`wait_for_pending_writes` + `_meta.index_stale`)"
    Every read tool that queries the FTS index — `search`, `list_documents`, `list_folders`, `list_tags`, `stats`, `get_recent`, `get_backlinks`, `get_outlinks`, `get_broken_links`, `get_similar`, `get_context`, `get_orphan_notes`, `get_most_linked`, and `get_connection_path` — accepts an optional **`wait_for_pending_writes`** (`bool`, default `false`) parameter and reports index freshness **out-of-band in the MCP response's `_meta.index_stale` field** rather than wrapping the payload in a `{stale, data}` envelope. The data payload is a **bare list/dict, identical whether the index is fresh or stale** — clients that do not care about drift ignore `_meta` entirely. Clients that need a fresh-read guarantee either inspect `result._meta.index_stale`, or pass `wait_for_pending_writes=true` to block until the writer drains (bounded by `MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S`, default 60s; on timeout it answers from the current index rather than raising). `index_stale` is `true` when the IndexWriter had pending or in-flight work at any of three observation points: the optional `wait_for_pending_writes` timed out, a write completed inside the read window, or the writer was non-idle at response time. The same `_meta.index_stale` field rides on the index-querying MCP **resources** (`config://`, `stats://`, `folders://`, `tags://`, `recent://`, `toc://`, `similar://`), readable via the resource read's `_meta` (resources carry no `wait_for_pending_writes` parameter — they signal only).

<!-- DOMAIN-TOOLS-LIST-START -->

## Quick Reference

| Tool | Category | Description |
|------|----------|-------------|
| [`search`](#search) | Read | Hybrid full-text + semantic search with optional frontmatter filters |
| [`read`](#read) | Read | Read a document or attachment by relative path |
| [`list_documents`](#list_documents) | Read | List indexed documents and optionally attachments |
| [`list_folders`](#list_folders) | Read | List all folder paths in the vault |
| [`list_tags`](#list_tags) | Read | List all unique frontmatter tag values |
| [`stats`](#stats) | Read | Get vault statistics and capabilities |
| [`embeddings_status`](#embeddings_status) | Read | Check embedding provider and vector index status |
| [`get_index_status`](#get_index_status) | Read | Check background FTS build state (queryable / building / failed) |
| [`get_backlinks`](#get_backlinks) | Read | Find all documents that link to a given document |
| [`get_outlinks`](#get_outlinks) | Read | Find all links from a document, with existence check |
| [`get_broken_links`](#get_broken_links) | Read | Find all links pointing to non-existent documents |
| [`get_similar`](#get_similar) | Read | Find semantically similar notes by document path |
| [`get_recent`](#get_recent) | Read | Get the most recently modified notes |
| [`get_context`](#get_context) | Read | Get a consolidated context dossier for a note |
| [`get_orphan_notes`](#get_orphan_notes) | Read | Find notes with no inbound or outbound links |
| [`get_most_linked`](#get_most_linked) | Read | Find the most-linked-to notes ranked by backlink count |
| [`get_connection_path`](#get_connection_path) | Read | Find the shortest path between two notes via link graph |
| [`get_history`](#get_history) | Read (git) | List commits that touched a note or the whole vault |
| [`get_diff`](#get_diff) | Read (git) | Return a unified diff of a note between two points in history |
| [`reindex`](#reindex) | Admin | Force a full reindex of the vault |
| [`build_embeddings`](#build_embeddings) | Admin | Build or rebuild vector embeddings |
| [`write`](#write) | Write | Create or overwrite a document or attachment |
| [`edit`](#edit) | Write | Replace a unique text span in a document |
| [`delete`](#delete) | Write | Delete a document or attachment |
| [`rename`](#rename) | Write | Rename/move a document or attachment |
| [`fetch`](#fetch) | Write | Download from URL and save to vault |
| [`git_sync`](#git_sync) | Write (git) | Force an immediate git pull / push / both, bypassing the periodic loops |
| [`create_download_link`](#create_download_link) | Transfer | Mint a one-time capability URL to download a vault file (HTTP/SSE only) |
| [`create_upload_link`](#create_upload_link) | Transfer | Mint a one-time capability URL to upload bytes to a fixed vault path (HTTP/SSE only) |
| [`browse_vault`](#browse_vault) | Apps | Open the vault explorer SPA |
| [`show_context`](#show_context) | Apps | Open the Context Card for a note |

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
| `filters` | object | `null` | Filter by indexed frontmatter field values (e.g. `{"tags": "pacing"}`) |
| `chunks_per_file` | int | server default (`2`) | Maximum number of matching sections returned per file. Overrides `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` for this call. `0` is rejected. |
| `snippet_words` | int | server default (`200`) | Approximate word budget for each section's `content` field. `0` returns the full chunk. Overrides `MARKDOWN_VAULT_MCP_SNIPPET_WORDS` for this call. |

**Returns:** List of grouped result dicts ranked by relevance, one entry per file with up to `chunks_per_file` best-matching sections. Each entry contains: `path`, `title`, `folder`, `score` (max section score), `search_type`, `frontmatter`, and `sections` — a list of `{heading, content, score}` dicts sorted by score then document order.

!!! note "Grouped result shape"
    Each file appears at most once in results, with up to `chunks_per_file` sections nested under `sections`. The top-level `score` is the maximum of the section scores (MaxP aggregation). Iterate `sections` to drill into individual matches.

!!! note "Snippet content and full-chunk recovery"
    By default, each section's `content` is a snippet of approximately 200 words centered on the query terms — not the full chunk. Pass `snippet_words=0` to receive the complete chunk. To read the full section after receiving a search result, call `read(path=result["path"], section=result["sections"][0]["heading"])` — this returns the entire chunk from the index without re-reading the whole document.

!!! tip "Choosing a search mode"
    - Use `mode="hybrid"` when semantic search is available — it combines keyword precision with semantic understanding
    - Use `mode="keyword"` for exact term matches
    - Use `mode="semantic"` for meaning-based similarity
    - Check `stats` to see if `semantic_search_available` is true

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

Read the full content of a document or attachment by path. When combined with search, the optional `section` parameter lets you retrieve the full content of a specific chunk without loading the entire document.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative path to the document or attachment (e.g. `"Journal/note.md"` or `"assets/diagram.pdf"`) |
| `section` | string | `null` | Optional heading to select a single section chunk. Pass the `heading` field from a `search` result to retrieve the full chunk content. Matching collapses internal whitespace on both sides — `"1.3.  Reducing..."` (two spaces) matches a stored `"1.3. Reducing..."` (one space) and vice versa. On miss, the error lists the document's actual stored headings so callers can recover. Raises an error if the heading is not found or is empty. |

!!! tip "Recovering full chunks after search"
    When `search` returns a snippet result, pass `result["heading"]` as the `section` parameter to recover the complete chunk: `read(path=result["path"], section=result["heading"])`. If the document has no sub-headings (preamble content), omit `section` to read the whole document.

!!! note "Heading matching tolerates whitespace differences"
    The `section` lookup compares heading strings after collapsing all whitespace runs to single spaces (and stripping leading/trailing whitespace). This handles the common case where an LLM caller infers a heading from a rendered TOC that normalises whitespace differently from the source markdown. Markdown emphasis (`**bold**`, `_italic_`) and case are still significant — pass the heading as it would appear in the document source.

**Context cost:** every byte returned counts against the LLM's context
budget. Reads above `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` (default
256 KB for `.md`) or `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` (default
1 MB for binaries) raise an error — use `section=result["heading"]` for
partial markdown reads (see the tip above).

**Returns:**

=== "Markdown document"

    ```json
    {
      "path": "Journal/note.md",
      "title": "My Note",
      "folder": "Journal",
      "content": "The markdown body...",
      "frontmatter": {"title": "My Note", "tags": ["journal"]},
      "modified_at": 1741564800.0
    }
    ```

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
| `pattern` | string | `null` | Unix glob matched against relative paths (e.g. `"Journal/*.md"`) |
| `include_attachments` | bool | `false` | When true, also returns non-`.md` files that match the configured allowlist |

**Returns:** List of info dicts. Every entry has a `kind` field (`"note"` or `"attachment"`). Body content is not included — call `read` for full text.

### `list_folders`

List all folder paths that contain documents. Use this to discover valid folder names for filtering `search` or `list_documents`. The root folder (top-level documents) is represented as an empty string `""`.

**Returns:** Sorted list of folder paths, e.g. `["", "Journal", "Projects"]`.

### `list_tags`

List all distinct values for a frontmatter field across the vault.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `field` | string | `"tags"` | Frontmatter field name to enumerate. Must match a field in `indexed_frontmatter_fields` (check `stats`) |

**Returns:** Sorted list of distinct string values, e.g. `["craft", "pacing", "worldbuilding"]`.

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
`"build_failed"`, `"timeout"`, `"broken"`, or `"busy"`) — the `status` field
distinguishes "still building" from "build failed," and the `error`
field carries the diagnostic message from the last failed background
build attempt.

**Returns:**
- `status`: `"queryable"`, `"building"`, or `"failed"`.
- `documents_indexed`: count of documents committed to the FTS index
  right now (rises during `"building"`). `0` both for an empty index
  and when the count could not be read — check `documents_indexed_error`
  to tell them apart.
- `documents_indexed_error`: `null` on a normal read; the SQLite error
  message when the document count could not be read (e.g. a locked or
  closed database), in which case `documents_indexed` is `0`.
- `error`: `null` unless the background build raised; otherwise the
  exception message.

**Tags:** read-only.

---

## Index Management

!!! note "Cold-start blocking"
    Calls to `reindex` and `build_embeddings` during a cold-start background FTS build block via the tool-layer `needs_queryable` decorator. If the build takes longer than `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S` (default 60s), the tool returns `IndexUnavailableError(reason="timeout")`. The same exception fires with `reason="build_failed"` if a scheduled background build ran and failed — read `get_index_status`'s `error` field for the captured diagnostic. The decorator additionally remaps a SQLite `OperationalError` from the handler call to `IndexUnavailableError(reason="broken")` (corruption / I/O failure / unknown codes) or `reason="busy"` (SQLITE_BUSY/LOCKED — lock contention); inspect the exception's `__cause__` for the underlying SQLite error. Poll `get_index_status` to observe build state without blocking.

### `reindex`

Incrementally update the full-text search index to reflect file changes made outside this server. Only processes changed files — unchanged documents are skipped.

If semantic search is configured, the queued reindex job re-embeds the changed documents on the writer thread. Poll `get_index_status` and watch the `dirty_embeddings` counter to observe completion.

**Returns:** `{"status": "queued"}`. The reindex runs asynchronously on the single-owner :class:`IndexWriter` thread (#559); poll `get_server_info` or `get_index_status` for completion. `get_index_status` exposes `queue_depth`, `in_flight`, `dirty_paths`, and `dirty_embeddings` so you can observe progress without blocking.

### `build_embeddings`

Build vector embeddings to enable semantic and hybrid search. This can be slow for large vaults.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force` | bool | `false` | When true, discards existing embeddings and rebuilds from scratch. Use only if the embedding model has changed |

**Returns:** `{"status": "queued"}`. The build runs asynchronously on the single-owner :class:`IndexWriter` thread (#559); poll `get_server_info` or `get_index_status` for completion.

!!! note "When to use"
    Call `build_embeddings` once to enable semantic search for the first time. After that, `reindex` handles incremental re-embedding automatically.

---

## Write Operations

!!! warning "Write tools require `MARKDOWN_VAULT_MCP_READ_ONLY=false`"
    These tools are hidden when the server is in read-only mode (the default).

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
LLM's own output budget.  The `content_base64` parameter (binary) inflates
by ~33%.

**Returns:** `{"path": "Journal/note.md", "created": true}`

!!! warning
    `write` replaces the entire file — use `edit` for targeted changes to existing documents.

### `edit`

Make a targeted text replacement in an existing document. Supports three modes:

- **Exact match** (`old_text` only) — must appear exactly once in the document.
- **Line-range** (`line_start` + `line_end`, no `old_text`) — replaces the specified lines. Pass `if_match` for safety.
- **Scoped match** (`old_text` + `line_start`/`line_end`) — searches for `old_text` within the specified line range only.

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

`match_type` is `"exact"` when the text matched byte-for-byte, or `"normalized"` when it matched after Unicode/whitespace normalization.

!!! tip "Usage pattern"
    Always call `read` first to get the exact current text and line numbers. For small edits, use `old_text` (exact match). For large block replacements, use `line_start`/`line_end` with the line numbers shown by `read`. Frontmatter can be edited — `old_text` may span the YAML block.

!!! info "Normalized matching"
    When exact match fails, the tool automatically tries a normalized comparison: Unicode NFC, dash normalization (en-dash/em-dash → hyphen), smart quote normalization, whitespace collapsing. If a unique match is found, it proceeds and returns `match_type: "normalized"`.

!!! warning "Diagnostic errors"
    When no match is found, the error message includes diagnostic info: the closest matching line number, the character position of the first difference, and short snippets showing what was expected vs. what was found. This helps identify the exact mismatch.

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

### `fetch`

Download a file from a URL and save it to the vault as a note or attachment. Designed for MCP-to-MCP file transfer when content is too large for the LLM context window.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | Source URL to download. Only `http`/`https` schemes allowed; private/loopback IPs are blocked; redirects are not followed (SSRF protection) |
| `path` | string | required | Destination path in vault. Extension determines handling: `.md` for notes, anything else for attachments |
| `frontmatter` | object | `null` | Optional YAML frontmatter dict for `.md` files. Ignored for attachments |
| `if_match` | string | `null` | Optional etag from a previous `read` call for optimistic concurrency |
| `timeout_s` | float | `30.0` | Download timeout in seconds |

**Context cost:** zero — the file is downloaded server-side.  Reference
the saved file by `path` for downstream tools rather than `read()`-ing it
back into context.

**Returns:** `{"path": "notes/report.md", "created": true, "content_length": 4096, "content_type": "text/markdown"}`

!!! note "Dependency"
    Requires `httpx`. Install with `pip install 'markdown-vault-mcp[all]'`.

### `git_sync`

Force an immediate `git pull` / `git push` / both, bypassing the periodic
pull interval and write-idle push delay. Returns a structured payload
with the local HEAD SHA, branch, and per-leg results so an LLM agent can
confirm "your changes are now on the remote" or recover from a divergent
history before continuing the conversation.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `direction` | `"pull"` \| `"push"` \| `"both"` | `"both"` | Which leg(s) to run. `"both"` runs pull first, then push; if pull fails (`pull.applied=false`) the push leg is skipped and `push` stays `null` so a callable can inspect `pull.reason` before retrying. |
| `dry_run` | bool | `false` | When `true`, the pull leg runs `git fetch` and reports what *would* happen (`would_apply: bool`, projected `to_sha`) without moving HEAD. The push leg has no safe local probe for "would the remote accept this", so a dry-run push is a no-op that returns `applied=false` with `reason="dry_run_unsupported"`. |

**Returns:** Dict with the following fields:

- `direction` (str) — the requested direction, echoed back.
- `head_sha` (str) — local HEAD SHA after the operation. Differs from
  the pre-call HEAD when the pull leg advanced the branch.
- `branch` (str) — current branch name (or `"HEAD"` on detached HEAD).
- `pull` (dict | null) — payload from the pull leg, or `null` when
  `direction="push"`. Fields: `applied`, `fast_forward`,
  `commits_pulled`, `from_sha`, `to_sha`; optional `reason`,
  `conflict_files`; `would_apply` (only in `dry_run` mode).
  `commits_pulled` is reliable on the fast-forward path. On
  `reason="rebased"` and `reason="conflicts_resolved_with_siblings"` it
  is `0` even when HEAD advanced — the rebase replays local commits *on
  top of* the upstream rather than fast-forwarding, so inspect
  `from_sha != to_sha` to detect the actual change.
- `push` (dict | null) — payload from the push leg, or `null` when
  `direction="pull"` or when the pull leg failed in
  `direction="both"`. Fields: `applied`, `commits_pushed`,
  `remote_sha_before`, `remote_sha_after`; optional `reason`, `hint`.
- `dry_run` (bool) — present only when `dry_run=true` was passed.

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
sibling(s) and propose how to merge the local content back in.
`commits_pulled` is `0` on this path because the rebase replays local
commits *on top of* the remote — the remote commits are reconciled, not
"pulled forward" in the linear-history sense.

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
| `"no_remote"` | Neither `@{upstream}` nor `origin/HEAD` could be resolved on the local clone. | `false` |
| `"rebased"` | Local and remote diverged but `git rebase @{upstream}` replayed local commits cleanly. `conflict_files` empty. | `true` |
| `"conflicts_resolved_with_siblings"` | Rebase hit real conflicts; resolved by accepting upstream and writing local versions as `.conflict-mcp-*` siblings (#232). `conflict_files` populated. | `true` |
| `"conflict_resolution_failed"` | The conflict-resolution loop could not produce a recoverable working tree; rebase was aborted. HEAD did not move. | `false` |
| `"non_fast_forward_with_conflicts"` | Rare catastrophic fallback when even the conflict-resolution path could not stabilise the working tree. HEAD did not move. | `false` |

**`push.reason` values** (`null` on success including the
already-up-to-date no-op):

| Reason | Meaning | `applied` |
|--------|---------|-----------|
| `"dry_run_unsupported"` | Caller passed `dry_run=true`. Git has no safe local probe for "would the remote accept this", so the push leg is a deliberate no-op. | `false` |
| `"no_remote"` | Upstream tracking branch could not be resolved (no `@{upstream}` and no `origin/HEAD`). Push not attempted. | `false` |
| `"non_fast_forward"` | Remote rejected the push because the local branch is not a strict descendant of the remote tip. `hint` points at `git_sync(direction='pull')` to reconcile first. | `false` |
| `"push_failed"` | `git push origin` exited non-zero for any other reason (network, auth, server-side hook). `hint` carries the truncated stderr. | `false` |

**Context cost:** small — structured dict only, no file bytes.

**Tag:** `{write, git-managed}`. Hidden when
`MARKDOWN_VAULT_MCP_READ_ONLY=true` or when the deployment is not in
managed git mode (`MARKDOWN_VAULT_MCP_GIT_REPO_URL` not set).

**Errors:**

- `ValueError` — raised at call time when the underlying strategy is
  not a managed `GitWriteStrategy` (i.e. `MARKDOWN_VAULT_MCP_GIT_REPO_URL`
  is unset). The visibility tag normally hides the tool in that case;
  this error guards the path where a client invokes the tool by name
  despite it not being advertised.

!!! note "Requirements"
    Only available in managed git mode. Set
    `MARKDOWN_VAULT_MCP_GIT_REPO_URL` and a working
    `MARKDOWN_VAULT_MCP_GIT_TOKEN` (with the
    `MARKDOWN_VAULT_MCP_GIT_USERNAME` appropriate for your provider —
    see the [Git Integration guide](../guides/git-integration.md#provider-username-reference)).

---

## Link Graph

!!! note "Cold-start blocking"
    Calls to `get_backlinks`, `get_outlinks`, `get_similar`, `get_context`, and `get_connection_path` during a cold-start background FTS build block via the tool-layer `needs_queryable` decorator. If the build takes longer than `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S` (default 60s), the tool returns `IndexUnavailableError(reason="timeout")`. The same exception fires with `reason="build_failed"` if a scheduled background build ran and failed — read `get_index_status`'s `error` field for the captured diagnostic. The decorator additionally remaps a SQLite `OperationalError` from the handler call to `IndexUnavailableError(reason="broken")` (corruption / I/O failure / unknown codes) or `reason="busy"` (SQLITE_BUSY/LOCKED — lock contention); inspect the exception's `__cause__` for the underlying SQLite error. Poll `get_index_status` to observe build state without blocking.

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
| `wait_for_pending_writes` | bool | `false` | Block until the IndexWriter drains before answering, then report freshness via `_meta.index_stale` (see the *Index freshness on read tools* note at the top of this page). |

**Returns:** List of grouped similar-document dicts ranked by cosine similarity, one entry per file with up to `chunks_per_file` best-matching sections. Each entry contains: `path`, `title`, `folder`, `score` (max section score), `search_type` (`"semantic"`), `frontmatter`, and `sections` — a list of `{heading, content, score}` dicts sorted by score then document order. Index freshness is reported in `_meta.index_stale` (see the freshness note at the top of this page).

!!! note "Grouped result shape"
    Returns one entry per file with up to `chunks_per_file` best-matching sections. Default is 2 sections per file; pass `chunks_per_file=1` for compact dossiers.

### `get_recent`

Get the most recently modified notes.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | `20` | Maximum results to return |
| `folder` | string | `null` | Optional folder filter; only returns notes from this folder (e.g. `"Journal"`) |

**Returns:** List of notes with Unix timestamps (`modified_at` as float), sorted by modification time (newest first).

### `get_context`

Get a consolidated context dossier for a note. Combines backlinks, outlinks, similar notes, folder peers, tags, and modification time into a single response.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative path to the document |
| `similar_limit` | int | `5` | Max similar files to include. Pass `0` to skip the similarity lookup (e.g. when `stats` shows `semantic_search_available=false`) |
| `link_limit` | int | `10` | Max backlinks and outlinks to include each |
| `wait_for_pending_writes` | bool | `false` | Block until the IndexWriter drains before answering, then report freshness via `_meta.index_stale` (see the *Index freshness on read tools* note at the top of this page). |

**Returns:** Object with `path`, `title`, `folder`, `frontmatter`, `modified_at`, `backlinks`, `outlinks`, `similar`, `folder_notes`, and `tags` fields. The `similar` list contains grouped result dicts — one entry per file with up to `chunks_per_file` best-matching sections (default 1 for `get_context` to keep dossiers compact). Index freshness is reported in `_meta.index_stale` (see the freshness note at the top of this page).

!!! note "Grouped similar shape"
    Each `similar` entry contains `path`, `title`, `folder`, `score`, `search_type`, `frontmatter`, and `sections` — a list of `{heading, content, score}` dicts. `get_context` defaults to one section per file for compact dossiers; `search` and `get_similar` default to 2.

### `get_orphan_notes`

Find all notes with no inbound or outbound links — isolated documents that may need cross-referencing.

**Returns:** List of `NoteInfo` objects (`path`, `title`, `folder`, `frontmatter`, `modified_at`, `kind`), ordered by path. Returns ALL orphans with no limit — check `stats.orphan_count` before calling on large vaults.

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

**Returns:** Object with `found` (bool), `path` (ordered list of note paths from source to target), and `hops` (number of edges, or `-1` if not found). Index freshness is reported in `_meta.index_stale` (see the freshness note at the top of this page).

### `get_history`

List commits that touched a note (or the whole vault) within an optional time window, up to a maximum count. Only available for git-backed vaults.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | `null` | Relative vault path (e.g. `"notes/alpha.md"`). Omit for vault-wide history. Must end with `.md`. |
| `since` | string | `null` | ISO 8601 datetime string (`"2026-04-01T00:00:00"`) or git date expression (`"1 week ago"`). Passed as `--since` to `git log`. Inclusive at the boundary. |
| `until` | string | `null` | ISO 8601 datetime string or git date expression, passed as `--until` to `git log`. Combined with `since` to bound a window. Inclusive at the boundary. |
| `limit` | int | `20` | Maximum number of commits to return. Capped at 100. |

**Returns:** Object with `commits` (list of commit entries, newest-first) and `total` (count — always equals `len(commits)`; does NOT indicate how many commits exist beyond the `limit` cap). The envelope keeps the structured payload self-describing on the wire instead of relying on FastMCP's auto-wrapping `result` key. Each entry in `commits` contains:

| Field | Type | Description |
|-------|------|-------------|
| `sha` | string | Full 40-character commit SHA |
| `short_sha` | string | 7-character abbreviated SHA |
| `timestamp` | string | ISO 8601 author timestamp |
| `author` | string | Committer name and email |
| `message` | string | First line of the commit message |
| `paths_changed` | list[string] | Files touched by the commit. Populated for vault-wide queries (`path=null`); always empty for single-note queries, since the path is already determined by the query arguments — callers know which file the commit touched without needing it echoed back. |

**Raises:** `ToolError` if `path` is invalid.

### `get_diff`

Return the diff of a specific note between a reference point and `HEAD`. Only available for git-backed vaults.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative vault path. Must end with `.md`. |
| `since_sha` | string | `null` | A commit SHA (full or abbreviated, at least 4 hex digits) to diff from. Mutually exclusive with `since_timestamp`. |
| `since_timestamp` | string | `null` | ISO 8601 datetime string, resolved via `git rev-list --before=<ts> -1 HEAD` to the most recent commit at or before that instant. Boundary is **inclusive**: a commit whose committer date equals `since_timestamp` IS the resolved ref. Mutually exclusive with `since_sha`. |
| `per_commit` | bool | `false` | When `false`, return a single unified diff. When `true`, return one diff per intervening commit, newest-first. |
| `limit` | int | `null` | Only meaningful when `per_commit=true`. Caps the number of commits returned to the `limit` most recent ones. Clamped to `[1, 100]`. `null` = unbounded (still bounded by the `since..HEAD` range). Silently ignored when `per_commit=false`. |

Exactly one of `since_sha` / `since_timestamp` must be supplied.

**Returns:**

- `per_commit=false`: object with `diff` (string) — unified diff from reference to HEAD. May include `[diff truncated: N bytes omitted]` if output exceeds 50 KB.
- `per_commit=true`: object with `commits` (list of per-commit entries, newest-first — each containing `sha`, `short_sha`, `timestamp`, `message`, and `diff`) and `total` (count — always equals `len(commits)`; does NOT indicate how many commits exist beyond the `limit` cap). The envelope keeps the structured payload self-describing on the wire instead of relying on FastMCP's auto-wrapping `result` key.

**Raises:** `ToolError` if parameters are invalid or the reference commit is not found.

---

## One-Time Transfer Links

Transfer tools mint short-lived capability URLs so large files can move between the vault and a browser or another service without inflating the LLM context window. The unguessable token in the URL is the authorization — no separate `Authorization` header is needed.

!!! note "HTTP/SSE transport only"
    Transfer tools require a running HTTP or SSE server with `MARKDOWN_VAULT_MCP_BASE_URL` set. They are not available on stdio transport.

!!! warning "Write tool visibility"
    `create_download_link` is available in read-only mode. `create_upload_link` is a write tool and is hidden when `MARKDOWN_VAULT_MCP_READ_ONLY=true`.

### `create_download_link`

Mint a one-time capability URL to download a vault note or attachment. The file must exist at link-creation time. The URL can be fetched exactly once — after a successful download the token is consumed. A failed or interrupted download does not consume the token.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative path to the vault file (note or attachment). The file must exist; a missing path raises an error immediately |
| `ttl_seconds` | int | server default (`MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S`) | Token lifetime in seconds. Clamped to `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S`. Omit to use the server default |

**Returns:**

```json
{
  "url": "https://mcp.example.com/transfer/...",
  "path": "notes/report.md",
  "expires_at": "2026-06-05T14:00:00+00:00",
  "expires_in_seconds": 3600
}
```

**Example usage:**

```json
{"path": "assets/diagram.pdf", "ttl_seconds": 600}
```

Then in a terminal:

```bash
curl "https://mcp.example.com/transfer/<token>" -o diagram.pdf
```

!!! note "Read-lazy"
    The file is read from disk at fetch time, not at link-creation time. If the file is modified between link creation and download, the downloader receives the version current at fetch time.

### `create_upload_link`

Mint a one-time capability URL to upload bytes to a fixed, pre-validated destination path in the vault. The destination path is decided at link creation; the uploader sends raw bytes via `POST` (or `PUT` as an alias). The upload commits via the normal write path (traversal and extension validation, size cap, index update, and git-commit callback).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Destination path in the vault. Validated for path traversal and allowed extension at link-creation time. May name a new or existing path; an existing file is overwritten on upload |
| `ttl_seconds` | int | server default (`MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S`) | Token lifetime in seconds. Clamped to `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S`. Omit to use the server default |

**Returns:**

```json
{
  "url": "https://mcp.example.com/transfer/...",
  "path": "assets/upload.pdf",
  "expires_at": "2026-06-05T14:00:00+00:00",
  "expires_in_seconds": 3600
}
```

**Example usage:**

```json
{"path": "assets/uploaded-diagram.pdf"}
```

Then in a terminal:

```bash
curl -X POST --data-binary @local-diagram.pdf \
     "https://mcp.example.com/transfer/<token>"
```

!!! note "Raw body — not multipart"
    The upload endpoint expects the raw file bytes as the request body. Do not use `multipart/form-data`; send the content directly (curl's `--data-binary` flag does this correctly).

!!! note "One-time"
    The token is consumed on the first successful upload. A transient failure (network error, size limit exceeded) does not consume the token — retry is permitted until the TTL expires.

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
