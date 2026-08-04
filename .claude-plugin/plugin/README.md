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

Enabling the plugin opens a configuration prompt. The only required field is
the vault directory; everything else has a sensible default or can stay
empty. Highlights of the screen:

| Setting | Default | What it sets |
|---|---|---|
| Vault directory | *(required)* | `MARKDOWN_VAULT_MCP_SOURCE_DIR` |
| Read-only mode | `false` | `MARKDOWN_VAULT_MCP_READ_ONLY`; set `true` to hide the write tools and serve a search-only vault |
| Exclude patterns | `.obsidian/**,.trash/**,.git/**` | `MARKDOWN_VAULT_MCP_EXCLUDE` |
| Embedding provider | *(empty)* | `fastembed` / `ollama` / `openai` / `voyage`; empty means keyword-only search |

Your answers persist across plugin updates, and sensitive fields (the OpenAI
API key, the git access token) are masked and stored in secure storage.
Restart Claude Code after configuring so the server starts with your values.

To change the configuration later, re-open the plugin's configuration from
the `/plugin` menu, or just ask Claude to set up or repair your vault (the
`vault-setup` skill walks through it).

Settings the screen does not wire stay reachable through env vars in the
`env` block of your user-scope `~/.claude/settings.json`; see the
[Configuration reference](https://pvliesdonk.github.io/markdown-vault-mcp/configuration/)
for the full list.

## What you get

- **Tools:** `search`, `read`, `get_context`, `get_backlinks`, `get_outlinks`,
  `get_similar`, `get_connection_path`, `get_recent`, `list_documents`, and
  (in write mode) `write`, `edit`, `rename`, `delete`.
- **Skills:** `vault-workflow` tells Claude Code when to use hybrid vs.
  keyword search, when to call `get_context` before `read`, and how to use
  `rename(update_links=True)` correctly; `vault-summarize` triggers on
  folder/multi-note summarization requests and runs the server's batched
  map-reduce recipe with parallel `vault-mapper` subagents, keeping note
  bodies out of the retained conversation context.
- **Agent:** `vault-mapper` — the summarize skill's map-phase worker,
  restricted to the vault read tools.
- **Configuration screen:** enabling the plugin prompts for the vault
  directory (required), read-only mode, embedding provider settings, and
  git sync — no shell-profile editing. Sensitive fields (API key, git
  token) are masked and stored securely. The screen is generated from the
  server's configuration surface, so it cannot drift from the code.
- **Bootstrap:** a `vault-setup` skill runs a guided discover → validate →
  write-config → restart flow for first-run setup and later repairs (moved
  vault, expired git token), and a SessionStart doctor hook
  (`scripts/doctor.sh`) tells you at session start when the server is down
  for configuration reasons — instead of a silent `Failed to connect` in
  `/mcp`. Healthy configs produce no output.
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
