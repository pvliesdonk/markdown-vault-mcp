<!-- mcp-name: io.github.pvliesdonk/markdown-vault-mcp -->
# markdown-vault-mcp

[![CI](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp/graph/badge.svg)](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp) [![PyPI](https://img.shields.io/pypi/v/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![Python](https://img.shields.io/pypi/pyversions/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![License](https://img.shields.io/github/license/pvliesdonk/markdown-vault-mcp)](LICENSE) [![Docker](https://img.shields.io/github/v/release/pvliesdonk/markdown-vault-mcp?label=ghcr.io&logo=docker)](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp) [![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://pvliesdonk.github.io/markdown-vault-mcp/) [![llms.txt](https://img.shields.io/badge/llms.txt-available-brightgreen)](https://pvliesdonk.github.io/markdown-vault-mcp/llms.txt) [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/pvliesdonk/markdown-vault-mcp)

A generic markdown collection [MCP](https://modelcontextprotocol.io/) server with FTS5 full-text search, semantic vector search, frontmatter-aware indexing, incremental reindexing, and non-markdown attachment support.

**[Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/)** | **[PyPI](https://pypi.org/project/markdown-vault-mcp/)** | **[Docker](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp)**

Point it at a directory of Markdown files (an Obsidian vault, a docs folder, a Zettelkasten, a PARA vault) and it exposes search, read, write, and edit tools over the Model Context Protocol.

## Features

- **Full-text search** — SQLite FTS5 with BM25 scoring, porter stemming
- **Semantic search** — cosine similarity over embedding vectors (FastEmbed, Ollama, or OpenAI)
- **Hybrid search** — Reciprocal Rank Fusion combining FTS5 and vector results
- **Frontmatter-aware** — indexes YAML frontmatter fields, supports required field enforcement
- **Incremental reindexing** — hash-based change detection, only re-processes modified files
- **Write operations** — create, edit, delete, rename documents with automatic index updates
- **Attachment support** — read, write, delete, and list non-markdown files (PDFs, images, etc.)
- **Git integration** — optional auto-commit and push on every write via `GIT_ASKPASS`
- **OIDC authentication** — optional token-based auth for HTTP deployments (Authelia, Keycloak, etc.)
- **MCP tools** — 28 LLM-visible tools including search, read, write, edit, delete, rename, git history, and admin operations; plus 6 app-only tools for MCP Apps clients
- **MCP resources** — 9 resources exposing vault configuration, statistics, tags, folders, document outlines, similar notes, recent notes, and an interactive SPA
- **MCP prompts** — 6 prompt templates including template-driven note creation

## What you can do with it

With this server mounted in Claude, you can:

- **Capture a URL as a note.** "Fetch <url>, summarize as a Resource note under `3-Resources/`, and link any existing notes on the topic." — Claude composes `fetch` + `search` + `write`.
- **Research a topic into your vault.** "Research product security regulations, compare them, and create a set of interlinked notes — one per regulation, plus a map-of-content." — Claude composes web-search tools (client-side) + `write` with wikilinks. See the [Research workflows guide](https://pvliesdonk.github.io/markdown-vault-mcp/guides/research-workflows/) for the full loop.
- **Distill today's thinking.** "Summarize today's conversations into Inbox notes." — Claude.ai only; uses `conversation_search` + `recent_chats` + `write`. The [`para-capture-chats`](examples/para/prompts/para-capture-chats.md) prompt is the one-click version.
- **Find missing links.** Fire the [`propose-links`](https://pvliesdonk.github.io/markdown-vault-mcp/prompts/#propose-links) prompt from the `+` menu — it scans recently-modified notes, proposes meaningful connections, and writes them on confirmation.
- **Split or merge captures.** "Split this Inbox note into two." / "Merge this into `<existing note>` instead of duplicating." — Claude composes `read` + `write` + `delete`.

No external scheduler, no separate capture app — the vault sits behind your conversations and absorbs their output.

## Installation

### From PyPI

```bash
pip install markdown-vault-mcp
```

With optional dependencies:

```bash
pip install markdown-vault-mcp[mcp]            # FastMCP server
pip install markdown-vault-mcp[embeddings-api]  # Ollama/OpenAI embeddings via HTTP
pip install markdown-vault-mcp[embeddings]      # FastEmbed local embeddings
pip install markdown-vault-mcp[all]             # MCP + FastEmbed + API embeddings
```

### From source

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
pip install -e ".[all,dev]"
```

### Docker

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:latest
```

The Docker image uses `[all]` (MCP + FastEmbed + API embeddings). By default, semantic search works locally with FastEmbed and can switch to Ollama/OpenAI when configured.

### Linux packages (.deb / .rpm)

Download `.deb` or `.rpm` packages from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page. These install a systemd service with security hardening. See the [systemd deployment guide](https://pvliesdonk.github.io/markdown-vault-mcp/deployment/systemd/) for details.

### Claude Desktop (.mcpb bundle)

Download the `.mcpb` bundle from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page. Double-click to install, or run:

```bash
mcpb install markdown-vault-mcp-<version>.mcpb
```

Claude Desktop opens a GUI wizard that prompts for required env vars — no manual JSON editing needed. See [Step 0 of the Claude Desktop guide](https://pvliesdonk.github.io/markdown-vault-mcp/guides/claude-desktop/#step-0-install-via-mcpb-bundle-easiest) for details.

### Claude Code plugin

```
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

Installs the MCP server and the `vault-workflow` skill. See the [Claude Code plugin guide](https://pvliesdonk.github.io/markdown-vault-mcp/guides/claude-code-plugin/) for details.

## Quick Start

### As a library

```python
from pathlib import Path
from markdown_vault_mcp import Collection

collection = Collection(source_dir=Path("/path/to/vault"))
results = collection.search("query text", limit=10)
```

### As an MCP server

```bash
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/vault
markdown-vault-mcp serve
```

### With Docker Compose

1. Copy an example env file:

   ```bash
   cp examples/obsidian-readonly.env .env
   ```

2. Edit `.env` to set `MARKDOWN_VAULT_MCP_SOURCE_DIR` to the absolute path of your vault on the host.

3. Start the service:

   ```bash
   docker compose up -d
   ```

4. Check the logs:

   ```bash
   docker compose logs -f markdown-vault-mcp
   ```

### Example env files

| File | Description |
|------|-------------|
| `examples/obsidian-readonly.env` | Obsidian vault, read-only, Ollama embeddings |
| `examples/obsidian-readwrite.env` | Obsidian vault, read-write with git auto-commit |
| `examples/obsidian-oidc.env` | Obsidian vault, read-only, OIDC authentication (Authelia) |
| `examples/ifcraftcorpus.env` | Strict frontmatter enforcement, read-only corpus |

For reverse proxy (Traefik) and deployment setup, see [`docs/deployment.md`](docs/deployment.md).

## Configuration

All configuration is via environment variables with the `MARKDOWN_VAULT_MCP_` prefix (except embedding provider settings, which use their own conventions).

### Core

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | — | **Yes** | Path to the markdown vault directory |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `true` | No | Set to `false` to enable write operations |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | in-memory | No | Path to the SQLite FTS5 index file; set for persistence across restarts |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | disabled | No | Path to the numpy embeddings file; required to enable semantic search |
| `MARKDOWN_VAULT_MCP_STATE_PATH` | `{SOURCE_DIR}/.markdown_vault_mcp/state.json` | No | Path to the change-tracking state file |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | — | No | Comma-separated frontmatter fields to promote to the tag index for structured filtering |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | — | No | Comma-separated frontmatter fields required on every document; documents missing any are excluded from the index |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | — | No | Comma-separated glob patterns to exclude from scanning (e.g. `.obsidian/**,.trash/**`) |
| `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` | `_templates` | No | Relative folder path where note templates live (used by the `create_from_template` prompt) |
| `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` | — | No | Path to a directory of `.md` prompt files that extend or override built-in prompts (see [User-defined prompts](#user-defined-prompts)) |

### Server identity

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_SERVER_NAME` | `markdown-vault-mcp` | MCP server name shown to clients; useful for multi-instance setups |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS` | (auto) | System-level instructions injected into LLM context; defaults to a description that reflects read-only vs read-write state |
| `MARKDOWN_VAULT_MCP_HTTP_PATH` | `/mcp` | HTTP endpoint path for streamable HTTP transport (used by `serve --transport http`) |
| `MARKDOWN_VAULT_MCP_EVENT_STORE_URL` | `file:///data/state/events` | Event store backend for HTTP session persistence. `file:///path` (default) survives restarts; `memory://` for dev (lost on restart). |
| `MARKDOWN_VAULT_MCP_APP_DOMAIN` | (auto) | Override the Claude app domain used for MCP Apps iframe sandboxing. Auto-computed from `BASE_URL` when not set. |
| `FASTMCP_LOG_LEVEL` | `INFO` | Log level for FastMCP internals (`DEBUG`, `INFO`, `WARNING`, `ERROR`). App loggers default to `INFO`. `-v` overrides both to `DEBUG`. |
| `FASTMCP_ENABLE_RICH_LOGGING` | `true` | Set to `false` for plain/structured JSON log output instead of Rich-formatted output. |

### Search and embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | auto-detect | Embedding provider: `openai`, `ollama`, or `fastembed` |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL (**not** `MARKDOWN_VAULT_MCP_`-prefixed) |
| `OPENAI_API_KEY` | — | OpenAI API key for the OpenAI embedding provider (**not** `MARKDOWN_VAULT_MCP_`-prefixed) |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | `nomic-embed-text` | Ollama embedding model name |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | `false` | Force Ollama to use CPU only |
| `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL` | `BAAI/bge-small-en-v1.5` | FastEmbed model name |
| `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR` | FastEmbed default | FastEmbed model cache directory (in Docker, stored under `/data/state/fastembed`) |

### Git integration

Git integration has three modes:

- **Managed mode** (`MARKDOWN_VAULT_MCP_GIT_REPO_URL` set): server owns repo setup.
  On startup it clones into `SOURCE_DIR` when empty, or validates existing `origin`.
  Pull loop + auto-commit + deferred push are enabled.
- **Unmanaged / commit-only mode** (no `GIT_REPO_URL`): writes are committed to a local git repo if `SOURCE_DIR` is already a git checkout. No pull, no push.
- **No-git mode**: if `SOURCE_DIR` is not a git repo, git callbacks are no-ops.

When token auth is used (`MARKDOWN_VAULT_MCP_GIT_TOKEN`), remotes must be HTTPS.
SSH remotes (for example `git@github.com:owner/repo.git`) are rejected with a startup error.
Fix with: `git -C /path/to/vault remote set-url origin https://github.com/owner/repo.git`

Backward compatibility: `MARKDOWN_VAULT_MCP_GIT_TOKEN` without `GIT_REPO_URL` still works (legacy mode) but logs a deprecation warning.

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | — | HTTPS remote URL for managed mode; enables clone/remote validation on startup |
| `MARKDOWN_VAULT_MCP_GIT_USERNAME` | `x-access-token` | Username for HTTPS auth prompts (`x-access-token` for GitHub, `oauth2` for GitLab, account name for Bitbucket) |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | — | Token/password for HTTPS auth (`GIT_ASKPASS`) |
| `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | `600` | Seconds between `git fetch` + ff-only update attempts; `0` disables periodic pull |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | `30` | Seconds of write-idle time before pushing; `0` = push only on shutdown |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | `markdown-vault-mcp` | Git committer name for auto-commits; **set this in Docker** where `git config user.name` is empty |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | `noreply@markdown-vault-mcp` | Git committer email for auto-commits |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | `true` | Enable Git LFS — runs `git lfs pull` on startup to fetch LFS-tracked attachments (PDFs, images). Set to `false` for repos without LFS. |

### Attachments

Non-markdown file support. See [Attachments](#attachments) for details.

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS` | (built-in list) | Comma-separated allowed extensions without dot (e.g. `pdf,png,jpg`); use `*` to allow all non-`.md` files |
| `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` | `10.0` | Maximum attachment size in MB for reads and writes; `0` disables the limit |

### Bearer token authentication

Simple static token auth for HTTP deployments. Set a single env var — clients must send `Authorization: Bearer <token>`.

| Variable | Required | Description |
|----------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BEARER_TOKEN` | Yes | Static bearer token; any non-empty string enables auth |

### OIDC authentication

Full OAuth 2.1 authentication for HTTP deployments. OIDC activates when all four required variables are set. See [Authentication](#authentication) for setup details.

> **Multi-auth:** If both `BEARER_TOKEN` and all OIDC variables are set, the server accepts **either** credential — a valid bearer token or a valid OIDC session. This is useful when different clients use different auth flows (e.g. Claude web via OIDC and Claude Code via bearer token).

| Variable | Required | Description |
|----------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | Yes | Public base URL of the server (e.g. `https://mcp.example.com`; include prefix if mounted under subpath, e.g. `https://mcp.example.com/vault`). Also required for `create_download_link` and used to auto-compute the MCP Apps domain. |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | Yes | OIDC discovery endpoint (e.g. `https://auth.example.com/.well-known/openid-configuration`) |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | Yes | OIDC client ID registered with your provider |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | Yes | OIDC client secret |
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | No | JWT signing key; **required on Linux/Docker** — the default is ephemeral and invalidates tokens on restart. Generate with `openssl rand -hex 32` |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | No | Expected JWT audience claim; leave unset if your provider does not set one |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | No | Comma-separated required scopes; default `openid` |
| `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN` | No | Set `true` to verify the upstream access token as a JWT instead of the id token. Only needed when your provider issues JWT access tokens and you require audience-claim validation on that token. Default: verify the id token (works with all providers, including opaque-token issuers like Authelia) |

## CLI Reference

```
markdown-vault-mcp <command> [options]
```

### `serve`

Start the MCP server.

```bash
markdown-vault-mcp serve [--transport {stdio|sse|http}] [--host HOST] [--port PORT] [--path PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--transport` | `stdio` | MCP transport: `stdio` (stdin/stdout, default), `sse` (Server-Sent Events), `http` (streamable-HTTP). Use `http` for Docker with a reverse proxy or when OIDC is enabled. |
| `--host` | `0.0.0.0` | Bind host for the `http` transport (ignored for `stdio` and `sse`) |
| `--port` | `8000` | Port for the `http` transport (ignored for `stdio` and `sse`) |
| `--path` | env `MARKDOWN_VAULT_MCP_HTTP_PATH` or `/mcp` | MCP HTTP path for `http` transport; useful for reverse-proxy subpath mounting (e.g. `/vault/mcp`) |

### Reverse Proxy Subpath Mounts

By default, HTTP transport serves MCP on `/mcp`. You can run it under a subpath:

```bash
markdown-vault-mcp serve --transport http --path /vault/mcp
```

Equivalent env-based config:

```bash
MARKDOWN_VAULT_MCP_HTTP_PATH=/vault/mcp
```

For reverse proxies, you can either:

- Keep app path at `/mcp` and use proxy rewrite/strip-prefix middleware.
- Set app path directly to the public path (`/vault/mcp`) and route without rewrite.

When OIDC is enabled under a subpath, the configuration is different: the subpath goes in `BASE_URL` only, and `HTTP_PATH` stays at `/mcp`. See [OIDC subpath deployments](https://pvliesdonk.github.io/markdown-vault-mcp/deployment/oidc/#subpath-deployments).

Then your redirect URI is:

```text
https://mcp.example.com/vault/auth/callback
```

### `index`

Build the full-text search index.

```bash
markdown-vault-mcp index [--source-dir PATH] [--index-path PATH] [--force]
```

### `search`

Search the collection from the CLI.

```bash
markdown-vault-mcp search <query> [-n LIMIT] [-m {keyword|semantic|hybrid}] [--folder PATH] [--json]
```

### `reindex`

Incrementally reindex the vault (only processes changed files).

```bash
markdown-vault-mcp reindex [--source-dir PATH] [--index-path PATH]
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search` | Hybrid full-text + semantic search with optional frontmatter filters |
| `read` | Read a document or attachment by relative path |
| `write` | Create or overwrite a document or attachment |
| `edit` | Replace text in a document — exact match, line-range, or scoped match with normalized fallback |
| `delete` | Delete a document or attachment and its index entries |
| `rename` | Rename/move a document or attachment, updating all index entries; pass `update_links=true` to also rewrite backlinks in other notes |
| `list_documents` | List indexed documents; pass `include_attachments=true` to also list non-markdown files |
| `list_folders` | List all folder paths in the vault |
| `list_tags` | List all unique frontmatter tag values |
| `reindex` | Force a full reindex of the vault |
| `stats` | Get collection statistics (document count, chunk count, link health metrics, etc.) |
| `build_embeddings` | Build or rebuild vector embeddings for semantic search |
| `embeddings_status` | Check embedding provider and index status |
| `get_backlinks` | Find all documents that link to a given document |
| `get_outlinks` | Find all links from a document, with existence check |
| `get_broken_links` | Find all links pointing to non-existent documents |
| `get_similar` | Find semantically similar notes by document path |
| `get_recent` | Get the most recently modified notes |
| `get_context` | Get a consolidated context dossier for a note (backlinks, outlinks, similar, folder peers, tags, modified time) |
| `get_orphan_notes` | Find all notes with no inbound or outbound links |
| `get_most_linked` | Find the most-linked-to notes ranked by backlink count |
| `get_connection_path` | Find the shortest path between two notes via BFS on the undirected link graph (max 10 hops) |
| `get_history` | List commits that touched a note or the whole vault (git-backed vaults only) |
| `get_diff` | Return a unified diff of a note between a reference commit/timestamp and HEAD (git-backed vaults only) |
| `fetch` | Download a file from a URL and save it to the vault as a note or attachment (MCP-to-MCP transfer) |
| `create_download_link` | Generate a one-time download URL for a vault file — enables MCP-to-MCP file transfer (HTTP/SSE transport only; requires `BASE_URL`) |
| `browse_vault` | Open the vault explorer SPA in a supporting MCP Apps client |
| `show_context` | Open the Context Card for a specific note in a supporting MCP Apps client |

Write tools (`write`, `edit`, `delete`, `rename`, `fetch`) are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

`browse_vault` and `show_context` are LLM-visible in all clients; when called in an MCP Apps-capable client they open the interactive SPA. Six additional internal tools (`vault_context`, `vault_list`, `vault_read`, `vault_search`, `vault_graph_neighborhood`, `vault_graph_hubs`) use `visibility="app"` and are used by the SPA only — they are never visible to the LLM.

### Resources

MCP resources expose vault metadata as structured JSON that clients can read directly without invoking tools.

| URI | Description |
|-----|-------------|
| `config://vault` | Current collection configuration (source dir, indexed fields, read-only state, etc.) |
| `stats://vault` | Collection statistics (document count, chunk count, embedding count, etc.) |
| `tags://vault` | All frontmatter tag values grouped by indexed field |
| `tags://vault/{field}` | Tag values for a specific indexed frontmatter field (template) |
| `folders://vault` | All folder paths in the vault |
| `toc://vault/{path}` | Table of contents (heading outline) for a specific document (template) |
| `similar://vault/{path}` | Top 10 semantically similar notes for a document (template) |
| `recent://vault` | 20 most recently modified notes with ISO timestamps |
| `ui://vault/app.html` | Interactive vault explorer SPA for MCP Apps clients |

### Prompts

Prompt templates guide the LLM through multi-step workflows using the vault tools.

| Prompt | Parameters | Description |
|--------|------------|-------------|
| `summarize` | `path` | Read a document and produce a structured summary with key themes and takeaways |
| `research` | `topic` | Search for a topic, synthesize findings, and create a new note at `research/{topic}.md` |
| `discuss` | `path` | Analyze a document and suggest improvements using `edit` (not `write`) |
| `create_from_template` | `template_name` (optional) | Discover templates (if needed), read a template, gather user values, and write a new note |
| `related` | `path` | Find related notes via search and suggest cross-references as markdown links |
| `compare` | `path1`, `path2` | Read two documents and produce a side-by-side comparison |

Write prompts (`research`, `discuss`, `create_from_template`) are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

Templates are regular markdown files. If placeholder template text pollutes search results, add your templates folder to `MARKDOWN_VAULT_MCP_EXCLUDE` (for example `_templates/**`).

### User-defined prompts

Mount a directory of `.md` prompt files to override or extend the built-in prompts. Set `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` to the path. Each file's frontmatter defines `description`, `arguments` (a list of objects, each with `name`, `description`, and `required` fields), and optional `tags`. A user prompt with the same name as a built-in replaces it.

For a complete example — including Zettelkasten capture, development, and review prompts — see the [Zettelkasten guide](https://pvliesdonk.github.io/markdown-vault-mcp/guides/zettelkasten/).
For an alternative action-oriented workflow — Projects, Areas, Resources, Archive with triage, kickoff, and weekly review prompts — see the [PARA guide](https://pvliesdonk.github.io/markdown-vault-mcp/guides/para/).

## MCP Apps

The server ships four browser-based views that MCP clients supporting the MCP Apps protocol can render inline or in fullscreen. They are delivered as a single HTML resource at `ui://vault/app.html` and registered using `visibility="app"` so they appear only in supporting clients and do not clutter the standard tool list. See the [MCP Apps guide](https://pvliesdonk.github.io/markdown-vault-mcp/guides/mcp-apps/) for details.

| View | Description |
|------|-------------|
| **Context Card** | Displays a note dossier (backlinks, outlinks, similar notes, tags) for the note currently in focus |
| **Graph Explorer** | Interactive force-directed link graph of the vault, powered by vis-network |
| **Vault Browser** | Searchable, filterable file tree for navigating the vault without issuing tool calls |
| **Note Preview** | Full-width markdown preview with frontmatter table and "Send to Claude" button |

The two primary tools exposed to MCP Apps clients are:

| Tool | Description |
|------|-------------|
| `browse_vault` | Returns the vault tree structure for the Vault Browser view |
| `show_context` | Returns the full context dossier for a given note path (used by the Context Card view) |

**Domain configuration:** MCP Apps iframes are sandboxed to a specific Claude app domain. The domain is auto-computed from `MARKDOWN_VAULT_MCP_BASE_URL`. Override with `MARKDOWN_VAULT_MCP_APP_DOMAIN` if your deployment is hosted on a custom domain or behind a proxy that changes the apparent hostname.

Vendored dependencies (bundled at build time, no runtime CDN): vis-network (graph rendering), marked.js (markdown rendering), DOMPurify (XSS sanitization), ext-apps SDK (MCP Apps lifecycle).

## Attachments

In addition to Markdown notes, the server can read, write, delete, rename, and list non-markdown files (PDFs, images, spreadsheets, etc.). All existing tools are overloaded — no new tool names.

### How it works

Path dispatch is extension-based: a path ending in `.md` is treated as a note; any other path is treated as an attachment if the extension is in the allowlist. The `kind` field on returned objects distinguishes the two: `"note"` or `"attachment"`.

### Reading attachments

`read` returns base64-encoded content for binary attachments:

```json
{
  "path": "assets/diagram.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 12345,
  "content_base64": "<base64 string>",
  "modified_at": 1741564800.0
}
```

### Writing attachments

`write` accepts a `content_base64` parameter for binary content:

```json
{ "path": "assets/diagram.pdf", "content_base64": "<base64 string>" }
```

### Listing attachments

`list_documents` with `include_attachments=true` returns both notes and attachments:

```json
[
  { "path": "notes/intro.md", "kind": "note", "title": "Intro", "folder": "notes", "frontmatter": {}, "modified_at": 1741564800.0 },
  { "path": "assets/diagram.pdf", "kind": "attachment", "folder": "assets", "mime_type": "application/pdf", "size_bytes": 12345, "modified_at": 1741564800.0 }
]
```

### Default allowed extensions

`pdf`, `docx`, `xlsx`, `pptx`, `odt`, `ods`, `odp`, `png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`, `bmp`, `tiff`, `zip`, `tar`, `gz`, `mp3`, `mp4`, `wav`, `ogg`, `txt`, `csv`, `tsv`, `json`, `yaml`, `toml`, `xml`, `html`, `css`, `js`, `ts`

Override with `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS`. Use `*` to allow all non-`.md` files.

> **Hidden directories:** Attachments inside hidden directories (`.git/`, `.obsidian/`, `.markdown_vault_mcp/`, etc.) are never listed, regardless of extension settings. `MARKDOWN_VAULT_MCP_EXCLUDE` patterns are also applied to attachments.

## Authentication

The server supports four auth modes:

1. **Multi-auth** — both bearer token and OIDC configured; either credential accepted (e.g. Claude web via OIDC + Claude Code via bearer token on the same instance)
2. **Bearer token** — set `MARKDOWN_VAULT_MCP_BEARER_TOKEN` to a secret string
3. **OIDC** — full OAuth 2.1 flow via `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, and `BASE_URL`
4. **No auth** — server accepts all connections (default)

**Auth requires `--transport http` (or `sse`).** It has no effect with `--transport stdio`.

For setup instructions, troubleshooting, and provider-specific guides, see the [Authentication guide](https://pvliesdonk.github.io/markdown-vault-mcp/guides/authentication/).

## Development

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
uv pip install -e ".[all,dev]"

# Run tests
uv run python -m pytest tests/ -x -q

# Lint and format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/
```

## License

MIT
