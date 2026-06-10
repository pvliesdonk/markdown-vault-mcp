"""Search ranking + snippet-truncation knobs."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError


@dataclass(frozen=True)
class SearchConfig:
    """Ranking/snippet tuning for keyword/semantic/hybrid search."""

    chunks_per_file: int = 2
    snippet_words: int = 200
    length_downweight_alpha: float = 0.25
    max_chunk_words: int = 400
    max_chunk_chars_override: int | None = None

    def __post_init__(self) -> None:
        """Validate ranges on every construction path (#638).

        Raises:
            ConfigurationError: If any field is out of range.
        """
        if self.chunks_per_file < 1:
            raise ConfigurationError(
                f"chunks_per_file must be >= 1, got {self.chunks_per_file}"
            )
        if self.snippet_words < 0:
            raise ConfigurationError(
                f"snippet_words must be >= 0, got {self.snippet_words}"
            )
        if self.length_downweight_alpha < 0:
            raise ConfigurationError(
                "length_downweight_alpha must be >= 0, got "
                f"{self.length_downweight_alpha}"
            )
        if self.max_chunk_words < 1:
            raise ConfigurationError(
                f"max_chunk_words must be >= 1, got {self.max_chunk_words}"
            )
        if (
            self.max_chunk_chars_override is not None
            and self.max_chunk_chars_override < 1
        ):
            raise ConfigurationError(
                f"max_chunk_chars must be >= 1, got {self.max_chunk_chars_override}"
            )

    @classmethod
    def from_env(cls, prefix: str) -> SearchConfig:
        """Construct SearchConfig by reading ``{prefix}_*`` env vars.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated SearchConfig with defaults for unset vars.

        Raises:
            ConfigurationError: If any search integer/float env var is invalid
                (non-numeric) or out of range.
        """
        from markdown_vault_mcp.config_sections._helpers import (
            env_float,
            env_int,
            opt_int,
        )

        return cls(
            chunks_per_file=env_int(prefix, "CHUNKS_PER_FILE", 2),
            snippet_words=env_int(prefix, "SNIPPET_WORDS", 200),
            length_downweight_alpha=env_float(prefix, "LENGTH_DOWNWEIGHT_ALPHA", 0.25),
            max_chunk_words=env_int(prefix, "MAX_CHUNK_WORDS", 400),
            max_chunk_chars_override=opt_int(prefix, "MAX_CHUNK_CHARS"),
        )
