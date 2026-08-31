<!-- DOMAIN-START -->
<p align="center">
  <img src="assets/icon.svg" alt="Markdown Vault MCP logo" width="128" height="128">
</p>
<!-- DOMAIN-END -->

# Markdown Vault MCP

<!-- mcp-name: io.github.pvliesdonk/markdown-vault-mcp -->

[![CI](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp/graph/badge.svg)](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp) [![repowise](https://api.repowise.dev/badge/wiki/pvliesdonk/markdown-vault-mcp.svg)](https://repowise.dev/repo/pvliesdonk/markdown-vault-mcp) [![Code health](https://api.repowise.dev/badge/health/pvliesdonk/markdown-vault-mcp.svg)](https://repowise.dev/repo/pvliesdonk/markdown-vault-mcp) [![PyPI](https://img.shields.io/pypi/v/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![Python](https://img.shields.io/pypi/pyversions/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![License](https://img.shields.io/github/license/pvliesdonk/markdown-vault-mcp)](LICENSE) [![Docker](https://img.shields.io/github/v/release/pvliesdonk/markdown-vault-mcp?label=ghcr.io&logo=docker)](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp) [![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://pvliesdonk.github.io/markdown-vault-mcp/) [![llms.txt](https://img.shields.io/badge/llms.txt-available-brightgreen)](https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt) [![Template](https://img.shields.io/badge/dynamic/yaml?url=https://raw.githubusercontent.com/pvliesdonk/markdown-vault-mcp/main/.copier-answers.yml&query=%24._commit&label=template)](https://github.com/pvliesdonk/fastmcp-server-template)

Generic markdown vault MCP with hybrid search

**[Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/)** | **[Config wizard](https://pvliesdonk.github.io/markdown-vault-mcp/latest/configuration-generator/)** | **[PyPI](https://pypi.org/project/markdown-vault-mcp/)** | **[Docker](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp)**

## Features

<!-- DOMAIN-START -->
- **Hybrid search**: SQLite FTS5 keyword search (BM25, porter stemming) and semantic search (FastEmbed, Ollama, OpenAI, or Voyage AI embeddings, plus any OpenAI-compatible endpoint via `OPENAI_BASE_URL`), fused with Reciprocal Rank Fusion; diversity-aware ranking returns sentence-scale snippets with full-section recovery via `read(path, section=heading)`. See the [Embeddings guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/embeddings/), including the [recipe for OpenAI-compatible endpoints](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/embeddings/#openai-compatible-endpoints).
- **Frontmatter-aware indexing**: YAML frontmatter fields become filterable and searchable, with optional required-field enforcement and adaptive heading-level chunking for long documents.
- **Write operations**: the write tools (`write`, `edit`, `append`, `delete`, `rename`, `move_folder`, `fetch`, `git_sync`, the `okf_*` tools, `create_upload_link`) are registered by default and hidden when `MARKDOWN_VAULT_MCP_READ_ONLY=true`; writes update the index automatically, per-folder `_conventions.md` authoring rules are surfaced to LLM clients at write time, and attachments (PDFs, images, and other non-markdown files) are read/write too.
- **Incremental reindexing**: hash-based change detection with boot-time reconciliation; the vector index converges to the reconciled chunk set, and parse-pipeline upgrades rebuild the index once automatically.
- **Git integration**: optional auto-commit on every write with deferred push, a pull loop or GitHub webhook for external changes, and git history/diff tools. See the [Git integration guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/git-integration/).
- **OKF-aware**: recognizes [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog) bundles and annotates results with each note's type, lifecycle status, staleness, and trust tier, plus conformance audit and migration tooling. See the [OKF guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/okf/).
- **MCP surface**: 34 LLM-visible tools, 9 resources, and 8 prompt templates, plus browser-based MCP Apps views and one-time transfer links. Full references: [Tools](https://pvliesdonk.github.io/markdown-vault-mcp/latest/tools/), [Resources](https://pvliesdonk.github.io/markdown-vault-mcp/latest/resources/), [Prompts](https://pvliesdonk.github.io/markdown-vault-mcp/latest/prompts/), [MCP Apps](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/mcp-apps/), [Transfer links](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/transfer-links/), [CLI](https://pvliesdonk.github.io/markdown-vault-mcp/latest/cli/).
<!-- DOMAIN-END -->

## What you can do with it

<!-- DOMAIN-START -->
With this server mounted in Claude, you can:

- **Capture a URL as a note.** "Fetch <url>, summarize as a Resource note under `3-Resources/`, and link any existing notes on the topic." Claude composes `fetch` + `search` + `write`.
- **Research a topic into your vault.** "Research product security regulations, compare them, and create a set of interlinked notes: one per regulation, plus a map-of-content." Claude composes web-search tools (client-side) + `write` with wikilinks. See the [Research workflows guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/research-workflows/) for the full loop.
- **Distill today's thinking.** "Summarize today's conversations into Inbox notes." Claude.ai only; uses `conversation_search` + `recent_chats` + `write`. The [`para-capture-chats`](examples/para/prompts/para-capture-chats.md) prompt is the one-click version.
- **Find missing links.** Fire the [`propose-links`](https://pvliesdonk.github.io/markdown-vault-mcp/latest/prompts/#propose-links) prompt from the `+` menu: it scans recently modified notes and proposes links between notes that aren't yet connected, writing them on confirmation.
- **Split or merge captures.** "Split this Inbox note into two." / "Merge this into `<existing note>` instead of duplicating." Claude composes `read` + `write` + `delete`.

The vault needs no external scheduler or separate capture app: it sits behind your conversations and absorbs their output.
<!-- DOMAIN-END -->

<!-- ===== TEMPLATE-OWNED SECTIONS BELOW — DO NOT EDIT; CHANGES WILL BE OVERWRITTEN ON COPIER UPDATE ===== -->

## Installation

### From PyPI

```bash
pip install markdown-vault-mcp
```

If you add optional extras via the `PROJECT-EXTRAS-START` / `PROJECT-EXTRAS-END` sentinels in `pyproject.toml`, document them below:

<!-- DOMAIN-START -->
```bash
pip install markdown-vault-mcp[mcp]             # FastMCP server
pip install markdown-vault-mcp[embeddings-api]  # Ollama/OpenAI embeddings via API
pip install markdown-vault-mcp[embeddings]      # FastEmbed local embeddings
pip install markdown-vault-mcp[file-watcher]    # watchdog-based external-change watcher
pip install markdown-vault-mcp[all]             # MCP + FastEmbed + API embeddings
```

For the Claude Code plugin channel (`/plugin install markdown-vault-mcp@pvliesdonk`) and all other install routes, see the [Installation guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/installation/) and the [Claude Code plugin guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/claude-code-plugin/).
<!-- DOMAIN-END -->

### From source

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
uv sync --all-extras --all-groups
```

### Docker

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:latest
```

To run the newest merged code instead of the newest release, use the rolling `edge` tag. It is rebuilt on every merge to `main` and carries no version identity. See [Image tags](docs/deployment/docker.md#image-tags) for the full tag list.

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:edge
```

A `compose.yml` ships at the repo root as a starting point. Copy `.env.example` to `.env`, edit, and `docker compose up -d`.

To attach a remote Python debugger (development only; the protocol is unauthenticated), see [Remote debugging](docs/deployment/docker.md#remote-debugging).

### Linux packages (.deb / .rpm)

Download `.deb` or `.rpm` packages from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page. Both install a hardened systemd unit; env configuration is sourced from `/etc/markdown-vault-mcp/env` (copy from the shipped `/etc/markdown-vault-mcp/env.example`).

### Claude Desktop (.mcpb bundle)

Download the `.mcpb` bundle from the [GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page and double-click to install, or run:

```bash
mcpb install markdown-vault-mcp-<version>.mcpb
```

Claude Desktop prompts for required env vars via a GUI wizard, with no manual JSON editing needed.

For manual Claude Desktop configuration and setup options, see [Claude Desktop deployment](docs/deployment/claude-desktop.md).

## Release channels

Artifacts ship on three channels. Each row lists exactly what that channel publishes.

| Channel | Version identity | Artifacts |
|---|---|---|
| `edge` (rolling) | None; the commit is the identity | Docker image `:edge` rebuilt on every merge to `main`; `.mcpb` bundle as the `mcpb-bundle-edge` workflow artifact; Claude Code plugin `.zip` as the `plugin-zip-edge` artifact; rolling `unstable` docs version. It leaves no git tag, GitHub release, or PyPI entry behind. |
| Pre-release | `vX.Y.Z-rc.N`, computed and reviewed in its release pull request | PyPI (as the pre-release `X.Y.ZrcN`); GitHub release with wheels, `sdist`, `.deb`/`.rpm` packages, `.mcpb` bundle, plugin `.zip`, and SBOM attached; Docker image under its immutable `vX.Y.Z-rc.N` tag plus the ordering-aware rolling `rc` tag. Skips the plugin marketplace, the MCP registry, and the docs deploy. |
| Stable | `vX.Y.Z` | Everything: PyPI, Docker (version tag plus ordering-aware `latest` / `vX` / `vX.Y`), `.deb`/`.rpm`, GitHub release assets (wheels, `sdist`, `.mcpb` bundle, plugin `.zip`, SBOM), plugin marketplace and MCP registry entries (when the release is the newest stable), versioned docs with an ordering-aware `latest` alias. |

Pre-releases reach PyPI so that a candidate's `.mcpb` bundle installs: the bundle points at PyPI rather than carrying the code. Ordinary installers never see them, because a PEP 440 resolver skips pre-releases unless the requirement pins one or you pass `--pre`. Ask for a candidate by name with `pip install markdown-vault-mcp==X.Y.ZrcN`. PyPI spells it in the PEP 440 canonical form, while tags use SemVer. Rolling pointers are ordering-aware, so a patch release cut from an old `release/X.Y` branch never moves `latest`-style tags back to older content, and a candidate for an already-released version never moves `rc`. See [Release process](docs/deployment/release-process.md) for the full model.

## Quick start

```bash
markdown-vault-mcp serve                                # stdio transport
markdown-vault-mcp serve --transport http --port 8000   # streamable HTTP
```

For library usage (embedding the domain logic without the MCP transport), import from the `markdown_vault_mcp` package directly. See the project's domain modules under `src/markdown_vault_mcp/` for entry points.

### Server info

The server registers a built-in `get_server_info` tool (via `fastmcp_pvl_core.register_server_info_tool`) so operators can confirm the deployed version with a single MCP call. The default response carries `server_name`, `server_version`, and `core_version`. Servers that talk to a remote upstream wire upstream version reporting inside the `DOMAIN-UPSTREAM-START` / `DOMAIN-UPSTREAM-END` sentinel in `src/markdown_vault_mcp/server.py`; see [`tool-registration`](.agents/skills/tool-registration/SKILL.md#server-info-tool-get_server_info) for the wiring pattern.

## Configuration

The most common environment variables, shared across all
`fastmcp-pvl-core`-based services:

<!-- GENERATED-ENV-TABLE-CORE-START — generated by scripts/gen_config_surface.py; do not edit -->
| Variable | Default | Description |
|---|---|---|
| `MARKDOWN_VAULT_MCP_KV_STORE_URL` | `file:///data/state` | Persistent-state backend URL shared by every pvl-core subsystem that needs state. `memory://` is in-process and lost on restart; `file:///path` persists on one server; `redis://`, `dynamodb://` and `mongodb://` each need their matching extra. When unset, defaults to `file:///data/state` (the volume family Docker images mount), or to `memory://` (with a warning) on a host where that directory is not usable. |
| `FASTMCP_LOG_LEVEL` | `INFO` | Log level for FastMCP internals and app loggers (DEBUG / INFO / WARNING / ERROR / CRITICAL). The -v CLI flag overrides to DEBUG. |
| `FASTMCP_ENABLE_RICH_LOGGING` | `true` | Set false for plain or structured JSON log output. |
<!-- GENERATED-ENV-TABLE-CORE-END -->

This table and the one under [Domain configuration](#domain-configuration)
are curated subsets. The complete generated reference, with every variable
the server reads, is the [configuration reference](docs/configuration.md);
`.env.example` lists the same surface in copy-paste form.

## Authentication

Callers authenticate via a bearer token or OIDC (mutually exclusive). See the [Authentication guide](docs/guides/authentication.md) for setup, mapped multi-subject tokens, OIDC, and troubleshooting.

## Post-scaffold checklist

After `copier copy` and `gh repo create --push`:

1. **Fill in the DOMAIN blocks** (every section marked with a `DOMAIN` sentinel comment) in this README and in `AGENTS.md`. The `GENERATED-ENV-TABLE-*` regions are not DOMAIN blocks; the config generator owns them and rewrites them on every run.
2. Configure GitHub secrets (see below).
3. Install dev + docs tooling: `uv sync --all-extras --all-groups`.
4. Install pre-commit hooks: `uv run pre-commit install`.
5. Run the gate locally: `uv run pytest -x -q && uv run ruff check --fix . && uv run ruff format . && uv run mypy src/ tests/`.
6. Push the first commit. CI should be green.

## GitHub secrets

CI workflows reference two required repository secrets and one optional Claude token. Configure them via **Settings → Secrets and variables → Actions** or with `gh secret set`:

| Secret | Used by | How to generate |
|---|---|---|
| `RELEASE_TOKEN` | `release-prepare.yml`, `release.yml`, `copier-update.yml`, `renovate.yml`, `bootstrap.yml` | Fine-grained PAT at <https://github.com/settings/personal-access-tokens/new> with `contents: write`, `pull_requests: write`, and `administration: write` (bootstrap applies the repository rulesets + auto-merge). Must belong to a repository admin: the shipped rulesets grant bypass to the admin role, and the release tag + GitHub release that knope creates after a release pull request merges rely on it (pull requests the token opens also need it so their CI runs). Scoped to this repo. |
| `CODECOV_TOKEN` | `ci.yml` | <https://codecov.io>: sign in with GitHub and add the repo. The upload token is on its settings page. |
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude.yml` | Optional. Run `claude setup-token` locally and configure this only for `@claude` or opted-in automatic review. |

```bash
gh secret set RELEASE_TOKEN
gh secret set CODECOV_TOKEN
# Optional: enables @claude and opted-in automatic review.
gh secret set CLAUDE_CODE_OAUTH_TOKEN
```

> Dependency updates are handled by **Renovate** (`renovate.yml`), which reuses
> `RELEASE_TOKEN`. It maintains `uv.lock` and auto-merges patch/minor bumps once
> the `CI Success` check is green; `bootstrap.yml` enables auto-merge and applies
> the repository rulesets (`.github/rulesets/`) on first push. See
> [Repository Protection](docs/deployment/repository-protection.md) for the
> per-branch posture and bypass model. GitHub Actions are updated in the copier
> template and arrive via `copier update`, not per-repo.

`GITHUB_TOKEN` is auto-provided; no action needed.

## Local development

The PR gate (matches CI):

```bash
uv run pytest -x -q                                  # tests
uv run ruff check --fix . && uv run ruff format .    # lint + format
uv run mypy src/ tests/                              # type-check
```

Pre-commit runs a subset of the gate on each commit; see `.pre-commit-config.yaml` for details, or [`AGENTS.md`](AGENTS.md) for the full Hard PR Acceptance Gates.

## Troubleshooting

### Moving a scaffolded project

`uv sync` creates `.venv/bin/*` scripts with absolute shebangs pointing at the venv Python. If you move the repo after scaffolding (`mv /old/path /new/path`), `uv run pytest` fails with `ModuleNotFoundError: No module named 'fastmcp'` because the stale shebang resolves to a different interpreter than the venv's site-packages.

**Fix:**

```bash
rm -rf .venv
uv sync --all-extras --all-groups
```

`uv run python -m pytest` also works as a one-shot workaround (bypasses the stale entry-script shim).

### `uv.lock` refresh after `copier update`

When `copier update` introduces new dependencies (such as a new extra added to `pyproject.toml.jinja`), the CI install step runs `uv sync --locked`, which fails against a stale lockfile. Run `uv lock` locally and commit the refreshed `uv.lock` alongside accepting the copier-update PR.

CI installs with `--locked` (and the review workflow with `--frozen`) so no job ever rewrites `uv.lock` in its own workspace: a job that re-locks hides the drift it just repaired, and a dirty workspace breaks any later `git checkout` in the same job. Lockfile drift then shows up as a red install step with a clear message, not as a silent mutation.

## Contributing

`CONTRIBUTING.md` holds the rules for issues and pull requests, and where a
fix belongs: `fastmcp-pvl-core` for library code, the template for
template-owned files, this repository for anything inside its `DOMAIN-*` /
`CONFIG-*` / `PROJECT-*` blocks. `AGENTS.md` carries the conventions and
gates; the skills under `.agents/skills/` carry the task procedures, among
them `code-review` (local self-review before a pull request),
`writing-release-notes` (release notes),
`applying-template-updates` (the weekly template update pull request) and
`authoring-issues-prs` (filing). The release procedure is in
[docs/deployment/release-process.md](docs/deployment/release-process.md);
the template update procedure in
[docs/deployment/template-updates.md](docs/deployment/template-updates.md).

## Links

- [Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/)
- [llms.txt](https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt)
- [FastMCP](https://gofastmcp.com)
- [fastmcp-pvl-core](https://pypi.org/project/fastmcp-pvl-core/)

<!-- ===== TEMPLATE-OWNED SECTIONS END ===== -->

## Domain configuration

The variables this project features as its entry points (domain variables use the `MARKDOWN_VAULT_MCP_` prefix):

<!-- GENERATED-ENV-TABLE-DOMAIN-START — generated by scripts/gen_config_surface.py; do not edit -->
| Variable | Default | Required | Description |
|---|---|---|---|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | `/data/vault` | No | Path to the markdown vault directory. Required; the server refuses to start without it. Symbolic links inside the vault are followed on Python 3.13+. |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `false` | No | Set to true to hide the write tools (write, edit, append, delete, rename, move_folder, fetch, git_sync, the okf_* tools, create_upload_link) and serve a search-only vault. git_sync also needs managed git mode; create_upload_link needs an HTTP transport. |
| `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING` | `false` | No | Set to true to refuse a write that would overwrite an existing file when no if_match etag is supplied. Deliberate replacement (read first, pass if_match) still works, and edit / append / delete / rename are unaffected. |
| `MARKDOWN_VAULT_MCP_DEFAULT_SEARCH_MODE` | `auto` | No | Mode used when a search call omits 'mode': auto, keyword, semantic, or hybrid. The default 'auto' picks hybrid when embeddings are configured and keyword when they are not. Pin 'keyword' to keep unqualified searches off the embedding provider (each hybrid or semantic search embeds the query, which costs an API call on a metered provider). A configured semantic/hybrid default also degrades to keyword without embeddings, so no setting can make a vault unsearchable; an explicit mode= argument is never downgraded. |
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | (none) | No | Embedding provider: openai, voyage, ollama, or fastembed. Unset auto-detects from the environment (never voyage). Any OpenAI-compatible endpoint works with openai plus OPENAI_BASE_URL; see the embeddings guide. |
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | (none) | No | HTTPS remote URL for managed git mode: the server clones into an empty SOURCE_DIR on startup (or validates an existing origin) and enables the pull loop, auto-commit, and deferred push. |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER` | `true` | No | Watch the vault for external filesystem changes; auto-disabled when git pull or the webhook is active. Requires the file-watcher extra. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL` | (none) | No | OpenAI-compatible endpoint base URL for the summarize tool; setting it enables the tool even without an API key. The bare OPENAI_BASE_URL routes traffic only when a key already enables the feature. |
<!-- GENERATED-ENV-TABLE-DOMAIN-END -->

This is a curated subset: a field appears here when its `tags` metadata includes `readme`. Every domain variable is documented in the [configuration reference](docs/configuration.md), grouped the same way the config wizard presents them.

Domain-config fields are composed inside `src/markdown_vault_mcp/config.py` between the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END` sentinels; env reads go through `fastmcp_pvl_core.env(_ENV_PREFIX, "SUFFIX", default)` so naming stays consistent, and field invariants go in `__post_init__` between the `CONFIG-VALIDATE-START` / `CONFIG-VALIDATE-END` sentinels. Each field's `metadata` `help`, `tags`, and `wizard` group generate the reference tables directly, so keep them accurate and complete.

## Key design decisions

<!-- DOMAIN-START -->
- **Document identity is the relative path** with `.md` extension; frontmatter is optional by default (`REQUIRED_FIELDS` opts into enforcement).
- **Hybrid search uses Reciprocal Rank Fusion** over the FTS5 and vector result lists, with diversity-aware ranking capping chunks per document.
- **Tool semantics mirror Claude Code's Read/Write/Edit patterns**, so LLM clients drive the vault with habits they already have.
- **The library is synchronous**; the MCP layer wraps calls in `asyncio.to_thread()`.
- **Indexing is hash-based**: unchanged files are never re-parsed, and any change to how stored rows derive from a note's bytes bumps `INDEX_SEMANTICS_VERSION` so deployed vaults rebuild themselves once on upgrade.

The full decision log lives in the [design document](docs/design/design.md).
<!-- DOMAIN-END -->
