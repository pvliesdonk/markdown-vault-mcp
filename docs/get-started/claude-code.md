---
description: "Run Markdown Vault MCP inside Claude Code through its plugin, or connect Claude Code to a server that already runs."
kind: tutorial
---

# Claude Code

Claude Code can start the server for you, as Claude Desktop does. It can also connect to a server that already runs somewhere else. Started locally, the server runs with your user account's access and trusts the Claude Code session that started it; the [security model](../security-model.md) says what that reaches.

## The plugin

The shortest route. In a Claude Code session, run:

```text
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

The first command registers the `pvliesdonk` marketplace, once. The second opens the plugin's details and asks for a scope:

- **user**: you, in every project on this machine;
- **project**: everyone who works in this repository;
- **local**: you, in this repository only.

The plugin follows stable releases and runs the released package with `uvx` at the version it was published with. Install `uv` first; the machine also needs network access the first time the server starts.

Run `/mcp` to see the server connected. To update or remove the plugin later, open `/plugin`, select it under **Installed**, and choose **Update now** or **Uninstall**; from a shell, `claude plugin update markdown-vault-mcp@pvliesdonk` does the update.

From a shell, without a session (a setup script, say):

```bash
claude plugin marketplace add pvliesdonk/claude-plugins
claude plugin install markdown-vault-mcp@pvliesdonk
```

The shell form installs at user scope unless `--scope project` or `--scope local` says otherwise, and the plugin loads when Claude Code next starts.

## The command

Without the plugin, install the server as a command, then register it with Claude Code:

```bash
uv tool install "markdown-vault-mcp"
claude mcp add --transport stdio markdown-vault-mcp -- markdown-vault-mcp serve
```

Claude Code runs whatever follows the `--`, so settings for the server go on the command line as environment variables: `--env NAME=value` before the server's name, one per variable, from the [configuration reference](../reference/configuration.md). The entry is stored for the current project; `--scope user` makes it available in every project. `/mcp` shows it connected.

## A server that already runs

When someone runs the server for you over HTTP, register its URL instead:

```bash
claude mcp add --transport http markdown-vault-mcp https://mcp.example.com/mcp
```

With a bearer token, add `--header "Authorization: Bearer <token>"`. Which credential the server wants is up to whoever runs it; [A remote server](http-client.md) covers both kinds.

## Ask Claude one question

> Which version of Markdown Vault MCP is running?

Claude calls the server's `get_server_info` tool, which reads the server's version and the protocol revision in use and changes nothing. Then the first task below.

### Your first task

<!-- DOMAIN-CLAUDE-CODE-FIRST-TASK-START -->
**With the plugin**, Claude Code asks for the plugin's settings when you enable it. Only the vault directory is required: the absolute path of the folder that holds your notes. For a first run, set **Read-only mode** to `true`. The plugin's default is `false`, which gives Claude the tools that change files. Restart Claude Code after answering, because the server starts with a session.

The plugin also brings its own skills. `vault-workflow` teaches Claude how to search, follow links and write safely. `vault-summarize` handles summaries across many notes, with a read-only `vault-mapper` subagent. Ask Claude to set up or repair your vault and `vault-setup` looks for candidate folders, checks your choice and records it. A check at the start of each session says so when no vault directory is set, or the one set has gone.

**With the command**, pass the same settings with `--env`:

```bash
claude mcp add --transport stdio \
  --env MARKDOWN_VAULT_MCP_SOURCE_DIR=/Users/me/Documents/Notes \
  --env MARKDOWN_VAULT_MCP_READ_ONLY=true \
  markdown-vault-mcp -- markdown-vault-mcp serve
```

Then give Claude its first task:

> Search my vault for notes about <a topic you have written about>, and summarize what they say.

Claude searches, reads the notes it found and answers, and nothing in the vault changes. [What this server needs](claude-desktop.md#what-this-server-needs) covers the other settings worth knowing on a first run.
<!-- DOMAIN-CLAUDE-CODE-FIRST-TASK-END -->

## Next

- [Claude Desktop](claude-desktop.md): the same server in the desktop app.
- [Installation](installation.md): the other ways to install it.
- [Use](../use/index.md): what the server does for real tasks.
