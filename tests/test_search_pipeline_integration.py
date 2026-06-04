"""End-to-end integration test for the search ranking pipeline.

Mirrors the diagnostic case described in
docs/superpowers/specs/2026-04-30-search-ranking-and-snippets-design.md:
one 12-section essay plus many short atomic notes that all mention the
query terms. The essay must not occupy more than 2 of the top 10 slots,
result payloads must be bounded, and other notes must get representation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.collection import Collection

from .conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def diagnostic_vault(tmp_path: Path) -> Path:
    """Build a vault: one 12-section essay + 20 short atomic notes.

    The essay's body is multi-line per section so the doc clears the
    30-line short-doc bypass and actually splits into 12+ chunks.
    """
    sections: list[str] = []
    for i in range(12):
        sections.append(f"## Section {i}")
        sections.extend(
            [
                f"On the etymology of secura: this {i} section "
                "repeats key terms etymology security care."
            ]
            * 30
        )
    essay = "# Lexicon Essay\n" + "\n".join(sections) + "\n"
    (tmp_path / "essay.md").write_text(essay, encoding="utf-8")

    # Twenty short atomic notes — each multi-line so they aren't bypassed.
    for i in range(20):
        body = (
            f"# Note {i}\n"
            + "\n".join(
                [
                    f"etymology and secura — note {i} discusses "
                    "security and care in brief."
                ]
                * 8
            )
            + "\n"
        )
        (tmp_path / f"note_{i:02d}.md").write_text(body, encoding="utf-8")
    return tmp_path


def _make_collection(vault: Path, *, with_embeddings: bool = False) -> Collection:
    """Create a Collection for the diagnostic vault.

    Args:
        vault: Path to the vault root.
        with_embeddings: Whether to configure an embedding provider.

    Returns:
        A configured Collection instance.
    """
    kwargs: dict = {
        "source_dir": vault,
        "index_path": None,  # in-memory SQLite
        "read_only": True,
    }
    if with_embeddings:
        provider = MockEmbeddingProvider()
        kwargs["embedding_provider"] = provider
        kwargs["embeddings_path"] = vault / ".embeddings"
    return Collection(**kwargs)


def test_essay_capped_in_top_ten_keyword(diagnostic_vault: Path) -> None:
    """The essay must not exceed chunks_per_file=2 sections in keyword mode."""
    coll = _make_collection(diagnostic_vault)
    coll.index.build_index()

    results = coll.reader.search(
        "etymology secura security care", mode="keyword", limit=10
    )
    essay_groups = [r for r in results if r.path == "essay.md"]
    # Grouped shape: essay appears at most once.
    assert len(essay_groups) <= 1
    if essay_groups:
        assert len(essay_groups[0].sections) <= 2, (
            f"essay.md surfaced {len(essay_groups[0].sections)} sections (keyword)"
        )
    # Other docs get representation.
    other_paths = {r.path for r in results} - {"essay.md"}
    assert len(other_paths) >= 5, (
        f"too few other docs in top 10 (keyword): {other_paths}"
    )


def test_essay_capped_in_top_ten_semantic(diagnostic_vault: Path) -> None:
    """The essay must not exceed chunks_per_file=2 sections in semantic mode."""
    coll = _make_collection(diagnostic_vault, with_embeddings=True)
    coll.index.build_index()
    coll.index.build_embeddings()

    results = coll.reader.search(
        "etymology secura security care", mode="semantic", limit=10
    )
    essay_groups = [r for r in results if r.path == "essay.md"]
    assert len(essay_groups) <= 1
    if essay_groups:
        assert len(essay_groups[0].sections) <= 2, (
            f"essay.md surfaced {len(essay_groups[0].sections)} sections (semantic)"
        )
    other_paths = {r.path for r in results} - {"essay.md"}
    assert len(other_paths) >= 5, (
        f"too few other docs in top 10 (semantic): {other_paths}"
    )


def test_essay_capped_in_top_ten_hybrid(diagnostic_vault: Path) -> None:
    """The essay must not exceed chunks_per_file=2 sections in hybrid mode."""
    coll = _make_collection(diagnostic_vault, with_embeddings=True)
    coll.index.build_index()
    coll.index.build_embeddings()

    results = coll.reader.search(
        "etymology secura security care", mode="hybrid", limit=10
    )
    essay_groups = [r for r in results if r.path == "essay.md"]
    assert len(essay_groups) <= 1
    if essay_groups:
        assert len(essay_groups[0].sections) <= 2, (
            f"essay.md surfaced {len(essay_groups[0].sections)} sections (hybrid)"
        )
    other_paths = {r.path for r in results} - {"essay.md"}
    assert len(other_paths) >= 5, (
        f"too few other docs in top 10 (hybrid): {other_paths}"
    )


def test_payloads_bounded_by_default(diagnostic_vault: Path) -> None:
    """Default snippet_words=200; per-section payloads stay below ~220 words."""
    coll = _make_collection(diagnostic_vault)
    coll.index.build_index()
    results = coll.reader.search("etymology secura", mode="keyword", limit=10)
    for r in results:
        for s in r.sections:
            assert len(s.content.split()) <= 220, (
                f"{r.path} section {s.heading!r}: "
                f"snippet has {len(s.content.split())} words"
            )
