# Deploy

These pages are for an operator running the server for others over HTTP, as a container or a system service, with every caller authenticated. Each page covers one task. Start with [Docker](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/docker/index.md) for a container or [systemd](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/systemd/index.md) for the `.deb`/`.rpm` package; then [Authentication](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/authentication/index.md), then the [reverse proxy](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/reverse-proxy/index.md) that exposes it.

- [Docker](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/docker/index.md): the image, Compose, ports, volumes and health checks.
- [systemd and the Linux packages](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/systemd/index.md): the package's contents and the environment file; the unit's confinement; upgrades.
- [Authentication](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/authentication/index.md): bearer tokens or OIDC, and when to use which.
- [OIDC](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/oidc/index.md): which of the two OIDC modes to run, and how to configure it.
- [OIDC providers](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/oidc-providers/index.md): Authelia, Keycloak, Google, and GitHub through a broker.
- [Reverse proxy](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/reverse-proxy/index.md): TLS, its own hostname or a path prefix, and the routing OAuth discovery needs.
- [MCP Apps](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/mcp-apps/index.md): which clients render the server's interface, and the one setting it may need.
- [Transfer links](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/transfer-links/index.md): one-time download and upload URLs, for the person holding one and for the operator.

Before exposing the server, read the [security model](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/security-model/index.md): an authenticated caller has every tool the instance exposes, and under OIDC the identity provider alone decides who gets in.

This server needs one thing to serve: a folder of Markdown notes, `MARKDOWN_VAULT_MCP_SOURCE_DIR`. With `MARKDOWN_VAULT_MCP_GIT_REPO_URL` set, an empty folder will do, because the server clones the repository into it. Beyond the vault it calls only what you configure: the git remote, an embedding provider (Ollama, OpenAI or Voyage; FastEmbed runs in the process but downloads its model once), and the language model behind `summarize`.

Example environment files for common setups are in [`examples/`](https://github.com/pvliesdonk/markdown-vault-mcp/tree/main/examples). They cover a read-only Obsidian vault with Ollama embeddings, a read-write vault with managed git, a read-only vault behind OIDC, and a corpus that requires frontmatter fields.
