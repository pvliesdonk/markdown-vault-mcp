---
name: vault-setup
description: Use when the markdown-vault MCP server is down or misconfigured — vault not set up yet, vault directory moved or renamed, git token expired — or when the user asks to set up, connect, or repair their markdown vault. Runs a guided discover → validate → write-config → restart flow.
---

# Setting up or repairing the markdown vault connection

The vault MCP server refuses to start without a valid
`MARKDOWN_VAULT_MCP_SOURCE_DIR`, and a broken value shows up only as a dead
`Failed to connect` entry in `/mcp`. This flow fixes both first-run setup
and later-life breakage (moved vault, expired git token). The vault tools
are down while you run it — use only local file access.

## 1. Diagnose

Read the effective configuration before changing anything. The plugin's
`.mcp.json` wires the server to plugin `userConfig` values, so the
authoritative source is the plugin's stored configuration:

- `CLAUDE_PLUGIN_OPTION_SOURCE_DIR` in the environment, and the persisted
  copy under `pluginConfigs` (the key containing `markdown-vault-mcp`) →
  `options.source_dir` in `~/.claude/settings.json`.
- Legacy fallbacks from installs that predate the config screen: the
  `env` block of `~/.claude/settings.json`, then the shell environment.
  If a legacy value disagrees with the plugin option, the plugin option
  is the one the server uses — say so.

If the directory is set and exists, the problem is elsewhere (embedding
provider, git token) — skip to step 4.

## 2. Discover candidate vaults

Look for likely vault directories and present what you find rather than
asking the user to type a path cold:

- `.obsidian/` markers: check common roots such as `~/Documents`,
  `~/Obsidian`, `~/Notes`, `~/vaults`, one level deep.
- Directories whose names suggest notes (`vault`, `notes`, `wiki`,
  `zettelkasten`) containing `.md` files.
- If the old configured path exists nearby under a new name (moved vault),
  suggest the match.

Offer the candidates; let the user pick or supply another path. Expand `~`.

## 3. Validate and write

Validate the choice before persisting: the directory must exist, be
readable, and (warn, do not block, if not) contain at least one `.md` file.

Then write the value where the plugin's `.mcp.json` substitution reads it —
the plugin's stored options in the **user-scope** `~/.claude/settings.json`
(project-scope settings are ignored for plugin configuration). Find the
existing `pluginConfigs` key containing `markdown-vault-mcp` (or create
one matching how other plugins are keyed there) and set:

```json
{
  "pluginConfigs": {
    "<existing markdown-vault-mcp key>": {
      "options": {
        "source_dir": "/absolute/path/to/vault"
      }
    }
  }
}
```

Merge into the existing file — read it first, preserve every other key, and
show the user the exact change before writing. Re-enabling the plugin also
re-opens the interactive configuration prompt if the user prefers a dialog
over an edit. Sensitive values (API keys, tokens) belong in the prompt's
masked fields, not in plain-text settings — say so whenever one comes up.

## 4. Later-life repairs

- **Moved vault**: same flow; re-point the plugin's `source_dir` option.
- **Git sync failing / token expired**: a new fine-grained token scoped to
  the vault repository goes in the plugin's `git_token` option — it is
  marked sensitive, so entering it through the plugin's configuration
  prompt stores it in secure storage instead of a plain-text file.
- **Embeddings misconfigured**: `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`
  plus its provider settings (`OLLAMA_HOST`, `OPENAI_API_KEY`, ...); an
  empty provider disables semantic search but the server still runs.

## 5. Finish: restart is the floor

MCP servers only start at session start; nothing picks up new configuration
mid-session. End every run of this flow by telling the user to **restart
Claude Code**, and that the vault tools will appear once `/mcp` shows
markdown-vault-mcp connected. Do not claim success before then.
