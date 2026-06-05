# markdown-vault-mcp

A generic markdown vault [MCP](https://modelcontextprotocol.io/) server with FTS5 full-text search, semantic vector search, frontmatter-aware indexing, incremental reindexing, and non-markdown attachment support.

Point it at a directory of Markdown files — an Obsidian vault, a docs folder, a Zettelkasten, a PARA vault — and it exposes search, read, write, and edit tools over the [Model Context Protocol](https://modelcontextprotocol.io/).

<!-- DOMAIN-INDEX-FEATURES-START -->
## Features

- **Full-text search** — SQLite FTS5 with BM25 scoring, porter stemming
- **Semantic search** — cosine similarity over embedding vectors (FastEmbed, Ollama, or OpenAI)
- **Hybrid search** — Reciprocal Rank Fusion combining FTS5 and vector results
- **Frontmatter-aware** — indexes YAML frontmatter fields, supports required field enforcement
- **Incremental reindexing** — hash-based change detection, only re-processes modified files
- **Write operations** — create, edit, delete, rename documents with automatic index updates
- **Attachment support** — read, write, delete, and list non-markdown files (PDFs, images, etc.)
- **Git integration** — optional auto-commit and push on every write via `GIT_ASKPASS`
- **OIDC authentication** — optional token-based auth for HTTP deployments
- **MCP tools** — search, read, write, edit, delete, rename, link graph analysis, and admin operations
- **MCP resources** — vault configuration, statistics, tags, folders, document outlines, similar notes, and recent notes
- **MCP prompts** — summarize, research, discuss, create from template, compare, and find related notes
- **MCP Apps** — four browser-based views (Context Card, Graph Explorer, Vault Browser, Note Preview) for clients supporting the MCP Apps protocol
- **One-time transfer links** — mint short-lived capability URLs to move files into or out of the vault out-of-band over HTTP (`create_download_link` / `create_upload_link`; HTTP/SSE transports only)
<!-- DOMAIN-INDEX-FEATURES-END -->

<!-- DOMAIN-INDEX-USE-CASES-START -->
## What you can do with it

A few flows the server enables with an LLM on top — none of these require a bespoke prompt:

- **"Fetch <url> and summarize into a Resource note."** Claude composes `fetch` + `search` + `write`.
- **"Research <topic> and create a set of interlinked notes."** Claude composes web tools + `write` with wikilinks. See the [Research workflows guide](guides/research-workflows.md) for the full loop.
- **"Summarize today's conversations into Inbox notes."** Claude.ai composes `conversation_search` + `recent_chats` + `write`; the [`para-capture-chats`](guides/para.md#using-the-para-prompts) prompt is the one-click version.
- **Find missing links.** The [`propose-links`](prompts.md) builtin prompt scans recently-modified notes and proposes meaningful connections.

See [MCP Prompts](prompts.md) for the codified workflows and the ambient-pattern reference.

## Quick Start

### As a library

```python
from pathlib import Path
from markdown_vault_mcp import Vault

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

```bash
cp examples/obsidian-readonly.env .env
# Edit .env to set MARKDOWN_VAULT_MCP_SOURCE_DIR
docker compose up -d
```

### As a Claude Code plugin

```
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

See [Installation](installation.md) for all installation methods (PyPI, uv, Docker, Linux packages, Claude Code plugin) and [Configuration](configuration.md) for all available options.

## Architecture

The library is fully synchronous — no asyncio in core modules. The MCP server layer uses `asyncio.to_thread()` to bridge to the async FastMCP framework.

```
┌──────────────┐
│  MCP Server   │  ← FastMCP, asyncio.to_thread()
├──────────────┤
│  Vault        │  ← Thin facade / public API
├──────────────┤
│  Scanner      │  ← File discovery, frontmatter parsing, chunking
│  FTS Index    │  ← SQLite FTS5, BM25 scoring
│  Vector Index │  ← numpy embeddings, cosine similarity
│  Tracker      │  ← Hash-based change detection
│  Providers    │  ← Embedding provider ABC + implementations
│  Git          │  ← Auto-commit/push strategy
├──────────────┤
│  Config       │  ← Environment variable loading
└──────────────┘
```

## License

[MIT](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/LICENSE)
<!-- DOMAIN-INDEX-USE-CASES-END -->
