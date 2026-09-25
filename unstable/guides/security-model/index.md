# Security model

This page states what markdown-vault-mcp protects and what it leaves to the operator. [Authentication](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/authentication/index.md) covers how to configure each mode, and `SECURITY.md` in the repository covers how to report a vulnerability.

## Authentication is the boundary

Over HTTP, authentication is the only access control: a bearer token, a mapped bearer token file, or OIDC. A request without a valid credential is refused before it reaches a tool. It is the same model as any other MCP server reached over HTTP.

## Running without authentication

With no authentication variables set, the server starts in mode `none` and logs `auth_mode_resolved` at warning level with `mode=none`. Every client that can open a connection to the port then has the full tool surface. Turning authentication off is a decision to trust everything that can reach the port.

The bind address decides which machines reach the port, and nothing more. Binding to `127.0.0.1` keeps other machines out. It does not keep out other processes on the host, nor a web page in a browser on the host that uses DNS rebinding. Without authentication, run the server only where everything that reaches the port is trusted, such as a single-user workstation or a private network you control.

## stdio

Over stdio there is no network listener and no authentication. The MCP client starts the server as a child process, and the server trusts that client. Authentication variables have no effect there, and the server logs `auth_configured_but_stdio_skips_enforcement` when they are set.

## What an authenticated caller can reach

An authenticated caller has every tool the instance exposes. Each tool runs with the server's own privileges: its filesystem and network access, and any credential in its configuration. `MARKDOWN_VAULT_MCP_TOOLS_ALLOW` and `MARKDOWN_VAULT_MCP_TOOLS_DENY` trim that set for an instance; see [Configuration](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/configuration/index.md).

Every tool works inside the vault directory the server serves, and a path that resolves outside it is refused. Read tools such as `search`, `read` and `get_history` only read the vault. Write tools (`write`, `edit`, `append`, `delete`, `rename`, `move_folder`, `fetch` and the OKF write tools) change files in it. `MARKDOWN_VAULT_MCP_READ_ONLY=true` hides every write tool. `reindex` and `build_embeddings` stay available in that mode, since they rebuild the index and leave notes untouched.

Some tools send data beyond the vault:

- **`fetch`** downloads an `http` or `https` URL the caller names and saves the response in the vault. Only publicly routable addresses are allowed, on the first request and on every redirect, and proxy settings from the environment don't apply.
- **Embeddings.** With a remote embedding provider (OpenAI-compatible, Voyage, or Ollama on another host), indexing sends note text to that provider, and `search` sends the query text. The local fastembed provider sends nothing.
- **`summarize`** sends the notes it summarizes to the OpenAI-compatible endpoint set in `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL`. Without a configured endpoint the tool is hidden.
- **Git.** With a remote configured, the server pushes to it after writes and pulls from it on a timer, and `git_sync` lets a caller start either direction.

Callers never pass credentials to these tools. The server uses its own: `MARKDOWN_VAULT_MCP_GIT_TOKEN` for an HTTPS remote, or the host's SSH setup for an SSH remote, and the API keys of the embedding provider and the summarize endpoint.

## What answers without a credential

Some routes answer anyone who can reach the port, with authentication on or off:

- `/health` and `/health/ready`. `MARKDOWN_VAULT_MCP_HEALTH_DETAIL` decides how much they say; see [Health](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deployment/docker/#health).
- In the OIDC modes, the OAuth metadata and sign-in routes a client uses before it holds a token.

## What the operator is responsible for

- **TLS.** The server speaks plain HTTP. Terminate TLS at a reverse proxy in front of it.
- **Exposure.** Which interfaces and networks can reach the port. [Ports](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deployment/docker/#ports) covers the published port of the Docker deployment.
- **Secrets.** Bearer tokens, OIDC client secrets and the credentials the tools use live in the environment or in files you control. Anyone who can read them can act as the server.
- **The debugger port.** A debug build's debugger port grants code execution to anyone who reaches it; see [Remote debugging](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deployment/docker/#remote-debugging).

## Host and Origin validation

FastMCP can refuse requests whose `Host` or `Origin` header does not name the server, which blocks DNS rebinding against a server running without authentication. It is off by default, and the model above does not depend on it. To turn it on, set `FASTMCP_HTTP_HOST_ORIGIN_PROTECTION` to `auto`, which checks requests that reach the server on a local address, or to `true`, which checks every request against `FASTMCP_HTTP_ALLOWED_HOSTS` (a JSON list of host names). Behind a reverse proxy, test it before relying on it: a request whose headers do not match is refused.

## Reporting a vulnerability

Read this page before reporting. A finding that depends on authentication being off describes the configuration above rather than a flaw in the server. `SECURITY.md` covers the reporting channel, scope and response targets.

## Routes that carry their own credential

Two more kinds of route answer without an MCP credential. Each checks a credential of its own instead.

- **Transfer links.** `create_upload_link` and `create_download_link` return a `/transfer/...` URL whose token is the credential: whoever holds the URL can use it. A link stops working after its first successful use, or when it expires. `MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S` sets the lifetime (one hour by default), and `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S` caps what a caller may ask for. The links exist only over HTTP with `MARKDOWN_VAULT_MCP_BASE_URL` set; see [Transfer links](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/transfer-links/index.md).
- **Git webhooks.** `/github-webhook` and `/gitlab-webhook` exist only when a webhook secret is set. A request must carry a valid signature or token for that secret, and a valid push event makes the server pull and reindex; see [Webhooks](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/git-integration/#push-triggered-pull-webhooks).
