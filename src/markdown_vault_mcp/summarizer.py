"""Summarization backends for the ``summarize`` tool.

Provides a provider-neutral :class:`Summarizer` ABC and one concrete
implementation, :class:`OpenAISummarizer`, which speaks the OpenAI-compatible
chat-completions API.  A single ``base_url`` / ``api_key`` / ``model`` triple
covers OpenAI, Ollama (``http://localhost:11434/v1``), Anthropic's
OpenAI-compat endpoint (``https://api.anthropic.com/v1``), vLLM, and any other
compatible server.  Use :func:`get_summarizer` to resolve a backend from a
:class:`~markdown_vault_mcp.config.ProjectConfig`.

The abstraction is the seam that keeps everything above it — the manager,
facet, tool, and gating logic — free of any provider name.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import ConfigurationError

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

    from markdown_vault_mcp.config import ProjectConfig

logger = logging.getLogger(__name__)

# Recognised backend names for the SUMMARIZE_PROVIDER selector.
_OPENAI = "openai"
# Removed backend name, kept for a targeted migration error (#915).
_ANTHROPIC_REMOVED = "anthropic"

# The openai SDK requires a non-None api_key string even for servers that
# ignore auth (Ollama's own docs suggest this placeholder).
_PLACEHOLDER_API_KEY = "ollama"


class Summarizer(ABC):
    """Abstract base class for summarization backends."""

    @abstractmethod
    def summarize(self, system: str, user: str) -> str:
        """Generate a summary for *user* content under the *system* instruction.

        Args:
            system: System-prompt text steering the summary's shape.
            user: The user message (the notes to summarise plus any focus).

        Returns:
            The generated summary text.

        Raises:
            RuntimeError: If the backend call fails.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable backend identifier (for logging)."""
        ...


class OpenAISummarizer(Summarizer):
    """Summarizer backed by an OpenAI-compatible chat-completions endpoint.

    Args:
        api_key: API key for the endpoint. ``None`` for keyless local
            endpoints (a placeholder is sent; e.g. Ollama ignores it).
        model: Chat model id (e.g. ``"gpt-5-mini"``).
        base_url: Endpoint base URL. ``None`` uses the SDK default
            (``https://api.openai.com/v1``).
        max_tokens: Upper bound on generated tokens per call.
        timeout: Per-request wall-clock budget in seconds. Exceeding it
            raises a clear, actionable :class:`RuntimeError` instead of
            running to the SDK's ~600s default (#937).
    """

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        base_url: str | None = None,
        max_tokens: int,
        timeout: float = 120.0,
    ) -> None:
        """Initialise the client, lazily importing the openai SDK.

        Args:
            api_key: API key, or ``None`` for keyless endpoints.
            model: Chat model id.
            base_url: Endpoint base URL, or ``None`` for the SDK default.
            max_tokens: Upper bound on generated tokens per call.
            timeout: Per-request wall-clock budget in seconds.

        Raises:
            ImportError: If the ``openai`` SDK is not installed.
        """
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "OpenAISummarizer requires the 'openai' SDK. "
                "Install it with: pip install 'markdown-vault-mcp[summarize]'"
            ) from exc
        self._openai = openai
        # A bounded per-request timeout (default 120s; same fixed-client-side-
        # timeout pattern as the embeddings transport, which pins 30s) so a
        # slow synthesis fails fast server-side with a specific error rather
        # than running to the SDK's ~600s default while the MCP client
        # abandons the request at its own opaque timeout (#937).
        self._client = openai.OpenAI(
            api_key=api_key or _PLACEHOLDER_API_KEY,
            base_url=base_url,
            timeout=timeout,
        )
        self._model = model
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._timeout = timeout

    def _create(self, system: str, user: str) -> Any:
        """Call chat.completions.create, adapting parameters to the server.

        Sends ``max_completion_tokens`` (required by newer OpenAI models,
        which reject ``max_tokens``) and ``reasoning_effort="low"`` (on
        reasoning models the token budget covers internal reasoning too;
        summarization is condensation, not problem-solving, so low effort
        keeps the budget for actual output — #919). When a strict
        OpenAI-compatible server rejects either parameter by name with a
        BadRequest error, that parameter is dropped (or swapped to legacy
        ``max_tokens``) and the call is retried; servers such as Ollama and
        Anthropic's compat endpoint ignore unknown fields outright.

        Args:
            system: System-prompt text.
            user: User message content.

        Returns:
            The chat-completion response object.
        """
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_completion_tokens": self._max_tokens,
            "reasoning_effort": "low",
            "messages": messages,
        }
        # At most one fallback per adaptive parameter, then a final attempt.
        for _ in range(2):
            try:
                return self._client.chat.completions.create(**kwargs)
            except self._openai.BadRequestError as exc:
                text = str(exc)
                if "reasoning_effort" in text and "reasoning_effort" in kwargs:
                    logger.debug(
                        "summarize_reasoning_effort_fallback model=%s error=%s",
                        self._model,
                        exc,
                    )
                    del kwargs["reasoning_effort"]
                    continue
                if (
                    "max_completion_tokens" in text
                    and "max_completion_tokens" in kwargs
                ):
                    logger.debug(
                        "summarize_token_param_fallback model=%s error=%s",
                        self._model,
                        exc,
                    )
                    kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
                    continue
                raise
        return self._client.chat.completions.create(**kwargs)

    def summarize(self, system: str, user: str) -> str:
        """Call the chat-completions API and return the message content.

        Args:
            system: System-prompt text.
            user: User message content.

        Returns:
            The generated summary text.

        Raises:
            RuntimeError: If the API call fails, the model refuses, or the
                response carries no text.
        """
        try:
            response = self._create(system, user)
        except self._openai.APITimeoutError as exc:
            # A vague client-side timeout is exactly what #937 is about:
            # convert it into a specific, actionable server-side error that
            # names the budget and the concrete ways to fit under it, so the
            # model/user sees guidance instead of an opaque "timed out".
            logger.warning(
                "summarize_timeout model=%s timeout=%ss", self._model, self._timeout
            )
            raise RuntimeError(
                f"Summarization exceeded the {self._timeout:g}s per-request budget. "
                "The selection is too large or the focus too demanding to "
                "generate within the limit. Narrow the request — fewer paths, "
                "a smaller max_notes, a more focused 'focus', or mode='per_note' "
                "— or, if the backend is simply slow, raise "
                "MARKDOWN_VAULT_MCP_SUMMARIZE_TIMEOUT (keep it below your MCP "
                "client's request timeout)."
            ) from exc
        except self._openai.OpenAIError as exc:
            # Expected upstream failures (auth, rate limit, 5xx): surface a
            # user-facing error string rather than leak the SDK exception.
            logger.warning("summarize_api_error model=%s error=%s", self._model, exc)
            raise RuntimeError(
                f"OpenAI-compatible summarization failed: {exc}"
            ) from exc

        choices = getattr(response, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        finish_reason = getattr(choices[0], "finish_reason", None) if choices else None
        # ``refusal`` exists on modern OpenAI responses; compat servers may
        # omit the field entirely, hence getattr.
        refusal = getattr(message, "refusal", None)
        if refusal:
            logger.warning(
                "summarize_refusal model=%s refusal=%s", self._model, refusal
            )
            raise RuntimeError(f"Summarization request was refused: {refusal}")
        text: str | None = getattr(message, "content", None)
        if not text:
            logger.warning(
                "summarize_empty_response model=%s finish_reason=%s",
                self._model,
                finish_reason,
            )
            if finish_reason == "length":
                # On reasoning models the token budget covers internal
                # reasoning too, so it can be exhausted before any visible
                # summary text is produced.
                raise RuntimeError(
                    "OpenAI-compatible summarization produced no text: the "
                    "output token budget was exhausted before any summary "
                    "was emitted (on reasoning models the budget covers "
                    "internal reasoning too). Raise "
                    "MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_TOKENS."
                )
            raise RuntimeError(
                "OpenAI-compatible summarization returned no text "
                f"(finish_reason={finish_reason!r})."
            )
        return text

    @property
    def provider_name(self) -> str:
        """Stable backend identifier."""
        return _OPENAI


def get_summarizer(config: ProjectConfig) -> Summarizer:
    """Resolve a summarization backend from config.

    Honours ``config.summarize.provider`` when set; otherwise auto-detects:
    the OpenAI-compatible backend is used when an API key or an explicit base
    URL is configured.  Mirrors
    :func:`markdown_vault_mcp.providers.get_embedding_provider`.

    Args:
        config: Vault configuration containing summarize settings.

    Returns:
        An initialised :class:`Summarizer`.

    Raises:
        RuntimeError: If no backend is available and no provider is explicitly
            selected.
        ConfigurationError: If ``config.summarize.provider`` is unrecognised
            (including the removed ``anthropic`` backend).
    """
    summ = config.summarize
    explicit = (summ.provider or "").strip().lower()

    if explicit == _ANTHROPIC_REMOVED:
        raise ConfigurationError(
            "The 'anthropic' summarize backend was removed (#915); Claude "
            "models are available through Anthropic's OpenAI-compatible "
            "endpoint instead. Set "
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL="
            "https://api.anthropic.com/v1, put the Anthropic API key in "
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY, and set "
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL (e.g. claude-haiku-4-5)."
        )

    if explicit and explicit != _OPENAI:
        raise ConfigurationError(
            f"Unrecognised summarize provider value: {explicit!r}. "
            "Valid values: 'openai'."
        )

    if explicit == _OPENAI and not summ.has_provider():
        # An explicit provider with neither a key nor an endpoint would
        # otherwise construct a client aimed at the SDK default endpoint
        # with a placeholder key, deferring the failure to request time.
        raise RuntimeError(
            "Summarize provider 'openai' is selected but no backend is "
            "configured. Set OPENAI_API_KEY (or "
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL for a keyless "
            "local endpoint)."
        )

    if explicit == _OPENAI or summ.has_provider():
        logger.info(
            "Using OpenAISummarizer (summarize_provider=%s) base_url=%s model=%s",
            explicit or "auto",
            summ.openai_base_url or "default",
            summ.openai_model,
        )
        return OpenAISummarizer(
            api_key=summ.openai_api_key,
            model=summ.openai_model,
            base_url=summ.openai_base_url,
            max_tokens=summ.max_tokens,
            timeout=summ.timeout,
        )

    raise RuntimeError(
        "No summarization backend is available. Set OPENAI_API_KEY (or "
        "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL for a keyless local "
        "endpoint) and install the SDK with: "
        "pip install 'markdown-vault-mcp[summarize]'"
    )
