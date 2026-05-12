# Configuration

All configuration is via environment variables. Most use the `MARKDOWN_VAULT_MCP_` prefix. `OLLAMA_HOST` and `OPENAI_API_KEY` are bare ecosystem-standard names; all other variables use the `MARKDOWN_VAULT_MCP_` prefix.

## Core

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | path | — | **Yes** | Path to the markdown vault directory |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | bool | `true` | No | Set to `false` to enable write operations |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | path | in-memory | No | Path to the SQLite FTS5 index file; set for persistence across restarts |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | path | disabled | No | Path to the numpy embeddings file; required to enable semantic search |
| `MARKDOWN_VAULT_MCP_STATE_PATH` | path | `{SOURCE_DIR}/.markdown_vault_mcp/state.json` | No | Path to the change-tracking state file |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | csv | — | No | Comma-separated frontmatter fields to promote to the tag index for structured filtering |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | csv | — | No | Comma-separated frontmatter fields required on every document; documents missing any are excluded from the index |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | csv | — | No | Comma-separated glob patterns to exclude from scanning (e.g. `.obsidian/**,.trash/**`) |
| `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` | string | `_templates` | No | Relative folder path used by the `create_from_template` prompt to discover/read template files |
| `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` | path | — | No | Path to a directory of `.md` prompt files that extend or override built-in prompts |

<!-- DOMAIN-CONFIG-VARS-START -->
## Server Identity

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_SERVER_NAME` | string | `markdown-vault-mcp` | MCP server name shown to clients; useful for multi-instance setups |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS` | string | (auto) | System-level instructions injected into LLM context; defaults to a description that reflects read-only vs read-write state |
| `MARKDOWN_VAULT_MCP_HTTP_PATH` | path | `/mcp` | HTTP endpoint path for streamable HTTP transport (`serve --transport http`) |
| `MARKDOWN_VAULT_MCP_BASE_URL` | url | — | Public base URL of the server (e.g. `https://mcp.example.com`). Required for OIDC auth, `create_download_link` tool, and MCP Apps domain computation |
| `MARKDOWN_VAULT_MCP_EVENT_STORE_URL` | url | `file:///data/state/events` | Event store backend for HTTP session persistence. `file:///path` survives restarts; `memory://` for dev (lost on restart) |
| `MARKDOWN_VAULT_MCP_APP_DOMAIN` | string | (auto) | Override the Claude app domain used for MCP Apps iframe sandboxing. Auto-computed from `BASE_URL` when not set |
| `FASTMCP_LOG_LEVEL` | string | `INFO` | Log level for FastMCP internals (`DEBUG`, `INFO`, `WARNING`, `ERROR`). `-v` CLI flag overrides both app and FastMCP loggers to `DEBUG` |
| `FASTMCP_ENABLE_RICH_LOGGING` | bool | `true` | Set to `false` for plain/structured JSON log output instead of Rich-formatted output |

## MCP File Exchange

