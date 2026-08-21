# Installation

## From PyPI

```bash
pip install markdown-vault-mcp
```

With optional dependencies:

=== "MCP server"

    ```bash
    pip install markdown-vault-mcp[mcp]
    ```
    Adds FastMCP for running as an MCP server.

=== "API embeddings"

    ```bash
    pip install markdown-vault-mcp[embeddings-api]
    ```
    Adds the openai SDK + httpx + numpy for Ollama/OpenAI embeddings via API.

=== "Local embeddings"

    ```bash
    pip install markdown-vault-mcp[embeddings]
    ```
    Adds FastEmbed + numpy for local embeddings.

=== "All (recommended)"

    ```bash
    pip install markdown-vault-mcp[all]
    ```
    MCP + FastEmbed + API embeddings.

## Using uv

```bash
uv pip install markdown-vault-mcp[all]
```

## From Source

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
uv sync --all-extras --all-groups
```

## Docker

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:latest
```

The Docker image uses `[all]` (MCP + FastEmbed + API embeddings). Semantic search is available by default with FastEmbed and can switch to Ollama/OpenAI when configured.

The `latest` tag is the newest stable release. For early adopters who want to test unreleased changes, the rolling `edge` tag tracks every merge to `main` and carries no version identity; see [Image tags](deployment/docker.md#image-tags) for the full list. The floating `:latest`, `:vN`, and `:vN.M` tags only move on stable releases.

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:edge
```

See [Docker deployment](deployment/docker.md) for compose setup and volume configuration.

## Linux Packages (.deb / .rpm)

Download `.deb` or `.rpm` packages from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page.

=== "Debian / Ubuntu"

    ```bash
    sudo dpkg -i markdown-vault-mcp_*.deb
    sudo apt-get install -f   # resolve dependencies if needed
    ```

=== "Fedora / RHEL"

    ```bash
    sudo rpm -i markdown-vault-mcp-*.rpm
    ```

The packages install:

| Path | Purpose |
|------|---------|
| `/opt/markdown-vault-mcp/venv/` | Python virtualenv (created by post-install) |
| `/etc/markdown-vault-mcp/env` | Configuration file (created from template on first install) |
| `/var/lib/markdown-vault-mcp/` | State directory (index, embeddings, vault data) |
| `/usr/lib/systemd/system/markdown-vault-mcp.service` | Systemd unit file with security hardening |

A `markdown-vault-mcp` system user and group are created automatically.

After installing, edit `/etc/markdown-vault-mcp/env` to set at least `MARKDOWN_VAULT_MCP_SOURCE_DIR`, then:

```bash
sudo systemctl enable --now markdown-vault-mcp
```

See the [systemd deployment guide](deployment/systemd.md) for full configuration and troubleshooting.

## Claude Code Plugin

Install markdown-vault-mcp directly in Claude Code using the plugin marketplace:

```
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

See the [Claude Code plugin guide](guides/claude-code-plugin.md) for configuration and usage details.

## Verify Installation

```bash
# Check the CLI is available
markdown-vault-mcp --help

# Quick test with a local vault
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/your/markdown/files
markdown-vault-mcp search "hello world"
```

<!-- DOMAIN-INSTALL-EXTRA-START -->
## Upgrading from earlier versions

- **Package root minimized (issue #903): import from submodules, not the package root.**
  The `markdown_vault_mcp` root package no longer re-exports the public API. Update
  library imports to their submodules:

  ```python
  # Before: from markdown_vault_mcp import Vault, ProjectConfig
  # After:  from markdown_vault_mcp.vault import Vault
  #         from markdown_vault_mcp.config import ProjectConfig
  ```

  Types such as `GroupedResult` come from `markdown_vault_mcp.types`. Only
  `__version__` remains importable from the root.
- **v2.0.0 (issue #469): `search`, `get_similar`, and `get_context.similar` now return grouped results.**
  Each file appears once with a `sections` list; the flat `content`, `heading`, and `score`
  fields have moved inside each `SectionHit`. Library consumers must update iteration:

  ```python
  # Before: result.content, result.heading
  # After:  result.sections[0].content, result.sections[0].heading
  ```

  `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` replaces `MARKDOWN_VAULT_MCP_CHUNKS_PER_DOC`.
  `SimilarItem` is removed; use `GroupedResult` (from `markdown_vault_mcp.types`).
- **Search returns snippets by default.** The `content` field carries a query-relevant
  snippet (approximately 200 words). Pass `snippet_words=0` to recover the prior
  full-chunk behaviour, or use `read(path, section=heading)` to fetch the full section
  after seeing a snippet.
- `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` default lowered from **10 MB**
  to **1 MB**.  Most LLM contexts can't survive a 10 MB base64-encoded
  attachment; the old default was a silent context-blow-up. If you have
  non-LLM consumers (scripts, CI) that need the old behaviour, set
  `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB=10` explicitly.
- `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` caps whole-document `.md` reads (default
  256 KB); reads above it raise `ValueError`. Partial reads via
  `read(path, section=heading)` bypass the cap.
<!-- DOMAIN-INSTALL-EXTRA-END -->
