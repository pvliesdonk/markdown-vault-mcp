"""Synthetic skill bytes shared by OKF read and retrieval tests."""

import pytest


@pytest.fixture
def constant_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give retrieval channels equal vectors so tests isolate metadata behavior."""
    from tests.conftest import MockEmbeddingProvider

    def embed(provider: MockEmbeddingProvider, texts: list[str]) -> list[list[float]]:
        return [[1.0] * provider.dimension for _ in texts]

    monkeypatch.setattr(MockEmbeddingProvider, "embed", embed)


SKILL = """---
name: demo
description: Demonstrate zebra maintenance.
metadata:
  status: deprecated
  stale_after: '2020-01-01'
  generated:
    by: human:alex
    at: '2026-11-13T00:00:00Z'
  verified:
    - by: human:reviewer
  sources:
    - resource: https://example.invalid/spec
---
# Demo

Zebra maintenance instructions.

## Steps

Run the zebra checks.
"""
