"""Summarization backends for the ``summarize`` tool.

Provides a provider-neutral :class:`Summarizer` ABC and one concrete
implementation, :class:`AnthropicSummarizer` (Claude Messages API).  Use
:func:`get_summarizer` to resolve a backend from a
:class:`~markdown_vault_mcp.config.ProjectConfig`.

The abstraction is the seam that keeps everything above it — the manager,
facet, tool, and gating logic — free of any provider name.  Adding an
``OpenAISummarizer`` later is one new class plus one branch in
:func:`get_summarizer`; nothing else changes.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from markdown_vault_mcp.exceptions import ConfigurationError

if TYPE_CHECKING:
    from markdown_vault_mcp.config import ProjectConfig

logger = logging.getLogger(__name__)

# Recognised backend names for the SUMMARIZE_PROVIDER selector.
_ANTHROPIC = "anthropic"


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


class AnthropicSummarizer(Summarizer):
    """Summarizer backed by the Anthropic Claude Messages API.

    Args:
        api_key: Anthropic API key. Must be non-empty.
        model: Claude model id (e.g. ``"claude-haiku-4-5"``).
        max_tokens: Upper bound on generated tokens per call.
    """

    def __init__(self, api_key: str, model: str, *, max_tokens: int) -> None:
        """Initialise the Anthropic client, lazily importing the SDK.

        Args:
            api_key: Anthropic API key. Must be non-empty.
            model: Claude model id.
            max_tokens: Upper bound on generated tokens per call.

        Raises:
            ImportError: If the ``anthropic`` SDK is not installed.
            RuntimeError: If *api_key* is empty.
        """
        if not api_key:
            raise RuntimeError("AnthropicSummarizer requires a non-empty api_key.")
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "AnthropicSummarizer requires the 'anthropic' SDK. "
                "Install it with: pip install 'markdown-vault-mcp[summarize]'"
            ) from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def summarize(self, system: str, user: str) -> str:
        """Call the Messages API and return the first text block.

        Args:
            system: System-prompt text.
            user: User message content.

        Returns:
            The generated summary text.

        Raises:
            RuntimeError: If the API call fails or returns no text.
        """
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except self._anthropic.APIError as exc:
            # Expected upstream failures (auth, rate limit, 5xx): surface a
            # user-facing error string rather than leak the SDK exception.
            logger.warning("summarize_api_error model=%s error=%s", self._model, exc)
            raise RuntimeError(f"Anthropic summarization failed: {exc}") from exc

        # ``response.content`` is a union of block types; only ``text`` blocks
        # carry ``.text``. Match on the discriminator and read via getattr so we
        # do not couple to the SDK's block classes at type-check time.
        text: str | None = None
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", None)
                break
        if not text:
            # Refusals / empty completions land here (e.g. stop_reason="refusal").
            logger.warning(
                "summarize_empty_response model=%s stop_reason=%s",
                self._model,
                getattr(response, "stop_reason", None),
            )
            raise RuntimeError(
                "Anthropic summarization returned no text "
                f"(stop_reason={getattr(response, 'stop_reason', None)!r})."
            )
        return text

    @property
    def provider_name(self) -> str:
        """Stable backend identifier."""
        return _ANTHROPIC


def get_summarizer(config: ProjectConfig) -> Summarizer:
    """Resolve a summarization backend from config.

    Honours ``config.summarize.provider`` when set; otherwise auto-detects the
    first backend whose credentials are present (currently: an Anthropic API
    key).  Mirrors :func:`markdown_vault_mcp.providers.get_embedding_provider`.

    Args:
        config: Vault configuration containing summarize settings.

    Returns:
        An initialised :class:`Summarizer`.

    Raises:
        RuntimeError: If no backend is available and no provider is explicitly
            selected, or if the explicitly requested backend lacks credentials.
        ConfigurationError: If ``config.summarize.provider`` is unrecognised.
    """
    summ = config.summarize
    explicit = (summ.provider or "").strip().lower()

    if explicit == _ANTHROPIC:
        logger.info("Using AnthropicSummarizer (summarize_provider=anthropic)")
        return AnthropicSummarizer(
            api_key=summ.anthropic_api_key or "",
            model=summ.anthropic_model,
            max_tokens=summ.max_tokens,
        )

    if explicit:
        raise ConfigurationError(
            f"Unrecognised summarize provider value: {explicit!r}. "
            "Valid values: 'anthropic'."
        )

    # Auto-detect: Anthropic API key present?
    if summ.anthropic_api_key:
        logger.info("Auto-detected AnthropicSummarizer (ANTHROPIC_API_KEY is set)")
        return AnthropicSummarizer(
            api_key=summ.anthropic_api_key,
            model=summ.anthropic_model,
            max_tokens=summ.max_tokens,
        )

    raise RuntimeError(
        "No summarization backend is available. Set ANTHROPIC_API_KEY and "
        "install the SDK with: pip install 'markdown-vault-mcp[summarize]'"
    )
