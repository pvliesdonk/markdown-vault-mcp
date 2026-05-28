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


class IndexNotReadyError(MarkdownMCPError):
    """Raised when a method requires a built FTS index and none exists.

    Bucket-3 relational / FTS-backed queries (``get_backlinks``,
    ``get_outlinks``, ``get_similar``, ``get_context``,
    ``get_connection_path``, ``get_toc``) and bucket-4 coordinators
    (``reindex``, ``build_embeddings``) cannot produce correct results
    against an empty / never-built index. They raise this rather than
    silently return wrong answers.

    Callers must call :meth:`Collection.build_index` before these
    methods. Once a background indexer lands (issue #513), the
    :meth:`Collection.wait_for_index_ready` primitive will block on a
    completion event instead of raising.
    """


class IndexBuildFailedError(MarkdownMCPError):
    """Raised when a background index build failed with an exception.

    The original exception is available via ``__cause__``.

    Distinguishes "build never finished" (:exc:`IndexNotReadyError`)
    from "build finished with an error" — both surface through
    :meth:`Collection.wait_for_index_ready` and bucket-3/4 calls but
    signal different operator action: not-ready means wait or check
    status; failed means inspect logs and decide whether to retry via
    CLI ``markdown-vault-mcp index``.
    """
