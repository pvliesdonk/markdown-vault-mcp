"""Shared embedding providers for synthetic tests."""

from tests.conftest import MockEmbeddingProvider


class CountingProvider(MockEmbeddingProvider):
    """Collect texts sent to the deterministic mock provider."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Record texts and return the mock vectors."""
        self.texts.extend(texts)
        return super().embed(texts)
