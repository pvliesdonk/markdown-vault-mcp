# Guides

Step-by-step walkthroughs for common deployment scenarios. Each guide takes you from zero to a working configuration with a verification step at the end.

## Which guide do I need?

| I want to… | Guide |
|---|---|
| Understand git modes (managed, commit-only, no-git) | [Git Integration](git-integration.md) |
| Connect my Obsidian vault to Claude Desktop | [Claude Desktop](../get-started/claude-desktop.md) |
| Enable write/edit operations with git auto-commit | [Git integration](git-integration.md) |
| Add semantic search to my vault | [Embeddings](embeddings.md) |
| Run the server in a Docker container | [Docker](../deploy/docker.md) |
| Add git write support to a container | [Git integration](git-integration.md#managed-mode-recommended-for-containerized-deployments) |
| Protect my server with a bearer token | [Authentication](../deploy/authentication.md#bearer-token) |
| Protect my server with OIDC authentication | [Authentication](../deploy/authentication.md#oidc) |
| Access my vault from desktop, mobile, AND Claude | [Obsidian Everywhere](obsidian-everywhere.md) |
| Do research (literature grounding, interconnected notes, paper drafting) | [Research workflows](research-workflows.md) |
| Use FastEmbed for local embeddings | [Embeddings](embeddings.md#fastembed) |
| Use Ollama for embeddings (CPU-only) | [Embeddings](embeddings.md#ollama) |
| Use OpenAI for embeddings | [Embeddings](embeddings.md#openai) |
| Set up OIDC with Authelia | [OIDC Providers](../deploy/oidc-providers.md#authelia) |
| Set up OIDC with Keycloak | [OIDC Providers](../deploy/oidc-providers.md#keycloak) |
| Set up OIDC with Google | [OIDC Providers](../deploy/oidc-providers.md#google) |
| Set up OIDC with GitHub (via Keycloak) | [OIDC Providers](../deploy/oidc-providers.md#github) |
| Build a Zettelkasten workflow | [Zettelkasten](zettelkasten.md) |
| Build a PARA workflow (Projects/Areas/Resources/Archive) | [PARA](para.md) |
| Use the browser-based vault views (Context Card, Graph, Browser) | [Vault explorer](../use/vault-explorer.md) |

## Prerequisites

All guides assume you have:

- A directory of markdown files (such as an Obsidian vault)
- Python 3.11+ installed (for local installs) or Docker (for container deployments)

For installation instructions, see [Installation](../get-started/installation.md). For the full environment variable reference, see [Configuration](../reference/configuration.md).
