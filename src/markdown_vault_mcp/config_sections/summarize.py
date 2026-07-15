"""Configuration for the LLM-backed ``summarize`` tool.

Provider-neutral by design: the summarization backend sits behind the
:class:`~markdown_vault_mcp.summarizer.Summarizer` abstraction.  The concrete
backend speaks the OpenAI-compatible chat-completions API, so a single
``base_url`` / ``api_key`` / ``model`` triple covers OpenAI, Ollama, Anthropic's
OpenAI-compat endpoint, and any other compatible server; nothing above the
abstraction names a vendor.
"""

from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_OPENAI_MODEL = "gpt-5-mini"


@dataclass(frozen=True)
class SummarizeConfig:
    """Backend selection + endpoint settings for the ``summarize`` tool.

    Attributes:
        provider: Explicitly selected backend name (only ``"openai"`` is
            recognised).  ``None`` (the default) auto-detects: the backend is
            used when credentials or an explicit endpoint are present.
        openai_api_key: API key for the OpenAI-compatible endpoint.  Read from
            ``{prefix}_SUMMARIZE_OPENAI_API_KEY`` with bare ``OPENAI_API_KEY``
            as fallback (ecosystem convention, shared with embeddings).
            ``None`` when unset — keyless local endpoints (Ollama) work
            without one.
        openai_base_url: OpenAI-compatible endpoint base URL.  ``None`` means
            the SDK default (``https://api.openai.com/v1``).  Setting it
            enables the tool even without an API key.
        openai_model: Chat model id used for summaries.
        max_tokens: Upper bound on generated tokens per call.  On
            reasoning models this budget covers internal reasoning tokens
            as well as the visible summary (#919).
        max_notes: Cap on the number of notes summarised in one call
            (subtree expansion is truncated to this many notes).
        max_input_chars: Aggregate cap on note characters sent to the model
            in one call (excess is truncated, with a flag on the result).
    """

    provider: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = _DEFAULT_OPENAI_MODEL
    max_tokens: int = 8192
    max_notes: int = 50
    max_input_chars: int = 200_000

    def __post_init__(self) -> None:
        """Validate numeric ranges (#638) and normalize the base URL."""
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {self.max_tokens!r}")
        if self.max_notes < 1:
            raise ValueError(f"max_notes must be >= 1, got {self.max_notes!r}")
        if self.max_input_chars < 1:
            raise ValueError(
                f"max_input_chars must be >= 1, got {self.max_input_chars!r}"
            )
        base = (self.openai_base_url or "").strip().rstrip("/") or None
        object.__setattr__(self, "openai_base_url", base)

    def has_provider(self) -> bool:
        """Return True when a usable summarization backend is configured.

        The backend is available when an API key is present (hosted
        endpoints) or a base URL is explicitly configured (keyless local
        endpoints such as Ollama).  Used to gate the ``summarize`` tool's
        visibility.

        Returns:
            True when a key or an explicit endpoint is configured.
        """
        return self.openai_api_key is not None or self.openai_base_url is not None

    @classmethod
    def from_env(cls, prefix: str) -> SummarizeConfig:
        """Construct SummarizeConfig by reading env vars.

        The API key uses prefixed-wins-bare-fallback semantics
        (``{prefix}_SUMMARIZE_OPENAI_API_KEY`` then bare ``OPENAI_API_KEY``,
        mirroring embeddings).  The bare ``OPENAI_BASE_URL`` is a *value*
        fallback only: it routes traffic when a key already enables the
        feature, but never enables summarize by itself — a user who sets it
        purely for embeddings must not get a surprise summarize tool.  Only
        the prefixed ``SUMMARIZE_OPENAI_BASE_URL`` counts toward enablement.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated SummarizeConfig with defaults for unset vars.
        """
        import os

        from markdown_vault_mcp.config_sections._helpers import env, env_int

        api_key = (
            env(prefix, "SUMMARIZE_OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip() or None
        prefixed_base = (env(prefix, "SUMMARIZE_OPENAI_BASE_URL") or "").strip() or None
        bare_base = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None
        return cls(
            provider=env(prefix, "SUMMARIZE_PROVIDER") or None,
            openai_api_key=api_key,
            openai_base_url=prefixed_base or (bare_base if api_key else None),
            openai_model=env(prefix, "SUMMARIZE_OPENAI_MODEL") or _DEFAULT_OPENAI_MODEL,
            max_tokens=env_int(prefix, "SUMMARIZE_MAX_TOKENS", 8192),
            max_notes=env_int(prefix, "SUMMARIZE_MAX_NOTES", 50),
            max_input_chars=env_int(prefix, "SUMMARIZE_MAX_INPUT_CHARS", 200_000),
        )
