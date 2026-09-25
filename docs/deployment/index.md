# Deployment

This section covers deploying markdown-vault-mcp in various environments.

## Deployment Options

| Method | Best For | Guide |
|--------|----------|-------|
| [Docker Compose](docker.md) | Self-hosted, homelab, production | Full compose setup with volumes, Traefik, and troubleshooting |
| [systemd](systemd.md) | Native Linux service, bare-metal | .deb/.rpm package or manual install with security hardening |
| [Claude Desktop](claude-desktop.md) | Local development, personal use | Direct stdio integration with Claude Desktop |
| [OIDC Authentication](oidc.md) | Multi-user, HTTP deployments | Token-based auth with Authelia, Keycloak, etc. |

## Prerequisites

- **Docker deployments:** Docker Engine 24+ and Docker Compose v2
- **systemd deployments:** Python 3.11+ on a Linux system with systemd
- **Claude Desktop:** Python 3.11+ with `markdown-vault-mcp[mcp]` installed
- **All deployments:** A directory of Markdown files to serve

## Transport Modes

The MCP server supports three transport modes:

| Transport | Flag | Use Case |
|-----------|------|----------|
| `stdio` | `--transport stdio` (default) | Claude Desktop and other stdio-based clients |
| `sse` | `--transport sse` | Server-Sent Events for web clients |
| `http` | `--transport http` | Streamable HTTP (required for Docker and OIDC) |

```bash
# Default (stdio) — for Claude Desktop
markdown-vault-mcp serve

# HTTP on this machine only (--host defaults to 127.0.0.1)
markdown-vault-mcp serve --transport http --port 8000

# HTTP for other machines: configure authentication first
MARKDOWN_VAULT_MCP_BEARER_TOKEN="$(openssl rand -hex 32)" \
  markdown-vault-mcp serve --transport http --host 0.0.0.0 --port 8000
```

Binding to `127.0.0.1` limits which machines can connect; authentication is what protects the server. See the [security model](../guides/security-model.md) and [Authentication](../guides/authentication.md).

## Example .env Files

| File | Description |
|------|-------------|
| `examples/obsidian-readonly.env` | Obsidian vault, read-only, Ollama embeddings |
| `examples/obsidian-readwrite.env` | Obsidian vault, read-write with managed git mode (clone/pull/commit/push) |
| `examples/obsidian-oidc.env` | Obsidian vault, read-only, OIDC authentication |
| `examples/ifcraftcorpus.env` | Strict frontmatter enforcement, read-only corpus |
