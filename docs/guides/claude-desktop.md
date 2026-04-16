# Claude Desktop

This guide walks through four progressive setups for using markdown-vault-mcp with [Claude Desktop](https://claude.ai/download):

1. **Basic** — read-only keyword search, no external services
2. **Git write support** — enable write/edit/delete with auto-commit
3. **Semantic search** — add embedding-based search for better results
4. **MCP Apps views** — browse your vault visually with Context Card, Graph Explorer, and more

Each step builds on the previous one. Start with Step 0 (easiest) or Step 1 (full control) and add features as needed.

## Step 0: Install via .mcpb bundle

**Goal:** Install markdown-vault-mcp in Claude Desktop with a guided wizard — no JSON editing required.

**Prerequisites:** Claude Desktop >= 0.10.0.

This is the easiest option for non-technical users.

### Install

1. Download the `.mcpb` file from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page.
2. Double-click the downloaded `.mcpb` file, or run:

    ```bash
    mcpb install <file>.mcpb
    ```

3. Claude Desktop opens a GUI wizard that prompts for the required env vars. No manual JSON editing needed.

!!! note "Fewer configurable env vars"
    The `.mcpb` bundle exposes a curated set of env vars (vault path, read-only mode, embedding provider, and exclude patterns). For full control over all configuration options, use Step 1 instead.

---

## Step 1: Basic read-only setup

**Goal:** Connect a local Obsidian vault to Claude Desktop with keyword search.

**Prerequisites:** Python 3.10+, Claude Desktop installed.

### Install

```bash
pip install markdown-vault-mcp[all]
```

Or with uv:

```bash
uv tool install markdown-vault-mcp[all]
```

### Configure Claude Desktop

Edit your Claude Desktop configuration file:

=== "macOS"

    `~/Library/Application Support/Claude/claude_desktop_config.json`

=== "Windows"

    `%APPDATA%\Claude\claude_desktop_config.json`

=== "Linux"

    `~/.config/Claude/claude_desktop_config.json`

=== "macOS / Linux"

    ```json
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

=== "Windows"

    ```json
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

!!! tip "Persist the index"
    Setting `MARKDOWN_VAULT_MCP_INDEX_PATH` stores the FTS5 index on disk. Without it, the index is built in memory on every startup. With it, only changed files are reindexed.

!!! tip "Exclude Obsidian internals"
    `MARKDOWN_VAULT_MCP_EXCLUDE` keeps `.obsidian/` config files and `.trash/` out of search results. Add any other directories you want to skip (e.g., `_templates/**`).

### Restart Claude Desktop

Quit and reopen Claude Desktop. The server tools should appear in Claude's tool list.

### Verify it works

In a Claude Desktop conversation, ask:

> Search my vault for "meeting notes"

Claude should use the `search` tool and return matching documents from your vault. If you get no results, verify that `MARKDOWN_VAULT_MCP_SOURCE_DIR` points to a directory containing `.md` files.

---

## Step 2: Enable git write support

**Goal:** Allow Claude to create, edit, delete, and rename notes in managed git mode — with every change auto-committed and pushed.

**Prerequisites:** Step 1 complete. Your vault directory must be a git repository with an HTTPS remote configured.

### Create a GitHub Personal Access Token

1. Go to [GitHub Settings > Developer settings > Fine-grained tokens](https://github.com/settings/personal-access-tokens/new)
2. Set repository access to your vault repository only
3. Grant **Contents: Read and write** permission
4. Copy the token (starts with `github_pat_`)

### Update the configuration

Add the highlighted lines to your existing config:

```json hl_lines="8-12"
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

- `READ_ONLY=false` — enables the write, edit, delete, and rename tools
- `GIT_REPO_URL` — enables managed mode (clone/remote validation)
- `GIT_USERNAME` / `GIT_TOKEN` — HTTPS auth for pull/push
- `GIT_PUSH_DELAY_S=60` — batches rapid writes, pushing after 60 seconds of idle time

!!! warning "Token security"
    The token is stored in plain text in your Claude Desktop config. Use a fine-grained token scoped to the single vault repository with minimal permissions.

### Restart and verify

Restart Claude Desktop, then ask:

> Create a new note at "test/hello.md" with the content "Hello from Claude!"

Claude should use the `write` tool. Check your git log to confirm the commit:

```bash
cd /path/to/your/ObsidianVault
git log --oneline -3
```

You should see a commit from `markdown-vault-mcp`. Delete the test note when done:

> Delete the note at "test/hello.md"

---

## Step 3: Add semantic search

**Goal:** Enable embedding-based search alongside keyword search for better recall on conceptual queries.

**Prerequisites:** Step 1 complete. Choose an embedding backend:

- **FastEmbed** (recommended for Windows and offline use) — local inference, no external service
- **Ollama** (macOS/Linux) — local inference via [Ollama](https://ollama.com)
- **OpenAI** — cloud-based, requires API key (see [Embeddings guide](embeddings.md))

### Install and configure

=== "FastEmbed (Windows / offline)"

    Install the embeddings extra:

    ```powershell
    pip install "markdown-vault-mcp[embeddings]"
    # or:
    uv tool install "markdown-vault-mcp[embeddings]"
    ```

    Update your `claude_desktop_config.json`:

    ```json hl_lines="9-13"
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

=== "Ollama (macOS / Linux)"

    Install and start Ollama:

    ```bash
    brew install ollama        # macOS
    ollama pull nomic-embed-text
    ```

    Update your config:

    ```json hl_lines="9-12"
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

- `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` — where to persist embedding vectors on disk (required to enable semantic search)
- `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` — `fastembed`, `ollama`, or `openai`
- `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL` / `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` — which model to use

### Pre-build embeddings before first launch

For large vaults, building embeddings on first startup can take several minutes — long enough for Claude Desktop to time out the connection. Pre-build from the command line instead:

=== "macOS / Linux"

    ```bash
    export MARKDOWN_VAULT_MCP_SOURCE_DIR="/path/to/your/ObsidianVault"
    export MARKDOWN_VAULT_MCP_INDEX_PATH="/path/to/store/index.db"
    export MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH="/path/to/store/embeddings"
    export MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER="ollama"

    markdown-vault-mcp reindex
    ```

=== "Windows (PowerShell)"

    ```powershell
    $env:MARKDOWN_VAULT_MCP_SOURCE_DIR = "C:\Users\YourName\Documents\ObsidianVault"
    $env:MARKDOWN_VAULT_MCP_INDEX_PATH = "C:\Users\YourName\vault_index.db"
    $env:MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH = "C:\Users\YourName\vault_embeddings"
    $env:MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER = "fastembed"
    $env:MARKDOWN_VAULT_MCP_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
    $env:MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR = "C:\Users\YourName\fastembed_cache"

    markdown-vault-mcp reindex
    ```

`reindex` builds (or updates) both the FTS index and the embedding vectors. Once complete, Claude Desktop will load the pre-built index on startup without needing to re-embed anything.

!!! note "Subsequent startups are fast"
    After the initial build, only new or changed files are reindexed. You can run `reindex` again any time to catch up if you edited files outside Claude.

### Restart and verify

Restart Claude Desktop, then ask:

> Search my vault for notes about "project planning and task management" using hybrid mode

Claude should use the `search` tool with `mode="hybrid"`. Hybrid search combines keyword (BM25) and semantic (cosine similarity) results using Reciprocal Rank Fusion, giving better results for conceptual queries.

Compare with keyword-only:

> Search my vault for "project planning" using keyword mode

Hybrid mode should return more conceptually related notes, even if they don't contain the exact phrase.

---

## Step 4: Use MCP Apps views

**Goal:** Browse your vault visually with the Context Card, Graph Explorer, Vault Browser, and Note Preview views.

**Prerequisites:** Step 1 complete. A Claude client that supports the [MCP Apps protocol](https://modelcontextprotocol.io/specification/2025-06-18/server/apps) (e.g., Claude on claude.ai).

MCP Apps views are automatically available — no extra configuration needed for stdio transport. The server registers two tools (`browse_vault` and `show_context`) that open interactive views in supporting clients.

### Try it

In a Claude conversation, ask:

> Browse my vault

Claude should use the `browse_vault` tool. In an Apps-capable client, this opens an interactive SPA with four tabs:

- **Context Card** — note dossier with backlinks, outlinks, similar notes, and tags
- **Graph Explorer** — interactive force-directed link graph
- **Vault Browser** — searchable file tree
- **Note Preview** — rendered markdown with a "Send to Claude" button

You can also ask for a specific note's context:

> Show me the context for "Projects/roadmap.md"

Claude uses the `show_context` tool to open the Context Card for that note.

!!! note "Non-Apps clients"
    Clients that don't support MCP Apps (e.g., Claude Code) receive a text-only summary instead of the interactive view. All the underlying data is still accessible via the `get_context`, `get_backlinks`, and other link graph tools.

For full details on views, configuration, and architecture, see the [MCP Apps guide](mcp-apps.md).
