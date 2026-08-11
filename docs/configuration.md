# Configuration

Markdown Vault MCP is configured via environment variables with the
`MARKDOWN_VAULT_MCP_` prefix.

See `fastmcp-pvl-core`'s README for the full list of universal
variables (`MARKDOWN_VAULT_MCP_TRANSPORT`, `MARKDOWN_VAULT_MCP_HOST`,
`MARKDOWN_VAULT_MCP_PORT`, `MARKDOWN_VAULT_MCP_HTTP_PATH`,
`MARKDOWN_VAULT_MCP_BASE_URL`, auth vars, etc.).

!!! tip "Prefer a guided setup?"
    Use the [Configuration Generator](configuration-generator.md) to answer a few
    questions and copy a ready-made `.env`, Docker, or Claude config.

!!! note "Configuration is validated at startup"
    Numeric variables are validated against the **Type** column below (such as `int ≥ 1`). A non-numeric or out-of-range value makes the server **fail fast** at startup with a `ConfigurationError` naming the offending setting, rather than silently falling back to a default. A typo in an env var surfaces immediately instead of producing surprising behavior later.

<!-- DOMAIN-CONFIG-VARS-START -->
## Core

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | path | (none) | **Yes** | Path to the markdown vault directory. Symbolic links inside the vault are followed on Python 3.13+ (3.11/3.12 do not follow symlinks); cyclic links hang the scan, so symlink-farm layouts must be acyclic |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | bool | `true` | No | Set to `false` to enable write operations |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | path | in-memory | No | Path to the SQLite FTS5 index file; set for persistence across restarts |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | path | disabled | No | Path to the numpy embeddings file; required to enable semantic search |
| `MARKDOWN_VAULT_MCP_STATE_PATH` | path | `{SOURCE_DIR}/.markdown_vault_mcp/state.json` | No | Path to the change-tracking state file |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | csv | (none) | No | Comma-separated frontmatter fields to promote to the tag index for structured filtering. Recorded as a warm-restart key, so setting or changing it cold-rebuilds the index once on next startup. When `SEARCHABLE_FIELDS` is unset, it defaults to this value, so a field indexed for filtering is also keyword/semantically searchable out of the box; a change here then also activates context-enriched embeddings and re-embeds the vault once. Set `SEARCHABLE_FIELDS` explicitly (or to `none`) to decouple. **One-time rebuild on upgrade:** a vault with `INDEXED_FIELDS` already configured cold-rebuilds once on its first startup after upgrading to the release that introduced this tracking ([#927](https://github.com/pvliesdonk/markdown-vault-mcp/issues/927)), and (if embeddings are enabled and `SEARCHABLE_FIELDS` was unset) also re-embeds once as the inherited default takes effect. No action is required; subsequent restarts warm-restart as normal |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | csv | (none) | No | Comma-separated frontmatter fields required on every document; documents missing any are excluded from the index |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | csv | (none) | No | Comma-separated glob patterns to exclude from scanning (such as `.obsidian/**,.trash/**`) |
| `MARKDOWN_VAULT_MCP_TITLE_FIELD` | string | `title` | No | Frontmatter field used as the document title. Resolution order: this field → `title` (when a custom field is set) → first H1 heading → filename stem. Recorded as a warm-restart key, so changing it cold-rebuilds the index once on next startup |
| `MARKDOWN_VAULT_MCP_SEARCHABLE_FIELDS` | csv | defaults to `INDEXED_FIELDS` | No | Comma-separated frontmatter fields whose scalar values are indexed into the FTS `summary` column (chunk-0 row of each document), making them keyword-searchable (including `summary:term` column filters). They are also prefixed to first-chunk embedding text (format v2), independent of `EMBED_CONTEXT`. When unset, defaults to `INDEXED_FIELDS`; set explicitly to a different field list to diverge from that default, or to the sentinel `none` for no searchable fields at all ("filterable but not searchable"). An empty string is treated the same as unset, matching every other list-valued config var. Recorded as a warm-restart key, so setting or changing it cold-rebuilds the index and re-embeds the vault once on next startup |
| `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` | string | `_templates` | No | Relative folder path used by the `create_from_template` prompt to discover/read template files |
| `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` | path | (none) | No | Path to a directory of `.md` prompt files that extend or override built-in prompts. Each declared argument name must be a plain Python identifier (letters, digits, underscore; not a keyword); a prompt with a non-conforming argument name is skipped with a logged warning. |
| `MARKDOWN_VAULT_MCP_CONVENTIONS_FILE` | string | `_conventions.md` | No | Filename of the per-folder [conventions files](tools/index.md#get_conventions) surfaced to clients at write time. Must be a bare `.md` filename without glob characters (`*?[]`). Set to `none` to disable folder conventions. Convention files are excluded from the search index but stay readable; existing notes matching the name are removed from the index on the next boot reconcile. Note: setting `MARKDOWN_VAULT_MCP_INSTRUCTIONS` replaces the default server instructions entirely, including the sentence that points clients at `get_conventions`; mention conventions in your custom instructions if you rely on them. |
| `MARKDOWN_VAULT_MCP_OKF_MODE` | string | `auto` | No | [OKF (Open Knowledge Format)](https://github.com/GoogleCloudPlatform/knowledge-catalog) read semantics. With `auto` (the default), read-side annotations (note type, lifecycle status, staleness, trust tier) switch on in `search`, `read`, `get_context`, and `stats` when the vault declares an `okf_version` field in its root `index.md` frontmatter. Use `off` to disable OKF semantics entirely, or `on` to force them for an undeclared vault. Annotations are read-only either way: this setting never changes write behavior. Note: setting `MARKDOWN_VAULT_MCP_INSTRUCTIONS` replaces the default server instructions entirely, including the OKF guidance sentence. |
| `MARKDOWN_VAULT_MCP_OKF_WRITE` | bool | `false` | No | OKF enforced write layer. When `true` on an OKF-active vault, every `write` and `edit` stamps `generated: {by, at}` provenance and clears any `verified` attestation (a content change invalidates prior review); `rename` does not. The actor is `human:<subject>` when the caller is authenticated, otherwise a tool actor. On a content write it also keeps the affected folder's `log.md` and `index.md` current as secondary writes (failures are logged and skipped, never rolling back the note write). Enabling it also exposes the [`okf_verify`](tools/index.md#okf_verify) tool. Requires `OKF_MODE` to be `auto` or `on`; a `true` value with `OKF_MODE=off` is a configuration error. See the [OKF guide](guides/okf.md#the-enforced-write-layer). |
| `MARKDOWN_VAULT_MCP_OKF_VERIFY` | string | `elicit` | No | How the [`okf_verify`](tools/index.md#okf_verify) tool confirms a human review. This applies only when `OKF_WRITE` is on, which gates the tool. With `elicit` (the default), the tool issues an MCP elicitation asking the human to confirm the review, and writes the `verified` entry only on an affirmative reply. It fails closed (a tool error, nothing written) when the client cannot elicit or the human declines, so a model that holds the human's token cannot self-attest a note as `human-reviewed`. Use `trust-auth` to attribute to the authenticated caller with no confirmation (refuses under auth mode `none`; safe only when the sole caller is a human-driven UI, not an agent), or `off` to hide the tool so `verified` is set only by external tooling. A non-default value with `OKF_WRITE` off is a configuration error. See the [OKF guide](guides/okf.md#recording-a-human-review). |

The file watcher never watches the directories that hold `INDEX_PATH`,
`EMBEDDINGS_PATH`, and `STATE_PATH` (or `.git`), so the writes the server makes
while indexing do not trigger it again. If you place one of these paths inside a
content directory, that whole top-level directory stops being watched, so keep
them at the vault root or outside the vault to keep sibling content watched live.

## Index Build Timeout

### `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S`

Default: `60` (seconds).

Maximum time the MCP-layer `needs_queryable` decorator waits for
the FTS index to become queryable before raising
`IndexUnavailableError(reason="timeout")` to the client. Applied to bucket-3/4 tool and resource calls during
a cold-start background FTS build. Increase for large vaults
where the initial scan takes longer; decrease for tighter feedback
on stuck builds.

### `MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S`

Default: `60` (seconds).

Maximum time an index-querying read tool (`search`, `list_documents`,
`list_folders`, `list_tags`, `stats`, `get_recent`, `get_backlinks`,
`get_outlinks`, `get_broken_links`, `get_similar`, `get_context`,
`get_orphan_notes`, `get_most_linked`, `get_connection_path`) waits for
the IndexWriter to drain when called with `wait_for_pending_writes=true`. On
timeout the tool answers from the current index rather than raising
(best-effort fresh read) and reports `index_stale=true` in the response's
`_meta`. Increase for large vaults where reindex / build_embeddings
jobs take longer; decrease for faster client feedback when the index has
chronic backlog.

## Server Identity

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_SERVER_NAME` | string | `markdown-vault-mcp` | MCP server name shown to clients; useful for multi-instance setups |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS` | string | (auto) | System-level instructions injected into LLM context; defaults to a description that reflects read-only vs read-write state |
| `MARKDOWN_VAULT_MCP_DISABLE_APPS_UI` | bool | `false` | Hide MCP-Apps UI tools (`browse_vault`, `show_context`) from the tool listing for clients that do not render MCP Apps panels (saves a few listing tokens) |
| `MARKDOWN_VAULT_MCP_HTTP_PATH` | path | `/mcp` | HTTP endpoint path for streamable HTTP transport (`serve --transport http`) |
| `MARKDOWN_VAULT_MCP_BASE_URL` | url | (none) | Public base URL of the server (such as `https://mcp.example.com`). Required for OIDC auth, MCP Apps domain computation, and the one-time transfer link tools |
| `MARKDOWN_VAULT_MCP_KV_STORE_URL` | url | `file:///data/state` | Unified key-value backend for HTTP session persistence (the `events` keyspace is namespaced inside the directory). `file:///path` survives restarts; `memory://` for dev (lost on restart). Preferred over `EVENT_STORE_URL`. |
| `MARKDOWN_VAULT_MCP_EVENT_STORE_URL` | url | (unset) | Legacy alias for `KV_STORE_URL`; honoured only when `KV_STORE_URL` is unset, and logs a one-shot deprecation warning. Prefer `KV_STORE_URL`. |
| `MARKDOWN_VAULT_MCP_APP_DOMAIN` | string | (auto) | Override the Claude app domain used for MCP Apps iframe sandboxing. Auto-computed from `BASE_URL` when not set |
| `FASTMCP_LOG_LEVEL` | string | `INFO` | Log level for FastMCP internals (`DEBUG`, `INFO`, `WARNING`, `ERROR`). `-v` CLI flag overrides both app and FastMCP loggers to `DEBUG` |
| `FASTMCP_ENABLE_RICH_LOGGING` | bool | `true` | Rich `key=value` text by default. Set to `false` for one-JSON-object-per-record output, recommended for production / log-aggregator deployments |

## Search Ranking and Snippet Truncation

| Env var | Default | Type | Notes |
|---|---|---|---|
| `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` | `2` | int ≥ 1 | Maximum number of matching sections returned per file (field collapsing). `0` is rejected. Per-call override available on the `search` and `get_similar` tools; `get_context` defaults to `1` for compact dossiers. |
| `MARKDOWN_VAULT_MCP_SNIPPET_WORDS` | `200` | int ≥ 0 | Approximate word budget for `SearchResult.content`. `0` returns the full chunk. Per-call override on the `search` tool. |
| `MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA` | `0.25` | float ≥ 0 | Strength of the per-channel length downweight: `score / (1 + alpha · log(chunk_count))`. `0` disables. Applied only to `search` modes (keyword/semantic/hybrid); `get_similar` and `get_context.similar` skip the downweight because grouping already handles multi-chunk dedup (see [#472](https://github.com/pvliesdonk/markdown-vault-mcp/issues/472)). Operator-only (no per-call override). |
| `MARKDOWN_VAULT_MCP_FOLDER_WEIGHTS` | (none) | `prefix:weight,...` | Folder-prefix score multipliers applied to every search mode (keyword, semantic, hybrid) just before results are grouped per file. A prefix matches a folder exactly or as a parent (`Projects` matches `Projects/2026` but never `ProjectsArchive`); the deepest matching prefix wins; weights must be > 0 (`0.5` demotes, `2` promotes). Query-time only: takes effect immediately, no reindex. `get_similar`/`get_context` are deliberately unaffected. |
| `MARKDOWN_VAULT_MCP_FTS_WEIGHTS` | (none) | `column:weight,...` | Per-column BM25 weights for keyword ranking, persisted into the FTS5 rank configuration at startup. Columns: `path`, `title`, `folder`, `heading`, `content`, `summary`; weights must be ≥ 0; unlisted columns weigh `1.0`. Unset (or all `1.0`) resets to the default `bm25()`. Takes effect on restart, no reindex. |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS` | `400` | int ≥ 1 | Hard cap on chunk word count. The adaptive chunker recursively re-splits at deeper heading levels (H1 → H6); anything still oversize (preambles and no-headings documents included) is further fragmented on paragraph and word boundaries. Match this to the embedding model's context window. The default FastEmbed model `BAAI/bge-small-en-v1.5` has only a 512-token context, so the default `400` words (≈ 600 tokens) **could exceed it**; `MAX_CHUNK_CHARS` (derived as ~1434 chars for this 512-token model) caps each chunk to the model's context, so manual adjustment is not needed. For long-context models such as `nomic-embed-text-v1.5` (8192 tokens natively; Ollama serves it with `n_ctx_train=2048` by default, see `OLLAMA_HOST`) the default `400` is comfortable. Setting a high value (such as `100000`) effectively disables the cap. When `CHUNK_OVERLAP_WORDS` is non-zero, an overlapped fragment can exceed this cap by up to the overlap word count. |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS` | *(bounded default)* | int ≥ 1, or `-1` | Hard cap on chunk **character** count, enforced by the chunker alongside the word cap (whichever budget is hit first triggers a split). Applies to token-dense content (CJK, code, tables). **Unset** uses the safe default `min(1500, round(context_length × 2.8))`: retrieval quality peaks at ~256 to 512 tokens per chunk regardless of the model's context, and 1500 chars keeps the fastembed/ONNX path clear of the out-of-memory regime seen with oversize chunks ([#306](https://github.com/pvliesdonk/markdown-vault-mcp/issues/306)). For `BAAI/bge-small-en-v1.5` (512-token context) this derives to ~1434 chars (unchanged from prior releases). A model with a shorter context (below ~536 tokens) uses its own smaller `round(context_length × 2.8)` value; a longer-context model is capped at the `1500` ceiling; an unknown context (no provider, or an unreachable Ollama instance) falls back to `1500`. Set a **positive integer** to force an exact cap. Set **`-1`** to opt into unbounded context-scaling (`round(context_length × 2.8)` with no ceiling, or `1500` when the context is unknown), which reproduces the pre-#790 context-scaling and **can OOM-kill the host** on the fastembed/ONNX path with a long-context model; use only with Ollama (out-of-process) or a remote provider. Like `MAX_CHUNK_WORDS`, this changes the chunk *index*, so a reindex is required for a new value to take effect. When `CHUNK_OVERLAP_WORDS` is non-zero, an overlapped fragment can exceed this cap by up to the overlap word count. |
| `MARKDOWN_VAULT_MCP_CHUNK_OVERLAP_WORDS` | `40` | int ≥ 0 | Words of overlap copied from the previous fragment onto each budget-split fragment of the **same heading section**. Improves retrieval recall at arbitrary (non-heading) split points; `0` disables. Overlap applies only where heading-based splitting has been exhausted and the chunker falls back to paragraph, line, or word splitting, never across a heading boundary. It is excluded from the warm-restart key, so changing it (or the default on upgrade) applies to new builds and any note re-indexed after an edit, without forcing a rebuild. Run `reindex` to apply a new value to an existing vault. A non-zero value can push an overlapped fragment past `MAX_CHUNK_WORDS` or `MAX_CHUNK_CHARS` by up to this many words; the excess is small and does not risk an out-of-memory condition. |

The first three knobs adjust *ranking and rendering* and take effect immediately. `MAX_CHUNK_WORDS` and `MAX_CHUNK_CHARS` change the chunk *index*; a reindex is required for a new value to take effect. Because the char cap is **derived from the embedding model**, changing the embedding model also changes the effective chunk boundaries: the FTS index is re-chunked, not just the embeddings. Changing the embedding model (or the explicit `MAX_CHUNK_CHARS` override) rejects the warm-restart short-circuit and triggers an automatic cold rebuild of the index on the next startup. No manual `reindex` is needed. **One-time rebuild on upgrade:** an existing *embedding-enabled* vault upgrading from a release before this one will also cold-rebuild once on next startup, because builds now record chunking provenance (embedding model + override) that older builds did not track. No action is required; subsequent restarts warm-restart as normal. FTS-only vaults (no embedding provider) are unaffected. **Upgrading note:** `search` now returns snippets (≤ ~200 words) by default. Set `snippet_words=0` per call or `MARKDOWN_VAULT_MCP_SNIPPET_WORDS=0` globally to restore full-chunk output. Existing vaults are also re-chunked on the next `reindex` when `MAX_CHUNK_WORDS` is set. **Bounded-cap upgrade ([#790](https://github.com/pvliesdonk/markdown-vault-mcp/issues/790)):** the smaller default (`min(1500, round(context_length × 2.8))`, reduced from the old unbounded `round(context_length × 2.8)`) applies to new builds and whenever the embedding model or an explicit `MAX_CHUNK_CHARS` changes. It does not auto-apply on upgrade. The warm-restart key is the model name together with the explicit override rather than the derived cap, so an existing vault on a long-context model with no explicit `MAX_CHUNK_CHARS` keeps its current coarser chunk boundaries. That old index stays usable; it is only larger-chunked. Run `reindex` when you want the smaller default to take effect, noting that a full re-embed can take many minutes on a large vault. bge-small vaults are unaffected. To keep unbounded scaling, set `MAX_CHUNK_CHARS=-1`.

## Search and Embeddings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | string | auto-detect | Embedding provider: `openai`, `ollama`, or `fastembed`. **Breaking change** from `EMBEDDING_PROVIDER` in older versions |
| `MARKDOWN_VAULT_MCP_EMBED_CONTEXT` | bool | `false` | Enrich each chunk's embedding input with the note title, the chunk heading, and (on the first chunk) the `SEARCHABLE_FIELDS` values, improving semantic recall for short or context-poor chunks. The raw note content on disk and in search snippets is unchanged. The active format is recorded in the vector sidecar, so flipping this (or changing `SEARCHABLE_FIELDS`) re-embeds the whole vault once on next startup |
| `MARKDOWN_VAULT_MCP_EMBED_TIMEOUT_S` | float | `30.0` | Per-request wall-clock budget in seconds for a single embedding HTTP call (OpenAI/Ollama). The local FastEmbed backend runs in-process with no network call and ignores this. CPU-only or large-model workloads may need 60-120 s; raise this if batches time out. |
| `MARKDOWN_VAULT_MCP_EMBEDDING_BATCH_SIZE` | int | `4` | Number of chunks sent per embedding request. Smaller batches shorten each request (useful under a tight timeout on slow models) at the cost of more round-trips. |
| `OLLAMA_HOST` | url | `http://localhost:11434` | Ollama server URL. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
| `OPENAI_API_KEY` | string | (none) | OpenAI API key for the OpenAI embedding provider. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
| `MARKDOWN_VAULT_MCP_OPENAI_BASE_URL` / `OPENAI_BASE_URL` | url | `https://api.openai.com/v1` | OpenAI-compatible API base URL for embeddings |
| `MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL` / `OPENAI_EMBEDDING_MODEL` | string | `text-embedding-3-small` | OpenAI-compatible embedding model name |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | string | `nomic-embed-text` | Ollama embedding model name |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | bool | `false` | Force Ollama to use CPU only |
| `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL` | string | `BAAI/bge-small-en-v1.5` | FastEmbed model name |
| `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR` | path | FastEmbed default | FastEmbed model cache directory (in Docker, stored under `/data/state/fastembed`) |

!!! note "Embedding provider auto-detection"
    When `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` is not set, the server tries providers in this order:

    1. **OpenAI** — if `OPENAI_API_KEY` is set
    2. **Ollama** — if `OLLAMA_HOST` is reachable
    3. **FastEmbed** — if the `fastembed` package is installed

    Both API providers speak the OpenAI-compatible embeddings protocol through the official `openai` SDK: the `ollama` provider is a preset that targets `{OLLAMA_HOST}/v1` with no key required. The `OLLAMA_*` settings and their behavior are unchanged; `OLLAMA_CPU_ONLY` uses Ollama's native API, which is the only way to request CPU-only inference.

    **Explicit vs. auto-detect failure handling:** when you set `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` to a specific backend and it cannot be constructed at startup — a missing dependency, missing/empty credentials, or an unrecognised value — the server **fails fast** with a `ConfigurationError` rather than silently falling back to keyword-only search. (An unreachable Ollama/OpenAI *service* does not prevent startup — the provider still loads; the failure surfaces later as an embedding error during index build.) When the variable is *unset* (auto-detect) and no backend is available, the server logs a warning and continues with semantic search disabled. Set the variable explicitly if you want a missing provider to be a hard startup error.

## Summarization

Powers the optional [`summarize`](tools/index.md#summarize) tool. The backend speaks the OpenAI-compatible chat-completions API, so one endpoint/key/model triple covers OpenAI, a local Ollama, the Anthropic compatibility endpoint, vLLM, and any other compatible server. The tool is only registered when a backend is configured: an API key, or an explicit base URL for local endpoints that need no key. Requires the `openai` SDK: `pip install 'markdown-vault-mcp[summarize]'`.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_SUMMARIZE_PROVIDER` | string | auto-detect | Summarization backend. Currently `openai`. Leave unset to auto-detect from available credentials |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY` | string | (none) | API key for the summarize endpoint. Presence enables the `summarize` tool. Falls back to the bare `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | string | (none) | Fallback API key (shared with embeddings). Presence enables the `summarize` tool. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL` | string | `https://api.openai.com/v1` | OpenAI-compatible endpoint base URL. Setting it enables the tool even without a key (local endpoints) |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL` | string | `gpt-5-mini` | Chat model id used for summaries |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_TOKENS` | int | `8192` | Upper bound on generated tokens per call. On reasoning models (such as the default `gpt-5-mini`) this budget also covers internal reasoning tokens, so it must be comfortably larger than the summary itself; the server requests low reasoning effort where supported. Raise this if summarize fails with an exhausted-budget error |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_NOTES` | int | `50` | Cap on notes summarised per tool call; this is the coverage and cost lever. It is also the ceiling for the tool's per-call `max_notes` parameter. Notes beyond it are omitted and counted in the response's `notes_omitted` |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_INPUT_CHARS` | int | `200000` | Character budget of a single model request. A larger input is split into batches, each batch summarized, and the partial summaries combined (map-reduce), so this bounds request size, not coverage. Smaller values mean more model calls |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_TIMEOUT` | float (s) | `120` | Per-request wall-clock budget for a single backend call. On timeout the tool fails with a clear message that says how to fit under the limit (narrow the request, or raise this) instead of running to the ~600 s default of the OpenAI SDK while the client abandons the request. Set it **below** your MCP client's request timeout |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_INLINE_TIMEOUT` | float (s) | `30` | Inline deadline: a `summarize` call that runs longer is promoted to a background job (`{"status": "in_progress", "job_id": …}`) and retrieved later via `get_summary`, so the tool always responds promptly. Keep it comfortably below the client's request timeout; must be `<=` `SUMMARIZE_TIMEOUT` |

The bare `OPENAI_BASE_URL` is honored as a *value* fallback when a key is set (such as an organization-wide proxy), but it never enables the tool by itself: setting it purely for embeddings does not switch summarization on.

!!! example "Provider recipes"
    - **OpenAI**: set `OPENAI_API_KEY` (or the prefixed `SUMMARIZE_OPENAI_API_KEY`). Done; the default model is `gpt-5-mini`.
    - **Ollama (local, no key)**: `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL=http://localhost:11434/v1` and `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL=llama3.2` (any installed model). No API key needed.
    - **Anthropic (Claude via the OpenAI compatibility endpoint)**: `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL=https://api.anthropic.com/v1`, the Anthropic key in `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY`, and `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL=claude-haiku-4-5`.

!!! warning "Note content leaves your environment"
    The `summarize` tool sends the referenced notes to the external model provider. Do not enable it for vaults whose content must not leave your environment.

!!! note "Explicit vs. auto-detect failure handling"
    Same posture as embeddings: if you set `MARKDOWN_VAULT_MCP_SUMMARIZE_PROVIDER` explicitly and the backend cannot be loaded (missing SDK, empty key, unrecognised value), startup **fails fast** with a `ConfigurationError`. When the provider is *unset* (auto-detect) and the backend cannot be loaded, the server logs a warning and the `summarize` tool stays hidden.

## Git Integration

Git integration has three modes:

- **Managed** (`GIT_REPO_URL` + `GIT_TOKEN`): server manages clone, pull, commit, and push.
- **Unmanaged / commit-only** (no `GIT_REPO_URL`, repo already exists): server commits writes locally only; you manage pull/push externally.
- **No-git** (default): plain directory; no git operations.

Backward compatibility: `GIT_TOKEN` without `GIT_REPO_URL` still works (legacy behavior) and logs a deprecation warning.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | string | (none) | HTTPS repo URL for managed mode. On startup, empty `SOURCE_DIR` is cloned from this URL |
| `MARKDOWN_VAULT_MCP_GIT_USERNAME` | string | `x-access-token` | Username for HTTPS auth prompts (`x-access-token` GitHub, `oauth2` GitLab, account name Bitbucket) |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | string | (none) | Token/password for HTTPS auth via `GIT_ASKPASS` |
| `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | int | `600` | Seconds between `git fetch` + ff-only update attempts; `0` disables periodic pull |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | float | `30` | Seconds of write-idle time before pushing; `0` = push only on shutdown |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | string | `markdown-vault-mcp` | Git committer name for auto-commits; **set this in Docker** where `git config user.name` is empty |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | string | `noreply@markdown-vault-mcp` | Git committer email for auto-commits |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` | string | (none) | OIDC claim key to use as the commit author name when a token is present (such as `name`); falls back to `GIT_COMMIT_NAME` when absent |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM` | string | (none) | OIDC claim key to use as the commit author e-mail when a token is present (such as `email`); falls back to `GIT_COMMIT_EMAIL` when absent |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | bool | `true` | Run `git lfs pull` on startup to resolve LFS pointers; set to `false` if git-lfs is not installed |

!!! tip "Push delay"
    The push delay batches rapid writes into a single push. Set to `0` to disable automatic pushing; the server will then push only on shutdown via `close()`.

!!! warning "HTTPS remotes only with token auth"
    When `GIT_TOKEN` is used, SSH remotes are rejected. Use an HTTPS URL for `origin` or `GIT_REPO_URL`.

### GitHub Webhook (push-triggered pull)

In multi-author deployments, the periodic pull loop introduces up to `GIT_PULL_INTERVAL_S` seconds of staleness. Setting a webhook secret enables a `POST /github-webhook` endpoint that triggers an immediate `force_pull` + reindex when GitHub delivers a `push` event, reducing the staleness window to webhook delivery latency (~2 s).

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET` | string | (none) | Shared secret for GitHub HMAC-SHA256 signature verification; when unset the endpoint is not mounted |

The endpoint is only available on HTTP/SSE transports. To set up:

1. Set `MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET` to a random secret (run `openssl rand -hex 32` to generate one).
2. In your GitHub repository, add a webhook pointing at `https://<your-host>/github-webhook` with content type `application/json` and the same secret.
3. Select the `push` event (other events are acknowledged with 200 and ignored).

The periodic pull loop (`GIT_PULL_INTERVAL_S`) remains active as a belt-and-suspenders fallback for missed webhook deliveries.

## File Watcher

Detects external file changes (edits by a local editor, sync daemon, or `cp -r`) without requiring git integration. Enabled by default for vaults that are not managed by git pull; automatically disabled when the periodic git pull loop (`GIT_PULL_INTERVAL_S > 0`) or the GitHub webhook (`GITHUB_WEBHOOK_SECRET`) is active, since those mechanisms already trigger reindex on their own cadence.

Requires the optional `watchdog` dependency: `pip install 'markdown-vault-mcp[file-watcher]'`.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_FILE_WATCHER` | bool | `true` | Enable filesystem-event watcher; auto-disabled when git pull or webhook is active |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S` | float | `2.0` | Seconds of quiet after the last event before triggering reindex; tune down for faster response on small vaults |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER_ROOT_FLOOR` | bool | `true` | Keep the non-recursive watch on the vault root so root-level `*.md` changes trigger a reindex; set `false` to drop it (root-level files then rely on scans) |

The watcher schedules one recursive watch per non-excluded immediate child directory of the source directory rather than a single recursive watch on the root, so excluded directories (`node_modules`, `.venv`, and similar) are never registered and content under a deliberately watched dot-directory (a vault rooted at `$HOME` with content under `.notes/`) delivers its own edits. New top-level directories created after start are not watched until the server restarts; the startup log line lists the scheduled root count and names.

!!! note "macOS access prompts"
    On macOS the root floor watch still opens an OS-level recursive `FSEvents` stream over the whole tree even though it delivers only direct entries, so a vault rooted at `$HOME` can trigger repeated "would like to access data from other apps" prompts. Set `MARKDOWN_VAULT_MCP_FILE_WATCHER_ROOT_FLOOR=false` to register zero source-directory-rooted `FSEvents` streams, accepting that root-level files are then only picked up by scans.

!!! note "Mutual exclusion with git"
    When `GIT_PULL_INTERVAL_S > 0` or `GITHUB_WEBHOOK_SECRET` is set, the file watcher is automatically disabled even if `FILE_WATCHER=true`. This prevents mid-checkout partial scans where git is modifying the working tree.

## Attachments

Non-markdown file support for PDFs, images, spreadsheets, and more.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS` | csv | (built-in list) | Comma-separated allowed extensions without dot (such as `pdf,png,jpg`); use `*` to allow all non-`.md` files |
| `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` | float | `1.0` | Maximum attachment size in MB returned by `read()` / accepted by `write()`; `0` disables the limit |
| `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` | int | `262144` | Maximum bytes returned by full-document `read()` for `.md` files; raises `ValueError` if exceeded. Use `read(path, section=...)` for partial reads. `0` disables the limit. |

**Default allowed extensions:** `pdf`, `docx`, `xlsx`, `pptx`, `odt`, `ods`, `odp`, `png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`, `bmp`, `tiff`, `zip`, `tar`, `gz`, `mp3`, `mp4`, `wav`, `ogg`, `txt`, `csv`, `tsv`, `json`, `yaml`, `toml`, `xml`, `html`, `css`, `js`, `ts`

!!! warning "Hidden directories"
    Attachments inside hidden directories (`.git/`, `.obsidian/`, `.markdown_vault_mcp/`, etc.) are never listed, regardless of extension settings. `MARKDOWN_VAULT_MCP_EXCLUDE` patterns are also applied to attachments.

!!! note "Upgrading"
    `MAX_ATTACHMENT_SIZE_MB` default lowered from **10 MB** to **1 MB**. Most LLM contexts can't survive a 10 MB base64-encoded attachment; the old default was a silent context-blow-up. If non-LLM consumers (scripts, CI) need the old behaviour, set `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB=10` explicitly.

    `MAX_NOTE_READ_BYTES` is a **new** env var (default 256 KB). Whole-document `.md` reads above this raise `ValueError`. Partial reads via `read(path, section=heading)` bypass the cap.

## Bearer Token Authentication

Simple static token auth for HTTP deployments. Set a single env var; clients must send `Authorization: Bearer <token>`.

| Variable | Type | Required | Description |
|----------|------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BEARER_TOKEN` | string | Yes | Static bearer token; any non-empty string enables auth |

!!! tip "Multi-auth"
    If both `BEARER_TOKEN` and all OIDC variables are set, the server accepts **either** credential. Useful when different clients use different auth flows (Claude web via OIDC, Claude Code via bearer token).

## One-Time Transfer Links

Short-lived capability URLs for transferring vault files out-of-band (browser download or third-party service upload) without inflating the LLM context window. The `GET /transfer/{token}` route is mounted outside the auth middleware; the unguessable token is the authorization.

The capability-link machinery (the token store, the `/transfer/{token}` route, and the two tools) is provided by `fastmcp-pvl-core`. The token store is backed by the configured state backend (`MARKDOWN_VAULT_MCP_KV_STORE_URL`, on-disk by default), so live links survive a server restart.

`MARKDOWN_VAULT_MCP_BASE_URL` is required for the transfer tools; it is used to construct the capability URL returned to the LLM. When `BASE_URL` is not set, the transfer tools and the `/transfer/{token}` route are not registered.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S` | float | `3600` | Default token lifetime in seconds when the caller omits `ttl_seconds`. Clamped to `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S` |
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S` | float | `86400` | Maximum permitted TTL. A requested `ttl_seconds` above this value is clamped to the ceiling |
| `MARKDOWN_VAULT_MCP_TRANSFER_GRACE_TTL_S` | float | `60` | Grace window in seconds after a successful transfer. The token's remaining lifetime shrinks to this, so a stalled or retried transfer can reclaim the link rather than be stranded by a spent one |
| `MARKDOWN_VAULT_MCP_TRANSFER_LEASE_S` | float | `60` | Reclaim window in seconds for an in-flight reservation. A crashed handler's token becomes claimable again once this lease lapses |
| `MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES` | int | `104857600` (100 MiB) | Per-upload size cap for the upload route. Requests whose body exceeds this limit are rejected with HTTP 413 |

!!! note "HTTP/SSE transport only"
    Transfer tools and the `/transfer/{token}` route are registered only when the server is running HTTP or SSE transport. They are not available on stdio transport.

## OIDC Authentication

Optional token-based authentication for HTTP deployments. OIDC activates when all four required variables are set. See [OIDC deployment](deployment/oidc.md) for setup details.

| Variable | Type | Required | Description |
|----------|------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | url | Yes | Public base URL (see [Server Identity](#server-identity) above); required for OIDC |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | url | Yes | OIDC discovery endpoint (such as `https://auth.example.com/.well-known/openid-configuration`) |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | string | Yes | OIDC client ID registered with your provider |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | string | Yes | OIDC client secret |
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | string | No | JWT signing key; **required on Linux/Docker** (the default is ephemeral and invalidates tokens on restart). Generate with `openssl rand -hex 32` |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | string | No | Expected JWT audience claim; leave unset if your provider does not set one |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | csv | `openid` | Comma-separated required scopes |
| `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN` | bool | `false` | Set `true` to verify the upstream access token as JWT instead of the id token. Only needed when your provider issues JWT access tokens and you require audience-claim validation on that token |

## Boolean Parsing

Boolean environment variables accept `true`, `1`, or `yes` (case-insensitive) as truthy. Everything else is treated as `false`.

## Example .env Files

| File | Description |
|------|-------------|
| [`examples/obsidian-readonly.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/obsidian-readonly.env) | Obsidian vault, read-only, Ollama embeddings |
| [`examples/obsidian-readwrite.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/obsidian-readwrite.env) | Obsidian vault, read-write with git auto-commit |
| [`examples/obsidian-oidc.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/obsidian-oidc.env) | Obsidian vault, read-only, OIDC authentication (Authelia) |
| [`examples/ifcraftcorpus.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/ifcraftcorpus.env) | Strict frontmatter enforcement, read-only corpus |
<!-- DOMAIN-CONFIG-VARS-END -->
