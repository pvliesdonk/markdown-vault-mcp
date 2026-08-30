"""Configuration for Markdown Vault MCP.

Composes :class:`fastmcp_pvl_core.ServerConfig` via the domain
:class:`ProjectConfig` dataclass — never inherits.

Add domain-specific fields between the CONFIG-FIELDS sentinels, populate
them in ``from_env`` between the CONFIG-FROM-ENV sentinels, and enforce
their invariants in ``__post_init__`` between the CONFIG-VALIDATE
sentinels; copier update preserves all three blocks across template
updates.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from fastmcp_pvl_core import JobsConfig, ServerConfig, TransferConfig

from markdown_vault_mcp._write_tools import gated_tool, write_tools_phrase
from markdown_vault_mcp.config_sections import (
    ContentConfig,
    EmbeddingsConfig,
    GitConfig,
    IndexingConfig,
    SearchConfig,
    SummarizeConfig,
    SyncConfig,
)
from markdown_vault_mcp.config_sections._assembly import (
    derive_max_chunk_chars as derive_max_chunk_chars,  # re-export: tests / scanner xref
)
from markdown_vault_mcp.config_sections._assembly import (
    normalize_templates_folder,
    read_server_name,
    require_source_dir,
    resolve_attachment_extensions,
    resolve_conventions_file,
    resolve_git_repo_url,
    resolve_prompts_folder,
    resolve_searchable_fields,
    resolve_summarize_api_key,
    resolve_summarize_base_url,
    to_bool,
)
from markdown_vault_mcp.config_sections._assembly import (
    to_vault_kwargs as to_vault_kwargs,  # re-export: cli / _server_deps consumers
)
from markdown_vault_mcp.config_sections._helpers import (
    WeightMap,
    env,
    env_float,
    env_int,
    opt_list,
    opt_path,
    parse_opt_int,
    parse_weight_map,
)

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"


@dataclass(frozen=True)
class ProjectConfig:
    """Domain config for Markdown Vault MCP.  Compose — don't inherit."""

    server: ServerConfig = field(default_factory=ServerConfig)

    # CONFIG-FIELDS-START — domain fields; kept across copier update
    #
    # One flat field per env var, named exactly after the var's suffix, so
    # the config-surface generator pairs each field's metadata with the
    # matching literal env read in from_env. The section views the codebase
    # consumes (config.git, config.search, ...) are properties below.
    source_dir: Path = field(
        default=Path("/data/vault"),
        metadata={
            "help": (
                "Path to the markdown vault directory. Required — the server "
                "refuses to start without it. Symbolic links inside the vault "
                "are followed on Python 3.13+."
            ),
            "tags": ("vault",),
        },
    )
    read_only: bool = field(
        default=False,
        metadata={
            "help": (
                "Set to true to hide the write tools "
                f"({write_tools_phrase()}) and serve a search-only vault. "
                f"{gated_tool('git_sync')} also needs managed git mode; "
                f"{gated_tool('create_upload_link')} needs an HTTP transport."
            ),
            "tags": ("vault",),
        },
    )
    write_protect_existing: bool = field(
        default=False,
        metadata={
            "help": (
                "Set to true to refuse a write that would overwrite an "
                "existing file when no if_match etag is supplied. Deliberate "
                "replacement (read first, pass if_match) still works, and "
                "edit / append / delete / rename are unaffected."
            ),
            "tags": ("vault",),
        },
    )
    # server_name is declared by the template-owned
    # config-presentation.yml (template provenance), so it carries no
    # metadata here and is read outside from_env (read_server_identity)
    # to keep the AST scan from double-declaring it. INSTRUCTIONS and
    # INSTRUCTIONS_EXTRA are read by pvl-core's finalize_instructions()
    # directly and never reach this config.
    server_name: str = "markdown-vault-mcp"
    disable_apps_ui: bool = field(
        default=False,
        metadata={
            "help": (
                "Hide the MCP Apps UI tools (browse_vault, show_context) from "
                "the tool listing for clients that do not render MCP Apps "
                "panels."
            ),
            "tags": ("apps",),
            "wizard": {"group": "MCP Apps"},
        },
    )
    index_path: Path | None = field(
        default=None,
        metadata={
            "help": (
                "Path to the SQLite FTS5 index file; unset keeps the index "
                "in memory. Set it for persistence across restarts."
            ),
            "tags": ("indexing",),
            "wizard": {"group": "Indexing"},
        },
    )
    state_path: Path | None = field(
        default=None,
        metadata={
            "help": (
                "Path to the change-tracking state file. Defaults to "
                "{SOURCE_DIR}/.markdown_vault_mcp/state.json."
            ),
            "tags": ("indexing",),
            "wizard": {"group": "Indexing"},
        },
    )
    embeddings_path: Path | None = field(
        default=None,
        metadata={
            "help": (
                "Path to the numpy embeddings file; required to enable semantic search."
            ),
            "tags": ("indexing",),
            "wizard": {"group": "Indexing"},
        },
    )
    indexed_fields: Sequence[str] | None = field(
        default=None,
        metadata={
            "help": (
                "Comma-separated frontmatter fields promoted to the tag index "
                "for structured filtering. Changing it cold-rebuilds the index "
                "once on next startup; SEARCHABLE_FIELDS inherits this value "
                "when unset."
            ),
            "tags": ("indexing",),
            "wizard": {"group": "Indexing"},
        },
    )
    required_fields: Sequence[str] | None = field(
        default=None,
        metadata={
            "help": (
                "Comma-separated frontmatter fields required on every "
                "document; documents missing any are excluded from the index."
            ),
            "tags": ("indexing",),
            "wizard": {"group": "Indexing"},
        },
    )
    exclude: Sequence[str] | None = field(
        default=None,
        metadata={
            "help": (
                "Comma-separated glob patterns excluded from scanning, e.g. "
                ".obsidian/**,.trash/**."
            ),
            "tags": ("indexing",),
            "wizard": {"group": "Indexing"},
        },
    )
    title_field: str = field(
        default="title",
        metadata={
            "help": (
                "Frontmatter field used as the document title (falls back to "
                "title, the first H1, then the filename). Changing it "
                "cold-rebuilds the index once on next startup."
            ),
            "tags": ("indexing",),
            "wizard": {"group": "Indexing"},
        },
    )
    searchable_fields: Sequence[str] | None = field(
        default=None,
        metadata={
            "help": (
                "Comma-separated frontmatter fields whose text values become "
                "keyword-searchable and enrich first-chunk embeddings. "
                "Inherits INDEXED_FIELDS when unset; the sentinel none means "
                "filterable but not searchable. Changing it cold-rebuilds the "
                "index and re-embeds once on next startup."
            ),
            "tags": ("indexing",),
            "wizard": {"group": "Indexing"},
        },
    )
    templates_folder: str = field(
        default="_templates",
        metadata={
            "help": (
                "Relative folder where note templates live (used by the "
                "create_from_template prompt)."
            ),
            "tags": ("content",),
            "wizard": {"group": "Content"},
        },
    )
    prompts_folder: str | None = field(
        default=None,
        metadata={
            "help": (
                "Directory of .md prompt files that extend or override "
                "built-in prompts; a relative path is resolved against "
                "SOURCE_DIR."
            ),
            "tags": ("content",),
            "wizard": {"group": "Content"},
        },
    )
    conventions_file: str | None = field(
        default="_conventions.md",
        metadata={
            "help": (
                "Filename of the per-folder conventions files surfaced to "
                "clients at write time (bare .md filename without glob "
                "characters). Set to none to disable folder conventions."
            ),
            "tags": ("content",),
            "wizard": {"group": "Content"},
        },
    )
    okf_mode: str = field(
        default="auto",
        metadata={
            "help": (
                "OKF (Open Knowledge Format) read semantics. With auto "
                "(the default), read annotations switch on when the vault "
                "declares an OKF version in its root index.md. Use off to "
                "disable OKF semantics entirely, or on to force them for an "
                "undeclared vault. Annotations are read-only; write "
                "behavior is never affected."
            ),
            "tags": ("content",),
            "wizard": {"group": "Content"},
        },
    )
    okf_write: bool = field(
        default=False,
        metadata={
            "help": (
                "OKF (Open Knowledge Format) enforced write layer. When true on "
                "an OKF-active vault, the server stamps generated provenance on "
                "each write and clears any verified attestation when a note's "
                "content changes. It also keeps each written folder's log.md and "
                "index.md current, and exposes the okf_verify tool. Requires "
                "OKF_MODE to be auto or on (a true value with OKF_MODE=off is a "
                "config error). Off by default."
            ),
            "tags": ("content",),
            "wizard": {"group": "Content"},
        },
    )
    okf_verify: str = field(
        default="elicit",
        metadata={
            "help": (
                "How the okf_verify tool attributes a human review. This "
                "applies only when OKF_WRITE is on, which gates the tool. With "
                "elicit (the default), okf_verify asks the human to confirm the "
                "review through an MCP elicitation and records the attestation "
                "only on an affirmative reply. It fails closed when the client "
                "cannot elicit or the human declines, so a model that holds the "
                "human's token cannot self-attest. Use trust-auth to attribute "
                "to the authenticated caller with no confirmation (safe only "
                "when the sole caller is a human-driven UI), or off to hide the "
                "tool so attestation happens through external tooling. A "
                "non-default value with OKF_WRITE off is a config error."
            ),
            "tags": ("content",),
            "wizard": {"group": "Content"},
        },
    )
    attachment_extensions: Sequence[str] | None = field(
        default=None,
        metadata={
            "help": (
                "Comma-separated allowed attachment extensions without the "
                "dot (e.g. pdf,png,jpg); use * to allow every non-markdown "
                "file. Unset selects the built-in allowlist."
            ),
            "tags": ("content",),
            "wizard": {"group": "Content"},
        },
    )
    max_attachment_size_mb: float = field(
        default=1.0,
        metadata={
            "help": (
                "Maximum attachment size in MB returned by read / accepted "
                "by write; 0 disables the limit."
            ),
            "tags": ("content",),
            "wizard": {"group": "Content"},
        },
    )
    max_note_read_bytes: int = field(
        default=262144,
        metadata={
            "help": (
                "Maximum bytes returned by a full-document read of a note; "
                "use `read(path, section=…)` for partial reads. 0 disables "
                "the limit."
            ),
            "tags": ("content",),
            "wizard": {"group": "Content"},
        },
    )
    default_search_mode: str = field(
        default="auto",
        metadata={
            "help": (
                "Mode used when a search call omits 'mode': auto, keyword, "
                "semantic, or hybrid. The default 'auto' picks hybrid when "
                "embeddings are configured and keyword when they are not. "
                "Pin 'keyword' to keep unqualified searches off the "
                "embedding provider (each hybrid or semantic search embeds "
                "the query, which costs an API call on a metered provider). "
                "A configured semantic/hybrid default also degrades to "
                "keyword without embeddings, so no setting can make a vault "
                "unsearchable; an explicit mode= argument is never "
                "downgraded."
            ),
            "tags": ("search",),
            "wizard": {"group": "Search tuning"},
        },
    )
    chunks_per_file: int = field(
        default=2,
        metadata={
            "help": "Maximum chunks returned per document in search results.",
            "tags": ("search",),
            "wizard": {"group": "Search tuning"},
        },
    )
    snippet_words: int = field(
        default=200,
        metadata={
            "help": (
                "Width of the snippet window (words) in search results; 0 "
                "returns full chunk content."
            ),
            "tags": ("search",),
            "wizard": {"group": "Search tuning"},
        },
    )
    length_downweight_alpha: float = field(
        default=0.25,
        metadata={
            "help": (
                "Down-weights longer chunks in ranking: "
                "score / (1 + alpha * log(chunk_count))."
            ),
            "tags": ("search",),
            "wizard": {"group": "Search tuning"},
        },
    )
    max_chunk_words: int = field(
        default=400,
        metadata={
            "help": (
                "Word cap per chunk; the adaptive chunker splits at deeper "
                "heading levels, then paragraph/word boundaries, to respect "
                "it. Match it to the embedding model's context. A reindex "
                "applies a new value."
            ),
            "tags": ("search",),
            "wizard": {"group": "Search tuning"},
        },
    )
    max_chunk_chars: int | None = field(
        default=None,
        metadata={
            "help": (
                "Character cap enforced alongside MAX_CHUNK_WORDS to bound "
                "token-dense chunks. Unset derives "
                "min(1500, model context * 2.8). Set a positive value for an "
                "exact cap, or -1 to scale with the model's full context "
                "(can exhaust memory on long-context models). A reindex "
                "applies a new value."
            ),
            "tags": ("search",),
            "wizard": {"group": "Search tuning"},
        },
    )
    chunk_overlap_words: int = field(
        default=40,
        metadata={
            "help": (
                "Words of overlap between adjacent budget-split fragments of "
                "the same heading section (0 disables). A reindex applies a "
                "new value."
            ),
            "tags": ("search",),
            "wizard": {"group": "Search tuning"},
        },
    )
    folder_weights: WeightMap | None = field(
        default=None,
        metadata={
            "help": (
                "Folder-prefix score multipliers (`prefix:weight` pairs, "
                "comma-separated, weights > 0) applied to all search modes; the deepest "
                "matching prefix wins (sessions:0.5 demotes sessions/**)."
            ),
            "tags": ("search",),
            "wizard": {"group": "Search tuning"},
        },
    )
    fts_weights: WeightMap | None = field(
        default=None,
        metadata={
            "help": (
                "Per-column BM25 weights (`column:weight` pairs, comma-separated, "
                "weights >= 0) for keyword ranking. Columns: path, title, folder, "
                "heading, content, summary."
            ),
            "tags": ("search",),
            "wizard": {"group": "Search tuning"},
        },
    )
    embedding_provider: str | None = field(
        default=None,
        metadata={
            "help": (
                "Embedding provider: openai, voyage, ollama, or fastembed. "
                "Unset auto-detects from the environment (never voyage). Any "
                "OpenAI-compatible endpoint works with openai plus "
                "OPENAI_BASE_URL; see the embeddings guide."
            ),
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    # ollama_host / (embeddings) openai_api_key / voyage_api_key are read
    # from the BARE environment (OLLAMA_HOST / OPENAI_API_KEY /
    # VOYAGE_API_KEY, ecosystem conventions) — invisible to the prefixed AST
    # scan, so they are declared in config-presentation.domain.yml instead of
    # via metadata here.
    ollama_host: str = "http://localhost:11434"
    openai_api_key: str | None = None
    voyage_api_key: str | None = None
    voyage_model: str = field(
        default="voyage-4",
        metadata={
            "help": "Voyage AI embedding model name.",
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    ollama_model: str = field(
        default="nomic-embed-text",
        metadata={
            "help": "Ollama embedding model name.",
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    ollama_cpu_only: bool = field(
        default=False,
        metadata={
            "help": "Force Ollama to embed on CPU only.",
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    openai_base_url: str = field(
        default="https://api.openai.com/v1",
        metadata={
            "help": (
                "OpenAI-compatible API base URL for embeddings; the bare "
                "OPENAI_BASE_URL is honoured as a fallback."
            ),
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    openai_embedding_model: str = field(
        default="text-embedding-3-small",
        metadata={
            "help": (
                "OpenAI-compatible embedding model name; the bare "
                "OPENAI_EMBEDDING_MODEL is honoured as a fallback."
            ),
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    fastembed_model: str = field(
        default="BAAI/bge-small-en-v1.5",
        metadata={
            "help": "FastEmbed model name.",
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    fastembed_cache_dir: str | None = field(
        default=None,
        metadata={
            "help": (
                "FastEmbed model cache directory (in Docker, stored under "
                "/data/state/fastembed)."
            ),
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    embed_context: bool = field(
        default=False,
        metadata={
            "help": (
                "Enrich embedding input with the note title, chunk heading, "
                "and (first chunk) searchable-field values. Flipping it "
                "re-embeds the whole vault once on next startup."
            ),
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    embed_timeout_s: float = field(
        default=30.0,
        metadata={
            "help": (
                "Per-request wall-clock budget in seconds for a single "
                "embedding HTTP call (OpenAI/Ollama). The local FastEmbed "
                "backend runs in-process with no network call and ignores "
                "this. CPU-only or large-model workloads may need 60-120 s; "
                "raise this if batches time out."
            ),
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    embedding_batch_size: int = field(
        default=4,
        metadata={
            "help": (
                "Number of chunks sent per embedding request. Smaller "
                "batches shorten each request (useful under a tight "
                "timeout on slow models) at the cost of more round-trips."
            ),
            "tags": ("embeddings",),
            "wizard": {"group": "Embeddings"},
        },
    )
    git_repo_url: str | None = field(
        default=None,
        metadata={
            "help": (
                "HTTPS remote URL for managed git mode: the server clones "
                "into an empty SOURCE_DIR on startup (or validates an "
                "existing origin) and enables the pull loop, auto-commit, "
                "and deferred push."
            ),
            "tags": ("git",),
            "wizard": {"group": "Git sync"},
        },
    )
    git_token: str | None = field(
        default=None,
        metadata={
            "help": (
                "Token/password for HTTPS git auth; remotes must be HTTPS when set."
            ),
            "tags": ("git",),
            "wizard": {"group": "Git sync", "secret": True},
        },
    )
    git_username: str = field(
        default="x-access-token",
        metadata={
            "help": (
                "Username for HTTPS git auth prompts (x-access-token for "
                "GitHub, oauth2 for GitLab, the account name for Bitbucket)."
            ),
            "tags": ("git",),
            "wizard": {"group": "Git sync"},
        },
    )
    git_pull_interval_s: int = field(
        default=600,
        metadata={
            "help": (
                "Seconds between git fetch + fast-forward update attempts; "
                "0 disables periodic pull."
            ),
            "tags": ("git",),
            "wizard": {"group": "Git sync"},
        },
    )
    git_push_delay_s: float = field(
        default=30.0,
        metadata={
            "help": (
                "Seconds of write-idle time before pushing; 0 pushes only on shutdown."
            ),
            "tags": ("git",),
            "wizard": {"group": "Git sync"},
        },
    )
    git_commit_name: str = field(
        default="markdown-vault-mcp",
        metadata={
            "help": (
                "Git committer name for auto-commits; set this in Docker "
                "where git config user.name is empty."
            ),
            "tags": ("git",),
            "wizard": {"group": "Git sync"},
        },
    )
    git_commit_email: str = field(
        default="noreply@markdown-vault-mcp",
        metadata={
            "help": "Git committer email for auto-commits.",
            "tags": ("git",),
            "wizard": {"group": "Git sync"},
        },
    )
    git_commit_name_claim: str | None = field(
        default=None,
        metadata={
            "help": (
                "OIDC claim key used as the commit author name (e.g. name); "
                "overrides GIT_COMMIT_NAME per request when an OIDC token is "
                "present. The claim is resolved when the tool call arrives "
                "and carried to the background commit, so it applies on "
                "every write."
            ),
            "tags": ("git",),
            "wizard": {"group": "Git sync"},
        },
    )
    git_commit_email_claim: str | None = field(
        default=None,
        metadata={
            "help": (
                "OIDC claim key used as the commit author email (e.g. "
                "email); overrides GIT_COMMIT_EMAIL per request when an OIDC "
                "token is present. Resolved and carried the same way as the "
                "name claim."
            ),
            "tags": ("git",),
            "wizard": {"group": "Git sync"},
        },
    )
    git_lfs: bool = field(
        default=True,
        metadata={
            "help": (
                "Run git lfs pull on startup to fetch LFS-tracked "
                "attachments; set to false for repos without LFS."
            ),
            "tags": ("git",),
            "wizard": {"group": "Git sync"},
        },
    )
    file_watcher: bool = field(
        default=True,
        metadata={
            "help": (
                "Watch the vault for external filesystem changes; "
                "auto-disabled when git pull or the webhook is active. "
                "Requires the file-watcher extra."
            ),
            "tags": ("sync",),
            "wizard": {"group": "Change detection"},
        },
    )
    file_watcher_debounce_s: float = field(
        default=2.0,
        metadata={
            "help": (
                "Seconds of quiet after the last filesystem event before reindexing."
            ),
            "tags": ("sync",),
            "wizard": {"group": "Change detection"},
        },
    )
    file_watcher_root_floor: bool = field(
        default=True,
        metadata={
            "help": (
                "Keep the non-recursive watch on the vault root; set false "
                "to register zero source-dir-rooted FSEvents streams (avoids "
                "repeated macOS access prompts on a home-rooted vault) at "
                "the cost of root-level files relying on scans."
            ),
            "tags": ("sync",),
            "wizard": {"group": "Change detection"},
        },
    )
    github_webhook_secret: str | None = field(
        default=None,
        metadata={
            "help": (
                "Shared secret for the GitHub push-event webhook; when set, "
                "mounts POST /github-webhook on HTTP/SSE transports to "
                "trigger an immediate pull + reindex on push events."
            ),
            "tags": ("sync",),
            "wizard": {"group": "Change detection", "when": "server", "secret": True},
        },
    )
    summarize_provider: str | None = field(
        default=None,
        metadata={
            "help": (
                "Summarization backend (only openai is recognised). Unset "
                "auto-detects: the backend activates when credentials or an "
                "explicit endpoint are present."
            ),
            "tags": ("summarize",),
            "wizard": {"group": "Summarize"},
        },
    )
    summarize_openai_api_key: str | None = field(
        default=None,
        metadata={
            "help": (
                "API key for the OpenAI-compatible summarize endpoint; the "
                "bare OPENAI_API_KEY is honoured as a fallback. Unset works "
                "for keyless local endpoints (Ollama)."
            ),
            "tags": ("summarize",),
            "wizard": {"group": "Summarize", "secret": True},
        },
    )
    summarize_openai_base_url: str | None = field(
        default=None,
        metadata={
            "help": (
                "OpenAI-compatible endpoint base URL for the summarize tool; "
                "setting it enables the tool even without an API key. The "
                "bare OPENAI_BASE_URL routes traffic only when a key already "
                "enables the feature."
            ),
            "tags": ("summarize",),
            "wizard": {"group": "Summarize"},
        },
    )
    summarize_openai_model: str = field(
        default="gpt-5-mini",
        metadata={
            "help": "Chat model id used for summaries.",
            "tags": ("summarize",),
            "wizard": {"group": "Summarize"},
        },
    )
    summarize_max_tokens: int = field(
        default=8192,
        metadata={
            "help": (
                "Upper bound on generated tokens per summarize call; on "
                "reasoning models this budget also covers internal reasoning "
                "tokens."
            ),
            "tags": ("summarize",),
            "wizard": {"group": "Summarize"},
        },
    )
    summarize_max_notes: int = field(
        default=50,
        metadata={
            "help": (
                "Cap on the number of notes summarised in one call (subtree "
                "expansion truncates to this many)."
            ),
            "tags": ("summarize",),
            "wizard": {"group": "Summarize"},
        },
    )
    summarize_max_input_chars: int = field(
        default=200_000,
        metadata={
            "help": (
                "Aggregate cap on note characters sent to the model in one "
                "call; excess is truncated with a flag on the result."
            ),
            "tags": ("summarize",),
            "wizard": {"group": "Summarize"},
        },
    )
    summarize_timeout: float = field(
        default=120.0,
        metadata={
            "help": (
                "Per-request wall-clock budget in seconds for a single "
                "summarize backend call; keep it below the MCP client's "
                "request timeout so the server-side error wins the race."
            ),
            "tags": ("summarize",),
            "wizard": {"group": "Summarize"},
        },
    )
    # The soft deadline before a still-running summarize call is promoted to
    # a background job is no longer a summarize-specific knob: it is owned by
    # the pvl-core jobs subsystem (JOBS_SOFT_DEADLINE_S and friends, #1033).
    # The transfer subsystem's operator knobs are owned by
    # ``fastmcp_pvl_core.TransferConfig`` (link TTL default/max, post-success
    # grace window, crashed-handler lease, per-upload cap). Compose it as a
    # single field — the config-surface generator discovers its env vars by
    # AST-walking ``TransferConfig.from_env`` and documents them from core's own
    # field metadata (help/tags/wizard), so no flat mirror field is needed.
    transfer: TransferConfig = field(
        default_factory=TransferConfig,
        metadata={"tags": ("transfer",)},
    )

    # The jobs subsystem's operator knobs (soft deadline before a slow tool
    # call is promoted to a pollable background job, job-record TTL,
    # per-subject cap) are owned by ``fastmcp_pvl_core.JobsConfig`` (#1033).
    # Composed the same way as ``transfer`` above so the config-surface
    # generator discovers the ``JOBS_*`` env vars from core's field metadata.
    jobs: JobsConfig = field(
        default_factory=JobsConfig,
        metadata={"tags": ("jobs",)},
    )

    @property
    def git(self) -> GitConfig:
        """The git section assembled from the flat ``git_*`` fields.

        A property rather than a composed field so the config-surface
        generator documents the flat fields' metadata. Construction runs
        ``GitConfig.__post_init__`` validation.
        """
        return GitConfig(
            token=self.git_token,
            repo_url=self.git_repo_url,
            username=self.git_username,
            push_delay_s=self.git_push_delay_s,
            commit_name=self.git_commit_name,
            commit_email=self.git_commit_email,
            commit_name_claim=self.git_commit_name_claim,
            commit_email_claim=self.git_commit_email_claim,
            lfs=self.git_lfs,
            pull_interval_s=self.git_pull_interval_s,
        )

    @property
    def indexing(self) -> IndexingConfig:
        """The indexing section assembled from the flat index/frontmatter fields."""
        return IndexingConfig(
            index_path=self.index_path,
            state_path=self.state_path,
            embeddings_path=self.embeddings_path,
            indexed_frontmatter_fields=self.indexed_fields,
            required_frontmatter=self.required_fields,
            exclude_patterns=self.exclude,
            title_field=self.title_field,
            searchable_frontmatter=self.searchable_fields,
        )

    @property
    def embeddings(self) -> EmbeddingsConfig:
        """The embeddings section assembled from the flat embedding fields."""
        return EmbeddingsConfig(
            provider=self.embedding_provider,
            ollama_host=self.ollama_host,
            ollama_model=self.ollama_model,
            ollama_cpu_only=self.ollama_cpu_only,
            openai_api_key=self.openai_api_key,
            openai_base_url=self.openai_base_url,
            openai_embedding_model=self.openai_embedding_model,
            voyage_api_key=self.voyage_api_key,
            voyage_model=self.voyage_model,
            fastembed_model=self.fastembed_model,
            fastembed_cache_dir=self.fastembed_cache_dir,
            embed_context=self.embed_context,
            embed_timeout_s=self.embed_timeout_s,
            embedding_batch_size=self.embedding_batch_size,
        )

    @property
    def search(self) -> SearchConfig:
        """The search section assembled from the flat ranking/chunking fields."""
        return SearchConfig(
            chunks_per_file=self.chunks_per_file,
            snippet_words=self.snippet_words,
            length_downweight_alpha=self.length_downweight_alpha,
            max_chunk_words=self.max_chunk_words,
            max_chunk_chars_override=self.max_chunk_chars,
            chunk_overlap_words=self.chunk_overlap_words,
            folder_weights=self.folder_weights,
            fts_weights=self.fts_weights,
            default_mode=self.default_search_mode,
        )

    @property
    def summarize(self) -> SummarizeConfig:
        """The summarize section assembled from the flat ``summarize_*`` fields."""
        return SummarizeConfig(
            provider=self.summarize_provider,
            openai_api_key=self.summarize_openai_api_key,
            openai_base_url=self.summarize_openai_base_url,
            openai_model=self.summarize_openai_model,
            max_tokens=self.summarize_max_tokens,
            max_notes=self.summarize_max_notes,
            max_input_chars=self.summarize_max_input_chars,
            timeout=self.summarize_timeout,
        )

    @property
    def sync(self) -> SyncConfig:
        """The sync section assembled from the flat watcher/webhook fields."""
        return SyncConfig(
            file_watcher_enabled=self.file_watcher,
            file_watcher_debounce_s=self.file_watcher_debounce_s,
            file_watcher_root_floor=self.file_watcher_root_floor,
            github_webhook_secret=self.github_webhook_secret,
        )

    @property
    def content(self) -> ContentConfig:
        """The content section assembled from the flat attachment/folder fields.

        A relative ``prompts_folder`` is resolved against ``source_dir`` here,
        so direct construction and ``from_env`` behave identically.
        """
        return ContentConfig(
            attachment_extensions=self.attachment_extensions,
            max_attachment_size_mb=self.max_attachment_size_mb,
            max_note_read_bytes=self.max_note_read_bytes,
            templates_folder=self.templates_folder,
            prompts_folder=resolve_prompts_folder(self.prompts_folder, self.source_dir),
            conventions_file=self.conventions_file,
            okf_mode=self.okf_mode,
            okf_write=self.okf_write,
            okf_verify=self.okf_verify,
        )

    # CONFIG-FIELDS-END

    def __post_init__(self) -> None:
        """Validate composed domain fields.  Raise ``ValueError`` when invalid.

        Runs on EVERY construction path — ``from_env`` and a direct
        ``ProjectConfig(field=...)`` alike.  That is what makes this the right
        home for a field invariant: ``env_float`` / ``env_int`` bounds check
        only the *env-sourced* value, never the default, so a direct
        construction slips past them.  They also cannot express an exclusive
        bound (their ``minimum`` / ``maximum`` are inclusive, so "must be > 0"
        lets ``0`` through) or a cross-field rule (A requires B,
        mutually-exclusive pairs).  All three belong here.

        The dataclass is ``frozen=True``: read fields freely, but plain
        assignment raises.  To *normalise* rather than merely check, use
        ``object.__setattr__(self, "name", value)``.
        """
        # CONFIG-VALIDATE-START — validate domain fields below; kept across copier update
        from markdown_vault_mcp.exceptions import ConfigurationError

        # Freeze sequence-valued flat fields into tuples for deep
        # immutability (#639); reject a bare str/bytes, which is itself a
        # Sequence[str] and would silently split into characters.
        sequence_fields = (
            "indexed_fields",
            "required_fields",
            "exclude",
            "searchable_fields",
            "attachment_extensions",
        )
        for name in sequence_fields:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, (str, bytes)):
                raise ConfigurationError(
                    f"{name} must be a sequence of strings, not a single "
                    f"{type(value).__name__}"
                )
            if not isinstance(value, tuple):
                object.__setattr__(self, name, tuple(value))
        object.__setattr__(
            self, "templates_folder", normalize_templates_folder(self.templates_folder)
        )
        # Constructing every section view runs its own __post_init__
        # bounds/shape validation (#638) on every construction path.
        _ = self.git
        _ = self.indexing
        _ = self.embeddings
        _ = self.search
        _ = self.summarize
        _ = self.sync
        _ = self.content
        # ``transfer`` and ``jobs`` are composed pvl-core section fields
        # (not section-view properties); each validates its own bounds in its
        # ``__post_init__`` at construction time (default_factory and from_env
        # alike), so neither needs re-construction here.
        # CONFIG-VALIDATE-END

    @classmethod
    def from_env(cls) -> ProjectConfig:
        """Load :class:`ProjectConfig` from ``MARKDOWN_VAULT_MCP_*`` env vars."""
        return cls(
            server=ServerConfig.from_env(_ENV_PREFIX),
            # CONFIG-FROM-ENV-START — populate domain fields below; kept across copier update
            #
            # Every prefixed read is a literal env(_ENV_PREFIX, "SUFFIX")
            # call so the config-surface generator's AST scan sees the full
            # domain surface; cross-field reuse threads through walrus
            # bindings. The bare-environment reads (OLLAMA_HOST,
            # OPENAI_API_KEY, and the OPENAI_BASE_URL /
            # OPENAI_EMBEDDING_MODEL fallbacks) are declared in
            # config-presentation.domain.yml instead.
            source_dir=require_source_dir(env(_ENV_PREFIX, "SOURCE_DIR")),
            read_only=to_bool(env(_ENV_PREFIX, "READ_ONLY"), default=False),
            write_protect_existing=to_bool(
                env(_ENV_PREFIX, "WRITE_PROTECT_EXISTING"), default=False
            ),
            server_name=read_server_name(_ENV_PREFIX),
            disable_apps_ui=to_bool(env(_ENV_PREFIX, "DISABLE_APPS_UI"), default=False),
            index_path=opt_path(env(_ENV_PREFIX, "INDEX_PATH")),
            state_path=opt_path(env(_ENV_PREFIX, "STATE_PATH")),
            embeddings_path=opt_path(env(_ENV_PREFIX, "EMBEDDINGS_PATH")),
            indexed_fields=(_indexed := opt_list(env(_ENV_PREFIX, "INDEXED_FIELDS"))),
            required_fields=opt_list(env(_ENV_PREFIX, "REQUIRED_FIELDS")),
            exclude=opt_list(env(_ENV_PREFIX, "EXCLUDE")),
            title_field=(env(_ENV_PREFIX, "TITLE_FIELD") or "").strip() or "title",
            searchable_fields=resolve_searchable_fields(
                env(_ENV_PREFIX, "SEARCHABLE_FIELDS"), _indexed
            ),
            templates_folder=normalize_templates_folder(
                env(_ENV_PREFIX, "TEMPLATES_FOLDER")
            ),
            prompts_folder=env(_ENV_PREFIX, "PROMPTS_FOLDER") or None,
            conventions_file=resolve_conventions_file(
                env(_ENV_PREFIX, "CONVENTIONS_FILE")
            ),
            okf_mode=(env(_ENV_PREFIX, "OKF_MODE", "auto") or "auto").strip().lower(),
            okf_write=to_bool(env(_ENV_PREFIX, "OKF_WRITE"), default=False),
            okf_verify=(env(_ENV_PREFIX, "OKF_VERIFY", "elicit") or "elicit")
            .strip()
            .lower(),
            attachment_extensions=resolve_attachment_extensions(
                env(_ENV_PREFIX, "ATTACHMENT_EXTENSIONS")
            ),
            max_attachment_size_mb=env_float(
                _ENV_PREFIX, "MAX_ATTACHMENT_SIZE_MB", 1.0
            ),
            max_note_read_bytes=env_int(_ENV_PREFIX, "MAX_NOTE_READ_BYTES", 262144),
            default_search_mode=env(_ENV_PREFIX, "DEFAULT_SEARCH_MODE") or "auto",
            chunks_per_file=env_int(_ENV_PREFIX, "CHUNKS_PER_FILE", 2),
            snippet_words=env_int(_ENV_PREFIX, "SNIPPET_WORDS", 200),
            length_downweight_alpha=env_float(
                _ENV_PREFIX, "LENGTH_DOWNWEIGHT_ALPHA", 0.25
            ),
            max_chunk_words=env_int(_ENV_PREFIX, "MAX_CHUNK_WORDS", 400),
            max_chunk_chars=parse_opt_int(
                env(_ENV_PREFIX, "MAX_CHUNK_CHARS"), f"{_ENV_PREFIX}_MAX_CHUNK_CHARS"
            ),
            chunk_overlap_words=env_int(_ENV_PREFIX, "CHUNK_OVERLAP_WORDS", 40),
            folder_weights=parse_weight_map(
                env(_ENV_PREFIX, "FOLDER_WEIGHTS"), f"{_ENV_PREFIX}_FOLDER_WEIGHTS"
            ),
            fts_weights=parse_weight_map(
                env(_ENV_PREFIX, "FTS_WEIGHTS"), f"{_ENV_PREFIX}_FTS_WEIGHTS"
            ),
            embedding_provider=env(_ENV_PREFIX, "EMBEDDING_PROVIDER") or None,
            ollama_host=os.environ.get("OLLAMA_HOST") or "http://localhost:11434",
            ollama_model=env(_ENV_PREFIX, "OLLAMA_MODEL") or "nomic-embed-text",
            ollama_cpu_only=to_bool(env(_ENV_PREFIX, "OLLAMA_CPU_ONLY"), default=False),
            openai_api_key=(os.environ.get("OPENAI_API_KEY") or "").strip() or None,
            voyage_api_key=(os.environ.get("VOYAGE_API_KEY") or "").strip() or None,
            voyage_model=env(_ENV_PREFIX, "VOYAGE_MODEL") or "voyage-4",
            openai_base_url=(
                env(_ENV_PREFIX, "OPENAI_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or ""
            ).strip()
            or "https://api.openai.com/v1",
            openai_embedding_model=(
                env(_ENV_PREFIX, "OPENAI_EMBEDDING_MODEL")
                or os.environ.get("OPENAI_EMBEDDING_MODEL")
                or ""
            ).strip()
            or "text-embedding-3-small",
            fastembed_model=env(_ENV_PREFIX, "FASTEMBED_MODEL")
            or "BAAI/bge-small-en-v1.5",
            fastembed_cache_dir=env(_ENV_PREFIX, "FASTEMBED_CACHE_DIR") or None,
            embed_context=to_bool(env(_ENV_PREFIX, "EMBED_CONTEXT"), default=False),
            embed_timeout_s=env_float(_ENV_PREFIX, "EMBED_TIMEOUT_S", 30.0),
            embedding_batch_size=env_int(_ENV_PREFIX, "EMBEDDING_BATCH_SIZE", 4),
            git_token=(_git_token := env(_ENV_PREFIX, "GIT_TOKEN") or None),
            git_repo_url=resolve_git_repo_url(
                env(_ENV_PREFIX, "GIT_REPO_URL"), _git_token, _ENV_PREFIX
            ),
            git_username=env(_ENV_PREFIX, "GIT_USERNAME") or "x-access-token",
            git_pull_interval_s=env_int(_ENV_PREFIX, "GIT_PULL_INTERVAL_S", 600),
            git_push_delay_s=env_float(_ENV_PREFIX, "GIT_PUSH_DELAY_S", 30.0),
            git_commit_name=env(_ENV_PREFIX, "GIT_COMMIT_NAME") or "markdown-vault-mcp",
            git_commit_email=env(_ENV_PREFIX, "GIT_COMMIT_EMAIL")
            or "noreply@markdown-vault-mcp",
            git_commit_name_claim=env(_ENV_PREFIX, "GIT_COMMIT_NAME_CLAIM") or None,
            git_commit_email_claim=env(_ENV_PREFIX, "GIT_COMMIT_EMAIL_CLAIM") or None,
            git_lfs=to_bool(env(_ENV_PREFIX, "GIT_LFS"), default=True),
            file_watcher=to_bool(env(_ENV_PREFIX, "FILE_WATCHER"), default=True),
            file_watcher_debounce_s=env_float(
                _ENV_PREFIX, "FILE_WATCHER_DEBOUNCE_S", 2.0
            ),
            file_watcher_root_floor=to_bool(
                env(_ENV_PREFIX, "FILE_WATCHER_ROOT_FLOOR"), default=True
            ),
            github_webhook_secret=env(_ENV_PREFIX, "GITHUB_WEBHOOK_SECRET") or None,
            summarize_provider=env(_ENV_PREFIX, "SUMMARIZE_PROVIDER") or None,
            summarize_openai_api_key=(
                _summarize_key := resolve_summarize_api_key(
                    env(_ENV_PREFIX, "SUMMARIZE_OPENAI_API_KEY")
                )
            ),
            summarize_openai_base_url=resolve_summarize_base_url(
                env(_ENV_PREFIX, "SUMMARIZE_OPENAI_BASE_URL"), _summarize_key
            ),
            summarize_openai_model=env(_ENV_PREFIX, "SUMMARIZE_OPENAI_MODEL")
            or "gpt-5-mini",
            summarize_max_tokens=env_int(_ENV_PREFIX, "SUMMARIZE_MAX_TOKENS", 8192),
            summarize_max_notes=env_int(_ENV_PREFIX, "SUMMARIZE_MAX_NOTES", 50),
            summarize_max_input_chars=env_int(
                _ENV_PREFIX, "SUMMARIZE_MAX_INPUT_CHARS", 200_000
            ),
            summarize_timeout=env_float(_ENV_PREFIX, "SUMMARIZE_TIMEOUT", 120.0),
            transfer=TransferConfig.from_env(_ENV_PREFIX),
            jobs=JobsConfig.from_env(_ENV_PREFIX),
            # CONFIG-FROM-ENV-END
        )
