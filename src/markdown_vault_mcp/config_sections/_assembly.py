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
    return GitWriteStrategy(
        token=token,
        repo_url=repo_url,
        managed=managed,
        enable_pull=enable_sync,
        enable_push=enable_sync,
        username=config.git.username,
        push_delay_s=config.git.push_delay_s,
        commit_name=config.git.commit_name,
        commit_email=config.git.commit_email,
        commit_name_claim=config.git.commit_name_claim,
        commit_email_claim=config.git.commit_email_claim,
        git_lfs=config.git.lfs,
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
        "conventions_file": config.content.conventions_file,
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
            kwargs["summarize_inline_timeout"] = config.summarize.inline_timeout
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
