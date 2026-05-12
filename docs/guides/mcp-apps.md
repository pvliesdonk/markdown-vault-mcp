# MCP Apps

The server ships four browser-based views that MCP clients supporting the [MCP Apps protocol](https://modelcontextprotocol.io/specification/2025-06-18/server/apps) can render inline or in fullscreen. They are delivered as a single HTML resource at `ui://vault/app.html` and use `visibility="app"` so they appear only in supporting clients.

## Views

### Context Card

Displays a note dossier for the note currently in focus. Includes:

- **Backlinks** — other notes that link to this note
- **Outlinks** — notes this document links to, with existence check
- **Similar notes** — semantically similar documents (requires embeddings)
- **Tags** — frontmatter tag values
- **Folder peers** — other notes in the same folder
- **Modification time** — last modified timestamp

Click any linked note to navigate to its context card.

### Graph Explorer

Interactive force-directed link graph of the vault, powered by vis-network. Two modes:

- **Neighborhood** — shows a note and its direct connections (configurable depth, soft-capped at `max_nodes=200` by default; the response carries `truncated=true` when the BFS hit the cap so the SPA can warn the user)
- **Hubs** — shows the most-linked notes in the vault and their connections

Click a node to view that note's context card. Toggle semantic similarity edges when embeddings are available.

### Vault Browser

Searchable, filterable file tree for navigating the vault without issuing tool calls. Features:

- Expandable folder tree
- Real-time search filtering
- Click a note to preview it in the Note tab

### Note Preview

Full-width markdown preview with:

- Rendered markdown (via marked.js, sanitized with DOMPurify)
- Frontmatter table
- **Send to Claude** button — sends the note content to the LLM conversation
- Navigation to Context Card or Graph Explorer for the same note

## Tools

Two primary tools are exposed to MCP clients:

| Tool | Description |
|------|-------------|
| `browse_vault` | Opens the vault explorer SPA; optionally focuses a specific note and view. Returns a text summary for non-Apps clients. |
| `show_context` | Opens the Context Card for a given note path. |

`browse_vault` accepts an optional `path` parameter to focus on a specific note, and a `view` parameter (`context`, `graph`, `browse`, or `note`). `show_context` requires a `path` parameter.

Six additional app-only tools (prefixed with `_vault_`) handle data fetching for the SPA views. These are hidden from the LLM and only callable from within the app itself.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | — | Public base URL of the server. Required for auto-computing the app domain. |
| `MARKDOWN_VAULT_MCP_APP_DOMAIN` | (auto) | Override the Claude app domain used for iframe sandboxing. Auto-computed from `BASE_URL` when not set. |

### Domain auto-computation

When `APP_DOMAIN` is not set, the server computes it from `BASE_URL` and `HTTP_PATH`:

1. Concatenate: `mcp_url = f"{BASE_URL}{HTTP_PATH}"`
2. SHA-256 hash the URL and take the first 32 hex characters
3. Result: `{hash_prefix}.claudemcpcontent.com`

Override `APP_DOMAIN` if your deployment is behind a proxy that changes the apparent hostname.

## Architecture

The SPA is a self-contained HTML file with all dependencies vendored at build time (no runtime CDN requests):

| Library | Purpose |
|---------|---------|
| vis-network | Force-directed graph rendering |
| marked.js | Markdown to HTML rendering |
| DOMPurify | XSS sanitization |
| @modelcontextprotocol/ext-apps | MCP Apps lifecycle, messaging, and theming |

The app integrates with the host client via the ext-apps SDK:

- **`app.callServerTool()`** — calls app-only tools to fetch data
- **`app.sendMessage()`** — sends note content to the LLM conversation
- **`app.updateModelContext()`** — keeps the LLM aware of which note the user is viewing
- **`app.requestDisplayMode()`** — requests fullscreen or inline display
- **Theme sync** — automatically adapts to the host's light/dark theme and CSS variables

## Client support

MCP Apps views require a client that supports the MCP Apps protocol. Currently supported by Claude on claude.ai. Clients without Apps support receive a text-only fallback from `browse_vault` and `show_context`.

### Firing prompts from Claude.ai's `+` menu

MCP Apps surface interactive views, but this server also ships MCP *prompts* — `summarize`, `research`, `propose-links`, and the workflow prompts from the PARA and Zettelkasten packs. On Claude.ai, every prompt appears in the compose area's `+` menu once the server is added as a connector. Click `+`, select **connectors**, pick the server, pick a prompt — Claude opens with the invocation scaffolded. See [How to invoke prompts](../prompts.md#how-to-invoke-prompts).
