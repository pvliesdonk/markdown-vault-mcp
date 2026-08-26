# Claude Code Plugin

This guide walks through installing markdown-vault-mcp as a [Claude Code](https://claude.ai/claude-code) plugin, either for the current project or globally.

## Overview

The Claude Code plugin installs markdown-vault-mcp directly into your Claude Code environment. Enabling it opens a configuration prompt for the settings that matter on a personal install (the vault directory, read-only mode, embedding provider, git sync), with sensitive values stored securely and no shell-profile editing. The plugin also installs a `vault-workflow` skill that gives Claude guidance on search strategy, reading patterns, link tools, and write semantics.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed
- Claude Code CLI installed and authenticated

## Install

Run these two commands in Claude Code:

```
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

The first command adds the `pvliesdonk/claude-plugins` marketplace to your Claude Code configuration. The second installs the markdown-vault-mcp plugin from that marketplace.

Project vs. global install

By default, `/plugin install` installs into the current project. To install globally for all projects, add the `--global` flag:

```
/plugin install --global markdown-vault-mcp@pvliesdonk
```

## Configure

Enabling the plugin opens a configuration prompt. The only required field is the vault directory; everything else has a sensible default or can stay empty. Your answers persist across plugin updates, and sensitive fields (the OpenAI API key, the git access token) are masked and stored in secure storage rather than a settings file. Restart Claude Code after configuring so the server starts with your values.

To change the configuration later, re-open the plugin's configuration from the `/plugin` menu, or just ask Claude to set up or repair your vault (the `vault-setup` skill walks through it).

## What you get

The configuration prompt covers these settings, each wired to the matching server option. Fields marked *(empty)* can stay blank, which means "feature disabled" or "use the server's built-in default":

| Setting                 | Default                          | What it sets                                                                                                                                                                                                                                  |
| ----------------------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Vault directory         | *(required)*                     | `MARKDOWN_VAULT_MCP_SOURCE_DIR`: the vault to serve                                                                                                                                                                                           |
| Read-only mode          | `false`                          | `MARKDOWN_VAULT_MCP_READ_ONLY`; the write tools (`write`, `edit`, `append`, `delete`, `rename`, `move_folder`, `fetch`, `git_sync`, the `okf_*` tools, `create_upload_link`) are available out of the box. Set `true` for a search-only vault |
| Exclude patterns        | `.obsidian/**,.trash/**,.git/**` | `MARKDOWN_VAULT_MCP_EXCLUDE`: comma-separated globs kept out of the index                                                                                                                                                                     |
| Embedding provider      | *(empty)*                        | `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` (`fastembed`, `ollama`, `openai`, `voyage`); empty means keyword-only search                                                                                                                          |
| Ollama host             | `http://localhost:11434`         | `OLLAMA_HOST`; used only with the `ollama` provider                                                                                                                                                                                           |
| Ollama embedding model  | `nomic-embed-text`               | `MARKDOWN_VAULT_MCP_OLLAMA_MODEL`                                                                                                                                                                                                             |
| FastEmbed model         | `BAAI/bge-small-en-v1.5`         | `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL`                                                                                                                                                                                                          |
| OpenAI API key          | *(empty, masked)*                | `OPENAI_API_KEY`; used only with the `openai` provider                                                                                                                                                                                        |
| OpenAI base URL         | `https://api.openai.com/v1`      | `OPENAI_BASE_URL`                                                                                                                                                                                                                             |
| OpenAI embedding model  | `text-embedding-3-small`         | `OPENAI_EMBEDDING_MODEL`                                                                                                                                                                                                                      |
| Voyage API key          | *(empty, masked)*                | `VOYAGE_API_KEY`; used only with the `voyage` provider                                                                                                                                                                                        |
| Voyage embedding model  | `voyage-4`                       | `MARKDOWN_VAULT_MCP_VOYAGE_MODEL`                                                                                                                                                                                                             |
| Git sync repository URL | *(empty)*                        | `MARKDOWN_VAULT_MCP_GIT_REPO_URL`; empty disables git integration                                                                                                                                                                             |
| Git access token        | *(empty, masked)*                | `MARKDOWN_VAULT_MCP_GIT_TOKEN`                                                                                                                                                                                                                |
| Server name             | `markdown-vault-mcp`             | `MARKDOWN_VAULT_MCP_SERVER_NAME`                                                                                                                                                                                                              |
| Log level               | `INFO`                           | `FASTMCP_LOG_LEVEL`                                                                                                                                                                                                                           |

Settings outside this screen (state and index paths, tuning, and the rest of [Configuration](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/configuration/index.md)) stay reachable through env vars: the `env` block of your user-scope `~/.claude/settings.json` reaches the server process for anything the screen does not wire.

The plugin also installs the **`vault-workflow` skill**, which gives Claude guidance on:

- **Search strategy**: when to use keyword vs. semantic vs. hybrid search
- **Reading patterns**: note reading, link traversal, `get_context` usage, and efficient navigation
- **Link tools**: `get_backlinks`, `get_outlinks`, `get_connection_path`, and the graph tools
- **Write semantics**: creating, editing, renaming, and deleting notes safely

A second skill, **`vault-summarize`**, triggers when you ask for a summary or overview spanning more than one note. It checks scope with `get_toc`, prefers the server-side `summarize` tool when your deployment configured one, and otherwise runs the server's `summarize-subtree` recipe with parallel **`vault-mapper`** subagents (a restricted read-only agent the plugin ships), so note bodies stay confined to the subagents instead of filling the main conversation.

## Troubleshooting and guided setup

If the vault server shows `Failed to connect` in `/mcp`, the plugin can repair itself in-session: skills, agents, and hooks keep working while the MCP server is down.

- A **SessionStart doctor hook** checks the effective configuration when a session starts. When the vault directory is unset or no longer exists, it says so up front and offers help; when the configuration is healthy it stays silent.
- Asking Claude to set up (or fix) your vault triggers the **`vault-setup` skill**. It looks for candidate vault directories (`.obsidian/` markers and note-like folders), checks that your choice exists and is readable, and then records it in the plugin's stored options in your user-scope `~/.claude/settings.json` (the same place the configuration prompt writes). The flow always ends with a restart of Claude Code, because MCP servers only start at session start.
- The same flow covers later breakage: a moved vault, an expired git token, or a broken embedding-provider setting.

One precedence rule worth knowing: the plugin's stored options are what the server actually receives; legacy configuration through the settings-file `env` block or a shell profile only applies when no plugin option is set. If an old shell-profile value disagrees with the plugin's configuration, the plugin's value is the one that counts.

## Update

To update the plugin to the latest version:

```
/plugin update markdown-vault-mcp
```

## Uninstall

To remove the plugin:

```
/plugin uninstall markdown-vault-mcp
```

## Next steps

- See [Configuration](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/configuration/index.md) for all available env vars, including git write support and semantic search options
- See [Claude Desktop](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/claude-desktop/index.md) if you also use Claude Desktop with the same vault
