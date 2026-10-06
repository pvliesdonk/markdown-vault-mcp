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
Give Claude, or any MCP client, a folder of Markdown notes to search, read and write. An Obsidian vault works as it is.

- **Hybrid search.** Keyword search with SQLite FTS5 and, once an embedding provider is configured, search by meaning, fused by Reciprocal Rank Fusion. Results are short snippets; `read` fetches the whole section. See [Embeddings](docs/use/embeddings.md).
- **Frontmatter as data.** YAML frontmatter fields become search filters, and long notes are split at their headings.
- **Careful writes.** The write tools (`write`, `edit`, `append`, `delete`, `rename`, `move_folder`, `fetch`, `git_sync`, the `okf_*` tools, `create_upload_link`) are on by default and hidden when `MARKDOWN_VAULT_MCP_READ_ONLY=true`. Replacing a file takes the etag from reading it, and per-folder `_conventions.md` rules reach the client as it writes.
- **Git.** Optional commit per write with a delayed push, pull or webhook sync, and history and diffs; an overwritten note can be read back at the revision it replaced. See [Git integration](docs/use/git-integration.md).
- **Links.** Backlinks, outlinks, broken links and the path between two notes, for wikilinks and Markdown links alike, with interactive views in clients that render [MCP Apps](docs/deploy/mcp-apps.md). With `MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS=true`, `write`, `edit` and `append` name each link they added that doesn't resolve.
- **Open Knowledge Format.** OKF bundles are recognized, and results carry each note's type, status and trust tier. See [OKF](docs/use/okf.md).

The [tools reference](docs/reference/tools/index.md) lists every tool. The same engine is a Python library: see the [Vault API](docs/reference/api/vault.md).
<!-- DOMAIN-README-PITCH-END -->

## Does it fit?

What the server can reach, what it changes and who gets in is set out in the [security model](docs/security-model.md); the block below says who it serves and where it stops.

<!-- DOMAIN-README-FIT-START -->
It suits one person or a small team who keep notes as Markdown files and want Claude to search them, follow their links and write back into them, on their own machine or as a shared server. It reaches the vault folder, plus only what you configure: a git remote, an embedding or summarizing model, and the URLs a `fetch` call names.

What it assumes:

- **One vault per server.** Several vaults take one server each, for now ([#1232](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1232)); give each a `MARKDOWN_VAULT_MCP_SERVER_NAME`.
- **Markdown is what gets searched.** Other files, such as PDFs and images, can be read and written as attachments, but their contents are not indexed yet ([#1234](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1234)).
- **Embeddings live in memory.** Search by meaning holds every vector at 4 bytes × chunks × dimensions: about 70 MB for 23,000 chunks at 768 dimensions, about 900 MiB at ten times that and 1,024 dimensions. Memory, not query time, is the first limit ([#1377](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1377)).
- **State sits on local disk.** The index and embeddings are files, and the change-tracking file sits beside the index, never inside the vault. Without `MARKDOWN_VAULT_MCP_INDEX_PATH`, the index and that file stay in memory and are rebuilt at each start.

Reach for something else for a corpus of hundreds of thousands of chunks (a vector database behind a retrieval pipeline), for mostly scanned or office documents (a document management system with text recognition), or for many users who must not see each other's notes (a multi-tenant knowledge platform): every caller the server admits gets every tool it exposes.
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
The plain package covers keyword search, the write tools and git. Search by meaning, the file watcher and the `summarize` tool need extras; `[all]` installs every one:

```bash
uv tool install "markdown-vault-mcp[all]"
```

The Docker image, the `.mcpb` bundle and the Claude Code plugin already include them. [Installation](docs/get-started/installation.md#notes-for-this-server) lists each extra.
<!-- DOMAIN-README-EXTRAS-END -->

## Configuration

Everything is configured through environment variables with the `MARKDOWN_VAULT_MCP_` prefix. The ones most installs set:

<!-- GENERATED-ENV-TABLE-DOMAIN-START — generated by scripts/gen_config_surface.py; do not edit -->
| Variable | Default | Required | Description |
|---|---|---|---|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | `/data/vault` | No | Path to the markdown vault directory. When it does not exist, or the server cannot access it, the server starts but every tool fails with a message saying which until it is fixed (managed git mode clones into it). Symbolic links inside the vault are followed on Python 3.13+. |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `false` | No | Set to true to hide the write tools (write, edit, append, delete, rename, move_folder, fetch, git_sync, the okf_* tools, create_upload_link) and serve a search-only vault. |
| `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING` | `true` | No | Refuse a write that would overwrite an existing file when no if_match etag is supplied. Deliberate replacement still works: read the file first, then pass if_match. Unaffected: edit, append, delete, rename. Set to false to allow blind overwrites. |
| `MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS` | `false` | No | Set to true to have write, edit and append list, under unresolved_links, the target of each link the call added that get_broken_links reports. Links the note already held are left out. Off by default, leaving those responses unchanged. |
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
- **The library is synchronous**; the MCP layer wraps calls in `asyncio.to_thread()`. File writes return after saving, while index updates run in the background. Index-dependent mutations wait for prior writes; see [index freshness](docs/reference/api/vault.md#index-freshness-after-writes).
- **Indexing is hash-based**: unchanged files are never re-parsed, and any change to how stored rows derive from a note's bytes bumps `INDEX_SEMANTICS_VERSION` so deployed vaults rebuild themselves once on upgrade.

The full decision log lives in the [design document](docs/design/design.md).
<!-- DOMAIN-README-DESIGN-END -->

## Links

- [Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/) and its [llms.txt](https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt)
- [FastMCP](https://gofastmcp.com) and [fastmcp-pvl-core](https://pypi.org/project/fastmcp-pvl-core/), which this server is built on
- [fastmcp-server-template](https://github.com/pvliesdonk/fastmcp-server-template), which generated this repository
