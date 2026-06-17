# Claude Desktop

This guide walks through four progressive setups for using markdown-vault-mcp with [Claude Desktop](https://claude.ai/download):

1. **Basic**: read-only keyword search, no external services
1. **Git write support**: enable write/edit/delete with auto-commit
1. **Semantic search**: add embedding-based search for better results
1. **MCP Apps views**: interactive visual interface for your vault

Each step builds on the previous one. Start with Step 0 (easiest) or Step 1 (full control) and add features as needed.

## Step 0: Install via .mcpb bundle

**Goal:** Install markdown-vault-mcp in Claude Desktop with a guided wizard (no JSON editing required).

**Prerequisites:** Claude Desktop >= 0.10.0.

This is the easiest option for non-technical users.

### Install

1. Download the `.mcpb` file from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page.

1. Double-click the downloaded `.mcpb` file, or run:

   ```
   mcpb install <file>.mcpb
   ```

1. Claude Desktop opens a GUI wizard that prompts for the required env vars. No manual JSON editing needed.

Configured through the GUI wizard

The `.mcpb` bundle surfaces a broad set of env vars in Claude Desktop's wizard: vault path, read-only mode, state/index/embedding paths, the embedding provider and its per-provider model settings, git configuration, and auth (bearer / OIDC), among others. If you need to set an option the wizard doesn't expose, use Step 1 (manual config) instead.

______________________________________________________________________

## Step 1: Basic read-only setup

**Goal:** Connect a local Obsidian vault to Claude Desktop with keyword search.

**Prerequisites:** Python 3.11+, Claude Desktop installed.

### Install

```
pip install markdown-vault-mcp[all]
```

Or with uv:

```
uv tool install markdown-vault-mcp[all]
```

### Configure Claude Desktop

Edit your Claude Desktop configuration file:

`~/Library/Application Support/Claude/claude_desktop_config.json`

`%APPDATA%\Claude\claude_desktop_config.json`

`~/.config/Claude/claude_desktop_config.json`

```
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/path/to/your/ObsidianVault",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "my-vault",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "/path/to/store/index.db"
      }
    }
  }
}
```

```
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "C:\\Users\\YourName\\Documents\\ObsidianVault",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "my-vault",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "C:\\Users\\YourName\\vault_index.db"
      }
    }
  }
}
```

Replace the paths with the actual locations on your machine.

Persist the index

Setting `MARKDOWN_VAULT_MCP_INDEX_PATH` stores the FTS5 index on disk. Without it, the index is built in memory on every startup. With it, only changed files are reindexed.

Exclude Obsidian internals

`MARKDOWN_VAULT_MCP_EXCLUDE` keeps `.obsidian/` config files and `.trash/` out of search results. Add any other directories you want to skip, such as `_templates/**`.

### Restart Claude Desktop

Quit and reopen Claude Desktop. The server tools should appear in Claude's tool list.

### Verify it works

In a Claude Desktop conversation, ask:

> Search my vault for "meeting notes"

Claude should use the `search` tool and return matching documents from your vault. If you get no results, verify that `MARKDOWN_VAULT_MCP_SOURCE_DIR` points to a directory containing `.md` files.

______________________________________________________________________

## Step 2: Enable git write support

**Goal:** Allow Claude to create, edit, delete, and rename notes in managed git mode, with every change auto-committed and pushed.

**Prerequisites:** Step 1 complete. Your vault directory must be a git repository with an HTTPS remote configured.

### Create a GitHub Personal Access Token

1. Go to [GitHub Settings > Developer settings > Fine-grained tokens](https://github.com/settings/personal-access-tokens/new)
1. Set repository access to your vault repository only
1. Grant **Contents: Read and write** permission
1. Copy the token (starts with `github_pat_`)

### Update the configuration

Add the highlighted lines to your existing config:

```
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/path/to/your/ObsidianVault",
        "MARKDOWN_VAULT_MCP_READ_ONLY": "false",
        "MARKDOWN_VAULT_MCP_GIT_REPO_URL": "https://github.com/your-org/your-vault.git",
        "MARKDOWN_VAULT_MCP_GIT_USERNAME": "x-access-token",
        "MARKDOWN_VAULT_MCP_GIT_TOKEN": "github_pat_your_token_here",
        "MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S": "60",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "my-vault",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "/path/to/store/index.db"
      }
    }
  }
}
```

**What these do:**

- `READ_ONLY=false`: enables the write, edit, delete, and rename tools
- `GIT_REPO_URL`: enables managed mode (clone/remote validation)
- `GIT_USERNAME` / `GIT_TOKEN`: HTTPS auth for pull/push
- `GIT_PUSH_DELAY_S=60`: batches rapid writes, pushing after 60 seconds of idle time

Token security

The token is stored in plain text in your Claude Desktop config. Use a fine-grained token scoped to the single vault repository with minimal permissions.

### Restart and verify

Restart Claude Desktop, then ask:

> Create a new note at "test/hello.md" with the content "Hello from Claude!"

Claude should use the `write` tool. Check your git log to confirm the commit:

```
cd /path/to/your/ObsidianVault
git log --oneline -3
```

You should see a commit from `markdown-vault-mcp`. Delete the test note when done:

> Delete the note at "test/hello.md"

______________________________________________________________________

## Step 3: Add semantic search

**Goal:** Enable embedding-based search alongside keyword search for better recall on conceptual queries.

**Prerequisites:** Step 1 complete. Choose an embedding backend:

- **FastEmbed** (recommended for Windows and offline use): local inference, no external service
- **Ollama** (macOS/Linux): local inference via [Ollama](https://ollama.com)
- **OpenAI**: cloud-based, requires API key (see [Embeddings guide](https://pvliesdonk.github.io/markdown-vault-mcp/3.0/guides/embeddings/index.md))

### Install and configure

Install the embeddings extra:

```
pip install "markdown-vault-mcp[embeddings]"
# or:
uv tool install "markdown-vault-mcp[embeddings]"
```

Update your `claude_desktop_config.json`:

```
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "C:\\Users\\YourName\\Documents\\ObsidianVault",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "my-vault",
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH": "C:\\Users\\YourName\\vault_embeddings",
        "MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER": "fastembed",
        "MARKDOWN_VAULT_MCP_FASTEMBED_MODEL": "BAAI/bge-small-en-v1.5",
        "MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR": "C:\\Users\\YourName\\fastembed_cache",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "C:\\Users\\YourName\\vault_index.db",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**"
      }
    }
  }
}
```

Install and start Ollama:

```
brew install ollama        # macOS
ollama pull nomic-embed-text
```

Update your config:

```
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/path/to/your/ObsidianVault",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "my-vault",
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH": "/path/to/store/embeddings",
        "MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_HOST": "http://localhost:11434",
        "MARKDOWN_VAULT_MCP_OLLAMA_MODEL": "nomic-embed-text",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "/path/to/store/index.db"
      }
    }
  }
}
```

**Key env vars:**

- `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH`: where to persist embedding vectors on disk (required to enable semantic search)
- `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`: `fastembed`, `ollama`, or `openai`
- `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL` / `MARKDOWN_VAULT_MCP_OLLAMA_MODEL`: which model to use

### Pre-build embeddings before first launch

For large vaults, building embeddings on first startup can take several minutes, long enough for Claude Desktop to time out the connection. Pre-build from the command line instead:

```
export MARKDOWN_VAULT_MCP_SOURCE_DIR="/path/to/your/ObsidianVault"
export MARKDOWN_VAULT_MCP_INDEX_PATH="/path/to/store/index.db"
export MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH="/path/to/store/embeddings"
export MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER="ollama"

markdown-vault-mcp reindex
```

```
$env:MARKDOWN_VAULT_MCP_SOURCE_DIR = "C:\Users\YourName\Documents\ObsidianVault"
$env:MARKDOWN_VAULT_MCP_INDEX_PATH = "C:\Users\YourName\vault_index.db"
$env:MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH = "C:\Users\YourName\vault_embeddings"
$env:MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER = "fastembed"
$env:MARKDOWN_VAULT_MCP_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
$env:MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR = "C:\Users\YourName\fastembed_cache"

markdown-vault-mcp reindex
```

`reindex` builds (or updates) both the FTS index and the embedding vectors. Once complete, Claude Desktop will load the pre-built index on startup without needing to re-embed anything.

Subsequent startups are fast

After the initial build, only new or changed files are reindexed. You can run `reindex` again any time to catch up if you edited files outside Claude.

### Restart and verify

Restart Claude Desktop, then ask:

> Search my vault for notes about "project planning and task management" using hybrid mode

Claude should use the `search` tool with `mode="hybrid"`. Hybrid search combines keyword (BM25) and semantic (cosine similarity) results using Reciprocal Rank Fusion, giving better results for conceptual queries.

Compare with keyword-only:

> Search my vault for "project planning" using keyword mode

Hybrid mode should return more conceptually related notes, even if they don't contain the exact phrase.

______________________________________________________________________

## Step 4: Use MCP Apps views

**Goal:** Browse your vault visually using the interactive SPA views described below.

**Prerequisites:** Step 1 complete. A Claude client that supports the [MCP Apps protocol](https://modelcontextprotocol.io/specification/2025-06-18/server/apps) (such as Claude on claude.ai).

MCP Apps views are automatically available with no extra configuration needed for stdio transport. The server registers two tools (`browse_vault` and `show_context`) that open interactive views in supporting clients.

### Try it

In a Claude conversation, ask:

> Browse my vault

Claude should use the `browse_vault` tool. In an Apps-capable client, this opens an interactive SPA with four tabs:

- **Context Card**: note dossier with backlinks, outlinks, similar notes, and tags
- **Graph Explorer**: interactive force-directed link graph
- **Vault Browser**: searchable file tree
- **Note Preview**: rendered markdown with a "Send to Claude" button

You can also ask for a specific note's context:

> Show me the context for "Projects/roadmap.md"

Claude uses the `show_context` tool to open the Context Card for that note.

Non-Apps clients

Clients that don't support MCP Apps (such as Claude Code) receive a text-only summary instead of the interactive view. All the underlying data is still accessible via the `get_context`, `get_backlinks`, and other link graph tools.

For full details on views, configuration, and architecture, see the [MCP Apps guide](https://pvliesdonk.github.io/markdown-vault-mcp/3.0/guides/mcp-apps/index.md).

### Firing prompts from Claude.ai's `+` menu

This guide covers Claude Desktop. If you also use Claude.ai with the same server, its prompts can be fired from the compose area's `+` menu once the server is added as a connector. Click `+`, select **connectors**, pick the server, pick a prompt, and Claude opens with the prompt scaffolded. See [How to invoke prompts](https://pvliesdonk.github.io/markdown-vault-mcp/3.0/prompts/#how-to-invoke-prompts).
