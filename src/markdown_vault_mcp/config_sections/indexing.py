"""Index + scanner configuration (paths, frontmatter, exclusions)."""

from __future__ import annotations

# Imported at runtime (not under TYPE_CHECKING) so the frozen dataclass's field
# annotations stay resolvable if anything introspects them via get_type_hints.
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from markdown_vault_mcp.exceptions import ConfigurationError

_SEQUENCE_FIELDS = (
    "indexed_frontmatter_fields",
    "required_frontmatter",
    "exclude_patterns",
    "searchable_frontmatter",
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
    title_field: str = "title"
    searchable_frontmatter: Sequence[str] | None = None

    def __post_init__(self) -> None:
        """Freeze the sequence fields into tuples for deep immutability (#639).

        The fields accept any ``Sequence[str]`` (e.g. a list from ``from_env``)
        but are stored as tuples so a caller cannot mutate the frozen config's
        contents after construction. A bare ``str``/``bytes`` is rejected: it is
        itself a ``Sequence[str]`` and would otherwise be silently split into
        individual characters.

        ``title_field`` is stripped and must be non-empty.

        Raises:
            ConfigurationError: If a sequence field is set to a ``str`` or
                ``bytes`` instead of a sequence of strings, or if
                ``title_field`` is empty/whitespace.
        """
        for name in _SEQUENCE_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, (str, bytes)):
                raise ConfigurationError(
                    f"{name} must be a sequence of strings, not a single "
                    f"{type(value).__name__}"
                )
            if not isinstance(value, tuple):
                object.__setattr__(self, name, tuple(value))
        title = (self.title_field or "").strip()
        if not title:
            raise ConfigurationError("title_field must be a non-empty string")
        object.__setattr__(self, "title_field", title)
