"""Generic markdown vault with FTS5 + semantic search.

Public attributes are resolved lazily (PEP 562) instead of eagerly importing
every submodule here. An eager package root pulled the full dependency tree
(``config`` -> ``fastmcp_pvl_core`` -> ``beartype``, plus
``python-frontmatter`` -> PyYAML) into *any* import of this package -- which
broke ``pytest --cov=markdown_vault_mcp.<submodule>``: coverage.py resolves
dotted source packages with :func:`importlib.util.find_spec` inside a
sys.modules-restoring context (``coverage.misc.sys_modules_saved``), so the
heavy dependencies were imported and then purged from ``sys.modules`` while
their process-global side effects (beartype's claw ``sys.path_hooks`` entry,
PyYAML's cached single-phase-init C extension) survived, corrupting the
interpreter for every subsequent import. Keeping this module import-light
avoids that interaction entirely (and speeds up CLI startup). See issue #665.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import (
        ConcurrentModificationError,
        ConfigurationError,
        DocumentExistsError,
        DocumentNotFoundError,
        EditConflictError,
        EmbeddingsNotConfiguredError,
        IndexUnavailableError,
        IndexUnavailableReason,
        MarkdownMCPError,
        ReadOnlyError,
    )
    from markdown_vault_mcp.git import GitWriteStrategy, git_write_strategy
    from markdown_vault_mcp.types import (
        AttachmentContent,
        AttachmentInfo,
        BacklinkInfo,
        BrokenLinkInfo,
        ChangeSet,
        Chunk,
        CommitDiff,
        DeleteResult,
        EditResult,
        FTSResult,
        GroupedResult,
        HistoryEntry,
        IndexStats,
        LinkInfo,
        MostLinkedNote,
        NoteContent,
        NoteContext,
        NoteInfo,
        OutlinkInfo,
        ParsedNote,
        ReindexResult,
        RenameResult,
        SearchResult,
        SectionHit,
        SubtreeNote,
        SubtreeToc,
        TocEntry,
        VaultStats,
        WriteCallback,
        WriteResult,
    )
    from markdown_vault_mcp.vault import Vault

# Public attribute name -> defining submodule. Resolved on first access by
# __getattr__ below. Must stay in sync with __all__ (pinned by a test).
_EXPORTS: dict[str, str] = {
    "ProjectConfig": "markdown_vault_mcp.config",
    "ConcurrentModificationError": "markdown_vault_mcp.exceptions",
    "ConfigurationError": "markdown_vault_mcp.exceptions",
    "DocumentExistsError": "markdown_vault_mcp.exceptions",
    "DocumentNotFoundError": "markdown_vault_mcp.exceptions",
    "EditConflictError": "markdown_vault_mcp.exceptions",
    "EmbeddingsNotConfiguredError": "markdown_vault_mcp.exceptions",
    "IndexUnavailableError": "markdown_vault_mcp.exceptions",
    "IndexUnavailableReason": "markdown_vault_mcp.exceptions",
    "MarkdownMCPError": "markdown_vault_mcp.exceptions",
    "ReadOnlyError": "markdown_vault_mcp.exceptions",
    "GitWriteStrategy": "markdown_vault_mcp.git",
    "git_write_strategy": "markdown_vault_mcp.git",
    "AttachmentContent": "markdown_vault_mcp.types",
    "AttachmentInfo": "markdown_vault_mcp.types",
    "BacklinkInfo": "markdown_vault_mcp.types",
    "BrokenLinkInfo": "markdown_vault_mcp.types",
    "ChangeSet": "markdown_vault_mcp.types",
    "Chunk": "markdown_vault_mcp.types",
    "CommitDiff": "markdown_vault_mcp.types",
    "DeleteResult": "markdown_vault_mcp.types",
    "EditResult": "markdown_vault_mcp.types",
    "FTSResult": "markdown_vault_mcp.types",
    "GroupedResult": "markdown_vault_mcp.types",
    "HistoryEntry": "markdown_vault_mcp.types",
    "IndexStats": "markdown_vault_mcp.types",
    "LinkInfo": "markdown_vault_mcp.types",
    "MostLinkedNote": "markdown_vault_mcp.types",
    "NoteContent": "markdown_vault_mcp.types",
    "NoteContext": "markdown_vault_mcp.types",
    "NoteInfo": "markdown_vault_mcp.types",
    "OutlinkInfo": "markdown_vault_mcp.types",
    "ParsedNote": "markdown_vault_mcp.types",
    "ReindexResult": "markdown_vault_mcp.types",
    "RenameResult": "markdown_vault_mcp.types",
    "SearchResult": "markdown_vault_mcp.types",
    "SectionHit": "markdown_vault_mcp.types",
    "SubtreeNote": "markdown_vault_mcp.types",
    "SubtreeToc": "markdown_vault_mcp.types",
    "TocEntry": "markdown_vault_mcp.types",
    "VaultStats": "markdown_vault_mcp.types",
    "WriteCallback": "markdown_vault_mcp.types",
    "WriteResult": "markdown_vault_mcp.types",
    "Vault": "markdown_vault_mcp.vault",
}

__all__ = [
    "AttachmentContent",
    "AttachmentInfo",
    "BacklinkInfo",
    "BrokenLinkInfo",
    "ChangeSet",
    "Chunk",
    "CommitDiff",
    "ConcurrentModificationError",
    "ConfigurationError",
    "DeleteResult",
    "DocumentExistsError",
    "DocumentNotFoundError",
    "EditConflictError",
    "EditResult",
    "EmbeddingsNotConfiguredError",
    "FTSResult",
    "GitWriteStrategy",
    "GroupedResult",
    "HistoryEntry",
    "IndexStats",
    "IndexUnavailableError",
    "IndexUnavailableReason",
    "LinkInfo",
    "MarkdownMCPError",
    "MostLinkedNote",
    "NoteContent",
    "NoteContext",
    "NoteInfo",
    "OutlinkInfo",
    "ParsedNote",
    "ProjectConfig",
    "ReadOnlyError",
    "ReindexResult",
    "RenameResult",
    "SearchResult",
    "SectionHit",
    "SubtreeNote",
    "SubtreeToc",
    "TocEntry",
    "Vault",
    "VaultStats",
    "WriteCallback",
    "WriteResult",
    "git_write_strategy",
]


def __getattr__(name: str) -> Any:
    """Lazily resolve a public attribute from its defining submodule.

    Args:
        name: Attribute name being looked up on the package.

    Returns:
        The resolved attribute.

    Raises:
        AttributeError: If ``name`` is not a public attribute of this package.
    """
    try:
        module_name = _EXPORTS[name]
    except KeyError:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from None
    value = getattr(importlib.import_module(module_name), name)
    # Cache so subsequent lookups hit the module __dict__ directly instead
    # of re-entering __getattr__.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazily resolved public attributes to :func:`dir`.

    Returns:
        Sorted list of the package's attribute names, including lazy exports.
    """
    return sorted(set(__all__) | set(globals()))
