"""Exception types for markdown-vault-mcp."""

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


IndexUnavailableReason = Literal[
    "never_built", "build_failed", "timeout", "broken", "busy"
]
"""Discriminator for IndexUnavailableError's cause.

- ``"never_built"`` — no build has been scheduled (cold collection on a
  fresh process; ``build_index()`` / ``build_index_async()`` never called).
- ``"build_failed"`` — a build was scheduled, ran, and failed; the captured
  error is available via ``get_index_status()``'s ``error`` field (#586).
- ``"timeout"`` — caller waited via ``wait_until_queryable()`` and the
  bounded timeout elapsed before the build event was set.
- ``"broken"`` — a SQLite ``OperationalError`` surfaced from a
  bucket-3/4 handler call with an errorname OUTSIDE the busy set
  (e.g., ``SQLITE_CORRUPT``, ``SQLITE_NOTADB``, ``SQLITE_CANTOPEN``,
  ``SQLITE_SCHEMA``, ``SQLITE_IOERR``, ``SQLITE_FULL``, generic
  ``SQLITE_ERROR``). The chained ``__cause__`` carries the original
  exception with full traceback. Operator action: inspect the cause
  and likely rebuild the index or free disk space, depending on the
  underlying errorname.
- ``"busy"`` — a SQLite ``OperationalError`` with errorname in
  ``{SQLITE_BUSY, SQLITE_LOCKED}`` — lock contention from concurrent
  connections. The chained ``__cause__`` carries the original
  exception. A retry after a short backoff may succeed.
"""


class IndexUnavailableError(MarkdownMCPError):
    """Raised when the FTS index is not in a state to serve a query.

    Attributes:
        reason: One of ``"never_built"``, ``"build_failed"``,
            ``"timeout"``, ``"broken"``, ``"busy"`` — disambiguates which
            of the operational situations below fired. See the
            :data:`IndexUnavailableReason` Literal for definitions.

    Covers the following operational situations:

    - **Never built** (``reason="never_built"``). No build has been
      scheduled — the Collection has never had ``build_index()`` /
      ``build_index_async()`` called (cold collection on a fresh process).
    - **Build did not complete successfully** (``reason="build_failed"``).
      A previous build was scheduled, ran, raised, and was not retried
      (``_index_built`` remained False; the captured error is available
      via :meth:`IndexFacet.get_index_status`'s ``error`` field) (#586).
    - **Timeout** (``reason="timeout"``). A caller waited via
      :meth:`IndexFacet.wait_until_queryable` and the bounded timeout
      elapsed before the background build signaled completion.
    - **SQLite operational error — broken** (``reason="broken"``). A
      bucket-3/4 MCP handler call raised ``sqlite3.OperationalError``
      with an errorname outside the busy whitelist (e.g.,
      ``SQLITE_CORRUPT``, ``SQLITE_NOTADB``, ``SQLITE_IOERR``). The
      chained ``__cause__`` carries the original; operators should
      inspect and likely rebuild the index from scratch.
    - **SQLite operational error — busy** (``reason="busy"``). A
      bucket-3/4 MCP handler call raised ``sqlite3.OperationalError``
      with errorname in ``{SQLITE_BUSY, SQLITE_LOCKED}`` — lock
      contention from concurrent connections. A retry after a short
      backoff may succeed.

    A captured background-build error is NOT a separate exception
    class: it is diagnostic state surfaced via
    :meth:`IndexFacet.get_index_status`'s ``error`` field.
    """

    def __init__(self, message: str, *, reason: IndexUnavailableReason) -> None:
        super().__init__(message)
        self.reason: IndexUnavailableReason = reason
