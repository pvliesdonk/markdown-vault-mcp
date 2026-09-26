"""Exception types for markdown-vault-mcp."""

from __future__ import annotations

from typing import Literal

# ConfigurationError is owned by fastmcp-pvl-core — the shared base guaranteed
# across the whole *-mcp server series — and re-exported here as the project's
# one canonical config error (#638). env_int/env_float(strict=...) raise it,
# and config validation + git-remote checks raise the same catchable type, so
# `markdown_vault_mcp.exceptions.ConfigurationError` and the pvl-core class are
# identical.
from fastmcp_pvl_core import ConfigurationError as ConfigurationError


class MarkdownMCPError(Exception):
    """Base exception for all markdown-vault-mcp errors."""


class InvalidRequestError(MarkdownMCPError, ValueError):
    """Raised when the caller's request cannot be served as sent.

    The caller must send a different request: an argument out of range, a
    path outside the vault, a wrong file type, a heading or document that does
    not exist. It is also a ``ValueError``, so an ``except ValueError`` handler
    written before this type existed still catches it (#1608).
    """


FIND_NOTE = "Find the path with search or list_documents."
"""The next step a note refusal gives the model (#1639)."""


class DocumentNotFoundError(InvalidRequestError):
    """Raised when the requested document path does not exist on disk.

    A caller's request naming a document that is not there, so an
    :class:`InvalidRequestError`, and through it a ``ValueError`` (#1608).
    The constructors below give each kind of target the same message: a
    ``"<Kind> not found: <path>"`` prefix callers match on, then the tool
    that finds the right path (#1639).
    """

    @classmethod
    def note(cls, path: str) -> DocumentNotFoundError:
        """A note that is not there."""
        return cls(f"Document not found: {path!r}. {FIND_NOTE}")

    @classmethod
    def attachment(cls, path: str) -> DocumentNotFoundError:
        """An attachment that is not there."""
        return cls(
            f"Attachment not found: {path!r}. Find the path with "
            "list_documents(include_attachments=True)."
        )

    @classmethod
    def folder(cls, path: str) -> DocumentNotFoundError:
        """A folder that is not there."""
        return cls(f"Folder not found: {path!r}. Find the path with list_folders.")


class DocumentUnreadableError(MarkdownMCPError):
    """Raised when a document exists but the server cannot read or parse it.

    The file is there, so this is not "not found": the bytes are not valid
    UTF-8, the frontmatter block does not parse, or the file system refused a
    stat or a read. A revision read raises it too, when the note's content at
    that revision is not valid UTF-8 or is a Git LFS pointer. Not a
    ``ValueError``. The cause is chained as ``__cause__`` (#1608).

    Attributes:
        path: The vault-relative path that could not be read.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path} exists but cannot be read: {reason}")
        self.path = path


class ReadOnlyError(MarkdownMCPError):
    """Raised when a write operation is attempted on a read-only vault."""


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


class EmbeddingsNotConfiguredError(InvalidRequestError):
    """Raised when an embeddings operation runs without a provider/path configured.

    Embeddings are opt-in and off by default, so a vault without them is a
    choice the operator made, not a broken configuration: the caller must
    change the request (keyword search instead of semantic), hence an
    :class:`InvalidRequestError` (#1608).

    Still a :class:`ValueError` (through :class:`InvalidRequestError`), so
    callers that catch ``ValueError`` (the historical "embeddings not
    configured" contract) still catch it, while callers that want *only* this
    case, such as the ``index``/``reindex`` CLI commands, can narrow to it and
    let genuine internal ``ValueError``s from a corrupt vector index surface
    (#774).
    """


class SummarizeTimeoutError(InvalidRequestError, RuntimeError):
    """Raised when a summarization request outruns its per-request budget.

    The caller can fit under the budget by asking for less (fewer paths, a
    smaller ``max_notes``, a narrower ``focus``, ``per_note`` mode), so it is
    an :class:`InvalidRequestError`. It is also a ``RuntimeError``, the type
    summarization raised for a timeout before (#937), so a handler written
    against that contract still catches it (#1608).
    """


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


IndexUnavailableReason = Literal[
    "never_built", "build_failed", "timeout", "broken", "busy"
]
"""Discriminator for IndexUnavailableError's cause.

- ``"never_built"`` — no build has been scheduled (cold vault on a
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
      scheduled — the Vault has never had ``build_index()`` /
      ``build_index_async()`` called (cold vault on a fresh process).
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
