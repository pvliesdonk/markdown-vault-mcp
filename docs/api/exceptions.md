# Exceptions

All exceptions are importable from the `markdown_vault_mcp.exceptions` module.

```python
from markdown_vault_mcp.exceptions import DocumentNotFoundError, ReadOnlyError
```

Most exceptions inherit from `MarkdownMCPError`, so callers can catch the base class to handle any library error. The one exception is `ConfigurationError`, which is re-exported from `fastmcp-pvl-core` and is not a `MarkdownMCPError` subclass (see [Configuration Errors](#configuration-errors)). Startup config failures are meant to fail hard rather than be caught by a library-error handler.

## Base Exception

<!-- vale off -->
::: markdown_vault_mcp.exceptions.MarkdownMCPError
<!-- vale on -->

## Document Errors

<!-- vale off -->
::: markdown_vault_mcp.exceptions.DocumentNotFoundError

::: markdown_vault_mcp.exceptions.DocumentExistsError

::: markdown_vault_mcp.exceptions.EditConflictError

::: markdown_vault_mcp.exceptions.ConcurrentModificationError
<!-- vale on -->

## Access Errors

<!-- vale off -->
::: markdown_vault_mcp.exceptions.ReadOnlyError
<!-- vale on -->

## Configuration Errors

`markdown_vault_mcp.exceptions.ConfigurationError` is re-exported from
[`fastmcp-pvl-core`](https://github.com/pvliesdonk/fastmcp-pvl-core), the shared
base library across the `*-mcp` server series, so the whole ecosystem raises one
canonical config error. It is raised for invalid or out-of-range configuration at
startup (such as a non-numeric env var, a value outside its documented range, or a
missing required variable). Unlike the other exceptions on this page it is not
a subclass of `MarkdownMCPError`.
