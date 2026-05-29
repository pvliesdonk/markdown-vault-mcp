"""Exception types for markdown-vault-mcp."""

from __future__ import annotations

from typing import Literal


class MarkdownMCPError(Exception):
    """Base exception for all markdown-vault-mcp errors."""


class DocumentNotFoundError(MarkdownMCPError):
    """Raised when the requested document path does not exist on disk."""


class ReadOnlyError(MarkdownMCPError):
    """Raised when a write operation is attempted on a read-only collection."""


class EditConflictError(MarkdownMCPError):
    """Raised when ``old_text`` is not found or appears more than once in a document.

    Attributes:
        closest_match_line: 1-based file line where ``old_text`` first diverges.
        first_diff_char: Character offset of the divergence within that line.
        expected_snippet: The divergent ``old_text`` line (truncated).
        found_snippet: The corresponding file line (truncated, empty past EOF).
    """

    def __init__(
        self,
        message: str,
        *,
        closest_match_line: int | None = None,
        first_diff_char: int | None = None,
        expected_snippet: str | None = None,
        found_snippet: str | None = None,
    ) -> None:
        super().__init__(message)
        self.closest_match_line = closest_match_line
        self.first_diff_char = first_diff_char
        self.expected_snippet = expected_snippet
        self.found_snippet = found_snippet


class DocumentExistsError(MarkdownMCPError):
    """Raised when the target path already exists (e.g. rename destination)."""


class ConcurrentModificationError(MarkdownMCPError):
    """Raised when an ``if_match`` etag does not match the current file state.

    Attributes:
        path: Relative path of the document that was modified concurrently.
        expected: The etag value the caller provided.
        actual: The etag value found on disk.
    """

    def __init__(self, path: str, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Concurrent modification on {path}: "
            f"expected etag {expected!r}, actual {actual!r}"
        )


class ConfigurationError(MarkdownMCPError):
    """Raised for invalid or unsupported configuration at startup."""


IndexNotReadyReason = Literal["never_built", "timeout", "broken"]


class IndexNotReadyError(MarkdownMCPError):
    """Raised when a method requires a built FTS index that is not currently usable.

    Carries a structured ``reason`` discriminator so callers (notably the
    MCP layer and operators reading status output) can tell apart the
    three not-ready cases without parsing exception messages:

    - ``"never_built"``: ``Collection.build_index`` has not produced a
      usable FTS DB yet (cold collection, in-flight first build, or a
      previously-broken DB never recovered).
    - ``"timeout"``: caller waited via
      :meth:`Collection.wait_for_index_ready` and the bounded timeout
      elapsed before the background build signaled completion.
    - ``"broken"``: a ``sqlite3.OperationalError`` surfaced from the FTS
      layer during the operation. Set only by the MCP-layer
      :func:`needs_index_ready` decorator boundary — never by library
      code.

    The previous PR #529 companion class ``IndexBuildFailedError`` was
    deleted in issue #533: captured background-build errors are now
    diagnostic events (surfaced via ``Collection.get_index_status``) and
    no longer raised from the read path.
    """

    reason: IndexNotReadyReason

    def __init__(self, message: str, *, reason: IndexNotReadyReason) -> None:
        super().__init__(message)
        self.reason = reason
