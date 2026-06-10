# Exceptions

All exceptions are importable from the top-level `markdown_vault_mcp` package.

```python
from markdown_vault_mcp import DocumentNotFoundError, ReadOnlyError
```

Most exceptions inherit from `MarkdownMCPError`, so callers can catch the base class to handle any library error. The one exception is `ConfigurationError`, which is re-exported from `fastmcp-pvl-core` and is **not** a `MarkdownMCPError` subclass (see [Configuration Errors](#configuration-errors)) — startup config failures are meant to fail hard rather than be caught by a library-error handler.

## Base Exception

::: markdown_vault_mcp.exceptions.MarkdownMCPError

## Document Errors

::: markdown_vault_mcp.exceptions.DocumentNotFoundError

::: markdown_vault_mcp.exceptions.DocumentExistsError

::: markdown_vault_mcp.exceptions.EditConflictError

::: markdown_vault_mcp.exceptions.ConcurrentModificationError

## Access Errors

::: markdown_vault_mcp.exceptions.ReadOnlyError

## Configuration Errors

`markdown_vault_mcp.exceptions.ConfigurationError` is re-exported from
[`fastmcp-pvl-core`](https://github.com/pvliesdonk/fastmcp-pvl-core) — the shared
base library across the `*-mcp` server series — so the whole ecosystem raises one
canonical config error. It is raised for invalid or out-of-range configuration at
startup (e.g. a non-numeric env var, a value outside its documented range, or a
missing required variable). Unlike the other exceptions on this page it is **not**
a subclass of `MarkdownMCPError`.
