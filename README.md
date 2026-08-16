<!-- DOMAIN-START -->
<p align="center">
  <img src="assets/icon.svg" alt="Markdown Vault MCP logo" width="128" height="128">
</p>
<!-- DOMAIN-END -->

# Markdown Vault MCP

<!-- mcp-name: io.github.pvliesdonk/markdown-vault-mcp -->

[![CI](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp/graph/badge.svg)](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp) [![PyPI](https://img.shields.io/pypi/v/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![Python](https://img.shields.io/pypi/pyversions/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![License](https://img.shields.io/github/license/pvliesdonk/markdown-vault-mcp)](LICENSE) [![Docker](https://img.shields.io/github/v/release/pvliesdonk/markdown-vault-mcp?label=ghcr.io&logo=docker)](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp) [![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://pvliesdonk.github.io/markdown-vault-mcp/) [![llms.txt](https://img.shields.io/badge/llms.txt-available-brightgreen)](https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt) [![Template](https://img.shields.io/badge/dynamic/yaml?url=https://raw.githubusercontent.com/pvliesdonk/markdown-vault-mcp/main/.copier-answers.yml&query=%24._commit&label=template)](https://github.com/pvliesdonk/fastmcp-server-template)

Generic markdown vault MCP server with FTS5 + semantic search

**[Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/)** | **[Config wizard](https://pvliesdonk.github.io/markdown-vault-mcp/latest/configuration-generator/)** | **[PyPI](https://pypi.org/project/markdown-vault-mcp/)** | **[Docker](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp)**

## Features

<!-- DOMAIN-START -->
- **Hybrid search**: SQLite FTS5 keyword search (BM25, porter stemming) and semantic search (FastEmbed, Ollama, or OpenAI embeddings), fused with Reciprocal Rank Fusion; diversity-aware ranking returns sentence-scale snippets with full-section recovery via `read(path, section=heading)`. See the [Embeddings guide](https://pvliesdonk.github.io/markdown-vault-mcp/latest/guides/embeddings/).
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

The server registers a built-in `get_server_info` tool (via `fastmcp_pvl_core.register_server_info_tool`) so operators can confirm the deployed version with a single MCP call. The default response carries `server_name`, `server_version`, and `core_version`. Servers that talk to a remote upstream wire upstream version reporting inside the `DOMAIN-UPSTREAM-START` / `DOMAIN-UPSTREAM-END` sentinel in `src/markdown_vault_mcp/server.py`; see [`CLAUDE.md`](CLAUDE.md#server-info-tool-get_server_info) for the wiring pattern.

## Configuration

Core environment variables shared across all `fastmcp-pvl-core`-based services:

<!-- GENERATED-ENV-TABLE-CORE-START — generated by scripts/gen_config_surface.py; do not edit -->
| Variable | Default | Description |
|---|---|---|
| `MARKDOWN_VAULT_MCP_KV_STORE_URL` | `file:///data/state` | Persistent-state backend URL shared by every pvl-core subsystem that needs state. `memory://` is in-process and lost on restart; `file:///path` persists on one server; `redis://`, `dynamodb://` and `mongodb://` each need their matching extra. When unset, defaults to `file:///data/state` (the volume family Docker images mount), or to `memory://`; with a warning; on a host where that directory is not usable. |
| `FASTMCP_LOG_LEVEL` | `INFO` | Log level for FastMCP internals and app loggers (DEBUG / INFO / WARNING / ERROR / CRITICAL). The -v CLI flag overrides to DEBUG. |
| `FASTMCP_ENABLE_RICH_LOGGING` | `true` | Set false for plain or structured JSON log output. |
<!-- GENERATED-ENV-TABLE-CORE-END -->

Domain-specific variables go below under [Domain configuration](#domain-configuration).

## Authentication

Callers authenticate via a bearer token or OIDC (mutually exclusive). See the [Authentication guide](docs/guides/authentication.md) for setup, mapped multi-subject tokens, OIDC, and troubleshooting.

## Post-scaffold checklist

After `copier copy` and `gh repo create --push`:

1. **Fill in the DOMAIN blocks** (every section marked with a `DOMAIN` sentinel comment) in this README and in `CLAUDE.md`. The `GENERATED-ENV-TABLE-*` regions are not DOMAIN blocks; the config generator owns them and rewrites them on every run.
2. Configure GitHub secrets (see below).
3. Install dev + docs tooling: `uv sync --all-extras --all-groups`.
4. Install pre-commit hooks: `uv run pre-commit install`.
5. Run the gate locally: `uv run pytest -x -q && uv run ruff check --fix . && uv run ruff format . && uv run mypy src/ tests/`.
6. Push the first commit. CI should be green.

## GitHub secrets

CI workflows reference three repository secrets. Configure them via **Settings → Secrets and variables → Actions** or with `gh secret set`:

| Secret | Used by | How to generate |
|---|---|---|
| `RELEASE_TOKEN` | `release-prepare.yml`, `release.yml`, `release-notes.yml`, `copier-update.yml`, `renovate.yml`, `bootstrap.yml` | Fine-grained PAT at <https://github.com/settings/personal-access-tokens/new> with `contents: write`, `pull_requests: write`, and `administration: write` (bootstrap applies the repository rulesets + auto-merge). Must belong to a repository admin: the shipped rulesets grant bypass to the admin role, and the release tag + GitHub release that knope creates after a release pull request merges rely on it (pull requests the token opens also need it so their CI runs). Scoped to this repo. |
| `CODECOV_TOKEN` | `ci.yml` | <https://codecov.io>: sign in with GitHub and add the repo. The upload token is on its settings page. |
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude.yml`, `claude-code-review.yml`, `release-notes.yml` | Run `claude setup-token` locally and paste the result. |

```bash
gh secret set RELEASE_TOKEN
gh secret set CODECOV_TOKEN
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

Pre-commit runs a subset of the gate on each commit; see `.pre-commit-config.yaml` for details, or [`CLAUDE.md`](CLAUDE.md) for the full Hard PR Acceptance Gates.

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

## Links

- [Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/)
- [llms.txt](https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt)
- [FastMCP](https://gofastmcp.com)
- [fastmcp-pvl-core](https://pypi.org/project/fastmcp-pvl-core/)

<!-- ===== TEMPLATE-OWNED SECTIONS END ===== -->

## Domain configuration

Domain environment variables use the `MARKDOWN_VAULT_MCP_` prefix:

<!-- GENERATED-ENV-TABLE-DOMAIN-START — generated by scripts/gen_config_surface.py; do not edit -->
| Variable | Default | Required | Description |
|---|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | No | Ollama server URL for the ollama embedding provider. Bare (not MARKDOWN_VAULT_MCP_-prefixed), matching the Ollama ecosystem convention. |
| `OPENAI_API_KEY` | (none) | No | OpenAI API key for the openai embedding provider, and the fallback key for the summarize tool when MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY is unset. Bare (not MARKDOWN_VAULT_MCP_-prefixed), matching the OpenAI ecosystem convention. |
| `VOYAGE_API_KEY` | (none) | No | Voyage AI API key for the voyage embedding provider. Bare (not MARKDOWN_VAULT_MCP_-prefixed), matching the OPENAI_API_KEY / OLLAMA_HOST convention. Setting it never auto-selects the provider; choose it explicitly with MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=voyage. |
| `OPENAI_BASE_URL` | (none) | No | Bare fallback for MARKDOWN_VAULT_MCP_OPENAI_BASE_URL (embeddings). For the summarize tool it only routes traffic when an API key already enables the feature; it never enables summarize by itself. |
| `OPENAI_EMBEDDING_MODEL` | (none) | No | Bare fallback for MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL. |
| `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S` | `60` | No | Maximum seconds an index-backed tool or resource waits for the FTS index to become queryable during a cold-start background build before raising IndexUnavailableError(reason="timeout"). Increase for large vaults. |
| `MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S` | `60` | No | Maximum seconds an index-querying read tool waits for the IndexWriter to drain when called with wait_for_pending_writes=true. On timeout the tool answers from the current index and reports index_stale=true in the response _meta. |
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | `/data/vault` | No | Path to the markdown vault directory. Required; the server refuses to start without it. Symbolic links inside the vault are followed on Python 3.13+. |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `false` | No | Set to true to hide the write tools (write, edit, append, delete, rename, move_folder, fetch, git_sync, the okf_* tools, create_upload_link) and serve a search-only vault. git_sync also needs managed git mode; create_upload_link needs an HTTP transport. |
| `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING` | `false` | No | Set to true to refuse a write that would overwrite an existing file when no if_match etag is supplied. Deliberate replacement (read first, pass if_match) still works, and edit / append / delete / rename are unaffected. |
| `MARKDOWN_VAULT_MCP_DISABLE_APPS_UI` | `false` | No | Hide the MCP Apps UI tools (browse_vault, show_context) from the tool listing for clients that do not render MCP Apps panels. |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | (none) | No | Path to the SQLite FTS5 index file; unset keeps the index in memory. Set it for persistence across restarts. |
| `MARKDOWN_VAULT_MCP_STATE_PATH` | (none) | No | Path to the change-tracking state file. Defaults to {SOURCE_DIR}/.markdown_vault_mcp/state.json. |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | (none) | No | Path to the numpy embeddings file; required to enable semantic search. |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | (none) | No | Comma-separated frontmatter fields promoted to the tag index for structured filtering. Changing it cold-rebuilds the index once on next startup; SEARCHABLE_FIELDS inherits this value when unset. |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | (none) | No | Comma-separated frontmatter fields required on every document; documents missing any are excluded from the index. |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | (none) | No | Comma-separated glob patterns excluded from scanning (.obsidian/**,.trash/**). |
| `MARKDOWN_VAULT_MCP_TITLE_FIELD` | `title` | No | Frontmatter field used as the document title (falls back to title, the first H1, then the filename). Changing it cold-rebuilds the index once on next startup. |
| `MARKDOWN_VAULT_MCP_SEARCHABLE_FIELDS` | (none) | No | Comma-separated frontmatter fields whose text values become keyword-searchable and enrich first-chunk embeddings. Inherits INDEXED_FIELDS when unset; the sentinel none means filterable but not searchable. Changing it cold-rebuilds the index and re-embeds once on next startup. |
| `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` | `_templates` | No | Relative folder where note templates live (used by the create_from_template prompt). |
| `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` | (none) | No | Directory of .md prompt files that extend or override built-in prompts; a relative path is resolved against SOURCE_DIR. |
| `MARKDOWN_VAULT_MCP_CONVENTIONS_FILE` | `_conventions.md` | No | Filename of the per-folder conventions files surfaced to clients at write time (bare .md filename without glob characters). Set to none to disable folder conventions. |
| `MARKDOWN_VAULT_MCP_OKF_MODE` | `auto` | No | OKF (Open Knowledge Format) read semantics. With auto (the default), read annotations switch on when the vault declares an OKF version in its root index.md. Use off to disable OKF semantics entirely, or on to force them for an undeclared vault. Annotations are read-only; write behavior is never affected. |
| `MARKDOWN_VAULT_MCP_OKF_WRITE` | `false` | No | OKF (Open Knowledge Format) enforced write layer. When true on an OKF-active vault, the server stamps generated provenance on each write and clears any verified attestation when a note's content changes. It also keeps each written folder's log.md and index.md current, and exposes the okf_verify tool. Requires OKF_MODE to be auto or on (a true value with OKF_MODE=off is a config error). Off by default. |
| `MARKDOWN_VAULT_MCP_OKF_VERIFY` | `elicit` | No | How the okf_verify tool attributes a human review. This applies only when OKF_WRITE is on, which gates the tool. With elicit (the default), okf_verify asks the human to confirm the review through an MCP elicitation and records the attestation only on an affirmative reply. It fails closed when the client cannot elicit or the human declines, so a model that holds the human's token cannot self-attest. Use trust-auth to attribute to the authenticated caller with no confirmation (safe only when the sole caller is a human-driven UI), or off to hide the tool so attestation happens through external tooling. A non-default value with OKF_WRITE off is a config error. |
| `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS` | (none) | No | Comma-separated allowed attachment extensions without the dot (such as pdf,png,jpg); use * to allow every non-markdown file. Unset selects the built-in allowlist. |
| `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` | `1.0` | No | Maximum attachment size in MB returned by read / accepted by write; 0 disables the limit. |
| `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` | `262144` | No | Maximum bytes returned by a full-document read of a note; use `read(path, section=…)` for partial reads. 0 disables the limit. |
| `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` | `2` | No | Maximum chunks returned per document in search results. |
| `MARKDOWN_VAULT_MCP_SNIPPET_WORDS` | `200` | No | Width of the snippet window (words) in search results; 0 returns full chunk content. |
| `MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA` | `0.25` | No | Down-weights longer chunks in ranking: score / (1 + alpha * log(chunk_count)). |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS` | `400` | No | Word cap per chunk; the adaptive chunker splits at deeper heading levels, then paragraph/word boundaries, to respect it. Match it to the embedding model's context. A reindex applies a new value. |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS` | (none) | No | Character cap enforced alongside MAX_CHUNK_WORDS to bound token-dense chunks. Unset derives min(1500, model context * 2.8). Set a positive value for an exact cap, or -1 to scale with the model's full context (can exhaust memory on long-context models). A reindex applies a new value. |
| `MARKDOWN_VAULT_MCP_CHUNK_OVERLAP_WORDS` | `40` | No | Words of overlap between adjacent budget-split fragments of the same heading section (0 disables). A reindex applies a new value. |
| `MARKDOWN_VAULT_MCP_FOLDER_WEIGHTS` | (none) | No | Folder-prefix score multipliers (`prefix:weight` pairs, comma-separated, weights > 0) applied to all search modes; the deepest matching prefix wins (sessions:0.5 demotes sessions/**). |
| `MARKDOWN_VAULT_MCP_FTS_WEIGHTS` | (none) | No | Per-column BM25 weights (`column:weight` pairs, comma-separated, weights >= 0) for keyword ranking. Columns: path, title, folder, heading, content, summary. |
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | (none) | No | Embedding provider: openai, voyage, ollama, or fastembed. Unset auto-detects from the environment (never voyage). |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | `nomic-embed-text` | No | Ollama embedding model name. |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | `false` | No | Force Ollama to embed on CPU only. |
| `MARKDOWN_VAULT_MCP_VOYAGE_MODEL` | `voyage-4` | No | Voyage AI embedding model name. |
| `MARKDOWN_VAULT_MCP_OPENAI_BASE_URL` | `https://api.openai.com/v1` | No | OpenAI-compatible API base URL for embeddings; the bare OPENAI_BASE_URL is honoured as a fallback. |
| `MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | No | OpenAI-compatible embedding model name; the bare OPENAI_EMBEDDING_MODEL is honoured as a fallback. |
| `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL` | `BAAI/bge-small-en-v1.5` | No | FastEmbed model name. |
| `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR` | (none) | No | FastEmbed model cache directory (in Docker, stored under /data/state/fastembed). |
| `MARKDOWN_VAULT_MCP_EMBED_CONTEXT` | `false` | No | Enrich embedding input with the note title, chunk heading, and (first chunk) searchable-field values. Flipping it re-embeds the whole vault once on next startup. |
| `MARKDOWN_VAULT_MCP_EMBED_TIMEOUT_S` | `30.0` | No | Per-request wall-clock budget in seconds for a single embedding HTTP call (OpenAI/Ollama). The local FastEmbed backend runs in-process with no network call and ignores this. CPU-only or large-model workloads may need 60-120 s; raise this if batches time out. |
| `MARKDOWN_VAULT_MCP_EMBEDDING_BATCH_SIZE` | `4` | No | Number of chunks sent per embedding request. Smaller batches shorten each request (useful under a tight timeout on slow models) at the cost of more round-trips. |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | (none) | No | Token/password for HTTPS git auth; remotes must be HTTPS when set. |
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | (none) | No | HTTPS remote URL for managed git mode: the server clones into an empty SOURCE_DIR on startup (or validates an existing origin) and enables the pull loop, auto-commit, and deferred push. |
| `MARKDOWN_VAULT_MCP_GIT_USERNAME` | `x-access-token` | No | Username for HTTPS git auth prompts (x-access-token for GitHub, oauth2 for GitLab, the account name for Bitbucket). |
| `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | `600` | No | Seconds between git fetch + fast-forward update attempts; 0 disables periodic pull. |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | `30.0` | No | Seconds of write-idle time before pushing; 0 pushes only on shutdown. |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | `markdown-vault-mcp` | No | Git committer name for auto-commits; set this in Docker where git config user.name is empty. |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | `noreply@markdown-vault-mcp` | No | Git committer email for auto-commits. |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` | (none) | No | OIDC claim key used as the commit author name (such as name); overrides GIT_COMMIT_NAME per request when an OIDC token is present. |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM` | (none) | No | OIDC claim key used as the commit author email (such as email); overrides GIT_COMMIT_EMAIL per request when an OIDC token is present. |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | `true` | No | Run git lfs pull on startup to fetch LFS-tracked attachments; set to false for repos without LFS. |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER` | `true` | No | Watch the vault for external filesystem changes; auto-disabled when git pull or the webhook is active. Requires the file-watcher extra. |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S` | `2.0` | No | Seconds of quiet after the last filesystem event before reindexing. |
| `MARKDOWN_VAULT_MCP_FILE_WATCHER_ROOT_FLOOR` | `true` | No | Keep the non-recursive watch on the vault root; set false to register zero source-dir-rooted FSEvents streams (avoids repeated macOS access prompts on a home-rooted vault) at the cost of root-level files relying on scans. |
| `MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET` | (none) | No | Shared secret for the GitHub push-event webhook; when set, mounts POST /github-webhook on HTTP/SSE transports to trigger an immediate pull + reindex on push events. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_PROVIDER` | (none) | No | Summarization backend (only openai is recognised). Unset auto-detects: the backend activates when credentials or an explicit endpoint are present. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY` | (none) | No | API key for the OpenAI-compatible summarize endpoint; the bare OPENAI_API_KEY is honoured as a fallback. Unset works for keyless local endpoints (Ollama). |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL` | (none) | No | OpenAI-compatible endpoint base URL for the summarize tool; setting it enables the tool even without an API key. The bare OPENAI_BASE_URL routes traffic only when a key already enables the feature. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL` | `gpt-5-mini` | No | Chat model id used for summaries. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_TOKENS` | `8192` | No | Upper bound on generated tokens per summarize call; on reasoning models this budget also covers internal reasoning tokens. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_NOTES` | `50` | No | Cap on the number of notes summarised in one call (subtree expansion truncates to this many). |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_INPUT_CHARS` | `200000` | No | Aggregate cap on note characters sent to the model in one call; excess is truncated with a flag on the result. |
| `MARKDOWN_VAULT_MCP_SUMMARIZE_TIMEOUT` | `120.0` | No | Per-request wall-clock budget in seconds for a single summarize backend call; keep it below the MCP client's request timeout so the server-side error wins the race. |
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S` | `3600.0` | No | Link lifetime in seconds when the caller requests no explicit TTL. |
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S` | `86400.0` | No | Ceiling in seconds a caller-requested link TTL is clamped to. |
| `MARKDOWN_VAULT_MCP_TRANSFER_GRACE_TTL_S` | `60.0` | No | Post-success grace window in seconds: a served token's TTL shrinks to this so a stalled transfer can retry within it. |
| `MARKDOWN_VAULT_MCP_TRANSFER_LEASE_S` | `60.0` | No | Crashed-handler reclaim window in seconds for an in-flight reservation. |
| `MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES` | `104857600` | No | Maximum size in bytes of a single upload. |
| `MARKDOWN_VAULT_MCP_JOBS_SOFT_DEADLINE_S` | `25.0` | No | Seconds a long-running tool call may run in the foreground before it is promoted to a background job and a job handle is returned instead. |
| `MARKDOWN_VAULT_MCP_JOBS_RESULT_TTL_S` | `3600.0` | No | Seconds a background-job record (working or finished) is retained for polling before it expires from the store. |
| `MARKDOWN_VAULT_MCP_JOBS_MAX_PER_SUBJECT` | `256` | No | Maximum live background jobs per calling subject; further promotions are rejected until older records expire. |
<!-- GENERATED-ENV-TABLE-DOMAIN-END -->

Domain-config fields are composed inside `src/markdown_vault_mcp/config.py` between the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END` sentinels; env reads go through `fastmcp_pvl_core.env(_ENV_PREFIX, "SUFFIX", default)` so naming stays consistent, and field invariants go in `__post_init__` between the `CONFIG-VALIDATE-START` / `CONFIG-VALIDATE-END` sentinels. Each field's `metadata` `help` and `tags` generate the table above directly, so keep them accurate and complete.

## Key design decisions

<!-- DOMAIN-START -->
- **Document identity is the relative path** with `.md` extension; frontmatter is optional by default (`REQUIRED_FIELDS` opts into enforcement).
- **Hybrid search uses Reciprocal Rank Fusion** over the FTS5 and vector result lists, with diversity-aware ranking capping chunks per document.
- **Tool semantics mirror Claude Code's Read/Write/Edit patterns**, so LLM clients drive the vault with habits they already have.
- **The library is synchronous**; the MCP layer wraps calls in `asyncio.to_thread()`.
- **Indexing is hash-based**: unchanged files are never re-parsed, and any change to how stored rows derive from a note's bytes bumps `INDEX_SEMANTICS_VERSION` so deployed vaults rebuild themselves once on upgrade.

The full decision log lives in the [design document](docs/design/design.md).
<!-- DOMAIN-END -->
