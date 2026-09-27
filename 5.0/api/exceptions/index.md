# Exceptions

All exceptions are importable from the `markdown_vault_mcp.exceptions` module.

```
from markdown_vault_mcp.exceptions import DocumentNotFoundError, ReadOnlyError
```

Most exceptions inherit from `MarkdownMCPError`, so callers can catch the base class to handle any library error. The one exception is `ConfigurationError`, which is re-exported from `fastmcp-pvl-core` and is not a `MarkdownMCPError` subclass (see [Configuration Errors](#configuration-errors)). Startup config failures are meant to fail hard rather than be caught by a library-error handler.

## Base Exception

## `MarkdownMCPError`

Bases: `Exception`

Base exception for all markdown-vault-mcp errors.

## Request Errors

`InvalidRequestError` marks a request the caller must change, such as a path outside the vault or a heading the document does not have. It is also a `ValueError`, so an `except ValueError` handler still catches it. `DocumentNotFoundError` is its subclass.

## `InvalidRequestError`

Bases: `MarkdownMCPError`, `ValueError`

Raised when the caller's request cannot be served as sent.

The caller must send a different request: an argument out of range, a path outside the vault, a wrong file type, a heading or document that does not exist. It is also a `ValueError`, so an `except ValueError` handler written before this type existed still catches it (#1608).

## `SummarizeTimeoutError`

Bases: `InvalidRequestError`, `RuntimeError`

Raised when a summarization request outruns its per-request budget.

The caller can fit under the budget by asking for less (fewer paths, a smaller `max_notes`, a narrower `focus`, `per_note` mode), so it is an :class:`InvalidRequestError`. It is also a `RuntimeError`, the type summarization raised for a timeout before (#937), so a handler written against that contract still catches it (#1608).

## `NoteTooLargeError`

Bases: `InvalidRequestError`

Raised when `read()` of a whole note is over the server's read limit.

A read at a git revision applies the same cap but raises a plain :class:`InvalidRequestError`.

The caller can read the note one section at a time instead, so it is an :class:`InvalidRequestError`. Its own type lets a caller that skips such notes, such as `summarize`, say why (#1637).

## Document Errors

## `DocumentNotFoundError`

Bases: `InvalidRequestError`

Raised when the requested document path does not exist on disk.

A caller's request naming a document that is not there, so an :class:`InvalidRequestError`, and through it a `ValueError` (#1608). The constructors below give each kind of target the same message: a `"<Kind> not found: '<path>'"` prefix callers match on, then the tool that finds the right path (#1639).

### `note(path)`

A note that is not there.

### `attachment(path)`

An attachment that is not there.

### `folder(path)`

A folder that is not there.

## `DocumentUnreadableError(path, reason)`

Bases: `MarkdownMCPError`

Raised when a document exists but the server cannot read or parse it.

The file is there, so this is not "not found": the bytes are not valid UTF-8, the frontmatter block does not parse, or the file system refused a stat or a read. A revision read raises it too, when the note's content at that revision is not valid UTF-8 or is a Git LFS pointer. Not a `ValueError`. The cause is chained as `__cause__` (#1608).

Attributes:

| Name   | Type | Description                                     |
| ------ | ---- | ----------------------------------------------- |
| `path` |      | The vault-relative path that could not be read. |

## `DocumentExistsError`

Bases: `MarkdownMCPError`

Raised when the target path already exists (e.g. rename destination).

## `EditConflictError(message, *, closest_match_line=None, first_diff_char=None, expected_snippet=None, found_snippet=None)`

Bases: `MarkdownMCPError`

Raised when `old_text` is not found or appears more than once in a document.

Attributes:

| Name                 | Type | Description                                              |
| -------------------- | ---- | -------------------------------------------------------- |
| `closest_match_line` |      | 1-based file line where old_text first diverges.         |
| `first_diff_char`    |      | Character offset of the divergence within that line.     |
| `expected_snippet`   |      | The divergent old_text line (truncated).                 |
| `found_snippet`      |      | The corresponding file line (truncated, empty past EOF). |

## `ConcurrentModificationError(path, expected, actual)`

Bases: `MarkdownMCPError`

Raised when an `if_match` etag does not match the current file state.

Attributes:

| Name       | Type | Description                                                   |
| ---------- | ---- | ------------------------------------------------------------- |
| `path`     |      | Relative path of the document that was modified concurrently. |
| `expected` |      | The etag value the caller provided.                           |
| `actual`   |      | The etag value found on disk.                                 |

## Access Errors

## `ReadOnlyError`

Bases: `MarkdownMCPError`

Raised when a write operation is attempted on a read-only vault.

## Configuration Errors

`markdown_vault_mcp.exceptions.ConfigurationError` is re-exported from [`fastmcp-pvl-core`](https://github.com/pvliesdonk/fastmcp-pvl-core), the shared base library across the `*-mcp` server series, so the whole ecosystem raises one canonical config error. It is raised for invalid or out-of-range configuration at startup (such as a non-numeric env var, a value outside its documented range, or a missing required variable). Unlike the other exceptions on this page it is not a subclass of `MarkdownMCPError`.
