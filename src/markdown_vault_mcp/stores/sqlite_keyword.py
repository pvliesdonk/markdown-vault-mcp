"""The SQLite keyword backend: opens ``FTSIndex`` with typed outcomes (#1766).

Reachable outcomes: *unavailable* (the file cannot be opened: a parent that
is not a directory, a refused permission, a read-only file on a writable
open), *corrupt* (the bytes are not a SQLite database) and *unsupported
schema* (a ``schema_version`` above what this code reads, raised by
``FTSIndex`` itself). *Not configured* cannot happen, because no location
means an in-memory index, and *incompatible identity* cannot either, because
a keyword store has no identity.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from markdown_vault_mcp.exceptions import (
    StoreCorruptError,
    StoreOpenError,
    StoreUnavailableError,
)
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.stores.registry import register_backend

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.types import StoreFamily

_CORRUPT_ERRORNAMES = frozenset({"SQLITE_NOTADB", "SQLITE_CORRUPT"})


class SqliteKeywordBackend:
    """Open the keyword index SQLite serves, classifying what goes wrong.

    The backend carries the index's configuration; :meth:`open` takes the
    location and whether this process owns the file. ``None`` or
    ``":memory:"`` opens an in-memory index, which is always this process's
    own and therefore writable whatever *owns_files* says (#1758).

    Args:
        indexed_frontmatter_fields: Frontmatter keys indexed for filtering.
        searchable_frontmatter_fields: Frontmatter keys folded into search.
        fts_weights: Per-column BM25 weights.
    """

    family: StoreFamily = "keyword"
    name = "sqlite"

    def __init__(
        self,
        *,
        indexed_frontmatter_fields: list[str] | None = None,
        searchable_frontmatter_fields: list[str] | None = None,
        fts_weights: dict[str, float] | None = None,
    ) -> None:
        self._indexed_frontmatter_fields = indexed_frontmatter_fields
        self._searchable_frontmatter_fields = searchable_frontmatter_fields
        self._fts_weights = fts_weights

    def open(
        self,
        location: Path | str | None,
        identity: object | None = None,
        *,
        owns_files: bool = True,
    ) -> FTSIndex:
        """Open the index at *location*.

        Args:
            location: The SQLite file, or ``None`` / ``":memory:"`` for an
                in-memory index.
            identity: Must be ``None``; a keyword store has no identity.
            owns_files: ``False`` opens a file read-only (#1758) and is
                ignored for an in-memory index.

        Returns:
            The open :class:`~markdown_vault_mcp.fts_index.FTSIndex`.

        Raises:
            ValueError: If *identity* is given.
            StoreUnavailableError: If the file cannot be opened or written.
            StoreCorruptError: If the file is not a SQLite database.
            StoreUnsupportedSchemaError: If its ``schema_version`` is newer.
        """
        if identity is not None:
            raise ValueError("A keyword store has no identity to check.")
        db_path: Path | str = ":memory:" if location is None else location
        is_memory = str(db_path) == ":memory:"
        try:
            return FTSIndex(
                db_path=db_path,
                indexed_frontmatter_fields=self._indexed_frontmatter_fields,
                searchable_frontmatter_fields=self._searchable_frontmatter_fields,
                fts_weights=self._fts_weights,
                read_only=not owns_files and not is_memory,
            )
        except sqlite3.DatabaseError as exc:
            raise self._classify(exc, str(db_path)) from exc

    def _classify(self, exc: sqlite3.DatabaseError, location: str) -> StoreOpenError:
        """Map a SQLite error at open to the contract's outcome."""
        errorname = getattr(exc, "sqlite_errorname", None)
        corrupt = errorname in _CORRUPT_ERRORNAMES or "not a database" in str(exc)
        if corrupt:
            return StoreCorruptError(
                f"Index at {location} is not a SQLite database: {exc}",
                backend=self.name,
                location=location,
            )
        return StoreUnavailableError(
            f"Index at {location} cannot be opened: {exc}",
            backend=self.name,
            location=location,
        )


register_backend("keyword", "sqlite", SqliteKeywordBackend)