!!! info "Upload wired (#443); download deferred to #431"
    These env vars come from `fastmcp-pvl-core` 2.1.0+'s
    `register_file_exchange` / `register_file_exchange_upload` helpers.
    The **upload direction is wired** in `server.py` as of #443: the
    `MARKDOWN_VAULT_MCP_UPLOAD_*` variables below take effect today and
    govern the [`create_upload_link`](tools/index.md#create_upload_link)
    tool plus the `POST /markdown-vault-mcp/uploads/{token}` route. The **download
    direction is not wired** — `markdown-vault-mcp` has its own
    `create_download_link(path, ttl_seconds)` tool whose name collides
    with the pvl-core spec-compliant version, so
    `register_file_exchange(...)` remains commented out pending the
    migration in #431. The `MARKDOWN_VAULT_MCP_FILE_EXCHANGE_*`
    variables are documented here for completeness; setting them has
    no effect until #431 lands.

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_FILE_EXCHANGE_ENABLED` | `true` on HTTP/SSE, `false` on stdio | Master switch for the download direction. |
| `MARKDOWN_VAULT_MCP_FILE_EXCHANGE_PRODUCE` | `true` | Allow this server to mint `FileRef` objects via `handle.publish(...)`. |
| `MARKDOWN_VAULT_MCP_FILE_EXCHANGE_CONSUME` | `true` | Master toggle for the consumer side. Only effective when `consumer_sink=` is wired in `server.py`. |
| `MARKDOWN_VAULT_MCP_FILE_EXCHANGE_TTL` | `3600` | Lifetime in seconds for download links and exchange-volume records. |
| `MARKDOWN_VAULT_MCP_UPLOAD_ENABLED` | `true` on HTTP/SSE, `false` on stdio | Master switch for the upload direction (wired since #443). Also requires `MARKDOWN_VAULT_MCP_BASE_URL`. Set to `false` to disable the `create_upload_link` tool and `POST /markdown-vault-mcp/uploads/{token}` route. |
| `MARKDOWN_VAULT_MCP_UPLOAD_MAX_BYTES` | `10485760` (10 MiB) | Maximum POST body size for the upload route. Bodies exceeding this return HTTP 413. |
| `MARKDOWN_VAULT_MCP_UPLOAD_TTL` | `300` | Default lifetime in seconds for upload links. Caller-requested TTL is clamped to `MARKDOWN_VAULT_MCP_UPLOAD_TTL_MAX`. |
| `MARKDOWN_VAULT_MCP_UPLOAD_TTL_MAX` | `3600` | Operator ceiling for caller-requested upload-link TTL. |

Upload-direction variables are namespaced under `_UPLOAD_*` (not
`_FILE_EXCHANGE_UPLOAD_*`) per the upstream `fastmcp-pvl-core` 2.1.0
contract. Download-direction variables keep the historical
`_FILE_EXCHANGE_*` namespace.

## Search Ranking and Snippet Truncation

| Env var | Default | Type | Notes |
|---|---|---|---|
| `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` | `2` | int ≥ 1 | Maximum number of matching sections returned per file (field collapsing). `0` is rejected. Per-call override available on the `search` and `get_similar` tools; `get_context` defaults to `1` for compact dossiers. |
| `MARKDOWN_VAULT_MCP_SNIPPET_WORDS` | `200` | int ≥ 0 | Approximate word budget for `SearchResult.content`. `0` returns the full chunk. Per-call override on the `search` tool. |
| `MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA` | `0.25` | float ≥ 0 | Strength of the per-channel length downweight: `score / (1 + alpha · log(chunk_count))`. `0` disables. Applied only to `search` modes (keyword/semantic/hybrid); `get_similar` and `get_context.similar` skip the downweight because grouping already handles multi-chunk dedup (see [#472](https://github.com/pvliesdonk/markdown-vault-mcp/issues/472)). Operator-only (no per-call override). |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS` | `400` | int ≥ 1 | Threshold above which the adaptive chunker recursively re-splits at deeper heading levels. Set very large (e.g. `100000`) to disable adaptive splitting. |

The first three knobs adjust *ranking and rendering* — they take effect immediately. `MAX_CHUNK_WORDS` changes the chunk *index*; a reindex is required for the new value to take effect. **Upgrading note:** `search` now returns snippets (≤ ~200 words) by default — set `snippet_words=0` per call or `MARKDOWN_VAULT_MCP_SNIPPET_WORDS=0` globally to restore full-chunk output. Existing vaults are also re-chunked on the next `reindex` when `MAX_CHUNK_WORDS` is set.

## Search and Embeddings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | string | auto-detect | Embedding provider: `openai`, `ollama`, or `fastembed`. **Breaking change** from `EMBEDDING_PROVIDER` in older versions |
| `OLLAMA_HOST` | url | `http://localhost:11434` | Ollama server URL. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
| `OPENAI_API_KEY` | string | — | OpenAI API key for the OpenAI embedding provider. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
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
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S`` | float | `30` | Seconds of write-idle time before pushing; `0` = push only on shutdown |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | string | `markdown-vault-mcp` | Git committer name for auto-commits; **set this in Docker** where `git config user.name` is empty |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | string | `noreply@markdown-vault-mcp` | Git committer email for auto-commits |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | bool | `true` | Run `git lfs pull` on startup to resolve LFS pointers; set to `false` if git-lfs is not installed |

!!! tip "Push delay"
    The push delay batches rapid writes into a single push. Set to `0` to disable automatic pushing — the server will push only on shutdown via `close()`.

!!! warning "HTTPS remotes only with token auth"
    When `GIT_TOKEN` is used, SSH remotes are rejected. Use an HTTPS URL for `origin` or `GIT_REPO_URL`.

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
