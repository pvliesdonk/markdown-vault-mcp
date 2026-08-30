"""Settings object for :class:`~markdown_vault_mcp.vault.Vault` construction.

:class:`VaultSettings` groups every *config-derived* ``Vault.__init__``
parameter into one frozen dataclass so the constructor no longer needs one
keyword per knob (#1158).  The never-config-derived collaborators —
``embedding_provider``, ``summarizer``, ``git_strategy``, ``on_write``, and
``chunk_strategy`` — stay explicit ``Vault`` keywords; the server path carries
them in :class:`~markdown_vault_mcp.config_sections._assembly.VaultInstances`.

Field defaults deliberately mirror the *library* defaults of the legacy
``Vault.__init__`` keywords (pinned by a signature drift-guard test), which is
why ``chunk_overlap_words`` defaults to ``0`` here while
:class:`~markdown_vault_mcp.config_sections.search.SearchConfig` defaults to
``40``, and ``read_only`` defaults to ``True`` while the server env default is
``False`` — the two tiers are distinct on purpose (see the ``read_only``
rationale in the ``Vault`` docstring).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from markdown_vault_mcp.okf import OKF_INDEXED_FIELDS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from markdown_vault_mcp.config import ProjectConfig

#: Default state-file location under the vault root, shared with
#: ``markdown_vault_mcp.vault`` (which re-exports both names).
_DEFAULT_STATE_SUBDIR = ".markdown_vault_mcp"
_DEFAULT_STATE_FILENAME = "state.json"


@dataclasses.dataclass(frozen=True)
class VaultSettings:
    """Config-derived construction settings for a :class:`~markdown_vault_mcp.vault.Vault`.

    One field per config-derived ``Vault`` parameter, carrying the same name,
    type, and default as the corresponding (docstring-deprecated) legacy
    keyword — see the ``Vault`` docstring for per-knob semantics.  Construct
    directly for library use, or from a served config via
    :meth:`from_project_config` /
    :func:`~markdown_vault_mcp.config_sections._assembly.to_vault_settings`.

    Attributes:
        index_path: Path to the SQLite index file (``None`` = in-memory).
        embeddings_path: Base path for the vector sidecar files (``None``
            disables semantic search).
        read_only: When ``True`` (library default), write operations raise.
        write_protect_existing: Require an ``if_match`` etag to overwrite.
        state_path: Hash-state JSON path (``None`` = derived default, see
            :meth:`effective_state_path`).
        indexed_frontmatter_fields: Frontmatter keys promoted to
            ``document_tags`` for structured filtering.
        required_frontmatter: Fields a document must carry to be indexed.
        git_pull_interval_s: Periodic-pull interval; ``0`` disables the loop.
        exclude_patterns: Configured glob patterns excluded from indexing
            (before the conventions-file derivation, see
            :meth:`effective_exclude_patterns`).
        attachment_extensions: Attachment extension allowlist.
        max_attachment_size_mb: Attachment context-size cap (``0`` = off).
        max_note_read_bytes: Full-document read cap in bytes (``0`` = off).
        chunks_per_file: Search results kept per file before grouping.
        snippet_words: Snippet truncation length in words.
        length_downweight_alpha: Length-downweight exponent for ranking.
        default_search_mode: Mode used when ``search`` gets no ``mode``.
        max_chunk_words: Word cap for the default heading chunker.
        max_chunk_chars: Character cap for the default heading chunker.
        max_chunk_chars_override: Explicit operator char-cap override — the
            stable warm-restart key (#649).
        chunk_overlap_words: Overlap carried between split chunks.
        summarize_max_notes: Cap on notes per ``summarize`` call.
        summarize_max_input_chars: Char budget per summarization request.
        title_field: Frontmatter key consulted first for document titles.
        searchable_frontmatter_fields: Frontmatter keys made keyword-searchable
            (activates embed-text format v2).
        embed_context: Force context-enriched (v2) embedding text.
        embedding_batch_size: Chunk texts per embedding-provider call.
        folder_weights: Folder-prefix score multipliers for search results.
        fts_weights: Per-column BM25 weights for the FTS5 rank config.
        conventions_file: Per-folder conventions filename (``None`` disables).
        okf_mode: OKF read-semantics mode (``auto`` / ``off`` / ``on``).
        okf_write: Enable the OKF enforced-write layer.
    """

    index_path: Path | None = None
    embeddings_path: Path | None = None
    read_only: bool = True
    write_protect_existing: bool = False
    state_path: Path | None = None
    indexed_frontmatter_fields: Sequence[str] | None = None
    required_frontmatter: Sequence[str] | None = None
    git_pull_interval_s: int = 0
    exclude_patterns: Sequence[str] | None = None
    attachment_extensions: Sequence[str] | None = None
    max_attachment_size_mb: float = 1.0
    max_note_read_bytes: int = 262144
    chunks_per_file: int = 2
    snippet_words: int = 200
    length_downweight_alpha: float = 0.25
    default_search_mode: str = "auto"
    max_chunk_words: int = 400
    max_chunk_chars: int | None = None
    max_chunk_chars_override: int | None = None
    chunk_overlap_words: int = 0
    summarize_max_notes: int = 50
    summarize_max_input_chars: int = 200_000
    title_field: str = "title"
    searchable_frontmatter_fields: Sequence[str] | None = None
    embed_context: bool = False
    embedding_batch_size: int = 4
    folder_weights: dict[str, float] | None = None
    fts_weights: dict[str, float] | None = None
    conventions_file: str | None = "_conventions.md"
    okf_mode: str = "auto"
    okf_write: bool = False

    @classmethod
    def from_project_config(
        cls,
        config: ProjectConfig,
        *,
        embedding_context_length: int | None = None,
    ) -> VaultSettings:
        """Map a served :class:`ProjectConfig` onto vault settings.

        Absorbs the historical ``to_vault_kwargs`` renames
        (``searchable_frontmatter`` → ``searchable_frontmatter_fields``,
        ``default_mode`` → ``default_search_mode``) and the weight-map
        tuple → dict conversions (#639).  The git pull interval resolves the
        same way the git-strategy assembly does: ``config.git.pull_interval_s``
        only when a remote is configured (repo URL or token), else ``0``.

        Args:
            config: The served project configuration.
            embedding_context_length: Token context length of the resolved
                embedding provider, used to derive ``max_chunk_chars``
                (``None`` — no provider — falls back to the bounded ceiling).
                Prefer
                :func:`~markdown_vault_mcp.config_sections._assembly.to_vault_settings`,
                which resolves the provider and threads this automatically.

        Returns:
            The mapped :class:`VaultSettings`.
        """
        # Function-local import: _assembly imports the git package at module
        # level; keep this module import-light and cycle-free.
        from markdown_vault_mcp.config_sections._assembly import derive_max_chunk_chars

        git = config.git
        return cls(
            index_path=config.indexing.index_path,
            embeddings_path=config.indexing.embeddings_path,
            read_only=config.read_only,
            write_protect_existing=config.write_protect_existing,
            state_path=config.indexing.state_path,
            indexed_frontmatter_fields=config.indexing.indexed_frontmatter_fields,
            required_frontmatter=config.indexing.required_frontmatter,
            # Mirrors the git-strategy assembly: only the managed and
            # token-compat modes run the pull loop; commit-only mode never
            # pulls, whatever the (defaulted) configured interval says.
            git_pull_interval_s=(
                git.pull_interval_s
                if (git.repo_url is not None or git.token is not None)
                else 0
            ),
            exclude_patterns=config.indexing.exclude_patterns,
            attachment_extensions=config.content.attachment_extensions,
            max_attachment_size_mb=config.content.max_attachment_size_mb,
            max_note_read_bytes=config.content.max_note_read_bytes,
            chunks_per_file=config.search.chunks_per_file,
            snippet_words=config.search.snippet_words,
            length_downweight_alpha=config.search.length_downweight_alpha,
            default_search_mode=config.search.default_mode,
            max_chunk_words=config.search.max_chunk_words,
            max_chunk_chars=derive_max_chunk_chars(
                context_length=embedding_context_length,
                override=config.search.max_chunk_chars_override,
            ),
            max_chunk_chars_override=config.search.max_chunk_chars_override,
            chunk_overlap_words=config.search.chunk_overlap_words,
            summarize_max_notes=config.summarize.max_notes,
            summarize_max_input_chars=config.summarize.max_input_chars,
            title_field=config.indexing.title_field,
            searchable_frontmatter_fields=config.indexing.searchable_frontmatter,
            embed_context=config.embeddings.embed_context,
            embedding_batch_size=config.embeddings.embedding_batch_size,
            # Weight maps are stored as frozen sorted tuples on the config
            # (#639); the Vault API takes plain dicts.
            folder_weights=(
                dict(config.search.folder_weights)
                if config.search.folder_weights is not None
                else None
            ),
            fts_weights=(
                dict(config.search.fts_weights)
                if config.search.fts_weights is not None
                else None
            ),
            conventions_file=config.content.conventions_file,
            okf_mode=config.content.okf_mode,
            okf_write=config.content.okf_write,
        )

    def effective_indexed_fields(self, *, okf_active: bool) -> list[str]:
        """Return the indexed-frontmatter set, OKF-extended when active.

        When OKF read semantics are active at construction time, the OKF
        scalar keys (``type`` / ``status`` / ``stale_after``) join the
        configured set so ``document_tags`` carries them (design okf.md §3).

        Args:
            okf_active: Whether the vault's OKF detection probe reports
                active read semantics.

        Returns:
            The effective field list (possibly empty, never ``None``).
        """
        fields = list(self.indexed_frontmatter_fields or [])
        if okf_active:
            fields.extend(key for key in OKF_INDEXED_FIELDS if key not in fields)
        return fields

    def effective_exclude_patterns(self) -> Sequence[str] | None:
        """Return the exclusion patterns with the conventions-file derivation.

        When a conventions file is configured, both fnmatch forms are
        appended (``name`` and ``**/name`` — the ``**/`` form alone does not
        match a root-level file) so convention files stay out of the index
        while remaining disk-readable.  Without one, the configured patterns
        pass through unchanged (possibly ``None``).

        Returns:
            The effective pattern sequence, or ``None`` when nothing is
            excluded.

        Raises:
            ValueError: If *conventions_file* contains fnmatch
                metacharacters (which would invert the exclusion).
        """
        if not self.conventions_file:
            return self.exclude_patterns
        if any(ch in self.conventions_file for ch in "*?[]"):
            raise ValueError(
                "conventions_file must not contain fnmatch metacharacters "
                f"(*, ?, [, ]), got {self.conventions_file!r}"
            )
        return [
            *(self.exclude_patterns or []),
            self.conventions_file,
            f"**/{self.conventions_file}",
        ]

    def effective_state_path(self, source_dir: Path) -> Path:
        """Return the hash-state path, defaulting under the vault root.

        Args:
            source_dir: The vault root, used when no explicit ``state_path``
                is configured.

        Returns:
            The explicit ``state_path``, or
            ``{source_dir}/.markdown_vault_mcp/state.json``.
        """
        if self.state_path is not None:
            return self.state_path
        return source_dir / _DEFAULT_STATE_SUBDIR / _DEFAULT_STATE_FILENAME
