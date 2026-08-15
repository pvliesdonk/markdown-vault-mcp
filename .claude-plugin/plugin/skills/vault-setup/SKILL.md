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

Read the effective configuration before changing anything:

- The shell environment: `MARKDOWN_VAULT_MCP_SOURCE_DIR` and friends.
- The `env` block of `~/.claude/settings.json`, which also reaches the
  server process and **takes precedence over the shell environment** —
  surprising to users who configured a shell profile first; say so if both
  are set and disagree.

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

Then write the value where the plugin's `.mcp.json` expansion will find it —
the **user-scope** `~/.claude/settings.json` `env` block (project-scope
settings are not appropriate for a per-user vault path):

```json
{
  "env": {
    "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/absolute/path/to/vault"
  }
}
```

Merge into the existing file — read it first, preserve every other key, and
show the user the exact change before writing. Never store secrets here in
plain text without saying so.

## 4. Later-life repairs

- **Moved vault**: same flow; re-point `MARKDOWN_VAULT_MCP_SOURCE_DIR`.
- **Git sync failing / token expired**: a new fine-grained token scoped to
  the vault repository goes in `MARKDOWN_VAULT_MCP_GIT_TOKEN` (same `env`
  block; warn that it is stored in plain text there).
- **Embeddings misconfigured**: `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`
  plus its provider settings (`OLLAMA_HOST`, `OPENAI_API_KEY`, ...); an
  empty provider disables semantic search but the server still runs.

## 5. Finish: restart is the floor

MCP servers only start at session start; nothing picks up new configuration
mid-session. End every run of this flow by telling the user to **restart
Claude Code**, and that the vault tools will appear once `/mcp` shows
markdown-vault-mcp connected. Do not claim success before then.
