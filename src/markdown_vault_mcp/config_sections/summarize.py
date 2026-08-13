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
        timeout: Per-request wall-clock budget, in seconds, for a single
            backend call. When a call exceeds it the tool returns a clear,
            actionable error instead of running to the SDK's ~600s default
            while the MCP client abandons the request at its own (shorter,
            opaque) timeout (#937). Set it *below* your MCP client's request
            timeout so the server-side error wins the race and reaches the
            model/user. The soft deadline before a still-running call is
            promoted to a pollable background job is not a summarize knob:
            it belongs to the pvl-core jobs subsystem
            (``JOBS_SOFT_DEADLINE_S``, #1033).
    """

    provider: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = _DEFAULT_OPENAI_MODEL
    max_tokens: int = 8192
    max_notes: int = 50
    max_input_chars: int = 200_000
    timeout: float = 120.0

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
        if self.timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {self.timeout!r}")
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
