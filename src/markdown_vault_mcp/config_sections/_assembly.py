"""Domain config-assembly logic extracted out of the template-owned ``config.py``.

``config.py`` is a copier-template-owned file: it must stay at the skeleton shape
(module docstring, ``ProjectConfig`` dataclass, ``from_env`` scaffold) with domain
content confined to the ``CONFIG-FIELDS`` / ``CONFIG-FROM-ENV`` sentinels. All the
domain assembly logic that used to live in its body — the ``Vault(**kwargs)``
builder, the git-strategy construction, the chunk-cap heuristic, and the
``from_env`` field validators — lives here instead (#900, epic #898).

Imports only fastmcp_pvl_core, stdlib, and sibling domain modules (never
``config`` at runtime) so ``config.py`` can import these without a cycle.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp_pvl_core import parse_bool as _parse_bool

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git import GitWriteStrategy

if TYPE_CHECKING:
    from markdown_vault_mcp.config import ProjectConfig

logger = logging.getLogger(__name__)

# Full env-var name for the required source dir, used in the missing-var message.
_SOURCE_DIR_VAR = "MARKDOWN_VAULT_MCP_SOURCE_DIR"

# Heuristic ratio converting an embedding model's token context length into a
# conservative character budget for the chunker. English prose averages ~4
# chars/token; 2.8 leaves headroom for token-dense (CJK, code, tables) content
# so a derived char cap stays safely under the model's real token limit.
_CHARS_PER_TOKEN = 2.8

# Ceiling on the derived chunker char cap. Retrieval quality peaks at ~256-512
# tokens per chunk regardless of the model's context length, so the cap is bounded
# rather than scaled to context. 1500 chars (~535 tokens at _CHARS_PER_TOKEN) sits
# just above that band and keeps the fastembed/ONNX fp32 path clear of the #306
# OOM regime. Also the fallback when the model's context length is unknown.
_MAX_CHUNK_CHARS_CEILING = 1500


def derive_max_chunk_chars(*, context_length: int | None, override: int | None) -> int:
    """Resolve the chunker character cap.

    A positive override is used verbatim; ``-1`` opts into unbounded
    context-scaling; otherwise the cap is the bounded default
    ``min(_MAX_CHUNK_CHARS_CEILING, round(context * 2.8))``, falling back to
    ``_MAX_CHUNK_CHARS_CEILING`` when the context length is unknown.

    Args:
        context_length: The embedding model's maximum input length in tokens,
            or ``None`` when it cannot be determined.
        override: An explicit operator-supplied char cap. A positive value is
            used verbatim. ``-1`` opts into unbounded context-scaling (the cap
            tracks the model's full context with no ceiling, or
            ``_MAX_CHUNK_CHARS_CEILING`` when the context length is unknown),
            which can OOM the fastembed/ONNX path on a long-context model.
            ``None`` selects the bounded default
            ``min(_MAX_CHUNK_CHARS_CEILING, round(context * 2.8))``.

    Returns:
        The character budget to pass to the chunker.
    """
    if override == -1:
        # Opt-in: track the model's full context with no ceiling. Documented
        # footgun — a long-context model can OOM the fastembed/ONNX path (#306).
        if context_length is not None and context_length > 0:
            return round(context_length * _CHARS_PER_TOKEN)
        return _MAX_CHUNK_CHARS_CEILING
    if override is not None:
        return override
    # Default: retrieval-optimal and OOM-safe. ``context_length > 0`` guards a
    # degenerate 0 cap; a small-context model clamps *down* so chunks never exceed
    # what it can ingest.
    if context_length is not None and context_length > 0:
        return min(_MAX_CHUNK_CHARS_CEILING, round(context_length * _CHARS_PER_TOKEN))
    return _MAX_CHUNK_CHARS_CEILING


def require_source_dir(raw: str | None) -> Path:
    """Validate the required ``SOURCE_DIR`` env value into a :class:`Path`.

    Takes the already-read env value (so ``config.from_env`` keeps a literal
    ``env(..., "SOURCE_DIR")`` call the wizard drift gate can see) and raises
    when it is unset or blank.

    Raises:
        ConfigurationError: If *raw* is ``None`` or whitespace-only.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        raise ConfigurationError(
            f"{_SOURCE_DIR_VAR} is required but not set. "
            "Set it to the path of your markdown vault."
        )
    return Path(cleaned)


def to_bool(raw: str | None, *, default: bool) -> bool:
    """Parse a boolean env value, falling back to *default* when unset."""
    return _parse_bool(raw) if raw is not None else default


