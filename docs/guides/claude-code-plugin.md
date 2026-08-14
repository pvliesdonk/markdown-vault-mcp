# Claude Code Plugin

This guide walks through installing markdown-vault-mcp as a [Claude Code](https://claude.ai/claude-code) plugin, either for the current project or globally.

## Overview

The Claude Code plugin installs markdown-vault-mcp directly into your Claude Code environment. It wires up the most commonly needed env vars with sensible defaults, and also installs a `vault-workflow` skill that gives Claude guidance on search strategy, reading patterns, link tools, and write semantics.

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

!!! tip "Project vs. global install"
    By default, `/plugin install` installs into the current project. To install globally for all projects, add the `--global` flag:

    ```
    /plugin install --global markdown-vault-mcp@pvliesdonk
    ```

## Configure

When you enable the plugin, Claude Code shows a configuration screen for the
essentials:

| Setting | Default | Description |
|---------|---------|-------------|
| Vault directory | _(required)_ | Path to your markdown (or Obsidian) vault |
| Read-only mode | on | Turn off to enable the write tools (`write`, `edit`, `append`, `delete`, `rename`, `move_folder`, `fetch`, `git_sync`, the `okf_*` tools, `create_upload_link`) |
| Embedding provider | _(empty)_ | Semantic-search backend (`fastembed`, `ollama`, `openai`); leave empty for keyword-only search |

To change these later, open `/plugin`, select the plugin, and edit its
configuration. Restart Claude Code afterwards so the server relaunches with
the new values.

!!! warning "Upgrading from an earlier plugin version"
    Earlier versions read the vault path, read-only flag, and embedding
    provider from shell env vars (`MARKDOWN_VAULT_MCP_SOURCE_DIR` and
    friends). Those three now come from the configuration screen instead —
    shell exports for them no longer reach the plugin's server. Enter the
    values in the configuration screen when Claude Code prompts for them.

## What you get

Beyond the configuration screen, the plugin wires up the following optional
env vars from your shell environment. Vars with a default are filled in when
the shell variable is unset; vars marked _(empty)_ stay blank when unset,
which usually means "feature disabled" or "use the server's built-in
default". Restart Claude Code after changing them:

| Env var | Default | Description |
|---------|---------|-------------|
| `MARKDOWN_VAULT_MCP_EXCLUDE` | `.obsidian/**,.trash/**,.git/**` | Comma-separated glob patterns to exclude from indexing |
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | _(empty)_ | Remote repository URL for git-backed vault sync; leave empty to disable git integration |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | _(empty)_ | Personal access token for the git remote; leave empty to disable git integration |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | _(empty)_ | Base path for the embeddings `.npy` sidecar (runtime appends the suffix); leave empty to disable semantic search |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | _(empty)_ | Override the FTS5 SQLite index file path; leave empty to use an in-memory index (rebuilt on every startup) |
| `OLLAMA_HOST` | `http://localhost:11434` | Base URL for the Ollama API; only used when `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=ollama` |

The plugin also installs the **`vault-workflow` skill**, which gives Claude guidance on:

- **Search strategy**: when to use keyword vs. semantic vs. hybrid search
- **Reading patterns**: note reading, link traversal, `get_context` usage, and efficient navigation
- **Link tools**: `get_backlinks`, `get_outlinks`, `get_connection_path`, and the graph tools
- **Write semantics**: creating, editing, renaming, and deleting notes safely

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

- See [Configuration](../configuration.md) for all available env vars, including git write support and semantic search options
- See [Claude Desktop](claude-desktop.md) if you also use Claude Desktop with the same vault
