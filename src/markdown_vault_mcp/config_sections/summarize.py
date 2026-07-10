"""Configuration for the LLM-backed ``summarize`` tool.

Provider-neutral by design: the summarization backend sits behind the
:class:`~markdown_vault_mcp.summarizer.Summarizer` abstraction, so a future
OpenAI (or other) implementation is a drop-in.  Anthropic/Haiku is the first
implementation; nothing above the abstraction names Anthropic.
"""

from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"


@dataclass(frozen=True)
class SummarizeConfig:
    """Backend selection + per-provider settings for the ``summarize`` tool.

    Attributes:
        provider: Explicitly selected backend name (e.g. ``"anthropic"``).
            ``None`` (the default) auto-detects the first backend whose
            credentials are present.
        anthropic_api_key: Anthropic API key (read from the bare
            ``ANTHROPIC_API_KEY`` env var, ecosystem convention). ``None``
            when unset.
        anthropic_model: Anthropic model id used for summaries.
        max_tokens: Upper bound on generated summary tokens per call.
        max_notes: Cap on the number of notes summarised in one call
            (subtree expansion is truncated to this many notes).
        max_input_chars: Aggregate cap on note characters sent to the model
            in one call (excess is truncated, with a flag on the result).
    """

    provider: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str = _DEFAULT_ANTHROPIC_MODEL
    max_tokens: int = 2048
    max_notes: int = 50
    max_input_chars: int = 200_000

    def __post_init__(self) -> None:
        """Validate numeric ranges (also covers direct construction, #638)."""
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {self.max_tokens!r}")
        if self.max_notes < 1:
            raise ValueError(f"max_notes must be >= 1, got {self.max_notes!r}")
        if self.max_input_chars < 1:
            raise ValueError(
                f"max_input_chars must be >= 1, got {self.max_input_chars!r}"
            )

    def has_provider(self) -> bool:
        """Return True when a usable summarization backend is configured.

        Provider-neutral: as backends are added, OR their credential checks
        here.  Used to gate the ``summarize`` tool's visibility without any
        caller referencing a specific provider.

        Returns:
            True when credentials for at least one backend are present.
        """
        return self.anthropic_api_key is not None

    @classmethod
    def from_env(cls, prefix: str) -> SummarizeConfig:
        """Construct SummarizeConfig by reading env vars.

        Reads ``ANTHROPIC_API_KEY`` from the bare (unprefixed) environment,
        matching the ecosystem convention (mirrors ``OPENAI_API_KEY`` for
        embeddings).  Everything else is namespaced under *prefix*.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated SummarizeConfig with defaults for unset vars.
        """
        import os

        from markdown_vault_mcp.config_sections._helpers import env, env_int

        return cls(
            provider=env(prefix, "SUMMARIZE_PROVIDER") or None,
            anthropic_api_key=(os.environ.get("ANTHROPIC_API_KEY") or "").strip()
            or None,
            anthropic_model=env(prefix, "SUMMARIZE_ANTHROPIC_MODEL")
            or _DEFAULT_ANTHROPIC_MODEL,
            max_tokens=env_int(prefix, "SUMMARIZE_MAX_TOKENS", 2048),
            max_notes=env_int(prefix, "SUMMARIZE_MAX_NOTES", 50),
            max_input_chars=env_int(prefix, "SUMMARIZE_MAX_INPUT_CHARS", 200_000),
        )