def _build_git_strategy(
    config: ProjectConfig,
    *,
    token: str | None,
    managed: bool,
    enable_sync: bool,
    repo_url: str | None = None,
) -> GitWriteStrategy:
    """Build a GitWriteStrategy with the kwargs shared across all three git modes.

    ``enable_sync`` toggles both pull and push together — every call site enables
    or disables them in lockstep.
    """
    git = config.git
    return GitWriteStrategy(
        token=token,
        repo_url=repo_url,
        managed=managed,
        enable_pull=enable_sync,
        enable_push=enable_sync,
        username=git.username,
        push_delay_s=git.push_delay_s,
        commit_name=git.commit_name,
        commit_email=git.commit_email,
        commit_name_claim=git.commit_name_claim,
        commit_email_claim=git.commit_email_claim,
        git_lfs=git.lfs,
        repo_path=config.source_dir,
    )


def to_vault_kwargs(config: ProjectConfig) -> dict[str, Any]:
    """Return keyword arguments suitable for ``Vault(**kwargs)``.

    Resolves the embedding provider (when ``indexing.embeddings_path``
    is set) and creates a :class:`~markdown_vault_mcp.git.GitWriteStrategy`.

    Args:
        config: The :class:`~markdown_vault_mcp.config.ProjectConfig` to build from.

    Returns:
        Dict of keyword arguments accepted by
        :class:`~markdown_vault_mcp.vault.Vault.__init__`.
    """
    kwargs: dict[str, Any] = {
        "source_dir": config.source_dir,
        "read_only": config.read_only,
        "index_path": config.indexing.index_path,
        "embeddings_path": config.indexing.embeddings_path,
        "state_path": config.indexing.state_path,
        "indexed_frontmatter_fields": config.indexing.indexed_frontmatter_fields,
        "required_frontmatter": config.indexing.required_frontmatter,
        "exclude_patterns": config.indexing.exclude_patterns,
        "title_field": config.indexing.title_field,
        "searchable_frontmatter_fields": config.indexing.searchable_frontmatter,
        "embed_context": config.embeddings.embed_context,
        "embedding_batch_size": config.embeddings.embedding_batch_size,
        "conventions_file": config.content.conventions_file,
        "okf_mode": config.content.okf_mode,
        "okf_write": config.content.okf_write,
        "attachment_extensions": config.content.attachment_extensions,
        "max_attachment_size_mb": config.content.max_attachment_size_mb,
        "max_note_read_bytes": config.content.max_note_read_bytes,
        "git_pull_interval_s": 0,
        "chunks_per_file": config.search.chunks_per_file,
        "snippet_words": config.search.snippet_words,
        "length_downweight_alpha": config.search.length_downweight_alpha,
        "max_chunk_words": config.search.max_chunk_words,
        "chunk_overlap_words": config.search.chunk_overlap_words,
        # Weight maps are stored as frozen sorted tuples on the config
        # (#639); the Vault API takes plain dicts.
        "folder_weights": (
            dict(config.search.folder_weights)
            if config.search.folder_weights is not None
            else None
        ),
        "fts_weights": (
            dict(config.search.fts_weights)
            if config.search.fts_weights is not None
            else None
        ),
    }

    # Semantic search is gated by the storage path in config.indexing,
    # while the provider lives in config.embeddings (cross-section coupling).
    # An unrecognised provider name raises ConfigurationError from the
    # resolver and propagates here unchanged.
    provider = None
    if config.indexing.embeddings_path is not None:
        explicit_provider = (config.embeddings.provider or "").strip()
        try:
            from markdown_vault_mcp import providers as _providers

            provider = _providers.get_embedding_provider(config)
            kwargs["embedding_provider"] = provider
        except (ImportError, RuntimeError) as exc:
            if explicit_provider:
                # The operator explicitly chose a backend; a load failure is
                # a configuration error that must surface, not silently fall
                # back to keyword-only search.
                raise ConfigurationError(
                    f"Embedding provider {explicit_provider!r} was explicitly "
                    "configured (MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER) but "
                    f"could not be loaded: {exc}. Fix the configuration, or "
                    "unset the variable to fall back to auto-detection."
                ) from exc
            logger.warning(
                "Could not auto-detect an embedding provider; semantic "
                "search disabled. Set MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER "
                "to require a specific backend.",
                exc_info=True,
            )

    # Derive the chunker char cap from the embedding model's token context
    # (a token-dense chunk that fits max_chunk_words can still exceed the
    # model context). A positive override wins verbatim; -1 opts into
    # unbounded context-scaling (#790); otherwise the default is bounded by
    # the ceiling, which is also the fallback when the context is unknown.
    kwargs["max_chunk_chars"] = derive_max_chunk_chars(
        context_length=(provider.context_length if provider is not None else None),
        override=config.search.max_chunk_chars_override,
    )
    # The explicit override is also threaded straight through as the stable
    # warm-restart key (#649): the coordinator compares it (not the derived
    # cap) so a transient model-context read cannot trigger a rebuild.
    kwargs["max_chunk_chars_override"] = config.search.max_chunk_chars_override

    # LLM summarization is gated on a backend being configured (an API key
    # or an explicit OpenAI-compatible base URL).
    # Same posture as embeddings: an explicit provider that fails to load is
    # a configuration error; auto-detect failure warns and disables.
    if config.summarize.has_provider():
        explicit_summarizer = (config.summarize.provider or "").strip()
        try:
            from markdown_vault_mcp import summarizer as _summarizer

            kwargs["summarizer"] = _summarizer.get_summarizer(config)
            kwargs["summarize_max_notes"] = config.summarize.max_notes
            kwargs["summarize_max_input_chars"] = config.summarize.max_input_chars
        except (ImportError, RuntimeError) as exc:
            if explicit_summarizer:
                raise ConfigurationError(
                    f"Summarize provider {explicit_summarizer!r} was "
                    "explicitly configured "
                    "(MARKDOWN_VAULT_MCP_SUMMARIZE_PROVIDER) but could not "
                    f"be loaded: {exc}. Fix the configuration, or unset the "
                    "variable to fall back to auto-detection."
                ) from exc
            logger.warning(
                "Could not load a summarization backend; the summarize "
                "tool is disabled. Install the SDK with "
                "pip install 'markdown-vault-mcp[summarize]'.",
                exc_info=True,
            )

    if config.git.repo_url is not None:
        git_strategy = _build_git_strategy(
            config,
            token=config.git.token,
            repo_url=config.git.repo_url,
            managed=True,
            enable_sync=True,
        )
        kwargs["git_pull_interval_s"] = config.git.pull_interval_s
        kwargs["git_strategy"] = git_strategy
        kwargs["on_write"] = git_strategy
        return kwargs

    # Backward compatibility mode: token without explicit repo URL keeps
    # pull+push semantics, using the existing local checkout's origin.
    if config.git.token is not None:
        git_strategy = _build_git_strategy(
            config,
            token=config.git.token,
            managed=False,
            enable_sync=True,
        )
        kwargs["git_pull_interval_s"] = config.git.pull_interval_s
        kwargs["git_strategy"] = git_strategy
        kwargs["on_write"] = git_strategy
        return kwargs

    # Unmanaged / commit-only mode: commit locally if repo exists, never pull/push.
    git_strategy = _build_git_strategy(
        config,
        token=None,
        managed=False,
        enable_sync=False,
    )
    kwargs["git_strategy"] = git_strategy
    kwargs["on_write"] = git_strategy
    return kwargs


