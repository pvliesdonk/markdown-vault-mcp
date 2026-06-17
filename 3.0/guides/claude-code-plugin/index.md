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

Project vs. global install

By default, `/plugin install` installs into the current project. To install globally for all projects, add the `--global` flag:

```
/plugin install --global markdown-vault-mcp@pvliesdonk
```

## Configure

The only required env var is `MARKDOWN_VAULT_MCP_SOURCE_DIR`. Set it to the path of your vault:

```
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/your/vault
```

Add to your `~/.bashrc`, `~/.zshrc`, or equivalent:

```
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/your/vault
```

Restart Claude Code after setting the env var so the plugin picks it up.

## What you get

The plugin wires up the following env vars. Vars with a default are filled in when the shell variable is unset; vars marked *(empty)* stay blank when unset, which usually means "feature disabled" or "use the server's built-in default":

| Env var                                 | Default                          | Description                                                                                                      |
| --------------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `MARKDOWN_VAULT_MCP_SOURCE_DIR`         | *(required)*                     | Path to your vault directory                                                                                     |
| `MARKDOWN_VAULT_MCP_READ_ONLY`          | `true`                           | Set to `false` to enable write/edit/delete/rename tools                                                          |
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | *(empty)*                        | Embedding backend (`fastembed`, `ollama`, `openai`); leave empty for keyword-only search                         |
| `MARKDOWN_VAULT_MCP_EXCLUDE`            | `.obsidian/**,.trash/**,.git/**` | Comma-separated glob patterns to exclude from indexing                                                           |
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL`       | *(empty)*                        | Remote repository URL for git-backed vault sync; leave empty to disable git integration                          |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN`          | *(empty)*                        | Personal access token for the git remote; leave empty to disable git integration                                 |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH`    | *(empty)*                        | Base path for the embeddings `.npy` sidecar (runtime appends the suffix); leave empty to disable semantic search |
| `MARKDOWN_VAULT_MCP_INDEX_PATH`         | *(empty)*                        | Override the FTS5 SQLite index file path; leave empty to use an in-memory index (rebuilt on every startup)       |
| `OLLAMA_HOST`                           | `http://localhost:11434`         | Base URL for the Ollama API; only used when `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=ollama`                       |

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

- See [Configuration](https://pvliesdonk.github.io/markdown-vault-mcp/3.0/configuration/index.md) for all available env vars, including git write support and semantic search options
- See [Claude Desktop](https://pvliesdonk.github.io/markdown-vault-mcp/3.0/guides/claude-desktop/index.md) if you also use Claude Desktop with the same vault
