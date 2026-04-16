# Types

All data types returned by the `Collection` API are importable from the top-level `markdown_vault_mcp` package.

```python
from markdown_vault_mcp import NoteContent, SearchResult, NoteContext
```

## Document Types

::: markdown_vault_mcp.types.NoteContent

::: markdown_vault_mcp.types.NoteInfo

::: markdown_vault_mcp.types.ParsedNote

::: markdown_vault_mcp.types.Chunk

## Search & Link Types

::: markdown_vault_mcp.types.SearchResult

::: markdown_vault_mcp.types.FTSResult

::: markdown_vault_mcp.types.BacklinkInfo

::: markdown_vault_mcp.types.OutlinkInfo

::: markdown_vault_mcp.types.BrokenLinkInfo

::: markdown_vault_mcp.types.LinkInfo

::: markdown_vault_mcp.types.SimilarItem

::: markdown_vault_mcp.types.NoteContext

::: markdown_vault_mcp.types.MostLinkedNote

## Operation Results

::: markdown_vault_mcp.types.WriteResult

::: markdown_vault_mcp.types.EditResult

::: markdown_vault_mcp.types.DeleteResult

::: markdown_vault_mcp.types.RenameResult

::: markdown_vault_mcp.types.IndexStats

::: markdown_vault_mcp.types.ReindexResult

::: markdown_vault_mcp.types.CollectionStats

::: markdown_vault_mcp.types.ChangeSet

## Attachment Types

::: markdown_vault_mcp.types.AttachmentContent

::: markdown_vault_mcp.types.AttachmentInfo

## Git Types

::: markdown_vault_mcp.types.HistoryEntry

::: markdown_vault_mcp.types.CommitDiff

## Callbacks

**`WriteCallback`**

Type alias for the `on_write` callback passed to `Collection`. Called after each successful write operation (write, edit, delete, rename).

```python
WriteCallback = Callable[[Path, str, Literal["write", "edit", "delete", "rename"]], None]
```

Arguments received by the callback:

| Argument | Type | Description |
|----------|------|-------------|
| `path` | `Path` | Absolute path of the modified file |
| `content` | `str` | New file content (empty string for binary attachments and deletes) |
| `op` | `Literal[...]` | Operation type: `"write"`, `"edit"`, `"delete"`, or `"rename"` |