def read_server_name(prefix: str) -> str:
    """Read ``{prefix}_SERVER_NAME``, falling back to the project name.

    Lives outside ``ProjectConfig.from_env`` deliberately: the var is
    declared by the template-owned ``config-presentation.yml`` (``template``
    provenance), so a literal ``env(...)`` read inside ``from_env`` would be
    AST-discovered as a ``domain`` var too and trip the generator's
    duplicate-name guard.

    Args:
        prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

    Returns:
        The configured server name, or ``markdown-vault-mcp``.
    """
    from fastmcp_pvl_core import env as _env

    return (_env(prefix, "SERVER_NAME") or "").strip() or "markdown-vault-mcp"


def read_instructions(prefix: str) -> str | None:
    """Read ``{prefix}_INSTRUCTIONS`` (template provenance — see above).

    Args:
        prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

    Returns:
        The custom MCP instructions text, or ``None`` when unset.
    """
    from fastmcp_pvl_core import env as _env

    return (_env(prefix, "INSTRUCTIONS") or "").strip() or None


def resolve_git_repo_url(raw: str | None, token: str | None, prefix: str) -> str | None:
    """Resolve ``GIT_REPO_URL``, warning when a token is set without it.

    Args:
        raw: The already-read ``GIT_REPO_URL`` env value.
        token: The already-resolved ``GIT_TOKEN`` value (or ``None``).
        prefix: Env var prefix, used in the warning message.

    Returns:
        The repo URL, or ``None`` when unset.
    """
    repo_url = raw or None
    if token and not repo_url:
        logger.warning(
            "from_env: %s_GIT_TOKEN is set without %s_GIT_REPO_URL. This "
            "legacy mode is deprecated; set GIT_REPO_URL to enable explicit "
            "managed mode.",
            prefix,
            prefix,
        )
    return repo_url


