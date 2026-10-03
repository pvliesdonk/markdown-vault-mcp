---
description: "Every way to install Markdown Vault MCP, by where it runs, with a check of what you installed."
kind: how-to
---

# Installation

The client tutorials each install the server one way. This page lists every way, so you can pick by where the server runs, and ends with how to check what you installed. Which channel each release reaches is on the [Upgrade](../upgrade/index.md#release-channels) page.

Before you expose the server beyond your own machine, read the [security model](../security-model.md).

## As a command on your machine

For a client that starts the server itself (Claude Desktop, or Claude Code over stdio), install it as a command with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install "markdown-vault-mcp"
```

uv gives the server its own environment and finds a Python for it, downloading one when your machine has none. The `markdown-vault-mcp` command lands in uv's executable directory, `~/.local/bin` unless you configured another (`uv tool dir --bin` prints it). `uv tool upgrade markdown-vault-mcp` moves to the newest release; `uv tool install "markdown-vault-mcp==X.Y.Z"` pins one, a release candidate included (`X.Y.ZrcN`).

In an environment you manage yourself, `pip install "markdown-vault-mcp"` installs the same package. To run a release without installing anything:

```bash
uvx --from "markdown-vault-mcp" markdown-vault-mcp serve
```

Quote the package name whenever it carries an extra, `"markdown-vault-mcp[extra]"`: without the quotes, zsh, the default shell on macOS, reads the brackets as a pattern and the command fails. The extras this server defines are under [Notes for this server](#notes-for-this-server).

## Claude Desktop

Each release ships a `.mcpb` bundle on the [releases page](https://github.com/pvliesdonk/markdown-vault-mcp/releases). Open it with Claude Desktop, or choose **Settings** › **Extensions** › **Advanced settings** › **Install Extension…**; Claude Desktop asks for the settings this server declares and configures itself. The bundle carries no code: it pins the released package at its own version and fetches it from PyPI with `uvx` on first launch, so the machine needs network access then. A release candidate's bundle installs the same way. The [Claude Desktop](claude-desktop.md) tutorial takes it from there.

## Claude Code

Two commands inside Claude Code install the plugin, which runs the released package:

```text
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

The marketplace entry follows stable releases only. The [Claude Code](claude-code.md) tutorial covers the scope question, settings and the command-line route.

## Docker

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:latest
```

`latest` is the newest stable release. The image's other tags (exact versions, `rc`, `edge`) are on the [Docker](../deploy/docker.md#image-tags) page, which is where a deployment continues.

## Linux packages

Each release ships a `.deb` and an `.rpm`. The names below carry no version and fetch the newest stable release:

```bash
curl -LO https://github.com/pvliesdonk/markdown-vault-mcp/releases/latest/download/markdown-vault-mcp_latest.deb
sudo apt install ./markdown-vault-mcp_latest.deb
```

```bash
curl -LO https://github.com/pvliesdonk/markdown-vault-mcp/releases/latest/download/markdown-vault-mcp_latest.rpm
sudo dnf install ./markdown-vault-mcp_latest.rpm
```

The package needs `python3` 3.11 or newer with `venv`. It creates a virtual environment under `/opt/markdown-vault-mcp/venv` and installs `markdown-vault-mcp` at the package's own version from PyPI, so the machine needs network access during the install; a release candidate's package installs the wheel attached to its own GitHub release instead. The package also installs:

- `/etc/markdown-vault-mcp/env`, copied from `env.example` on first install, readable by root only and never overwritten by an upgrade: the server's configuration, read by the service.
- `/var/lib/markdown-vault-mcp`, the state directory, owned by the `markdown-vault-mcp` system user the package creates.
- The systemd unit `markdown-vault-mcp.service`, which runs `markdown-vault-mcp serve --transport http` as that user. It is installed but not enabled; to start it now and on every boot:

```bash
sudo systemctl enable --now markdown-vault-mcp
```

An upgrade restarts a running service. [Deploy](../deploy/index.md) covers the environment file and the service from here.

## From source

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp
cd markdown-vault-mcp
uv sync --all-extras --all-groups
uv run markdown-vault-mcp serve
```

This is the setup for changing the project; [Contribute](../contribute/index.md) continues from here.

## Check what you installed

- The command: `markdown-vault-mcp --help` lists its commands and options; the [command line](../reference/cli.md) reference describes them.
- A connected client, local or remote: ask Claude which version is running. The `get_server_info` tool reports `server_version`.

## Notes for this server

<!-- DOMAIN-INSTALL-EXTRA-START -->
## Optional extras

The PyPI package installs the library alone. Pick the extras for how you run it:

=== "MCP server"

    ```bash
    pip install "markdown-vault-mcp[mcp]"
    ```
    Adds FastMCP for running as an MCP server.

=== "API embeddings"

    ```bash
    pip install "markdown-vault-mcp[embeddings-api]"
    ```
    Adds the openai SDK + httpx + numpy for Ollama/OpenAI/Voyage embeddings via API.

=== "Local embeddings"

    ```bash
    pip install "markdown-vault-mcp[embeddings]"
    ```
    Adds FastEmbed + numpy for local embeddings.

=== "All (recommended)"

    ```bash
    pip install "markdown-vault-mcp[all]"
    ```
    MCP + FastEmbed + API embeddings.

## Using uv

```bash
uv pip install "markdown-vault-mcp[all]"
```

## Docker image

The Docker image uses `[all]` (MCP + FastEmbed + API embeddings). Semantic search is available by default with FastEmbed and can switch to Ollama/OpenAI/Voyage when configured.

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:edge
```

See [Docker deployment](../deploy/docker.md) for compose setup and volume configuration.

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

See the [systemd deployment guide](../deployment/systemd.md) for full configuration and troubleshooting.

## Claude Code Plugin

Install markdown-vault-mcp directly in Claude Code using the plugin marketplace:

```
/plugin marketplace add pvliesdonk/claude-plugins
/plugin install markdown-vault-mcp@pvliesdonk
```

See the [Claude Code plugin guide](../guides/claude-code-plugin.md) for configuration and usage details.

## Verify Installation

```bash
# Check the CLI is available
markdown-vault-mcp --help

# Quick test with a local vault
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/your/markdown/files
markdown-vault-mcp search "hello world"
```

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
