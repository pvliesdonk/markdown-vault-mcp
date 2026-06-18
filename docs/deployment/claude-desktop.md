# Claude Desktop

<<<<<<< before updating
markdown-vault-mcp integrates with [Claude Desktop](https://claude.ai/download) via the stdio transport.
=======
Markdown Vault MCP integrates with [Claude Desktop](https://claude.ai/download) via the stdio transport.
>>>>>>> after updating

## Setup

### 1. Install

<<<<<<< before updating
```bash
pip install markdown-vault-mcp[all]
```

Or with uv:

```bash
uv tool install markdown-vault-mcp[all]
```

### 2. Configure Claude Desktop

Add the server to your Claude Desktop configuration file:

=== "macOS"

    Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

=== "Windows"

    Edit `%APPDATA%\Claude\claude_desktop_config.json`:

=== "Linux"

    Edit `~/.config/Claude/claude_desktop_config.json`:
=======
From PyPI:

```bash
pip install markdown-vault-mcp
```

Or with uv (installs `markdown-vault-mcp` as a global command on your PATH):

```bash
uv tool install markdown-vault-mcp
```

Or download the `.mcpb` bundle from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page and double-click to install; Claude Desktop prompts for required env vars via a GUI wizard, no manual JSON editing needed.

### 2. Configure Claude Desktop

If you installed via `.mcpb`, skip this step — Claude Desktop was configured automatically by the wizard.

Otherwise, add the server to your Claude Desktop configuration file. The path varies by operating system:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`
>>>>>>> after updating

```json
{
  "mcpServers": {
    "markdown-vault-mcp": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
<<<<<<< before updating
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/path/to/your/vault"
=======
        "MARKDOWN_VAULT_MCP_READ_ONLY": "true"
>>>>>>> after updating
      }
    }
  }
}
```

### 3. Restart Claude Desktop

<<<<<<< before updating
Restart the application to pick up the new configuration. You should see the markdown-vault-mcp tools available in Claude's tool list.

## Configuration Examples

### Read-only with Ollama embeddings

```json
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/Users/me/Documents/ObsidianVault",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "my-vault",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "/Users/me/.local/share/markdown-vault-mcp/index.db",
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH": "/Users/me/.local/share/markdown-vault-mcp/embeddings",
        "MARKDOWN_VAULT_MCP_INDEXED_FIELDS": "tags",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**",
        "MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_HOST": "http://localhost:11434"
      }
    }
  }
}
```

### Read-write with managed git mode

```json
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/Users/me/Documents/ObsidianVault",
        "MARKDOWN_VAULT_MCP_READ_ONLY": "false",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "/Users/me/.local/share/markdown-vault-mcp/index.db",
        "MARKDOWN_VAULT_MCP_GIT_REPO_URL": "https://github.com/your-org/your-vault.git",
        "MARKDOWN_VAULT_MCP_GIT_USERNAME": "x-access-token",
        "MARKDOWN_VAULT_MCP_GIT_TOKEN": "ghp_your_token_here",
        "MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S": "60"
      }
    }
  }
}
```

### Read-write with unmanaged / commit-only mode

```json
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/Users/me/Documents/ObsidianVault",
        "MARKDOWN_VAULT_MCP_READ_ONLY": "false",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "/Users/me/.local/share/markdown-vault-mcp/index.db"
      }
    }
  }
}
```

In unmanaged mode, writes are committed only if `SOURCE_DIR` is already a git repository. Pull/push are handled externally.

### Multiple vaults
=======
Restart the application to pick up the new configuration. If the server connects successfully, `Markdown Vault MCP` tools appear in Claude's tool list. If not, see [Troubleshooting](#troubleshooting) below.

## Configuration examples

<!-- DOMAIN-CLAUDE-DESKTOP-START -->
<!-- Add domain-specific Claude Desktop configuration examples here.
     Kept across copier update. -->
<!-- DOMAIN-CLAUDE-DESKTOP-END -->

## Troubleshooting

### Server not appearing in Claude Desktop

1. Check the config file path is correct for your OS
2. Ensure the JSON is valid (no trailing commas)
3. Restart Claude Desktop completely (quit and reopen)
4. Check Claude Desktop logs for error messages

### "Command not found"

Ensure `markdown-vault-mcp` is on your PATH. If installed in a virtualenv, use the full path to the binary. Replace only the `"command"` value in your existing config — keep `"args"` and `"env"` as-is.

macOS/Linux:
>>>>>>> after updating

```json
{
  "mcpServers": {
<<<<<<< before updating
    "notes": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/Users/me/Documents/Notes",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "notes"
      }
    },
    "docs": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/Users/me/Projects/docs",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "docs"
      }
    }
  }
}
```

!!! tip "Naming instances"
    Use `MARKDOWN_VAULT_MCP_SERVER_NAME` to give each instance a descriptive name. This helps Claude distinguish between vaults when multiple instances are configured.

## Using with uv

If you installed with `uv tool install`, the command is already on your PATH. If you installed in a project with `uv pip install`, point to the uv-managed binary:
=======
    "markdown-vault-mcp": {
      "command": "/Users/me/.venvs/mcp/bin/markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_READ_ONLY": "true"
      }
    }
  }
}
```

Windows (`Scripts\` not `bin\`, `.exe` suffix):
>>>>>>> after updating

```json
{
  "mcpServers": {
<<<<<<< before updating
    "my-vault": {
      "command": "uv",
      "args": ["run", "markdown-vault-mcp", "serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/path/to/vault"
      }
    }
  }
}
```

## Troubleshooting

### Server not appearing in Claude Desktop

1. Check the config file path is correct for your OS
2. Ensure the JSON is valid (no trailing commas)
3. Restart Claude Desktop completely (quit and reopen)
4. Check Claude Desktop logs for error messages

### "Command not found"

Ensure `markdown-vault-mcp` is on your PATH. If installed in a virtualenv, use the full path:

```json
{
  "command": "/Users/me/.venvs/mcp/bin/markdown-vault-mcp"
}
```

### Slow startup

The first startup builds the full-text index. Set `MARKDOWN_VAULT_MCP_INDEX_PATH` to persist the index between restarts; subsequent starts will only process changed files.
=======
    "markdown-vault-mcp": {
      "command": "C:\\Users\\me\\.venvs\\mcp\\Scripts\\markdown-vault-mcp.exe",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_READ_ONLY": "true"
      }
    }
  }
}
```
>>>>>>> after updating
