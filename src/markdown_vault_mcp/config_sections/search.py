"""Search ranking + snippet-truncation knobs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchConfig:
    """Ranking/snippet tuning for keyword/semantic/hybrid search."""

    chunks_per_file: int = 2
    snippet_words: int = 200
    length_downweight_alpha: float = 0.25
    max_chunk_words: int = 400
    max_chunk_chars_override: int | None = None

    @classmethod
    def from_env(cls, prefix: str) -> SearchConfig:
        """Construct SearchConfig by reading ``{prefix}_*`` env vars.

        Raises:
            ValueError: If any search integer/float env var is invalid or
                out of range.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated SearchConfig with defaults for unset vars.
        """
        # Only env (not parse_int/float_env): SearchConfig raises on invalid input, not warn-and-default.
        from markdown_vault_mcp.config_sections._helpers import env

        raw_chunks = (env(prefix, "CHUNKS_PER_FILE") or "").strip()
        if raw_chunks:
            try:
                chunks_per_file = int(raw_chunks)
            except ValueError as exc:
                raise ValueError(
                    f"{prefix}_CHUNKS_PER_FILE must be a positive integer, "
                    f"got {raw_chunks!r}"
                ) from exc
        else:
            chunks_per_file = 2
        if chunks_per_file < 1:
            raise ValueError(
                f"chunks_per_file must be >= 1, got {chunks_per_file}; set "
                f"{prefix}_CHUNKS_PER_FILE to a positive integer."
            )

        raw_snippet = (env(prefix, "SNIPPET_WORDS") or "").strip()
        if raw_snippet:
            try:
                snippet_words = int(raw_snippet)
            except ValueError as exc:
                raise ValueError(
                    f"{prefix}_SNIPPET_WORDS must be a non-negative integer, "
                    f"got {raw_snippet!r}"
                ) from exc
        else:
            snippet_words = 200
        if snippet_words < 0:
            raise ValueError(f"snippet_words must be >= 0, got {snippet_words}")

        raw_alpha = (env(prefix, "LENGTH_DOWNWEIGHT_ALPHA") or "").strip()
        if raw_alpha:
            try:
                alpha = float(raw_alpha)
            except ValueError as e:
                raise ValueError(
                    f"{prefix}_LENGTH_DOWNWEIGHT_ALPHA must be a non-negative "
                    f"float, got {raw_alpha!r}"
                ) from e
            if alpha < 0:
                raise ValueError(f"length_downweight_alpha must be >= 0, got {alpha}")
        else:
            alpha = 0.25

        raw_max_chunk = (env(prefix, "MAX_CHUNK_WORDS") or "").strip()
        if raw_max_chunk:
            try:
                max_chunk_words = int(raw_max_chunk)
            except ValueError as exc:
                raise ValueError(
                    f"{prefix}_MAX_CHUNK_WORDS must be a positive integer, "
                    f"got {raw_max_chunk!r}"
                ) from exc
        else:
            max_chunk_words = 400
        if max_chunk_words < 1:
            raise ValueError(f"max_chunk_words must be >= 1, got {max_chunk_words}")

        raw_max_chars = (env(prefix, "MAX_CHUNK_CHARS") or "").strip()
        max_chunk_chars_override: int | None
        if raw_max_chars:
            try:
                max_chunk_chars_override = int(raw_max_chars)
            except ValueError as exc:
                raise ValueError(
                    f"{prefix}_MAX_CHUNK_CHARS must be a positive integer, "
                    f"got {raw_max_chars!r}"
                ) from exc
            if max_chunk_chars_override < 1:
                raise ValueError(
                    f"max_chunk_chars must be >= 1, got {max_chunk_chars_override}"
                )
        else:
            max_chunk_chars_override = None

        return cls(
            chunks_per_file=chunks_per_file,
            snippet_words=snippet_words,
            length_downweight_alpha=alpha,
            max_chunk_words=max_chunk_words,
            max_chunk_chars_override=max_chunk_chars_override,
        )
