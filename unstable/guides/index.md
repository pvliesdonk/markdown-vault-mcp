# Guides

Step-by-step walkthroughs for common deployment scenarios. Each guide takes you from zero to a working configuration with a verification step at the end.

## Which guide do I need?

| I want to…                                                               | Guide                                                                                                                             |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| Understand git modes (managed, commit-only, no-git)                      | [Git Integration](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/git-integration/index.md)                       |
| Connect my Obsidian vault to Claude Desktop                              | [Claude Desktop](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/claude-desktop/index.md)                         |
| Enable write/edit operations with git auto-commit                        | [Claude Desktop](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/claude-desktop/#step-2-enable-git-write-support) |
| Add semantic search to my vault                                          | [Claude Desktop](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/claude-desktop/#step-3-add-semantic-search)      |
| Run the server in a Docker container                                     | [Docker](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/docker/index.md)                                         |
| Add git write support to a container                                     | [Docker](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/docker/#step-2-add-git-write-support)                    |
| Protect my server with a bearer token                                    | [Authentication](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/authentication/#bearer-token)                    |
| Protect my server with OIDC authentication                               | [Authentication](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/authentication/#oidc)                            |
| Access my vault from desktop, mobile, AND Claude                         | [Obsidian Everywhere](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/obsidian-everywhere/index.md)               |
| Do research (literature grounding, interconnected notes, paper drafting) | [Research workflows](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/research-workflows/index.md)                 |
| Use FastEmbed for local embeddings                                       | [Embeddings](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/embeddings/#fastembed)                               |
| Use Ollama for embeddings (CPU-only)                                     | [Embeddings](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/embeddings/#ollama)                                  |
| Use OpenAI for embeddings                                                | [Embeddings](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/embeddings/#openai)                                  |
| Set up OIDC with Authelia                                                | [OIDC Providers](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/oidc-providers/#authelia)                        |
| Set up OIDC with Keycloak                                                | [OIDC Providers](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/oidc-providers/#keycloak)                        |
| Set up OIDC with Google                                                  | [OIDC Providers](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/oidc-providers/#google)                          |
| Set up OIDC with GitHub (via Keycloak)                                   | [OIDC Providers](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/oidc-providers/#github)                          |
| Build a Zettelkasten workflow                                            | [Zettelkasten](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/zettelkasten/index.md)                             |
| Build a PARA workflow (Projects/Areas/Resources/Archive)                 | [PARA](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/para/index.md)                                             |
| Use the browser-based vault views (Context Card, Graph, Browser)         | [MCP Apps](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/mcp-apps/index.md)                                     |

## Prerequisites

All guides assume you have:

- A directory of markdown files (such as an Obsidian vault)
- Python 3.11+ installed (for local installs) or Docker (for container deployments)

For installation instructions, see [Installation](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/installation/index.md). For the full environment variable reference, see [Configuration](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/configuration/index.md).
