# Exceptions

All exceptions are importable from the top-level `markdown_vault_mcp` package.

```
from markdown_vault_mcp import DocumentNotFoundError, ReadOnlyError
```

All exceptions inherit from `MarkdownMCPError`, so callers can catch the base class to handle any library error.

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

| Name                 | Type | Description                                                      |
| -------------------- | ---- | ---------------------------------------------------------------- |
| `closest_match_line` |      | 1-based line number of the nearest fuzzy match, if any.          |
| `first_diff_char`    |      | Character offset of the first difference from the nearest match. |
| `expected_snippet`   |      | The old_text that was searched for (truncated).                  |
| `found_snippet`      |      | The nearest match found in the document (truncated).             |

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

Raised when a write operation is attempted on a read-only collection.

## Configuration Errors

## `ConfigurationError`

Bases: `MarkdownMCPError`

Raised for invalid or unsupported configuration at startup.
