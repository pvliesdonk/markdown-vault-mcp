"""Exception types for markdown-vault-mcp."""


class MarkdownMCPError(Exception):
    """Base exception for all markdown-vault-mcp errors."""


class DocumentNotFoundError(MarkdownMCPError):
    """Raised when the requested document path does not exist on disk."""


class ReadOnlyError(MarkdownMCPError):
    """Raised when a write operation is attempted on a read-only collection."""


class EditConflictError(MarkdownMCPError):
    """Raised when ``old_text`` is not found or appears more than once in a document.

    Attributes:
        closest_match_line: 1-based line number of the nearest fuzzy match, if any.
        first_diff_char: Character offset of the first difference from the nearest match.
        expected_snippet: The ``old_text`` that was searched for (truncated).
        found_snippet: The nearest match found in the document (truncated).
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
