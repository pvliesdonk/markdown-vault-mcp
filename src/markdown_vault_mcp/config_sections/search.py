"""Search ranking + snippet-truncation knobs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SearchConfig:
    """Ranking/snippet tuning for keyword/semantic/hybrid search."""

    chunks_per_file: int = 2
    snippet_words: int = 200
    length_downweight_alpha: float = 0.25
    max_chunk_words: int = 400
