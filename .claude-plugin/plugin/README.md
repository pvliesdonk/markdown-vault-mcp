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

Set `MARKDOWN_VAULT_MCP_SOURCE_DIR` in your shell profile before starting
Claude Code. On macOS/Linux:

```bash
echo 'export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/your/vault' >> ~/.zshrc
```

On Windows PowerShell:

```powershell
[Environment]::SetEnvironmentVariable(
    "MARKDOWN_VAULT_MCP_SOURCE_DIR",
    "C:\Users\You\Vault",
    "User")
```

Optional environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `true` | Disable write tools. |
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | *(empty)* | `fastembed` / `ollama` / `openai`. Leave blank for keyword-only search. |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | `.obsidian/**,.trash/**,.git/**` | Glob patterns to skip. |

For the full list of env vars, see the
[Configuration reference](https://pvliesdonk.github.io/markdown-vault-mcp/configuration/).

## What you get

- **Tools:** `search`, `read`, `get_context`, `get_backlinks`, `get_outlinks`,
  `get_similar`, `get_connection_path`, `get_recent`, `list_documents`, and
  (in write mode) `write`, `edit`, `rename`, `delete`.
- **Skill:** `vault-workflow` tells Claude Code when to use hybrid vs.
  keyword search, when to call `get_context` before `read`, and how to use
  `rename(update_links=True)` correctly.
- **Prompts:** `summarize`, `research`, `discuss`, `related`, `compare`
  (available as slash commands via MCP prompt surfacing).

## Updating

```bash
/plugin update markdown-vault-mcp@pvliesdonk
```

## Documentation

Full docs: <https://pvliesdonk.github.io/markdown-vault-mcp/>
Issues: <https://github.com/pvliesdonk/markdown-vault-mcp/issues>
License: MIT.
