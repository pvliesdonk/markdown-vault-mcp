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
    Adds httpx + numpy for Ollama/OpenAI embeddings via HTTP.

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

For early adopters who want to test unreleased changes, an `:unstable` tag is published by the release workflow's pre-release mode. It tracks the latest release candidate and may include in-progress features. The floating `:latest`, `:vN`, and `:vN.M` tags only move on stable releases.

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:unstable
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
<!-- Project-specific notes for installation go here; kept across copier
     update. (E.g. system dependencies, optional extras, custom configuration
     steps.) -->
<!-- DOMAIN-INSTALL-EXTRA-END -->
