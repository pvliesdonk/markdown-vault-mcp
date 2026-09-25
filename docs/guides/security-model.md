# Security model

This page states what markdown-vault-mcp protects and what it leaves to the operator. [Authentication](authentication.md) covers how to configure each mode, and `SECURITY.md` in the repository covers how to report a vulnerability.

## Authentication is the boundary

Over HTTP, authentication is the only access control: a bearer token, a mapped bearer token file, or OIDC. A request without a valid credential is refused before it reaches a tool. It is the same model as any other MCP server reached over HTTP.

## Running without authentication

With no authentication variables set, the server starts in mode `none` and logs `auth_mode_resolved` at warning level with `mode=none`. Every client that can open a connection to the port then has the full tool surface. Turning authentication off is a decision to trust everything that can reach the port.

The bind address decides which machines reach the port, and nothing more. Binding to `127.0.0.1` keeps other machines out. It does not keep out other processes on the host, nor a web page in a browser on the host that uses DNS rebinding. Without authentication, run the server only where everything that reaches the port is trusted, such as a single-user workstation or a private network you control.

## stdio

Over stdio there is no network listener and no authentication. The MCP client starts the server as a child process, and the server trusts that client. Authentication variables have no effect there, and the server logs `auth_configured_but_stdio_skips_enforcement` when they are set.

## What an authenticated caller can reach

An authenticated caller has every tool the instance exposes. Each tool runs with the server's own privileges: its filesystem and network access, and any credential in its configuration. `MARKDOWN_VAULT_MCP_TOOLS_ALLOW` and `MARKDOWN_VAULT_MCP_TOOLS_DENY` trim that set for an instance; see [Configuration](../configuration.md).

<!-- DOMAIN-SECURITY-MODEL-SURFACE-START — what THIS server's tools reach; kept across copier update -->
Every tool operates on the vault directory the server is configured to serve. Read tools (`search`, `read`, `list`, `toc`, `similar`, `context`, `stats`, git history) only read it. Write tools (`write`, `edit`, `delete`, `rename`, move, attachment writes) create, change, and remove files inside that directory; path-traversal validation confines every path to the vault root regardless of what the caller passes.

Three tools reach outside the vault directory:

- **`fetch`** downloads a caller-supplied `http://` or `https://` URL and saves the response into the vault. It is SSRF-hardened: the resolved IP must be publicly routable (private, loopback, link-local, CGNAT, and other reserved ranges are refused), the connection is pinned to the validated address, ambient proxy and `.netrc` settings are ignored, and every redirect hop is re-validated the same way.
- **Semantic search and embeddings** (`search`, `build_index`/`reindex`, embeddings status), when an embedding provider is configured, send note text to that provider's endpoint (Ollama, an OpenAI-compatible API, or Voyage) to compute vectors.
- **`summarize`**, when a summarization backend is configured, sends note text to that backend's OpenAI-compatible chat-completions endpoint.

In git-managed mode, the git tools (sync, history, diff) push to and pull from the configured remote over the configured protocol; the GitHub/GitLab webhook routes accept pushes from that same remote and trigger a pull and reindex, originating no outbound request beyond it.

Credentials the tools use on the caller's behalf — never supplied per call, always read from the server's own environment — are the git remote's token or SSH key, the embedding provider's API key, and the summarization backend's API key.
<!-- DOMAIN-SECURITY-MODEL-SURFACE-END -->

## What answers without a credential

Some routes answer anyone who can reach the port, with authentication on or off:

- `/health` and `/health/ready`. `MARKDOWN_VAULT_MCP_HEALTH_DETAIL` decides how much they say; see [Health](../deployment/docker.md#health).
- In the OIDC modes, the OAuth metadata and sign-in routes a client uses before it holds a token.

## What the operator is responsible for

- **TLS.** The server speaks plain HTTP. Terminate TLS at a reverse proxy in front of it.
- **Exposure.** Which interfaces and networks can reach the port. [Ports](../deployment/docker.md#ports) covers the published port of the Docker deployment.
- **Secrets.** Bearer tokens, OIDC client secrets and the credentials the tools use live in the environment or in files you control. Anyone who can read them can act as the server.
- **The debugger port.** A debug build's debugger port grants code execution to anyone who reaches it; see [Remote debugging](../deployment/docker.md#remote-debugging).

## Host and Origin validation

FastMCP can refuse requests whose `Host` or `Origin` header does not name the server, which blocks DNS rebinding against a server running without authentication. It is off by default, and the model above does not depend on it. To turn it on, set `FASTMCP_HTTP_HOST_ORIGIN_PROTECTION` to `auto`, which checks requests that reach the server on a local address, or to `true`, which checks every request against `FASTMCP_HTTP_ALLOWED_HOSTS` (a JSON list of host names). Behind a reverse proxy, test it before relying on it: a request whose headers do not match is refused.

## Reporting a vulnerability

Read this page before reporting. A finding that depends on authentication being off describes the configuration above rather than a flaw in the server. `SECURITY.md` covers the reporting channel, scope and response targets.

<!-- DOMAIN-SECURITY-MODEL-EXTRA-START -->
<!-- Project-specific security notes go here; kept across copier update. -->
<!-- DOMAIN-SECURITY-MODEL-EXTRA-END -->
