"""Index + scanner configuration (paths, frontmatter, exclusions)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class IndexingConfig:
    """SQLite/vector index paths and what gets scanned + indexed."""

    index_path: Path | None = None
    state_path: Path | None = None
    embeddings_path: Path | None = None
    indexed_frontmatter_fields: list[str] | None = None
    required_frontmatter: list[str] | None = None
    exclude_patterns: list[str] | None = None
