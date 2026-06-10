"""Index + scanner configuration (paths, frontmatter, exclusions)."""

from __future__ import annotations

# Imported at runtime (not under TYPE_CHECKING) so the frozen dataclass's field
# annotations stay resolvable if anything introspects them via get_type_hints.
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_SEQUENCE_FIELDS = (
    "indexed_frontmatter_fields",
    "required_frontmatter",
    "exclude_patterns",
)


@dataclass(frozen=True)
class IndexingConfig:
    """SQLite/vector index paths and what gets scanned + indexed."""

    index_path: Path | None = None
    state_path: Path | None = None
    embeddings_path: Path | None = None
    indexed_frontmatter_fields: Sequence[str] | None = None
    required_frontmatter: Sequence[str] | None = None
    exclude_patterns: Sequence[str] | None = None

    def __post_init__(self) -> None:
        """Freeze the sequence fields into tuples for deep immutability (#639).

        The fields accept any ``Sequence[str]`` (e.g. a list from ``from_env``)
        but are stored as tuples so a caller cannot mutate the frozen config's
        contents after construction.
        """
        for name in _SEQUENCE_FIELDS:
            value = getattr(self, name)
            if value is not None and not isinstance(value, tuple):
                object.__setattr__(self, name, tuple(value))

    @classmethod
    def from_env(cls, prefix: str) -> IndexingConfig:
        """Construct IndexingConfig by reading ``{prefix}_*`` env vars.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated IndexingConfig with defaults for unset vars.
        """
        from fastmcp_pvl_core import parse_list

        from markdown_vault_mcp.config_sections._helpers import env

        def _path(name: str) -> Path | None:
            raw = (env(prefix, name) or "").strip()
            return Path(raw) if raw else None

        return cls(
            index_path=_path("INDEX_PATH"),
            state_path=_path("STATE_PATH"),
            embeddings_path=_path("EMBEDDINGS_PATH"),
            indexed_frontmatter_fields=parse_list(env(prefix, "INDEXED_FIELDS") or "")
            or None,
            required_frontmatter=parse_list(env(prefix, "REQUIRED_FIELDS") or "")
            or None,
            exclude_patterns=parse_list(env(prefix, "EXCLUDE") or "") or None,
        )
