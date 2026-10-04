# A remote server

This page is for the reader who was given a URL: someone runs Markdown Vault MCP over HTTP, and you want your client to use it. It is also the operator's checklist for connecting the first client; [Deploy](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deploy/index.md) covers running the server itself.

Over HTTP, authentication is the only boundary: every caller the server accepts has every tool it exposes, and whoever runs the server decides who gets a credential. The [security model](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/security-model/index.md) states it in full.

## What you need

- **The URL.** The server answers MCP requests at `https://<host>/mcp` unless the operator changed the path. `https://<host>/health` answers without a credential, so opening it in a browser tells you the server is up before you configure anything.
- **A credential.** Either a bearer token the operator hands you, or a sign-in with the operator's identity provider. Which one depends on how the server is configured; [Authentication](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deploy/authentication/index.md) is the operator's side of it.

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

- `browse_vault` and `show_context` open an interactive view in clients that render [MCP Apps](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deploy/mcp-apps/index.md); other clients get the same data as text.
- `create_download_link` and `create_upload_link` return a one-time URL for moving a file out of or into the vault, which works without your credential. See [Transfer links](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deploy/transfer-links/index.md).
- `summarize` appears only when the operator configured a language model for it.

## Next

- [Deploy](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deploy/index.md): run a server like this one yourself.
- [Use](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/index.md): what the server does for real tasks.
