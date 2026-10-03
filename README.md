<!-- DOMAIN-README-BADGES-START -->
<p align="center">
  <img src="assets/icon.svg" alt="Markdown Vault MCP logo" width="128" height="128">
</p>
<!-- DOMAIN-README-BADGES-END -->

# Markdown Vault MCP

<!-- mcp-name: io.github.pvliesdonk/markdown-vault-mcp -->

[![CI](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml) [![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=pvliesdonk_markdown-vault-mcp&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=pvliesdonk_markdown-vault-mcp) [![Coverage](https://sonarcloud.io/api/project_badges/measure?project=pvliesdonk_markdown-vault-mcp&metric=coverage)](https://sonarcloud.io/summary/new_code?id=pvliesdonk_markdown-vault-mcp) [![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=pvliesdonk_markdown-vault-mcp&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=pvliesdonk_markdown-vault-mcp) [![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=pvliesdonk_markdown-vault-mcp&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=pvliesdonk_markdown-vault-mcp) [![PyPI](https://img.shields.io/pypi/v/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![Python](https://img.shields.io/pypi/pyversions/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![License](https://img.shields.io/github/license/pvliesdonk/markdown-vault-mcp)](LICENSE) [![Docker](https://img.shields.io/github/v/release/pvliesdonk/markdown-vault-mcp?label=ghcr.io&logo=docker)](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp) [![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://pvliesdonk.github.io/markdown-vault-mcp/) [![llms.txt](https://img.shields.io/badge/llms.txt-available-brightgreen)](https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt) [![Template](https://img.shields.io/badge/dynamic/yaml?url=https://raw.githubusercontent.com/pvliesdonk/markdown-vault-mcp/main/.copier-answers.yml&query=%24._commit&label=template)](https://github.com/pvliesdonk/fastmcp-server-template)

Generic markdown vault MCP with hybrid search

**[Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/)** | **[Config wizard](https://pvliesdonk.github.io/markdown-vault-mcp/latest/reference/configuration-generator/)** | **[PyPI](https://pypi.org/project/markdown-vault-mcp/)** | **[Docker](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp)**

<!-- DOMAIN-README-PITCH-START -->
- **Hybrid search**: SQLite FTS5 keyword search (BM25, porter stemming) and semantic search (FastEmbed, Ollama, OpenAI, or Voyage AI embeddings, plus any OpenAI-compatible endpoint via `OPENAI_BASE_URL`), fused with Reciprocal Rank Fusion; diversity-aware ranking returns sentence-scale snippets with full-section recovery via `read(path, section=heading)`. See the [Embeddings guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/embeddings/), including the [recipe for OpenAI-compatible endpoints](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/embeddings/#openai-compatible-endpoints).
- **Frontmatter-aware indexing**: YAML frontmatter fields become filterable and searchable, with optional required-field enforcement and adaptive heading-level chunking for long documents.
- **Write operations**: the write tools (`write`, `edit`, `append`, `delete`, `rename`, `move_folder`, `fetch`, `git_sync`, the `okf_*` tools, `create_upload_link`) are registered by default and hidden when `MARKDOWN_VAULT_MCP_READ_ONLY=true`; writes update the index automatically, per-folder `_conventions.md` authoring rules are surfaced to LLM clients at write time, and attachments (PDFs, images, and other non-markdown files) are read/write too.
- **Incremental reindexing**: hash-based change detection with boot-time reconciliation; the vector index converges to the reconciled chunk set, and parse-pipeline upgrades rebuild the index once automatically.
- **Git integration**: optional auto-commit (one commit per write tool call) with deferred push, plus a pull loop or a GitHub or GitLab push webhook for external changes; history and diff tools read the log back. An overwriting `write` returns the revision holding the content it replaced, and `read(path, revision=sha)` reads a note back at that revision, so an overwrite is recoverable from the client that made it. When the clone stops reaching its remote, every write result carries a `remote` warning saying the content is committed locally only, and the log marks the transition rather than repeating each cycle. See the [Git integration guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/git-integration/).
- **OKF-aware**: recognizes [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog) bundles and annotates results with each note's type, lifecycle status, staleness, and trust tier, plus conformance audit and migration tooling. Static bearer writes use tool provenance; human review through a bearer credential requires confirmation with `okf_verify` in `elicit` mode. See the [OKF guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/okf/).
- **MCP surface**: 34 LLM-visible tools, 9 resources, and 8 prompt templates, plus browser-based MCP Apps views and one-time transfer links. Full references: [Tools](https://pvliesdonk.github.io/markdown-vault-mcp/latest/tools/), [Resources](https://pvliesdonk.github.io/markdown-vault-mcp/latest/resources/), [Prompts](https://pvliesdonk.github.io/markdown-vault-mcp/latest/prompts/), [MCP Apps](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/mcp-apps/), [Transfer links](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/transfer-links/), [CLI](https://pvliesdonk.github.io/markdown-vault-mcp/latest/cli/).

Overwrite protection is enabled by default. Before replacing an existing file
with `write` or `fetch`, read that destination and pass its etag as `if_match`.
New files need no etag. Transfer upload links require a
new destination because they have no `if_match` option. Set
`MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING=false` to allow blind overwrites;
see the [transfer guide](docs/guides/transfer-links.md#upload-walkthrough).

Python integrations use `VaultSettings` for configuration. See the
[Vault API](docs/api/vault.md#migrating-from-4x) for the migration from the
removed 4.x constructor keywords and the
[configuration API](docs/api/config.md#migrating-from-4x) for typed assembly.
The [Git API](docs/api/git.md#migrating-from-4x) covers removal of the deprecated
strategy claim keywords and the keyword-only LFS and repository options.
<!-- DOMAIN-README-PITCH-END -->

## Does it fit?

What the server can reach, what it changes and who gets in is set out in the [security model](docs/security-model.md); the block below says who it serves and where it stops.

<!-- DOMAIN-README-FIT-START -->
With this server mounted in Claude, you can:

- **Capture a URL as a note.** "Fetch <url>, summarize as a Resource note under `3-Resources/`, and link any existing notes on the topic." Claude composes `fetch` + `search` + `write`.
- **Research a topic into your vault.** "Research product security regulations, compare them, and create a set of interlinked notes: one per regulation, plus a map-of-content." Claude composes web-search tools (client-side) + `write` with wikilinks. See the [Research workflows guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/research-workflows/) for the full loop.
- **Distill today's thinking.** "Summarize today's conversations into Inbox notes." Claude.ai only; uses `conversation_search` + `recent_chats` + `write`. The [`para-capture-chats`](examples/para/prompts/para-capture-chats.md) prompt is the one-click version.
- **Find missing links.** Fire the [`propose-links`](https://pvliesdonk.github.io/markdown-vault-mcp/latest/prompts/#propose-links) prompt from the `+` menu: it scans recently modified notes and proposes links between notes that aren't yet connected, writing them on confirmation.
- **Split or merge captures.** "Split this Inbox note into two." / "Merge this into `<existing note>` instead of duplicating." Claude composes `read` + `write` + `delete`.

The vault needs no external scheduler or separate capture app: it sits behind your conversations and absorbs their output.
<!-- DOMAIN-README-FIT-END -->

## Quick start

Pick the client you use. Each line installs the released version; the [Get started](docs/get-started/index.md) tutorials carry on from there.

**Claude Desktop.** Download the `.mcpb` bundle from the [releases page](https://github.com/pvliesdonk/markdown-vault-mcp/releases) and open it with Claude Desktop (or **Settings** › **Extensions** › **Advanced settings** › **Install Extension…**). Claude Desktop asks for the required settings itself. [Tutorial](docs/get-started/claude-desktop.md).

**Claude Code.** Two commands inside Claude Code; the second asks for a scope. [Tutorial](docs/get-started/claude-code.md).

```text
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

**A client that runs a command** (stdio). To register it in Claude Code, see the [Claude Code](docs/get-started/claude-code.md) tutorial.

```bash
uv tool install "markdown-vault-mcp"
markdown-vault-mcp serve
```

**A server for remote clients** (streamable HTTP). [A remote server](docs/get-started/http-client.md) connects the clients.

```bash
docker run --rm -p 8000:8000 --env-file .env ghcr.io/pvliesdonk/markdown-vault-mcp:latest
```

A `compose.yml` ships at the repository root and runs as-is: copy `.env.example` to `.env`, then `docker compose up -d`. [Deploy](docs/deploy/index.md) covers authentication, OIDC, a reverse proxy and system packages (`.deb`/`.rpm` on the releases page). The server answers `/health` and `/health/ready` outside the MCP mount, and its `get_server_info` tool reports the running version.

<!-- DOMAIN-README-EXTRAS-START -->
```bash
pip install "markdown-vault-mcp[mcp]"             # FastMCP server
pip install "markdown-vault-mcp[embeddings-api]"  # Ollama/OpenAI embeddings via API
pip install "markdown-vault-mcp[embeddings]"      # FastEmbed local embeddings
pip install "markdown-vault-mcp[file-watcher]"    # watchdog-based external-change watcher
pip install "markdown-vault-mcp[all]"             # MCP + FastEmbed + API embeddings
```

For the Claude Code plugin channel (`/plugin install markdown-vault-mcp@pvliesdonk`) and all other install routes, see the [Installation guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/installation/) and the [Claude Code plugin guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/claude-code-plugin/).
<!-- DOMAIN-README-EXTRAS-END -->

## Configuration

Everything is configured through environment variables with the `MARKDOWN_VAULT_MCP_` prefix. The ones most installs set:

<!-- GENERATED-ENV-TABLE-DOMAIN-START — generated by scripts/gen_config_surface.py; do not edit -->
| Variable | Default | Required | Description |
|---|---|---|---|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | `/data/vault` | No | Path to the markdown vault directory. When it does not exist, or the server cannot access it, the server starts but every tool fails with a message saying which until it is fixed (managed git mode clones into it). Symbolic links inside the vault are followed on Python 3.13+. |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `false` | No | Set to true to hide the write tools (write, edit, append, delete, rename, move_folder, fetch, git_sync, the okf_* tools, create_upload_link) and serve a search-only vault. |
| `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING` | `true` | No | Refuse a write that would overwrite an existing file when no if_match etag is supplied. Deliberate replacement still works: read the file first, then pass if_match. Unaffected: edit, append, delete, rename. Set to false to allow blind overwrites. |
| `MARKDOWN_VAULT_MCP_DEFAULT_SEARCH_MODE` | `auto` | No | Mode used when a search call omits 'mode': auto, keyword, semantic, or hybrid. The default 'auto' picks hybrid when embeddings are configured and keyword when they are not. Pin 'keyword' to keep unqualified searches off the embedding provider (each hybrid or semantic search embeds the query, which costs an API call on a metered provider). A configured semantic/hybrid default also degrades to keyword without embeddings, so no setting can make a vault unsearchable; an explicit mode= argument is never downgraded. |
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | (none) | No | Embedding provider: openai, voyage, ollama, or fastembed. Unset auto-detects from the environment (never voyage). Any OpenAI-compatible endpoint works with openai plus OPENAI_BASE_URL; see the embeddings guide. |
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | (none) | No | HTTPS remote URL for managed git mode: the server clones into an empty SOURCE_DIR on startup (or validates an existing origin) and enables the pull loop, auto-commit, and deferred push. |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER` | `true` | No | Watch the vault for external filesystem changes; auto-disabled when git pull is active or a webhook can deliver (HTTP/SSE transports only). Requires the file-watcher extra. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL` | (none) | No | OpenAI-compatible endpoint base URL for the summarize tool; setting it enables the tool even without an API key. The bare OPENAI_BASE_URL routes traffic only when a key already enables the feature. |
<!-- GENERATED-ENV-TABLE-DOMAIN-END -->

Every variable the server reads, the shared ones included, is in the [configuration reference](docs/reference/configuration.md); `.env.example` lists the same surface in copy-paste form, and the [config wizard](https://pvliesdonk.github.io/markdown-vault-mcp/latest/reference/configuration-generator/) writes one for your deployment.

## Documentation

- [Security model](docs/security-model.md): what the server can reach, what it changes and who gets in.
- [Get started](docs/get-started/index.md): a first success with your client.
- [Deploy](docs/deploy/index.md): Docker, authentication, OIDC, reverse proxy.
- [Use](docs/use/index.md): the features, for real tasks.
- [Reference](docs/reference/configuration.md): configuration, [tools](docs/reference/tools/index.md), resources, prompts, command line.
- [Upgrade](docs/upgrade/index.md): release channels, and what an upgrade changes for your clients and your data.
- [Contribute](docs/contribute/index.md): local development, secrets, where a fix belongs; `CONTRIBUTING.md` and `SECURITY.md` at the root.

## Design decisions

<!-- DOMAIN-README-DESIGN-START -->
- **Document identity is the relative path** with `.md` extension; frontmatter is optional by default (`REQUIRED_FIELDS` opts into enforcement).
- **Hybrid search uses Reciprocal Rank Fusion** over the FTS5 and vector result lists, with diversity-aware ranking capping chunks per document.
- **Tool semantics mirror Claude Code's Read/Write/Edit patterns**, so LLM clients drive the vault with habits they already have.
- **The library is synchronous**; the MCP layer wraps calls in `asyncio.to_thread()`. File writes return after saving, while index updates run in the background. Index-dependent mutations wait for prior writes; see [index freshness](docs/api/vault.md#index-freshness-after-writes).
- **Indexing is hash-based**: unchanged files are never re-parsed, and any change to how stored rows derive from a note's bytes bumps `INDEX_SEMANTICS_VERSION` so deployed vaults rebuild themselves once on upgrade.

The full decision log lives in the [design document](docs/design/design.md).
<!-- DOMAIN-README-DESIGN-END -->

## Links

- [Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/) and its [llms.txt](https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt)
- [FastMCP](https://gofastmcp.com) and [fastmcp-pvl-core](https://pypi.org/project/fastmcp-pvl-core/), which this server is built on
- [fastmcp-server-template](https://github.com/pvliesdonk/fastmcp-server-template), which generated this repository
