"""Shared embedding providers for synthetic tests."""

from tests.conftest import MockEmbeddingProvider


class CountingProvider(MockEmbeddingProvider):
    """Collect texts sent to the deterministic mock provider."""

    def __init__(self, dim: int = 32) -> None:
        super().__init__(dim)
        self.texts: list[str] = []

    def record_texts(self, texts: list[str]) -> None:
        """Record inputs submitted to the provider."""
        self.texts.extend(texts)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Record texts and return the mock vectors."""
        self.record_texts(texts)
        return super().embed(texts)
