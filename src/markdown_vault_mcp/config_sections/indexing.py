"""Index + scanner configuration (paths, frontmatter, exclusions)."""

from __future__ import annotations

# Imported at runtime (not under TYPE_CHECKING) so the frozen dataclass's field
# annotations stay resolvable if anything introspects them via get_type_hints.
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from markdown_vault_mcp.exceptions import ConfigurationError

_SEQUENCE_FIELDS = (
    "indexed_frontmatter_fields",
    "required_frontmatter",
    "exclude_patterns",
    "searchable_frontmatter",
)


def store_default_search(store: str) -> str:
    """The search mode an embedding store gets when none is set (#1900).

    The numpy store can only hold its vectors in memory; the SQLite and
    PostgreSQL stores stream them, so the default store's memory does not
    grow with the vault.

    Args:
        store: The embedding store's registry name.

    Returns:
        ``"resident"`` for ``"numpy"``, ``"streamed"`` for any other store.
    """
    return "resident" if store == "numpy" else "streamed"


@dataclass(frozen=True)
class IndexingConfig:
    """SQLite/vector index paths and what gets scanned + indexed."""

    index_path: Path | None = None
    state_path: Path | None = None
    embeddings_path: Path | None = None
    embedding_store: str = "sqlite"
    embedding_search: str = ""
    embedding_search_workspace_mb: int = 1
    embedding_postgres_dsn: str | None = field(default=None, repr=False)
    embedding_postgres_schema: str | None = None
    embedding_postgres_pool_size: int = 4
    embedding_postgres_statement_timeout_s: float = 30.0
    indexed_frontmatter_fields: Sequence[str] | None = None
    required_frontmatter: Sequence[str] | None = None
    exclude_patterns: Sequence[str] | None = None
    title_field: str = "title"
    searchable_frontmatter: Sequence[str] | None = None

    @property
    def vector_storage_configured(self) -> bool:
        """Whether configuration names somewhere to keep the vectors.

        ``EMBEDDINGS_PATH`` names files; ``EMBEDDING_STORE=postgres`` names a
        database (#1504). Without either, an on-disk index may still keep
        them beside itself, which callers that know the index path check.
        """
        return self.embeddings_path is not None or self.embedding_store == "postgres"

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
