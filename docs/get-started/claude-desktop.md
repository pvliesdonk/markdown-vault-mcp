---
description: "Connect Claude Desktop to Markdown Vault MCP on your machine and make a first, read-only call."
kind: tutorial
---

# Claude Desktop

Claude Desktop starts the server on your machine when it opens and talks to it directly; nothing listens on the network. The server runs with your user account's access and trusts the app that started it; the [security model](../security-model.md) says what that reaches. Claude Desktop is available for macOS and Windows.

The steps below end with one question to Claude.

## 1. Install the command

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install the server as a command:

```bash
uv tool install "markdown-vault-mcp"
```

uv keeps the server in its own environment and fetches a Python when your machine has none. Check the result:

```bash
markdown-vault-mcp --help
```

Claude Desktop needs the command's full path, because it does not read your shell's `PATH`. Print it now and keep it for step 2:

```bash
which markdown-vault-mcp        # macOS
where.exe markdown-vault-mcp    # Windows
```

The path is inside uv's executable directory, `~/.local/bin` unless you configured another (`uv tool dir --bin` prints it).

**Without a terminal.** Each release ships a `.mcpb` bundle on the [releases page](https://github.com/pvliesdonk/markdown-vault-mcp/releases). Download it and open it with Claude Desktop, or choose **Settings** › **Extensions** › **Advanced settings** › **Install Extension…** and pick the file. Claude Desktop asks for the settings this server declares and writes its own configuration. The bundle carries no code: on first launch it fetches the released package from PyPI, so the machine needs network access then. Skip to [step 4](#4-ask-claude-one-question).

## 2. Tell Claude Desktop about it

In Claude Desktop, open the Claude menu, choose **Settings…**, then **Developer**, then **Edit Config**. That opens the configuration file, creating it when it does not exist:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add the server, with the path from step 1 as `command`. With this entry the server appears in Claude under the name `markdown-vault-mcp`:

```json { .config data-expect="server_name='markdown-vault-mcp'" }
{
  "mcpServers": {
    "markdown-vault-mcp": {
      "command": "/Users/me/.local/bin/markdown-vault-mcp",
      "args": ["serve"],
      "env": {}
    }
  }
}
```

On Windows the path ends in `.exe` and each backslash is doubled: `"C:\\Users\\me\\.local\\bin\\markdown-vault-mcp.exe"`.

Claude Desktop passes every value as written, so paths are absolute (`~` is not expanded). Keep the JSON valid: no trailing commas. Settings for the server go in `env`, one environment variable per key; the [configuration reference](../reference/configuration.md) lists them all.

### What this server needs

<!-- DOMAIN-CLAUDE-DESKTOP-START -->
The server needs one setting: `MARKDOWN_VAULT_MCP_SOURCE_DIR`, the absolute path of the folder that holds your notes. For a first run, also set `MARKDOWN_VAULT_MCP_READ_ONLY` to `true`. Claude then gets the tools that search and read, and none of the tools that change files:

```json { .config data-expect="read_only=True" }
{
  "mcpServers": {
    "markdown-vault-mcp": {
      "command": "/Users/me/.local/bin/markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/Users/me/Documents/Notes",
        "MARKDOWN_VAULT_MCP_READ_ONLY": "true",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**"
      }
    }
  }
}
```

`MARKDOWN_VAULT_MCP_EXCLUDE` keeps an Obsidian vault's settings folder and trash out of the results. Read-only covers the tools, not the folder: the server still keeps a small change-tracking file in a `.markdown_vault_mcp/` folder inside the vault, unless `MARKDOWN_VAULT_MCP_STATE_PATH` names a path elsewhere. The search index lives in memory and is rebuilt at each start; set `MARKDOWN_VAULT_MCP_INDEX_PATH` to a file outside the vault to keep it between starts.

The first task, after the check in step 4:

> Search my vault for notes about <a topic you have written about>, and summarize what they say.

Claude searches, reads the notes it found and answers, and nothing in the vault changes. To let Claude write, remove the `READ_ONLY` line, since writing is on by default. [Git integration](../guides/git-integration.md) adds a commit for every change, and [Embeddings](../guides/embeddings.md) adds search by meaning.
<!-- DOMAIN-CLAUDE-DESKTOP-END -->

## 3. Restart Claude Desktop

Quit it completely and open it again; the configuration is read at start. Then click the **+** button at the bottom left of the message box, open **Connectors**, then **Manage connectors**: `markdown-vault-mcp` is listed with its tools. If it is not, see [step 5](#5-if-it-does-not-appear).

## 4. Ask Claude one question

Ask Claude:

> Which version of Markdown Vault MCP is running?

Claude calls the server's `get_server_info` tool and answers with the server's version, the version of the shared library it is built on and the protocol revision in use. The call reads those fields and changes nothing, so it is a safe first check of the connection; the [tools reference](../reference/tools/index.md) says what else the server offers. Then try the first task named under [What this server needs](#what-this-server-needs).

## 5. If it does not appear

- The JSON is valid (a trailing comma is the usual culprit) and `command` is an absolute path.
- Run the command yourself in a terminal: `markdown-vault-mcp serve` starts the server and waits for a client; press Ctrl+C to stop it. An error here is the server's, not Claude Desktop's.
- Read the logs: `~/Library/Logs/Claude/mcp*.log` on macOS, `%APPDATA%\Claude\logs` on Windows. `mcp-server-markdown-vault-mcp.log` holds what the server wrote to its standard error.

## Next

- [Claude Code](claude-code.md): the same server inside Claude Code.
- [Installation](installation.md): the other ways to install it.
- [Use](../use/index.md): what the server does for real tasks.
