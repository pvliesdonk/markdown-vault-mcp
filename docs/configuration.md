# Configuration

All configuration is via environment variables. Most use the `MARKDOWN_VAULT_MCP_` prefix. `OLLAMA_HOST` and `OPENAI_API_KEY` are bare ecosystem-standard names; all other variables use the `MARKDOWN_VAULT_MCP_` prefix.

!!! note "Configuration is validated at startup"
    Numeric variables are validated against the **Type** column below (e.g. `int ≥ 1`). A non-numeric or out-of-range value makes the server **fail fast** at startup with a `ConfigurationError` naming the offending setting, rather than silently falling back to a default — so a typo in an env var surfaces immediately instead of producing surprising behavior later.

## Core

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | path | — | **Yes** | Path to the markdown vault directory. Symbolic links inside the vault are followed on Python 3.13+ (3.11/3.12 do not follow symlinks); cyclic links hang the scan, so symlink-farm layouts must be acyclic |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | bool | `true` | No | Set to `false` to enable write operations |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | path | in-memory | No | Path to the SQLite FTS5 index file; set for persistence across restarts |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | path | disabled | No | Path to the numpy embeddings file; required to enable semantic search |
| `MARKDOWN_VAULT_MCP_STATE_PATH` | path | `{SOURCE_DIR}/.markdown_vault_mcp/state.json` | No | Path to the change-tracking state file |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | csv | — | No | Comma-separated frontmatter fields to promote to the tag index for structured filtering |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | csv | — | No | Comma-separated frontmatter fields required on every document; documents missing any are excluded from the index |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | csv | — | No | Comma-separated glob patterns to exclude from scanning (e.g. `.obsidian/**,.trash/**`) |
| `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` | string | `_templates` | No | Relative folder path used by the `create_from_template` prompt to discover/read template files |
| `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` | path | — | No | Path to a directory of `.md` prompt files that extend or override built-in prompts |

## Index Build Timeout

### `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S`

Default: `60` (seconds).

Maximum time the MCP-layer `needs_queryable` decorator waits for
the FTS index to become queryable before raising
`IndexUnavailableError(reason="timeout")` to the client. Applied to bucket-3/4 tool and resource calls during
a cold-start background FTS build. Increase for very large vaults
where the initial scan takes longer; decrease for tighter feedback
on stuck builds.

### `MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S`

Default: `60` (seconds).

Maximum time an index-querying read tool (`search`, `list_documents`,
`list_folders`, `list_tags`, `stats`, `get_recent`, `get_backlinks`,
`get_outlinks`, `get_broken_links`, `get_similar`, `get_context`,
`get_orphan_notes`, `get_most_linked`, `get_connection_path`) waits for
the IndexWriter to drain when called with `wait_for_pending_writes=true`. On
timeout the tool answers from the current index rather than raising —
best-effort fresh read — and reports `index_stale=true` in the response's
`_meta`. Increase for very large vaults where reindex / build_embeddings
jobs take longer; decrease for faster client feedback when the index has
chronic backlog.

<!-- DOMAIN-CONFIG-VARS-START -->
## Server Identity

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_SERVER_NAME` | string | `markdown-vault-mcp` | MCP server name shown to clients; useful for multi-instance setups |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS` | string | (auto) | System-level instructions injected into LLM context; defaults to a description that reflects read-only vs read-write state |
| `MARKDOWN_VAULT_MCP_HTTP_PATH` | path | `/mcp` | HTTP endpoint path for streamable HTTP transport (`serve --transport http`) |
| `MARKDOWN_VAULT_MCP_BASE_URL` | url | — | Public base URL of the server (e.g. `https://mcp.example.com`). Required for OIDC auth, MCP Apps domain computation, and the one-time transfer link tools |
| `MARKDOWN_VAULT_MCP_KV_STORE_URL` | url | `file:///data/state` | Unified key-value backend for HTTP session persistence (the `events` keyspace is namespaced inside the directory). `file:///path` survives restarts; `memory://` for dev (lost on restart). Preferred over `EVENT_STORE_URL`. |
| `MARKDOWN_VAULT_MCP_EVENT_STORE_URL` | url | (unset) | Legacy alias for `KV_STORE_URL`; honoured only when `KV_STORE_URL` is unset, and logs a one-shot deprecation warning. Prefer `KV_STORE_URL`. |
| `MARKDOWN_VAULT_MCP_APP_DOMAIN` | string | (auto) | Override the Claude app domain used for MCP Apps iframe sandboxing. Auto-computed from `BASE_URL` when not set |
| `FASTMCP_LOG_LEVEL` | string | `INFO` | Log level for FastMCP internals (`DEBUG`, `INFO`, `WARNING`, `ERROR`). `-v` CLI flag overrides both app and FastMCP loggers to `DEBUG` |
| `FASTMCP_ENABLE_RICH_LOGGING` | bool | `true` | Rich `key=value` text by default. Set to `false` for one-JSON-object-per-record output — recommended for production / log-aggregator deployments |