def resolve_summarize_api_key(prefixed_raw: str | None) -> str | None:
    """Resolve the summarize API key: prefixed value, bare fallback, or None.

    Args:
        prefixed_raw: The already-read ``SUMMARIZE_OPENAI_API_KEY`` value.

    Returns:
        The effective API key (bare ``OPENAI_API_KEY`` as fallback), or
        ``None`` when neither is set.
    """
    import os

    return (prefixed_raw or os.environ.get("OPENAI_API_KEY") or "").strip() or None


def resolve_summarize_base_url(prefixed_raw: str | None, key: str | None) -> str | None:
    """Resolve the summarize base URL with enablement-safe bare fallback.

    The bare ``OPENAI_BASE_URL`` is a value fallback only: it routes traffic
    when a key already enables summarize, but never enables the tool by
    itself — a user setting it purely for embeddings must not get a surprise
    summarize tool. Only the prefixed var counts toward enablement.

    Args:
        prefixed_raw: The already-read ``SUMMARIZE_OPENAI_BASE_URL`` value.
        key: The already-resolved summarize API key (or ``None``).

    Returns:
        The effective base URL, or ``None``.
    """
    import os

    prefixed = (prefixed_raw or "").strip() or None
    bare = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None
    return prefixed or (bare if key else None)


def resolve_searchable_fields(
    raw: str | None, indexed: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    """Resolve the ``SEARCHABLE_FIELDS`` env value against ``INDEXED_FIELDS``.

    Unset/empty inherits *indexed* (a field configured for structured
    filtering is searchable out of the box); the sentinel ``none`` means
    "filterable but not searchable" — no fields, no inherit; anything else
    is parsed as a comma-separated list.

    Args:
        raw: The already-read ``SEARCHABLE_FIELDS`` env value.
        indexed: The already-parsed ``INDEXED_FIELDS`` tuple (or ``None``).

    Returns:
        The resolved searchable-fields tuple, or ``None``.
    """
    from markdown_vault_mcp.config_sections._helpers import opt_list

    if (raw or "").strip().lower() == "none":
        return None
    return opt_list(raw) or indexed


def resolve_conventions_file(raw: str | None) -> str | None:
    """Resolve the ``CONVENTIONS_FILE`` env value.

    Unset/empty defaults to ``_conventions.md``; the sentinel ``none``
    disables folder conventions entirely.

    Args:
        raw: The already-read ``CONVENTIONS_FILE`` env value.

    Returns:
        The conventions filename, or ``None`` when disabled.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return "_conventions.md"
    if cleaned.lower() == "none":
        return None
    return cleaned


def resolve_attachment_extensions(raw: str | None) -> tuple[str, ...] | None:
    """Resolve the ``ATTACHMENT_EXTENSIONS`` env value.

    ``*`` allows every non-markdown extension; unset/empty selects the
    built-in allowlist (``None``); anything else parses as a list.

    Args:
        raw: The already-read ``ATTACHMENT_EXTENSIONS`` env value.

    Returns:
        The extensions tuple, ``("*",)``, or ``None`` for the built-in list.
    """
    from markdown_vault_mcp.config_sections._helpers import opt_list

    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    if cleaned == "*":
        return ("*",)
    return opt_list(cleaned)


def resolve_prompts_folder(raw: str | None, source_dir: Path) -> str | None:
    """Resolve the ``PROMPTS_FOLDER`` value, joining a relative path to the vault.

    Args:
        raw: The prompts-folder value (env-read or field-supplied).
        source_dir: Vault root; used to resolve a relative prompts folder.

    Returns:
        The absolute prompts-folder path as a string, or ``None`` when unset.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    pf = Path(cleaned.replace("\\", "/"))
    if not pf.is_absolute():
        pf = source_dir / pf
    return str(pf)


def normalize_templates_folder(raw: str | None) -> str:
    """Normalize a templates-folder value (backslashes, edge slashes, default).

    Args:
        raw: The templates-folder value (env-read or field-supplied).

    Returns:
        The normalized relative folder path, defaulting to ``_templates``.
    """
    return ((raw or "").strip().replace("\\", "/").strip("/")) or "_templates"
