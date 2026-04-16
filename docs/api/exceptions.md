# Exceptions

All exceptions are importable from the top-level `markdown_vault_mcp` package.

```python
from markdown_vault_mcp import DocumentNotFoundError, ReadOnlyError
```

All exceptions inherit from `MarkdownMCPError`, so callers can catch the base class to handle any library error.

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

::: markdown_vault_mcp.exceptions.ConfigurationError