## Search Ranking and Snippet Truncation

| Env var | Default | Type | Notes |
|---|---|---|---|
| `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` | `2` | int ≥ 1 | Maximum number of matching sections returned per file (field collapsing). `0` is rejected. Per-call override available on the `search` and `get_similar` tools; `get_context` defaults to `1` for compact dossiers. |
| `MARKDOWN_VAULT_MCP_SNIPPET_WORDS` | `200` | int ≥ 0 | Approximate word budget for `SearchResult.content`. `0` returns the full chunk. Per-call override on the `search` tool. |
| `MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA` | `0.25` | float ≥ 0 | Strength of the per-channel length downweight: `score / (1 + alpha · log(chunk_count))`. `0` disables. Applied only to `search` modes (keyword/semantic/hybrid); `get_similar` and `get_context.similar` skip the downweight because grouping already handles multi-chunk dedup (see [#472](https://github.com/pvliesdonk/markdown-vault-mcp/issues/472)). Operator-only (no per-call override). |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS` | `400` | int ≥ 1 | Hard cap on chunk word count. The adaptive chunker first recursively re-splits at deeper heading levels (H1 → H6); anything still oversize, plus preambles and no-headings documents, is then fragmented on paragraph and word boundaries so every emitted chunk respects the budget. Match this to the embedding model's context window. The default FastEmbed model `BAAI/bge-small-en-v1.5` has only a 512-token context, so the default `400` words (≈ 600 tokens) **could exceed it** — but `MAX_CHUNK_CHARS` (derived as ~1434 chars for this 512-token model) now bounds every chunk to the model's context automatically, so lowering this manually is usually unnecessary. For long-context models such as `nomic-embed-text-v1.5` (8192 tokens natively; Ollama serves it with `n_ctx_train=2048` by default, see `OLLAMA_HOST`) the default `400` is comfortable. Setting very large (e.g. `100000`) effectively disables the cap. |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS` | *(derived)* | int ≥ 1 | Hard cap on chunk **character** count, enforced by the same chunker alongside the word cap (whichever budget is hit first triggers a split). Bounds token-dense content — CJK, code, tables — that fits the word cap yet exceeds the embedding model's token context. When unset, the cap is **derived from the embedding model's context length** as `round(context_length × 2.8)` (e.g. an 8192-token model → 22938 chars; the default `BAAI/bge-small-en-v1.5` model, 512-token context → ~1434 chars); if the model's context length is unknown (no provider, or an unreachable Ollama instance) a conservative fixed fallback of `6000` chars is used. Set this to override the derived value. Like `MAX_CHUNK_WORDS`, it changes the chunk *index*, so a reindex is required for a new value to take effect. |

The first three knobs adjust *ranking and rendering* — they take effect immediately. `MAX_CHUNK_WORDS` and `MAX_CHUNK_CHARS` change the chunk *index*; a reindex is required for a new value to take effect. Because the char cap is **derived from the embedding model**, changing the embedding model also changes the effective chunk boundaries — the FTS index is re-chunked, not just the embeddings. Changing the embedding model (or the explicit `MAX_CHUNK_CHARS` override) rejects the warm-restart short-circuit and triggers an automatic cold rebuild of the index on the next startup — no manual `reindex` is needed. **One-time rebuild on upgrade:** an existing *embedding-enabled* vault upgrading from a release before this one will also cold-rebuild once on next startup, because builds now record chunking provenance (embedding model + override) that older builds did not track. No action is required; subsequent restarts warm-restart as normal. FTS-only vaults (no embedding provider) are unaffected. **Upgrading note:** `search` now returns snippets (≤ ~200 words) by default — set `snippet_words=0` per call or `MARKDOWN_VAULT_MCP_SNIPPET_WORDS=0` globally to restore full-chunk output. Existing vaults are also re-chunked on the next `reindex` when `MAX_CHUNK_WORDS` is set.

## Search and Embeddings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | string | auto-detect | Embedding provider: `openai`, `ollama`, or `fastembed`. **Breaking change** from `EMBEDDING_PROVIDER` in older versions |
| `OLLAMA_HOST` | url | `http://localhost:11434` | Ollama server URL. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
| `OPENAI_API_KEY` | string | — | OpenAI API key for the OpenAI embedding provider. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
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

## Git Integration

Git integration has three modes:

- **Managed** (`GIT_REPO_URL` + `GIT_TOKEN`): server manages clone, pull, commit, and push.
- **Unmanaged / commit-only** (no `GIT_REPO_URL`, repo already exists): server commits writes locally only; you manage pull/push externally.
- **No-git** (default): plain directory; no git operations.

Backward compatibility: `GIT_TOKEN` without `GIT_REPO_URL` still works (legacy behavior) and logs a deprecation warning.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | string | — | HTTPS repo URL for managed mode. On startup, empty `SOURCE_DIR` is cloned from this URL |
| `MARKDOWN_VAULT_MCP_GIT_USERNAME` | string | `x-access-token` | Username for HTTPS auth prompts (`x-access-token` GitHub, `oauth2` GitLab, account name Bitbucket) |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | string | — | Token/password for HTTPS auth via `GIT_ASKPASS` |
| `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | int | `600` | Seconds between `git fetch` + ff-only update attempts; `0` disables periodic pull |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | float | `30` | Seconds of write-idle time before pushing; `0` = push only on shutdown |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | string | `markdown-vault-mcp` | Git committer name for auto-commits; **set this in Docker** where `git config user.name` is empty |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | string | `noreply@markdown-vault-mcp` | Git committer email for auto-commits |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` | string | — | OIDC claim key to use as the commit author name when a token is present (e.g. `name`); falls back to `GIT_COMMIT_NAME` when absent |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM` | string | — | OIDC claim key to use as the commit author e-mail when a token is present (e.g. `email`); falls back to `GIT_COMMIT_EMAIL` when absent |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | bool | `true` | Run `git lfs pull` on startup to resolve LFS pointers; set to `false` if git-lfs is not installed |

!!! tip "Push delay"
    The push delay batches rapid writes into a single push. Set to `0` to disable automatic pushing — the server will push only on shutdown via `close()`.

!!! warning "HTTPS remotes only with token auth"
    When `GIT_TOKEN` is used, SSH remotes are rejected. Use an HTTPS URL for `origin` or `GIT_REPO_URL`.

### GitHub Webhook (push-triggered pull)

In multi-author deployments, the periodic pull loop introduces up to `GIT_PULL_INTERVAL_S` seconds of staleness. Setting a webhook secret enables a `POST /github-webhook` endpoint that triggers an immediate `force_pull` + reindex when GitHub delivers a `push` event, reducing the staleness window to webhook delivery latency (~2 s).

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET` | string | — | Shared secret for GitHub HMAC-SHA256 signature verification; when unset the endpoint is not mounted |

The endpoint is only available on HTTP/SSE transports. To set up:

1. Set `MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET` to a random secret (e.g. `openssl rand -hex 32`).
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

!!! note "Mutual exclusion with git"
    When `GIT_PULL_INTERVAL_S > 0` or `GITHUB_WEBHOOK_SECRET` is set, the file watcher is automatically disabled even if `FILE_WATCHER=true`. This prevents mid-checkout partial scans where git is modifying the working tree.

## Attachments

Non-markdown file support for PDFs, images, spreadsheets, and more.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS` | csv | (built-in list) | Comma-separated allowed extensions without dot (e.g. `pdf,png,jpg`); use `*` to allow all non-`.md` files |
| `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` | float | `1.0` | Maximum attachment size in MB returned by `read()` / accepted by `write()`; `0` disables the limit |
| `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` | int | `262144` | Maximum bytes returned by full-document `read()` for `.md` files; raises `ValueError` if exceeded. Use `read(path, section=...)` for partial reads. `0` disables the limit. |

**Default allowed extensions:** `pdf`, `docx`, `xlsx`, `pptx`, `odt`, `ods`, `odp`, `png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`, `bmp`, `tiff`, `zip`, `tar`, `gz`, `mp3`, `mp4`, `wav`, `ogg`, `txt`, `csv`, `tsv`, `json`, `yaml`, `toml`, `xml`, `html`, `css`, `js`, `ts`

!!! warning "Hidden directories"
    Attachments inside hidden directories (`.git/`, `.obsidian/`, `.markdown_vault_mcp/`, etc.) are never listed, regardless of extension settings. `MARKDOWN_VAULT_MCP_EXCLUDE` patterns are also applied to attachments.

!!! note "Upgrading"
    `MAX_ATTACHMENT_SIZE_MB` default lowered from **10 MB** to **1 MB** — most LLM contexts can't survive a 10 MB base64-encoded attachment; the old default was a silent context-blow-up. If non-LLM consumers (scripts, CI) need the old behaviour, set `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB=10` explicitly.

    `MAX_NOTE_READ_BYTES` is a **new** env var (default 256 KB). Whole-document `.md` reads above this raise `ValueError`. Partial reads via `read(path, section=heading)` bypass the cap.

## Bearer Token Authentication

Simple static token auth for HTTP deployments. Set a single env var — clients must send `Authorization: Bearer <token>`.

| Variable | Type | Required | Description |
|----------|------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BEARER_TOKEN` | string | Yes | Static bearer token; any non-empty string enables auth |

!!! tip "Multi-auth"
    If both `BEARER_TOKEN` and all OIDC variables are set, the server accepts **either** credential. Useful when different clients use different auth flows (e.g. Claude web via OIDC + Claude Code via bearer token).

## One-Time Transfer Links

Short-lived capability URLs for transferring vault files out-of-band (browser download or third-party service upload) without inflating the LLM context window. The `GET /transfer/{token}` route is mounted outside the auth middleware — the unguessable token is the authorization.

`MARKDOWN_VAULT_MCP_BASE_URL` is required for the transfer tools to function; it is used to construct the capability URL returned to the LLM. If `BASE_URL` is not set, calling `create_download_link` or `create_upload_link` raises an error.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S` | int | `3600` | Default token lifetime in seconds when the caller omits `ttl_seconds`. Clamped to `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S` |
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S` | int | `86400` | Maximum permitted TTL. Any `ttl_seconds` above this value is silently clamped to the ceiling |
| `MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES` | int | `104857600` (100 MiB) | Per-upload size cap for the upload route. Requests whose body exceeds this limit are rejected with HTTP 413 |

!!! note "HTTP/SSE transport only"
    Transfer tools and the `/transfer/{token}` route are registered only when the server is running HTTP or SSE transport. They are not available on stdio transport.

## OIDC Authentication

Optional token-based authentication for HTTP deployments. OIDC activates when all four required variables are set. See [OIDC deployment](deployment/oidc.md) for setup details.

| Variable | Type | Required | Description |
|----------|------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | url | Yes | Public base URL (see [Server Identity](#server-identity) above); required for OIDC |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | url | Yes | OIDC discovery endpoint (e.g. `https://auth.example.com/.well-known/openid-configuration`) |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | string | Yes | OIDC client ID registered with your provider |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | string | Yes | OIDC client secret |
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | string | No | JWT signing key; **required on Linux/Docker** — the default is ephemeral and invalidates tokens on restart. Generate with `openssl rand -hex 32` |
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
