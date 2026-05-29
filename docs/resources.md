# MCP Resources

MCP resources expose vault metadata that clients can read directly without invoking tools. Most resources return `application/json`; `ui://vault/app.html` is an exception — it returns a self-contained HTML SPA for MCP Apps clients.

## Quick Reference

| URI | Description |
|-----|-------------|
| [`config://vault`](#configvault) | Current collection configuration |
| [`stats://vault`](#statsvault) | Collection statistics |
| [`tags://vault`](#tagsvault) | All tags grouped by indexed field |
| [`tags://vault/{field}`](#tagsvaultfield) | Tags for a specific field |
| [`folders://vault`](#foldersvault) | All folder paths |
| [`toc://vault/{path}`](#tocvaultpath) | Table of contents for a document |
| [`similar://vault/{path}`](#similarvaultpath) | Semantically similar notes for a document |
| [`recent://vault`](#recentvault) | Most recently modified notes |
| [`ui://vault/app.html`](#uivaultapphtml) | Interactive vault explorer SPA (MCP Apps) |

---

## `config://vault`

Current collection configuration and runtime state.

**Response:**

```json
{
  "source_dir": "/data/vault",
  "read_only": true,
  "indexed_fields": ["tags", "cluster"],
  "required_fields": [],
  "exclude_patterns": [".obsidian/**", ".trash/**"],
  "semantic_search_available": true,
  "attachment_extensions": ["pdf", "png", "jpg"]
}
```

## `stats://vault`

Collection statistics — document count, chunk count, and capabilities.

**Response:**

```json
{
  "document_count": 42,
  "chunk_count": 156,
  "folder_count": 5,
  "semantic_search_available": true,
  "indexed_frontmatter_fields": ["tags", "cluster"],
  "attachment_extensions": ["pdf", "png", "jpg"]
}
```

## `tags://vault`

All frontmatter tag values grouped by indexed field.

**Response:**

```json
{
  "tags": ["craft", "pacing", "worldbuilding"],
  "cluster": ["fiction", "non-fiction"]
}
```

## `tags://vault/{field}`

Tag values for a specific indexed frontmatter field. This is a URI template — replace `{field}` with the field name.

**Example:** `tags://vault/tags`

**Response:**

```json
["craft", "pacing", "worldbuilding"]
```

## `folders://vault`

All folder paths in the vault.

**Response:**

```json
["", "Journal", "Projects", "Research"]
```

The empty string `""` represents the root folder (top-level documents).

## `toc://vault/{path}`

Table of contents (heading outline) for a specific document. This is a URI template — replace `{path}` with the document's relative path.

!!! note "Cold-start blocking"
    Calls during a cold-start background FTS build block via the tool-layer `needs_index_ready` decorator and may surface `IndexBuildFailedError` if the background build raised. Poll `get_index_status` to observe build state without blocking.

**Example:** `toc://vault/Journal/note.md`

**Response:**

```json
[
  {"level": 1, "title": "My Note"},
  {"level": 2, "title": "Introduction"},
  {"level": 2, "title": "Main Points"},
  {"level": 3, "title": "First Point"},
  {"level": 2, "title": "Conclusion"}
]
```

The TOC prepends a synthetic H1 from the document title and deduplicates if the first real heading matches the title.

## `similar://vault/{path}`

Top 10 semantically similar notes for a document. Requires embeddings to be built. This is a URI template — replace `{path}` with the document's relative path.

!!! note "Cold-start blocking"
    `vault_similar` waits for BOTH the FTS and embeddings background builds when an `embedding_provider` is configured. Worst-case wait is `2 × MARKDOWN_VAULT_MCP_READY_TIMEOUT_S` (60s per phase, 120s total with defaults). When no provider is configured, the embeddings wait is a no-op. Poll `get_index_status` to observe progress without blocking. Raises `IndexBuildFailedError` if either background build failed.

Results are **grouped per file** — each file appears at most once, with up to `chunks_per_file` (server default `2`) best-matching sections in a `sections` array. Each entry is a `GroupedResult` dict with `path`, `title`, `folder`, `score` (max section score), `search_type` (`"semantic"`), `frontmatter`, and `sections` — a list of `{heading, content, score}` dicts sorted by score then document order.

**Example:** `similar://vault/Journal/note.md`

**Response:**

```json
[
  {
    "path": "Journal/related-note.md",
    "title": "Related Note",
    "folder": "Journal",
    "score": 0.87,
    "search_type": "semantic",
    "frontmatter": {},
    "sections": [
      {"heading": "Overview", "content": "...", "score": 0.87},
      {"heading": "Details", "content": "...", "score": 0.81}
    ]
  },
  {
    "path": "Research/topic.md",
    "title": "Topic Overview",
    "folder": "Research",
    "score": 0.82,
    "search_type": "semantic",
    "frontmatter": {},
    "sections": [
      {"heading": null, "content": "...", "score": 0.82}
    ]
  }
]
```

## `recent://vault`

The 20 most recently modified notes. Each entry is a full `NoteInfo` object with an added `modified_at_iso` field. The original `modified_at` is preserved as a Unix timestamp float.

**Response:**

```json
[
  {"path": "Journal/2024-01-15.md", "title": "Daily Note", "folder": "Journal", "frontmatter": {}, "kind": "note", "modified_at": 1705314600.0, "modified_at_iso": "2024-01-15T10:30:00+00:00"},
  {"path": "Projects/roadmap.md", "title": "Roadmap", "folder": "Projects", "frontmatter": {}, "kind": "note", "modified_at": 1705250700.0, "modified_at_iso": "2024-01-14T16:45:00+00:00"}
]
```

## `ui://vault/app.html`

Interactive vault explorer delivered as a single self-contained HTML resource. This is an [MCP Apps](https://modelcontextprotocol.io/specification/2025-06-18/server/apps) resource — clients that support the MCP Apps protocol render it as an interactive iframe. See the [MCP Apps guide](guides/mcp-apps.md) for details on the four views (Context Card, Graph Explorer, Vault Browser, Note Preview).
