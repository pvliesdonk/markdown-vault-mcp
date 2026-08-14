# markdown-vault-mcp (Claude Code plugin)

MCP server for markdown vaults: FTS5 + semantic search, link graph,
write/edit/git.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) installed on your machine. The plugin's
  `.mcp.json` launches the server via `uvx`, which is distributed with `uv`.
- A markdown directory (or Obsidian vault) you want to query.

## Install

```bash
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

## Configure

When you enable the plugin, Claude Code shows a configuration screen for the
essentials:

- **Vault directory** (required) — path to your markdown (or Obsidian) vault.
- **Read-only mode** (default: on) — turn off to enable the write tools.
- **Embedding provider** (default: empty) — `fastembed` / `ollama` /
  `openai`; leave blank for keyword-only search.

To change these later, open `/plugin`, select the plugin, and edit its
configuration; restart Claude Code afterwards so the server relaunches with
the new values.

> **Upgrading?** Earlier plugin versions read these three settings from shell
> env vars (`MARKDOWN_VAULT_MCP_SOURCE_DIR` and friends). They now come from
> the configuration screen instead; shell exports for them no longer reach
> the plugin's server.

Additional settings come from optional shell environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `MARKDOWN_VAULT_MCP_EXCLUDE` | `.obsidian/**,.trash/**,.git/**` | Glob patterns to skip. |
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` / `MARKDOWN_VAULT_MCP_GIT_TOKEN` | *(empty)* | Git-backed vault sync; leave empty to disable. |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` / `MARKDOWN_VAULT_MCP_INDEX_PATH` | *(empty)* | Persist the embeddings sidecar / FTS index between restarts. |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API base URL (embedding provider `ollama` only). |

For the full list of env vars, see the
[Configuration reference](https://pvliesdonk.github.io/markdown-vault-mcp/configuration/).

## What you get

- **Tools:** `search`, `read`, `get_context`, `get_backlinks`, `get_outlinks`,
  `get_similar`, `get_connection_path`, `get_recent`, `list_documents`, and
  (in write mode) `write`, `edit`, `rename`, `delete`.
- **Skill:** `vault-workflow` tells Claude Code when to use hybrid vs.
  keyword search, when to call `get_context` before `read`, and how to use
  `rename(update_links=True)` correctly.
- **Prompts:** `summarize`, `summarize-subtree`, `research`, `discuss`,
  `related`, `compare` (available as slash commands via MCP prompt surfacing).

## Updating

```bash
/plugin update markdown-vault-mcp@pvliesdonk
```

## Documentation

Full docs: <https://pvliesdonk.github.io/markdown-vault-mcp/>
Issues: <https://github.com/pvliesdonk/markdown-vault-mcp/issues>
License: MIT.
