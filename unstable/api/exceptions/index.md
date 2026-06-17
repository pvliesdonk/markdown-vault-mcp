# Exceptions

All exceptions are importable from the top-level `markdown_vault_mcp` package.

```
from markdown_vault_mcp import DocumentNotFoundError, ReadOnlyError
```

Most exceptions inherit from `MarkdownMCPError`, so callers can catch the base class to handle any library error. The one exception is `ConfigurationError`, which is re-exported from `fastmcp-pvl-core` and is not a `MarkdownMCPError` subclass (see [Configuration Errors](#configuration-errors)). Startup config failures are meant to fail hard rather than be caught by a library-error handler.

## Base Exception

## `MarkdownMCPError`

Bases: `Exception`

Base exception for all markdown-vault-mcp errors.

## Document Errors

## `DocumentNotFoundError`

Bases: `MarkdownMCPError`

Raised when the requested document path does not exist on disk.

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
