<!-- mcp-name: io.github.pvliesdonk/markdown-vault-mcp -->
# markdown-vault-mcp

[![CI](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp/graph/badge.svg)](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp) [![PyPI](https://img.shields.io/pypi/v/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![Python](https://img.shields.io/pypi/pyversions/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![License](https://img.shields.io/github/license/pvliesdonk/markdown-vault-mcp)](LICENSE) [![Docker](https://img.shields.io/github/v/release/pvliesdonk/markdown-vault-mcp?label=ghcr.io&logo=docker)](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp) [![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://pvliesdonk.github.io/markdown-vault-mcp/) [![llms.txt](https://img.shields.io/badge/llms.txt-available-brightgreen)](https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt) [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/pvliesdonk/markdown-vault-mcp) [![Template](https://img.shields.io/badge/dynamic/yaml?url=https://raw.githubusercontent.com/pvliesdonk/markdown-vault-mcp/main/.copier-answers.yml&query=%24._commit&label=template)](https://github.com/pvliesdonk/fastmcp-server-template)

<!-- DOMAIN-START -->
A generic markdown vault [MCP](https://modelcontextprotocol.io/) server with FTS5 full-text search, semantic vector search, frontmatter-aware indexing, incremental reindexing, and non-markdown attachment support.

**[Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/)** | **[Release notes](https://pvliesdonk.github.io/markdown-vault-mcp/latest/releases/)** | **[Config wizard](https://pvliesdonk.github.io/markdown-vault-mcp/latest/configuration-generator/)** | **[PyPI](https://pypi.org/project/markdown-vault-mcp/)** | **[Docker](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp)**

Point it at a directory of Markdown files (an Obsidian vault, a docs folder, a Zettelkasten, a PARA vault) and it exposes search, read, write, and edit tools over the Model Context Protocol.
<!-- DOMAIN-END -->

## Features

<!-- DOMAIN-START -->
- **Full-text search**: SQLite FTS5 with BM25 scoring, porter stemming
- **Semantic search**: cosine similarity over embedding vectors (FastEmbed, Ollama, or OpenAI)
- **Hybrid search**: Reciprocal Rank Fusion combining FTS5 and vector results
- **Diversity-aware ranking**: each search result list caps a single document at 2 chunks (configurable) and downweights chunks of long documents, returning sentence-scale snippets. This bounds LLM context cost per query, with full-section recovery via `read(path, section=heading)`
- **Adaptive heading-level chunking**: long sections are recursively re-split at deeper heading levels (H1 → H6) until each chunk fits a configurable word budget, improving retrieval precision on synthesising essays without manual restructuring

> **Upgrading.** As of this release, `search` returns query-relevant snippets in the `content` field by default (approximately 200 words). Pass `snippet_words=0` to recover the prior full-chunk behaviour, or use `read(path, section=heading)` to fetch the full section after seeing a snippet. Documents are also re-chunked on next `reindex` to honour the adaptive `MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS` threshold (default 400).
- **Frontmatter-aware**: indexes YAML frontmatter fields, supports required field enforcement
- **OKF-aware**: recognizes [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog) bundles (an `okf_version` declaration in the root `index.md`) and annotates search/read results with each note's type, lifecycle status, staleness, and trust tier. Those dimensions are filterable and also nudge ranking on a detected bundle (deprecated and stale notes rank lower, and the reserved `index.md` / `log.md` navigation files are demoted below real notes), and the server adds an `okf_validate` conformance audit plus one-shot migration transforms (`okf_convert_links`, `okf_generate_index`, `okf_seed_log`) for moving a vault into the format, plus a downloadable bundle export served through `create_download_link` with an `okf-bundle` reference. Read semantics are controlled by `MARKDOWN_VAULT_MCP_OKF_MODE`
- **Incremental reindexing**: hash-based change detection, only re-processes modified files; an automatic boot-time reconciliation pass picks up changes made while no server was running, and the vector index converges to the reconciled chunk set (embedding exactly the delta)
- **Write operations**: create, edit, append to, delete, rename documents, and move entire folder subtrees with automatic index updates
- **Folder conventions**: per-folder `_conventions.md` files carry your authoring rules, such as "reference notes stay self-contained"; the server surfaces them to LLM clients at write time via the `get_conventions` tool and in `write`/`edit` results, without interpreting them
- **Attachment support**: read, write, delete, and list non-markdown files (PDFs, images, etc.)
- **Git integration**: optional auto-commit and push on every write via `GIT_ASKPASS`
- **OIDC authentication**: optional token-based auth for HTTP deployments (Authelia, Keycloak, etc.)
- **MCP tools**: 34 LLM-visible tools including search, read, write, edit, append, delete, rename, `move_folder`, git history, manual git sync, one-time transfer links, and admin operations; plus 6 app-only tools for MCP Apps clients
- **MCP resources**: 9 resources exposing vault configuration, statistics, tags, folders, document outlines, similar notes, recent notes, and an interactive SPA
- **MCP prompts**: 8 prompt templates including template-driven note creation and client-side multi-note summarization
<!-- DOMAIN-END -->

## What you can do with it

<!-- DOMAIN-START -->
With this server mounted in Claude, you can:

- **Capture a URL as a note.** "Fetch <url>, summarize as a Resource note under `3-Resources/`, and link any existing notes on the topic." Claude composes `fetch` + `search` + `write`.
- **Research a topic into your vault.** "Research product security regulations, compare them, and create a set of interlinked notes: one per regulation, plus a map-of-content." Claude composes web-search tools (client-side) + `write` with wikilinks. See the [Research workflows guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/research-workflows/) for the full loop.
- **Distill today's thinking.** "Summarize today's conversations into Inbox notes." Claude.ai only; uses `conversation_search` + `recent_chats` + `write`. The [`para-capture-chats`](examples/para/prompts/para-capture-chats.md) prompt is the one-click version.
- **Find missing links.** Fire the [`propose-links`](https://pvliesdonk.github.io/markdown-vault-mcp/latest/prompts/#propose-links) prompt from the `+` menu: it scans recently modified notes and proposes links between notes that aren't yet connected, writing them on confirmation.
- **Split or merge captures.** "Split this Inbox note into two." / "Merge this into `<existing note>` instead of duplicating." Claude composes `read` + `write` + `delete`.

The vault needs no external scheduler or separate capture app: it sits behind your conversations and absorbs their output.
<!-- DOMAIN-END -->

<!-- ===== TEMPLATE-OWNED SECTIONS BELOW — DO NOT EDIT; CHANGES WILL BE OVERWRITTEN ON COPIER UPDATE ===== -->

## Installation

### From PyPI

```bash
pip install markdown-vault-mcp
```

<!-- DOMAIN-START -->
With optional dependencies:

```bash
pip install markdown-vault-mcp[mcp]            # FastMCP server
pip install markdown-vault-mcp[embeddings-api]  # Ollama/OpenAI embeddings via API
pip install markdown-vault-mcp[embeddings]      # FastEmbed local embeddings
pip install markdown-vault-mcp[all]             # MCP + FastEmbed + API embeddings
```
<!-- DOMAIN-END -->

### From source

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
uv sync --all-extras --all-groups
```

### Docker

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:latest
```

To run the newest merged code instead of the newest release, use the rolling `edge` tag. It is rebuilt on every merge to `main` and carries no version identity. See [Image tags](docs/deployment/docker.md#image-tags) for the full tag list.

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:edge
```

<!-- DOMAIN-START -->
The Docker image uses `[all]` (MCP + FastEmbed + API embeddings). By default, semantic search works locally with FastEmbed and can switch to Ollama/OpenAI when configured. A `compose.yml` ships at the repo root as a starting point: copy `.env.example` to `.env`, edit, and `docker compose up -d`.

To attach a remote Python debugger (development only; the protocol is unauthenticated), see [Remote debugging](docs/deployment/docker.md#remote-debugging).

### Linux packages (.deb / .rpm)

Download `.deb` or `.rpm` packages from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page. Both install a hardened systemd unit; env configuration is sourced from `/etc/markdown-vault-mcp/env` (copy from the shipped `/etc/markdown-vault-mcp/env.example`). See the [systemd deployment guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/deployment/systemd/) for details.

### Claude Desktop (.mcpb bundle)

Download the `.mcpb` bundle from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page. Double-click to install, or run:
<!-- DOMAIN-END -->

```bash
mcpb install markdown-vault-mcp-<version>.mcpb
```

<!-- DOMAIN-START -->
Claude Desktop opens a GUI wizard that prompts for required env vars; no manual JSON editing is needed. See [Step 0 of the Claude Desktop guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/claude-desktop/#step-0-install-via-mcpb-bundle-easiest) for details.

### Claude Code plugin

```
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

Installs the MCP server and the `vault-workflow` skill. See the [Claude Code plugin guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/claude-code-plugin/) for details.

## Release channels

Artifacts ship on three channels. Each row lists exactly what that channel publishes.

| Channel | Version identity | Artifacts |
|---|---|---|
| `edge` (rolling) | None; the commit is the identity | Docker image `:edge` rebuilt on every merge to `main`; `.mcpb` bundle as the `mcpb-bundle-edge` workflow artifact; rolling `unstable` docs version. It leaves no git tag, GitHub release, or PyPI entry behind. |
| Pre-release | `vX.Y.Z-rc.N`, computed and reviewed in its release pull request | GitHub release with wheels, `sdist`, `.deb`/`.rpm` packages, `.mcpb` bundle, and SBOM attached; Docker image under its immutable `vX.Y.Z-rc.N` tag plus the ordering-aware rolling `rc` tag. Skips PyPI, the plugin marketplace, the MCP registry, and the docs deploy. |
| Stable | `vX.Y.Z` | Everything: PyPI, Docker (version tag plus ordering-aware `latest` / `vX` / `vX.Y`), `.deb`/`.rpm`, GitHub release assets (wheels, `sdist`, `.mcpb` bundle, SBOM), plugin marketplace and MCP registry entries (when the release is the newest stable), versioned docs with an ordering-aware `latest` alias. |

The PyPI split is deliberate: `edge` and pre-release builds never reach PyPI, where every ordinary installer would see them. A pre-release's wheels are still attached to its GitHub release and installable by URL for anyone who opts in. Rolling pointers are ordering-aware, so a patch release cut from an old `release/X.Y` branch never moves `latest`-style tags back to older content, and a candidate for an already-released version never moves `rc`. See [Release process](docs/deployment/release-process.md) for the full model.

## Quick Start

### As a library

```python
from pathlib import Path
from markdown_vault_mcp.vault import Vault

vault = Vault(source_dir=Path("/path/to/vault"))
vault.index.build_index()
results = vault.reader.search("query text", limit=10)
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

For reverse proxy (Traefik) and deployment setup, see the [deployment guides](docs/deployment/index.md).

### Server info

The server registers a built-in `get_server_info` tool (via `fastmcp_pvl_core.register_server_info_tool`) so operators can confirm the deployed version with a single MCP call. The response carries `server_name`, `server_version`, and `core_version`.

## Configuration

All configuration is via environment variables with the `MARKDOWN_VAULT_MCP_` prefix (except embedding provider settings, which use their own conventions).

- [Configuration Generator](https://pvliesdonk.github.io/markdown-vault-mcp/latest/configuration-generator/): in-browser config / Docker / systemd builder
- [Configuration reference](https://pvliesdonk.github.io/markdown-vault-mcp/latest/configuration/): detailed per-variable documentation with operational caveats

### Domain variables

Domain environment variables use the `MARKDOWN_VAULT_MCP_` prefix (except the
embedding-provider conventions `OLLAMA_HOST`, `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, and `OPENAI_EMBEDDING_MODEL`, which are also honoured bare):

<!-- GENERATED-ENV-TABLE-DOMAIN-START — generated by scripts/gen_config_surface.py; do not edit -->
| Variable | Default | Required | Description |
|---|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | No | Ollama server URL for the ollama embedding provider. Bare (not MARKDOWN_VAULT_MCP_-prefixed), matching the Ollama ecosystem convention. |
| `OPENAI_API_KEY` | (none) | No | OpenAI API key for the openai embedding provider, and the fallback key for the summarize tool when MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY is unset. Bare (not MARKDOWN_VAULT_MCP_-prefixed), matching the OpenAI ecosystem convention. |
| `OPENAI_BASE_URL` | (none) | No | Bare fallback for MARKDOWN_VAULT_MCP_OPENAI_BASE_URL (embeddings). For the summarize tool it only routes traffic when an API key already enables the feature; it never enables summarize by itself. |
| `OPENAI_EMBEDDING_MODEL` | (none) | No | Bare fallback for MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL. |
| `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S` | `60` | No | Maximum seconds an index-backed tool or resource waits for the FTS index to become queryable during a cold-start background build before raising IndexUnavailableError(reason="timeout"). Increase for large vaults. |
| `MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S` | `60` | No | Maximum seconds an index-querying read tool waits for the IndexWriter to drain when called with wait_for_pending_writes=true. On timeout the tool answers from the current index and reports index_stale=true in the response _meta. |
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | `/data/vault` | No | Path to the markdown vault directory. Required; the server refuses to start without it. Symbolic links inside the vault are followed on Python 3.13+. |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `false` | No | Set to true to hide the write tools (write, edit, append, delete, rename, move_folder, fetch, git_sync, the okf_* tools, create_upload_link) and serve a search-only vault. git_sync also needs managed git mode; create_upload_link needs an HTTP transport. |
| `MARKDOWN_VAULT_MCP_DISABLE_APPS_UI` | `false` | No | Hide the MCP Apps UI tools (browse_vault, show_context) from the tool listing for clients that do not render MCP Apps panels. |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | (none) | No | Path to the SQLite FTS5 index file; unset keeps the index in memory. Set it for persistence across restarts. |
| `MARKDOWN_VAULT_MCP_STATE_PATH` | (none) | No | Path to the change-tracking state file. Defaults to {SOURCE_DIR}/.markdown_vault_mcp/state.json. |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | (none) | No | Path to the numpy embeddings file; required to enable semantic search. |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | (none) | No | Comma-separated frontmatter fields promoted to the tag index for structured filtering. Changing it cold-rebuilds the index once on next startup; SEARCHABLE_FIELDS inherits this value when unset. |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | (none) | No | Comma-separated frontmatter fields required on every document; documents missing any are excluded from the index. |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | (none) | No | Comma-separated glob patterns excluded from scanning (.obsidian/**,.trash/**). |
| `MARKDOWN_VAULT_MCP_TITLE_FIELD` | `title` | No | Frontmatter field used as the document title (falls back to title, the first H1, then the filename). Changing it cold-rebuilds the index once on next startup. |
| `MARKDOWN_VAULT_MCP_SEARCHABLE_FIELDS` | (none) | No | Comma-separated frontmatter fields whose text values become keyword-searchable and enrich first-chunk embeddings. Inherits INDEXED_FIELDS when unset; the sentinel none means filterable but not searchable. Changing it cold-rebuilds the index and re-embeds once on next startup. |
| `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` | `_templates` | No | Relative folder where note templates live (used by the create_from_template prompt). |
| `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` | (none) | No | Directory of .md prompt files that extend or override built-in prompts; a relative path is resolved against SOURCE_DIR. |
| `MARKDOWN_VAULT_MCP_CONVENTIONS_FILE` | `_conventions.md` | No | Filename of the per-folder conventions files surfaced to clients at write time (bare .md filename without glob characters). Set to none to disable folder conventions. |
| `MARKDOWN_VAULT_MCP_OKF_MODE` | `auto` | No | OKF (Open Knowledge Format) read semantics. With auto (the default), read annotations switch on when the vault declares an OKF version in its root index.md. Use off to disable OKF semantics entirely, or on to force them for an undeclared vault. Annotations are read-only; write behavior is never affected. |
| `MARKDOWN_VAULT_MCP_OKF_WRITE` | `false` | No | OKF (Open Knowledge Format) enforced write layer. When true on an OKF-active vault, the server stamps generated provenance on each write and clears any verified attestation when a note's content changes. It also keeps each written folder's log.md and index.md current, and exposes the okf_verify tool. Requires OKF_MODE to be auto or on (a true value with OKF_MODE=off is a config error). Off by default. |
| `MARKDOWN_VAULT_MCP_OKF_VERIFY` | `elicit` | No | How the okf_verify tool attributes a human review. This applies only when OKF_WRITE is on, which gates the tool. With elicit (the default), okf_verify asks the human to confirm the review through an MCP elicitation and records the attestation only on an affirmative reply. It fails closed when the client cannot elicit or the human declines, so a model that holds the human's token cannot self-attest. Use trust-auth to attribute to the authenticated caller with no confirmation (safe only when the sole caller is a human-driven UI), or off to hide the tool so attestation happens through external tooling. A non-default value with OKF_WRITE off is a config error. |
| `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS` | (none) | No | Comma-separated allowed attachment extensions without the dot (such as pdf,png,jpg); use * to allow every non-markdown file. Unset selects the built-in allowlist. |
| `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` | `1.0` | No | Maximum attachment size in MB returned by read / accepted by write; 0 disables the limit. |
| `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` | `262144` | No | Maximum bytes returned by a full-document read of a note; use `read(path, section=…)` for partial reads. 0 disables the limit. |
| `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` | `2` | No | Maximum chunks returned per document in search results. |
| `MARKDOWN_VAULT_MCP_SNIPPET_WORDS` | `200` | No | Width of the snippet window (words) in search results; 0 returns full chunk content. |
| `MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA` | `0.25` | No | Down-weights longer chunks in ranking: score / (1 + alpha * log(chunk_count)). |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS` | `400` | No | Word cap per chunk; the adaptive chunker splits at deeper heading levels, then paragraph/word boundaries, to respect it. Match it to the embedding model's context. A reindex applies a new value. |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS` | (none) | No | Character cap enforced alongside MAX_CHUNK_WORDS to bound token-dense chunks. Unset derives min(1500, model context * 2.8). Set a positive value for an exact cap, or -1 to scale with the model's full context (can exhaust memory on long-context models). A reindex applies a new value. |
| `MARKDOWN_VAULT_MCP_CHUNK_OVERLAP_WORDS` | `40` | No | Words of overlap between adjacent budget-split fragments of the same heading section (0 disables). A reindex applies a new value. |
| `MARKDOWN_VAULT_MCP_FOLDER_WEIGHTS` | (none) | No | Folder-prefix score multipliers (`prefix:weight` pairs, comma-separated, weights > 0) applied to all search modes; the deepest matching prefix wins (sessions:0.5 demotes sessions/**). |
| `MARKDOWN_VAULT_MCP_FTS_WEIGHTS` | (none) | No | Per-column BM25 weights (`column:weight` pairs, comma-separated, weights >= 0) for keyword ranking. Columns: path, title, folder, heading, content, summary. |
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | (none) | No | Embedding provider: openai, ollama, or fastembed. Unset auto-detects from the environment. |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | `nomic-embed-text` | No | Ollama embedding model name. |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | `false` | No | Force Ollama to embed on CPU only. |
| `MARKDOWN_VAULT_MCP_OPENAI_BASE_URL` | `https://api.openai.com/v1` | No | OpenAI-compatible API base URL for embeddings; the bare OPENAI_BASE_URL is honoured as a fallback. |
| `MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | No | OpenAI-compatible embedding model name; the bare OPENAI_EMBEDDING_MODEL is honoured as a fallback. |
| `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL` | `BAAI/bge-small-en-v1.5` | No | FastEmbed model name. |
| `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR` | (none) | No | FastEmbed model cache directory (in Docker, stored under /data/state/fastembed). |
| `MARKDOWN_VAULT_MCP_EMBED_CONTEXT` | `false` | No | Enrich embedding input with the note title, chunk heading, and (first chunk) searchable-field values. Flipping it re-embeds the whole vault once on next startup. |
| `MARKDOWN_VAULT_MCP_EMBED_TIMEOUT_S` | `30.0` | No | Per-request wall-clock budget in seconds for a single embedding HTTP call (OpenAI/Ollama). The local FastEmbed backend runs in-process with no network call and ignores this. CPU-only or large-model workloads may need 60-120 s; raise this if batches time out. |
| `MARKDOWN_VAULT_MCP_EMBEDDING_BATCH_SIZE` | `4` | No | Number of chunks sent per embedding request. Smaller batches shorten each request (useful under a tight timeout on slow models) at the cost of more round-trips. |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | (none) | No | Token/password for HTTPS git auth; remotes must be HTTPS when set. |
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | (none) | No | HTTPS remote URL for managed git mode: the server clones into an empty SOURCE_DIR on startup (or validates an existing origin) and enables the pull loop, auto-commit, and deferred push. |
| `MARKDOWN_VAULT_MCP_GIT_USERNAME` | `x-access-token` | No | Username for HTTPS git auth prompts (x-access-token for GitHub, oauth2 for GitLab, the account name for Bitbucket). |
| `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | `600` | No | Seconds between git fetch + fast-forward update attempts; 0 disables periodic pull. |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | `30.0` | No | Seconds of write-idle time before pushing; 0 pushes only on shutdown. |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | `markdown-vault-mcp` | No | Git committer name for auto-commits; set this in Docker where git config user.name is empty. |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | `noreply@markdown-vault-mcp` | No | Git committer email for auto-commits. |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` | (none) | No | OIDC claim key used as the commit author name (such as name); overrides GIT_COMMIT_NAME per request when an OIDC token is present. |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM` | (none) | No | OIDC claim key used as the commit author email (such as email); overrides GIT_COMMIT_EMAIL per request when an OIDC token is present. |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | `true` | No | Run git lfs pull on startup to fetch LFS-tracked attachments; set to false for repos without LFS. |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER` | `true` | No | Watch the vault for external filesystem changes; auto-disabled when git pull or the webhook is active. Requires the file-watcher extra. |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S` | `2.0` | No | Seconds of quiet after the last filesystem event before reindexing. |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER_ROOT_FLOOR` | `true` | No | Keep the non-recursive watch on the vault root; set false to register zero source-dir-rooted FSEvents streams (avoids repeated macOS access prompts on a home-rooted vault) at the cost of root-level files relying on scans. |
| `MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET` | (none) | No | Shared secret for the GitHub push-event webhook; when set, mounts POST /github-webhook on HTTP/SSE transports to trigger an immediate pull + reindex on push events. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_PROVIDER` | (none) | No | Summarization backend (only openai is recognised). Unset auto-detects: the backend activates when credentials or an explicit endpoint are present. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY` | (none) | No | API key for the OpenAI-compatible summarize endpoint; the bare OPENAI_API_KEY is honoured as a fallback. Unset works for keyless local endpoints (Ollama). |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL` | (none) | No | OpenAI-compatible endpoint base URL for the summarize tool; setting it enables the tool even without an API key. The bare OPENAI_BASE_URL routes traffic only when a key already enables the feature. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL` | `gpt-5-mini` | No | Chat model id used for summaries. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_TOKENS` | `8192` | No | Upper bound on generated tokens per summarize call; on reasoning models this budget also covers internal reasoning tokens. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_NOTES` | `50` | No | Cap on the number of notes summarised in one call (subtree expansion truncates to this many). |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_INPUT_CHARS` | `200000` | No | Aggregate cap on note characters sent to the model in one call; excess is truncated with a flag on the result. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_TIMEOUT` | `120.0` | No | Per-request wall-clock budget in seconds for a single summarize backend call; keep it below the MCP client's request timeout so the server-side error wins the race. |
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S` | `3600.0` | No | Link lifetime in seconds when the caller requests no explicit TTL. |
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S` | `86400.0` | No | Ceiling in seconds a caller-requested link TTL is clamped to. |
| `MARKDOWN_VAULT_MCP_TRANSFER_GRACE_TTL_S` | `60.0` | No | Post-success grace window in seconds: a served token's TTL shrinks to this so a stalled transfer can retry within it. |
| `MARKDOWN_VAULT_MCP_TRANSFER_LEASE_S` | `60.0` | No | Crashed-handler reclaim window in seconds for an in-flight reservation. |
| `MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES` | `104857600` | No | Maximum size in bytes of a single upload. |
| `MARKDOWN_VAULT_MCP_JOBS_SOFT_DEADLINE_S` | `25.0` | No | Seconds a long-running tool call may run in the foreground before it is promoted to a background job and a job handle is returned instead. |
| `MARKDOWN_VAULT_MCP_JOBS_RESULT_TTL_S` | `3600.0` | No | Seconds a background-job record (working or finished) is retained for polling before it expires from the store. |
| `MARKDOWN_VAULT_MCP_JOBS_MAX_PER_SUBJECT` | `256` | No | Maximum live background jobs per calling subject; further promotions are rejected until older records expire. |
<!-- GENERATED-ENV-TABLE-DOMAIN-END -->

Domain-config fields are composed inside `src/markdown_vault_mcp/config.py` between the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END` sentinels; env reads go through `fastmcp_pvl_core.env(_ENV_PREFIX, "SUFFIX", default)` so naming stays consistent, and field invariants go in `__post_init__` between the `CONFIG-VALIDATE-START` / `CONFIG-VALIDATE-END` sentinels. Each field's `metadata` `help` and `tags` generate the table above directly, so keep them accurate and complete. See the [configuration reference](https://pvliesdonk.github.io/markdown-vault-mcp/latest/configuration/) for the detailed prose documentation of every variable.

### Core settings

<!-- GENERATED-ENV-TABLE-CORE-START — generated by scripts/gen_config_surface.py; do not edit -->
| Variable | Default | Description |
|---|---|---|
| `MARKDOWN_VAULT_MCP_KV_STORE_URL` | `file:///data/state` | Persistent-state backend URL shared by every pvl-core subsystem that needs state. `memory://` is in-process and lost on restart; `file:///path` persists on one server; `redis://`, `dynamodb://` and `mongodb://` each need their matching extra. When unset, defaults to `file:///data/state` (the volume family Docker images mount), or to `memory://`; with a warning; on a host where that directory is not usable. |
| `FASTMCP_LOG_LEVEL` | `INFO` | Log level for FastMCP internals and app loggers (DEBUG / INFO / WARNING / ERROR / CRITICAL). The -v CLI flag overrides to DEBUG. |
| `FASTMCP_ENABLE_RICH_LOGGING` | `true` | Set false for plain or structured JSON log output. |
<!-- GENERATED-ENV-TABLE-CORE-END -->


### Search and embeddings

> The chunker's character cap (`MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS`) is derived from the embedding model's context length, so changing the embedding model re-chunks the FTS index (not just the embeddings) and triggers an automatic cold rebuild of the index on the next startup. The defaults stay memory-light (`BAAI/bge-small-en-v1.5` for FastEmbed, `nomic-embed-text` for Ollama); long-context models, such as `nomic-ai/nomic-embed-text-v1.5` (8192 tokens) for FastEmbed or `bge-m3:latest` for Ollama, are opt-in and need substantially more RAM/VRAM during indexing.

### Git integration

Git integration has three modes:

- **Managed mode** (`MARKDOWN_VAULT_MCP_GIT_REPO_URL` set): server owns repo setup.
  On startup it clones into `SOURCE_DIR` when empty, or validates existing `origin`.
  Pull loop + auto-commit + deferred push are enabled.
- **Unmanaged / commit-only mode** (no `GIT_REPO_URL`): writes are committed to a local git repo if `SOURCE_DIR` is already a git checkout. The server neither pulls nor pushes.
- **No-git mode**: if `SOURCE_DIR` is not a git repo, git callbacks are no-ops.

When token auth is used (`MARKDOWN_VAULT_MCP_GIT_TOKEN`), remotes must be HTTPS.
SSH remotes (such as `git@github.com:owner/repo.git`) are rejected with a startup error.
Fix with: `git -C /path/to/vault remote set-url origin https://github.com/owner/repo.git`

Backward compatibility: `MARKDOWN_VAULT_MCP_GIT_TOKEN` without `GIT_REPO_URL` still works (legacy mode) but logs a deprecation warning.


### File Watcher


Requires the `watchdog` optional extra: `pip install 'markdown-vault-mcp[file-watcher]'`. Automatically disabled when `GIT_PULL_INTERVAL_S > 0` or `GITHUB_WEBHOOK_SECRET` is set. The watcher scopes one recursive watch per non-excluded top-level directory (not a single recursive watch on the root), so excluded directories are never registered and content under a deliberately watched dot-directory delivers its own edits. See [docs/configuration.md](docs/configuration.md#file-watcher) for details.

### Attachments

Non-markdown file support. See [Attachments](#attachments) for details.


### Bearer token authentication

Simple static token auth for HTTP deployments. Set a single env var; clients must send `Authorization: Bearer <token>`.

| Variable | Required | Description |
|----------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BEARER_TOKEN` | Yes | Static bearer token; any non-empty string enables auth |

### OIDC authentication

Full OAuth 2.1 authentication for HTTP deployments. OIDC activates when all four required variables are set. See [Authentication](#authentication) for setup details.

> **Multi-auth:** If both `BEARER_TOKEN` and all OIDC variables are set, the server accepts **either** credential: a valid bearer token or a valid OIDC session. This is useful when different clients use different auth flows (such as Claude web via OIDC and Claude Code via bearer token).

| Variable | Required | Description |
|----------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | Yes | Public base URL of the server (such as `https://mcp.example.com`; include prefix if mounted under subpath, such as `https://mcp.example.com/vault`). Used for OIDC auth and to auto-compute the MCP Apps domain. |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | Yes | OIDC discovery endpoint (such as `https://auth.example.com/.well-known/openid-configuration`) |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | Yes | OIDC client ID registered with your provider |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | Yes | OIDC client secret |
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | No | JWT signing key. When unset, derived deterministically from the OIDC client secret, so tokens survive restarts; rotating the secret invalidates issued tokens. Set explicitly (generate with `openssl rand -hex 32`) to decouple token validity from secret rotation |
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
markdown-vault-mcp serve [--transport {stdio|sse|http}] [--host HOST] [--port PORT] [--http-path PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--transport` | `stdio` | MCP transport: `stdio` (stdin/stdout, default), `sse` (Server-Sent Events), `http` (streamable-HTTP). Use `http` for Docker with a reverse proxy or when OIDC is enabled. |
| `--host` | `127.0.0.1` | Bind host for the `http` transport (ignored for `stdio` and `sse`); pass `0.0.0.0` to bind all interfaces inside Docker |
| `--port` | `8000` | Port for the `http` transport (ignored for `stdio` and `sse`) |
| `--http-path` (alias `--path`) | env `MARKDOWN_VAULT_MCP_HTTP_PATH` or `/mcp` | MCP HTTP path for `http` transport; useful for reverse-proxy subpath mounting (such as `/vault/mcp`). The legacy `--path` spelling is still accepted. |

### Reverse Proxy Subpath Mounts

By default, HTTP transport serves MCP on `/mcp`. You can run it under a subpath:

```bash
markdown-vault-mcp serve --transport http --http-path /vault/mcp
```

Equivalent env-based config:

```bash
MARKDOWN_VAULT_MCP_HTTP_PATH=/vault/mcp
```

For reverse proxies, you can either:

- Keep app path at `/mcp` and use proxy rewrite/strip-prefix middleware.
- Set app path directly to the public path (`/vault/mcp`) and route without rewrite.

When OIDC is enabled under a subpath, the configuration is different: the subpath goes in `BASE_URL` only, and `HTTP_PATH` stays at `/mcp`. See [OIDC subpath deployments](https://pvliesdonk.github.io/markdown-vault-mcp/latest/deployment/oidc/#subpath-deployments).

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

Search the vault from the CLI.

```bash
markdown-vault-mcp search <query> [-n LIMIT] [-m {keyword|semantic|hybrid}] [--folder PATH] [--json]
```

### `reindex`

Incrementally reindex the vault (only processes changed files). When semantic search is configured, the vector index is converged to the updated chunk set: exactly the changed documents are re-embedded and orphaned vectors dropped, never the whole corpus.

```bash
markdown-vault-mcp reindex [--source-dir PATH] [--index-path PATH]
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search` | Hybrid full-text + semantic search with optional frontmatter filters |
| `read` | Read a document or attachment by relative path |
| `write` | Create or overwrite a document or attachment |
| `edit` | Replace text in a document: exact match, line-range, or scoped match with normalized fallback |
| `append` | Append text to the end of a note without reading it first (optional `create_if_missing`) |
| `delete` | Delete a document or attachment and its index entries |
| `rename` | Rename/move a document or attachment, updating all index entries; pass `update_links=true` to also rewrite backlinks in other notes |
| `move_folder` | Move an entire folder subtree to a new prefix, rewriting all vault links that point into the moved subtree in one call |
| `list_documents` | List indexed documents; pass `include_attachments=true` to also list non-markdown files |
| `list_folders` | List all folder paths in the vault |
| `list_tags` | List all unique frontmatter tag values |
| `reindex` | Incrementally reindex files changed outside the server; fast runs answer inline with real counts, slow runs promote to a job (`get_job_result`) |
| `stats` | Get vault statistics (document count, chunk count, link health metrics, etc.) |
| `build_embeddings` | Build or rebuild vector embeddings for semantic search; fast convergence answers inline, slow builds promote to a job (`get_job_result`) |
| `embeddings_status` | Check embedding provider and index status |
| `get_index_status` | Check background FTS build state (`queryable` / `building` / `failed`) |
| `get_backlinks` | Find all documents that link to a given document |
| `get_outlinks` | Find all links from a document, with existence check |
| `get_broken_links` | Find all links pointing to non-existent documents |
| `get_similar` | Find semantically similar notes by document path |
| `get_toc` | Heading outline for a note or a folder subtree |
| `get_recent` | Get the most recently modified notes |
| `get_context` | Get a consolidated context dossier for a note (backlinks, outlinks, similar, folder peers, tags, modified time) |
| `get_orphan_notes` | Find all notes with no inbound or outbound links |
| `get_most_linked` | Find the most-linked-to notes ranked by backlink count |
| `get_connection_path` | Find the shortest path between two notes via BFS on the undirected link graph (max 10 hops) |
| `summarize` | Summarize a note, a set of notes, or a folder subtree with an LLM; the synthesis references the individual source notes by path. Dual-mode: a task-capable MCP client runs it as a native background task, and for any other client a slow summary is promoted to a background job (retrieved via `get_job_result`) so the tool never hangs. Hidden unless an OpenAI-compatible backend is configured (`OPENAI_API_KEY` or a base URL). Sends note content to the configured backend; the `summarize-subtree` prompt is the client-side alternative that summarizes with the client's own model instead. |
| `get_job_result` | Retrieve the outcome of a background job started by a long-running tool (a promoted `summarize`, `reindex`, or `build_embeddings` call), by its `job_id`. Always registered. |
| `get_history` | List commits that touched a note, attachment, folder, or the whole vault (git-backed vaults only) |
| `get_diff` | Return a diff of a note or attachment between a reference commit/timestamp and HEAD; binary attachments return a `--stat` size summary instead of a unified patch (git-backed vaults only) |
| `git_sync` | Force an immediate git pull / push / both, bypassing the periodic loops. Returns structured state (SHAs, commit counts, Syncthing-style conflict file paths if any). Hidden when `MARKDOWN_VAULT_MCP_GIT_REPO_URL` isn't set or `READ_ONLY=true`. |
| `fetch` | Download a file from a URL and save it to the vault as a note or attachment (MCP-to-MCP transfer) |
| `create_download_link` | Mint a one-time capability URL to download a vault note or attachment (HTTP/SSE only; `BASE_URL` required) |
| `create_upload_link` | Mint a one-time capability URL to upload bytes to a fixed vault path (HTTP/SSE only; `BASE_URL` required; hidden when `READ_ONLY=true`) |
| `browse_vault` | Open the vault explorer SPA in a supporting MCP Apps client |
| `show_context` | Open the Context Card for a specific note in a supporting MCP Apps client |

Write tools (`write`, `edit`, `append`, `delete`, `rename`, `move_folder`, `fetch`, `git_sync`, the `okf_*` tools, `create_upload_link`) are registered by default and hidden when `MARKDOWN_VAULT_MCP_READ_ONLY=true`. `git_sync` also requires managed git mode (`MARKDOWN_VAULT_MCP_GIT_REPO_URL` set), and `create_upload_link` an HTTP transport with `BASE_URL` configured.

`summarize` is registered only when a summarization backend is configured: an `OPENAI_API_KEY`, or an OpenAI-compatible base URL for local endpoints that need no key. It needs the `openai` SDK (`pip install 'markdown-vault-mcp[summarize]'`) and sends note content to the model provider. Any OpenAI-compatible endpoint works: OpenAI itself, a local Ollama (`http://localhost:11434/v1`, no key needed), the Anthropic compatibility endpoint (`https://api.anthropic.com/v1`), vLLM, and others. See [Configuration](https://pvliesdonk.github.io/markdown-vault-mcp/latest/configuration/) for provider recipes.

`browse_vault` and `show_context` are LLM-visible in all clients; when called in an MCP Apps-capable client they open the interactive SPA. Six additional internal tools (`vault_context`, `vault_list`, `vault_read`, `vault_search`, `vault_graph_neighborhood`, `vault_graph_hubs`) use `visibility="app"` and are used by the SPA only; they are never visible to the LLM.

### Resources

MCP resources expose vault metadata as structured JSON that clients can read directly without invoking tools.

| URI | Description |
|-----|-------------|
| `config://vault` | Current vault configuration (source dir, indexed fields, read-only state, etc.) |
| `stats://vault` | Vault statistics (document count, chunk count, embedding count, etc.) |
| `tags://vault` | All frontmatter tag values grouped by indexed field |
| `tags://vault/{field}` | Tag values for a specific indexed frontmatter field (template) |
| `folders://vault` | All folder paths in the vault |
| `toc://vault/{path}` | Table of contents (heading outline) for a specific document (template) |
| `similar://vault/{path}` | Top 10 semantically similar notes for a document (template) |
| `recent://vault` | 20 most recently modified notes with ISO timestamps |
| `ui://markdown_vault_mcp/app.html` | Interactive vault explorer SPA for MCP Apps clients |

### Prompts

Prompt templates guide the LLM through multi-step workflows using the vault tools.

| Prompt | Parameters | Description |
|--------|------------|-------------|
| `summarize` | `path` | Read a document and produce a structured summary with key themes and takeaways |
| `summarize-subtree` | `paths`, `focus` (optional) | Summarize a folder subtree or set of notes with the client's own model, processing notes in batches so bodies stay out of the retained context (phases delegated to subagents when the client has them). Adapts to the server: when the `summarize` tool is registered the prompt opens by preferring it; when no backend is configured the prompt is the summarization route |
| `research` | `topic` | Search for a topic, synthesize findings, and create a new note at `research/{topic}.md` |
| `discuss` | `path` | Analyze a document and suggest improvements using `edit` (not `write`) |
| `create_from_template` | `template_name` (optional) | Discover templates (if needed), read a template, gather user values, and write a new note |
| `related` | `path` | Find related notes via search and suggest cross-references as markdown links |
| `compare` | `path1`, `path2` | Read two documents and produce a side-by-side comparison |
| `propose-links` | `scope` (optional), `per_note_limit` (optional) | Scan a candidate set of notes (a folder, `recent`, or `all`), propose links between semantically close notes that aren't already connected, and write them on confirmation |

Write prompts (`research`, `discuss`, `create_from_template`, `propose-links`) are hidden when `MARKDOWN_VAULT_MCP_READ_ONLY=true`.

Templates are regular markdown files. If placeholder template text pollutes search results, add your templates folder to `MARKDOWN_VAULT_MCP_EXCLUDE` (such as `_templates/**`).

### User-defined prompts

Mount a directory of `.md` prompt files to override or extend the built-in prompts. Set `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` to the path. Each file's frontmatter defines `description`, `arguments` (a list of objects, each with `name`, `description`, and `required` fields), and optional `tags`. A user prompt with the same name as a built-in replaces it.

For a complete example, including Zettelkasten capture, development, and review prompts, see the [Zettelkasten guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/zettelkasten/).
For an alternative action-oriented workflow (Projects, Areas, Resources, Archive with triage, kickoff, and weekly review prompts), see the [PARA guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/para/).

## MCP Apps

The server ships four browser-based views that MCP clients supporting the MCP Apps protocol can render inline or in fullscreen. They are delivered as a single HTML resource at `ui://markdown_vault_mcp/app.html` and registered using `visibility="app"` so they appear only in supporting clients and do not clutter the standard tool list. See the [MCP Apps guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/mcp-apps/) for details.

| View | Description |
|------|-------------|
| **Context Card** | Displays a note dossier (backlinks, outlinks, similar notes, tags) for the note currently in focus |
| **Graph Explorer** | Interactive force-directed link graph of the vault, powered by vis-network |
| **Vault Browser** | Searchable, filterable file tree for navigating the vault without issuing tool calls |
| **Note Preview** | Full-width markdown preview with a Contents popover, collapsible frontmatter properties and tags, copy-markdown / copy-vault-link controls, and a "Send to Claude" button |

The two primary tools exposed to MCP Apps clients are:

| Tool | Description |
|------|-------------|
| `browse_vault` | Returns the vault tree structure for the Vault Browser view |
| `show_context` | Returns the full context dossier for a given note path (used by the Context Card view) |

**Domain configuration:** MCP Apps iframes are sandboxed to a specific Claude app domain. The domain is auto-computed from `MARKDOWN_VAULT_MCP_BASE_URL`. Override with `MARKDOWN_VAULT_MCP_APP_DOMAIN` if your deployment is hosted on a custom domain or behind a proxy that changes the apparent hostname.

Vendored dependencies (JavaScript libraries bundled at build time, no runtime CDN): vis-network (graph rendering), marked.js (markdown rendering), DOMPurify (XSS sanitization), ext-apps SDK (MCP Apps lifecycle). The one runtime network dependency is web fonts (Newsreader, Public Sans, IBM Plex Mono), loaded from Google Fonts with system-font fallbacks.

## One-Time Transfer Links

`create_download_link` and `create_upload_link` mint short-lived capability URLs so vault files can move to a browser or another service without inflating the LLM context window. The token embedded in the URL is the only credential; no `Authorization` header is required on the `/transfer/{token}` route.

```
# Download a vault file
create_download_link(path="reports/q1.pdf", ttl_seconds=600)
# → {"url": "https://mcp.example.com/transfer/<token>", ...}
curl "https://mcp.example.com/transfer/<token>" -o q1.pdf

# Upload a file to the vault
create_upload_link(path="assets/new-diagram.png")
# → {"url": "https://mcp.example.com/transfer/<token>", ...}
curl -X POST --data-binary @new-diagram.png "https://mcp.example.com/transfer/<token>"
```

Each token grants exactly one operation. On success the link is **grace-settled**: its remaining lifetime shrinks to `MARKDOWN_VAULT_MCP_TRANSFER_GRACE_TTL_S` (default 60 seconds), so a stalled transfer can still retry. A failed or interrupted transfer releases the reservation with the full remaining TTL, so retry is permitted until the TTL expires.

Requirements: HTTP or SSE transport; `MARKDOWN_VAULT_MCP_BASE_URL` set. See the [transfer links guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/transfer-links/) for the full walkthrough and security model.


## Attachments

Beyond Markdown notes, the server can read, write, delete, rename, and list non-markdown files (PDFs, images, spreadsheets, etc.). All existing tools are overloaded; there are no new tool names.

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

1. **Multi-auth**: both bearer token and OIDC configured; either credential accepted (such as Claude web via OIDC + Claude Code via bearer token on the same instance)
2. **Bearer token**: set `MARKDOWN_VAULT_MCP_BEARER_TOKEN` to a secret string
3. **OIDC**: full OAuth 2.1 flow via `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, and `BASE_URL`
4. **No auth**: server accepts all connections (default)

**Auth requires `--transport http` (or `sse`).** It has no effect with `--transport stdio`.

For setup instructions, troubleshooting, and provider-specific guides, see the [Authentication guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/authentication/).

## Development

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
uv sync --all-extras --all-groups

# Run tests
uv run python -m pytest tests/ -x -q

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type check
uv run mypy src/ tests/
```
<!-- DOMAIN-END -->

## GitHub secrets

CI workflows reference three repository secrets. Configure them via **Settings → Secrets and variables → Actions** or with `gh secret set`:

| Secret | Used by | How to generate |
|---|---|---|
| `RELEASE_TOKEN` | `release-prepare.yml`, `release.yml`, `release-notes.yml`, `copier-update.yml`, `renovate.yml`, `bootstrap.yml` | Fine-grained PAT at <https://github.com/settings/personal-access-tokens/new> with `contents: write`, `pull_requests: write`, and `administration: write` (bootstrap applies the repository rulesets + auto-merge). Must belong to a repository admin: the shipped rulesets grant bypass to the admin role, and the release tag + GitHub release that knope creates after a release pull request merges rely on it (pull requests the token opens also need it so their CI runs). Scoped to this repo. |
| `CODECOV_TOKEN` | `ci.yml` | <https://codecov.io>: sign in with GitHub and add the repo. The upload token is on its settings page. |
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude.yml`, `claude-code-review.yml`, `release-notes.yml` | Run `claude setup-token` locally and paste the result. |

`GITHUB_TOKEN` is auto-provided; no action needed.

> Dependency updates are handled by **Renovate** (`renovate.yml`), which reuses
> `RELEASE_TOKEN`. It maintains `uv.lock` and auto-merges patch/minor bumps once
> the `CI Success` check is green; `bootstrap.yml` enables auto-merge and applies
> the repository rulesets (`.github/rulesets/`) on first push. See
> [Repository Protection](docs/deployment/repository-protection.md) for the
> per-branch posture and bypass model. GitHub Actions are updated in the copier
> template and arrive via `copier update`, not per-repo.

## Troubleshooting

### Moving a scaffolded project

`uv sync` creates `.venv/bin/*` scripts with absolute shebangs pointing at the venv Python. If you move the repo (`mv /old/path /new/path`), `uv run pytest` fails with `ModuleNotFoundError` because the stale shebang resolves to a different interpreter than the venv's site-packages.

**Fix:**

```bash
rm -rf .venv
uv sync --all-extras --all-groups
```

`uv run python -m pytest` also works as a one-shot workaround.

### `uv.lock` refresh after `copier update`

When `copier update` introduces new dependencies (such as a new extra added to `pyproject.toml.jinja`), the CI install step runs `uv sync --locked`, which fails against a stale lockfile. Run `uv lock` locally and commit the refreshed `uv.lock` alongside accepting the copier-update PR.

CI installs with `--locked` (and the review workflow with `--frozen`) so no job ever rewrites `uv.lock` in its own workspace: a job that re-locks hides the drift it just repaired, and a dirty workspace breaks any later `git checkout` in the same job. Lockfile drift then shows up as a red install step with a clear message, not as a silent mutation.

## Links

- [Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/)
- [llms.txt](https://pvliesdonk.github.io/markdown-vault-mcp/llms.txt)
- [FastMCP](https://gofastmcp.com)
- [fastmcp-pvl-core](https://pypi.org/project/fastmcp-pvl-core/)

<!-- ===== TEMPLATE-OWNED SECTIONS END ===== -->

## Upgrading from earlier versions

- **Package root minimized (issue #903): import from submodules, not the package root.**
  The `markdown_vault_mcp` root package no longer re-exports the public API. Update
  library imports to their submodules:

  ```python
  # Before: from markdown_vault_mcp import Vault, ProjectConfig
  # After:  from markdown_vault_mcp.vault import Vault
  #         from markdown_vault_mcp.config import ProjectConfig
  ```

  Types such as `GroupedResult` come from `markdown_vault_mcp.types`. Only
  `__version__` remains importable from the root.
- **v2.0.0 (issue #469): `search`, `get_similar`, and `get_context.similar` now return grouped results.**
  Each file appears once with a `sections` list; the flat `content`, `heading`, and `score`
  fields have moved inside each `SectionHit`. Library consumers must update iteration:

  ```python
  # Before: result.content, result.heading
  # After:  result.sections[0].content, result.sections[0].heading
  ```

  `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` replaces `MARKDOWN_VAULT_MCP_CHUNKS_PER_DOC`.
  `SimilarItem` is removed; use `GroupedResult` (from `markdown_vault_mcp.types`).
- `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` default lowered from **10 MB**
  to **1 MB**.  Most LLM contexts can't survive a 10 MB base64-encoded
  attachment; the old default was a silent context-blow-up. If you have
  non-LLM consumers (scripts, CI) that need the old behaviour, set
  `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB=10` explicitly.
- `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` is a **new** env var (default
  256 KB).  Whole-document `.md` reads above this raise `ValueError`.
  Partial reads via `read(path, section=heading)` bypass the cap.

## License

MIT
