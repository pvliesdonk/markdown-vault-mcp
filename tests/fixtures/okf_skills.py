"""Synthetic skill bytes shared by OKF read and retrieval tests."""

from pathlib import Path
from typing import Any

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


ROOT_OKF = {
    "type": "Literal",
    "status": "draft",
    "generated": {"by": "process:literal"},
    "verified": [{"by": "process:literal"}],
    "sources": [
        {"resource": "https://example.invalid/a"},
        {"resource": "https://example.invalid/b"},
    ],
    "stale_after": "2999-01-01",
}
NESTED_OKF = {
    "type": "Runbook",
    "status": "deprecated",
    "generated": {"by": "human:author"},
    "verified": [{"by": "human:reviewer"}],
    "sources": [{"resource": "https://example.invalid/nested"}],
    "stale_after": "2020-01-01",
}
ALIAS_CASES = [
    ("target/SKILL.md", "alias.md", "Runbook"),
    ("target/ordinary.md", "alias/SKILL.md", "Literal"),
    ("target/skill.md", "alias/SKILL.md", "Literal"),
]


@pytest.fixture(params=ALIAS_CASES)
def alias_note(
    tmp_path: Path, request: pytest.FixtureRequest
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Conflicting six-family metadata with an indexed target and alternate spelling."""
    from copy import deepcopy

    import frontmatter

    target, alias, expected_type = request.param
    raw = {**deepcopy(ROOT_OKF), "name": "discovery", "metadata": deepcopy(NESTED_OKF)}
    destination = tmp_path / target
    destination.parent.mkdir(parents=True)
    destination.write_text(
        frontmatter.dumps(
            frontmatter.Post("zebra instructions. See [peer](peer.md).", **raw)
        )
    )
    link = tmp_path / alias
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(destination)
    expected = deepcopy(NESTED_OKF if expected_type == "Runbook" else ROOT_OKF)
    return target, alias, raw, expected
