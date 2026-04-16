# Claude Code Plugin

This guide walks through installing markdown-vault-mcp as a [Claude Code](https://claude.ai/claude-code) plugin — either for the current project or globally.

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

The only required env var is `MARKDOWN_VAULT_MCP_SOURCE_DIR` — set it to the path of your vault:

=== "Shell (current session)"

    ```bash
    export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/your/vault
    ```

=== "Shell profile (persistent)"

    Add to your `~/.bashrc`, `~/.zshrc`, or equivalent:

    ```bash
    export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/your/vault
    ```

Restart Claude Code after setting the env var so the plugin picks it up.

## What you get

The plugin wires up four env vars:

| Env var | Default | Description |
|---------|---------|-------------|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | _(required)_ | Path to your vault directory |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `true` | Set to `false` to enable write/edit/delete/rename tools |
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | _(empty)_ | Embedding backend (`fastembed`, `ollama`, `openai`); leave empty for keyword-only search |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | `.obsidian/**,.trash/**,.git/**` | Comma-separated glob patterns to exclude from indexing |

In addition to the MCP server, the plugin installs the **`vault-workflow` skill**, which gives Claude guidance on:

- **Search strategy** — when to use keyword vs. semantic vs. hybrid search
- **Reading patterns** — how to read notes efficiently, follow links, and use `get_context`
- **Link tools** — how to use `get_backlinks`, `get_outlinks`, `get_connection_path`, and the graph tools
- **Write semantics** — how to create, edit, rename, and delete notes safely

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
