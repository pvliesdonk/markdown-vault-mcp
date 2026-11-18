# A remote server

This page is for the reader who was given a URL: someone runs Markdown Vault MCP over HTTP, and you want your client to use it. It is also the operator's checklist for connecting the first client; [Deploy](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/index.md) covers running the server itself.

Over HTTP, authentication is the only boundary: every caller the server accepts has every tool it exposes, and whoever runs the server decides who gets a credential. The [security model](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/security-model/index.md) states it in full.

## What you need

- **The URL.** The server answers MCP requests at `https://<host>/mcp` unless the operator changed the path. `https://<host>/health` answers without a credential, so opening it in a browser tells you the server is up before you configure anything.
- **A credential.** Either a bearer token the operator hands you, or a sign-in with the operator's identity provider. Which one depends on how the server is configured; [Authentication](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/authentication/index.md) is the operator's side of it.

## claude.ai and Claude Desktop

Claude on the web and Claude Desktop reach a remote server through a custom connector:

1. In your settings, open **Customize** › **Connectors**, choose **+ Add**, then **Add custom connector**.
1. Enter a name and the server's URL, then **Continue**.
1. Review the authentication settings Claude detected and change them if the server needs something else. Under **Authentication**, choose when to sign in:
   - **Sign in now**;
   - **Sign in when needed**;
   - **No sign in**.
1. For a server that takes a bearer token instead of a sign-in, add it as a fixed credential: a request header `Authorization: Bearer <token>` that Claude sends on every request.
1. Choose **Add**.

In a chat, click **+**, open **Connectors**, and switch the server on for that conversation. On a Team or Enterprise plan an organization owner adds the connector under **Organization settings** › **Connectors** first; members then connect it under **Customize** › **Connectors**.

## Claude Code

```
claude mcp add --transport http markdown-vault-mcp https://mcp.example.com/mcp
```

For a bearer token, add `--header "Authorization: Bearer <token>"`. The entry is stored for the current project; `--scope user` makes it available in every project. `/mcp` shows the connection.

## Another client

Any client that speaks the MCP streamable HTTP transport connects with the same URL. A bearer token travels in the `Authorization: Bearer <token>` header of every request.

## Ask Claude one question

> Which version of Markdown Vault MCP is running?

Claude calls the server's `get_server_info` tool, which reads the server's version and the protocol revision in use and changes nothing: a safe first check that the credential works.

### What to know about this server

A first task that changes nothing:

> Search the vault for notes about , and summarize what they say.

Whether Claude can also write is the operator's choice: a server in read-only mode lists none of the tools that change files. Some tools depend on the client or the operator:

- `browse_vault` and `show_context` open an interactive view in clients that render [MCP Apps](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/mcp-apps/index.md). Other clients get the same data as text.
- `create_download_link` and `create_upload_link` return a one-time URL for moving a file out of or into the vault, which works without your credential. See [Transfer links](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/transfer-links/index.md).
- `summarize` appears only when the operator configured a language model for it.

### Read a complete HTTP result

When building a client for this vault, discover `search` and `read` through `tools/list`. Search with `mode="keyword"` works without an embedding provider. Pass a returned note's `path` to `read`. The [terminal guide](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/use/terminal/index.md) shows the same workflow through the command line.

Keep the complete MCP result when calling these tools. For vault search, `structuredContent.result` holds the hit list; for `read`, `structuredContent` holds the note object. Search includes a Boolean `_meta.index_stale` describing index freshness; `read` reads the file directly. Keep search metadata alongside the hits so a caller can decide whether to wait for pending writes.

For result fields and error handling, see the [MCP tool-result contract](https://modelcontextprotocol.io/specification/2026-07-28/server/tools).

The synthetic HTTP contract tests exercise the native vault server and these whole-result shapes expected by an Executor caller. They do not establish acceptance by an installed Executor runtime or verify a personal connection. Use the operator's [authentication instructions](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/authentication/index.md) for credentials. The contract tests use only synthetic notes.

### VS Code: connect to this vault

Use the operator's full MCP endpoint, such as `https://mcp.example.com/mcp`, with server ID `markdown-vault-mcp` in [VS Code's add-server flow](https://code.visualstudio.com/docs/agent-customization/mcp-servers#add-a-server-by-url-or-command). Choose HTTP, then complete the operator's sign-in or bearer-token setup. Ask Copilot to search the vault for a known note to check the connection.

For this deployment's authentication mode, follow [Which mode](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/oidc/#which-mode), [provider setup](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/oidc-providers/index.md), and [VS Code's authorization guidance](https://code.visualstudio.com/api/extension-guides/ai/mcp#authorization). Check `MARKDOWN_VAULT_MCP_BASE_URL` using the [reverse-proxy checklist](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/reverse-proxy/#what-the-server-needs-from-the-proxy).

### Goose Desktop: connect to this vault

For a vault on your own machine, start with the [read-only local Goose configuration](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/get-started/installation/#goose-desktop-local-vault). For a hosted vault, use this extension entry in Goose's configuration; replace the example URL with the endpoint the operator supplied:

```
extensions:
  markdown-vault-mcp:
    name: Markdown Vault MCP
    type: streamable_http
    uri: https://mcp.example.com/mcp
    enabled: true
    timeout: 300
```

This entry targets the server's `http` transport, not its `sse` transport. It does not supply a credential. Complete the operator's [authentication setup](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/authentication/index.md) using [Goose's current extension guidance](https://goose-docs.ai/docs/getting-started/using-extensions/), including its pre-registered OAuth client settings when needed. For a bearer deployment, the extension's `headers` mapping can carry `Authorization: "Bearer <operator-provided-token>"`; keep the real token out of shared configuration and source control. [Goose's extension schema](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/extension.rs) also accepts `available_tools: [get_server_info, search, read]` to limit this vault's tool list. This client-side selection does not replace the server's read-only setting.

These examples were checked against the server configuration and primary client documentation on November 16, 2026, not against installed clients or a live identity provider. Confirm a read-only search before relying on a remote session; older client issue reports do not establish current compatibility with this deployment.

## Next

- [Deploy](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/index.md): run a server like this one yourself.
- [Use](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/use/index.md): what the server does for real tasks.
