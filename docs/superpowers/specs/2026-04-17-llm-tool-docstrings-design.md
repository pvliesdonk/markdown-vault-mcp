# LLM-Facing Tool Docstring Improvements Design

## Goal

Establish a consistent docstring schema for all 34 MCP tools (28 LLM-visible + 6 app-only), fix known correctness bugs, and ensure every tool has a Lucide SVG icon.

## Scope

Files touched:
- `src/markdown_vault_mcp/_server_tools.py` — 26 LLM-visible tools + `create_download_link`
- `src/markdown_vault_mcp/_server_apps.py` — `browse_vault`, `show_context` (LLM-visible) + 6 app-only `vault_*` tools
- `src/markdown_vault_mcp/_icons.py` — add 6 new icon keys
- `src/markdown_vault_mcp/static/icons/` — add `vault_graph_neighborhood.svg`, `vault_graph_hubs.svg`
- `tests/test_docstrings.py` — add icon coverage assertion

## Architecture

### Docstring Schema

Every `@mcp.tool` function docstring follows this template:

```
"""One-line imperative summary (present tense, no trailing period).

When-to-use paragraph: preconditions, which tool to call first, what to
expect. 2–4 lines max. Omit if the one-liner is self-explanatory.

Args:
    param: Type and semantics. Include valid values, defaults, units.
           Constraints on last line (e.g. "Case-sensitive.").

Returns:
    For dicts — bulleted field list:
    - field_name (type): Description. Units if applicable.
    For lists — "List of dicts, each with:" followed by bullet list.
    For scalars — one sentence describing the value.

Raises:
    SpecificExceptionClass: Condition that triggers this error.
"""
```

### Schema Rules (enforced by convention, verified in review)

**Path parameters** — uniform wording for every tool that accepts a `path`, `old_path`, `new_path`, `source`, or `target` argument:
> `Relative path to the document (e.g. "Journal/note.md"). Case-sensitive.`

**Score fields** — always document type and range:
- Keyword search (BM25): `score (float): BM25 relevance score; higher = better match.`
- Semantic search (cosine): `score (float): Cosine similarity score, 0.0–1.0; higher = more similar.`

**Returns format** — bulleted `- field (type): description` for all dict/list-of-dict return values. No mixing of prose and bullets within the same Returns block.

**Raises** — use the actual exception class exported from `markdown_vault_mcp.exceptions`, not generic `ValueError`, when a more specific class exists:
- Path not found → `DocumentNotFoundError`
- Ambiguous/missing text match → `EditConflictError`
- Etag mismatch → `ConcurrentModificationError`
- Read-only write attempt → `ReadOnlyError`

**Tool cross-references** — always single-quoted: `'stats'`, `'search'`, `'reindex'`.

### Specific Correctness Fixes

| Tool | Bug | Fix |
|------|-----|-----|
| `create_download_link` | Returns says "JSON with …" — actually returns a JSON *string* (`str`), not a dict | Change Returns to: "JSON string with fields: download_url, expires_in_seconds, path, content_type." |
| `get_similar` | Score range not documented | Add `score (float): Cosine similarity score, 0.0–1.0; higher = more similar.` |
| `search` | `heading` field missing from Returns bullet list | Add `- heading (str or null): Section heading of the matched chunk, or null for the intro.` |
| `build_embeddings` | force=False described as "skips if embeddings already exist" — wrong, it embeds only missing chunks (incremental) | Change to: "Without force, only embeds chunks not yet in the vector index (incremental update)." |
| `get_context` | Returns uses mixed prose/dict style | Convert to bulleted field list consistent with the schema |
| `get_history` | `paths_changed` note says "always empty for single-note queries" — confusing because the caller already knows which file | Rewrite: "Vault-wide queries: list of changed file paths. Single-note queries: always `[]` (the note path is implicit from the query)." |
| `delete` | Raises says `ValueError` for missing file | Change to `DocumentNotFoundError` |
| `rename` | Raises says `ValueError` for missing/existing path | Change to `DocumentNotFoundError` / `DocumentExistsError` as appropriate |
| `get_backlinks` | Raises says `ValueError` for missing file | Change to `DocumentNotFoundError` |
| `get_outlinks` | Raises says `ValueError` for missing file | Change to `DocumentNotFoundError` |
| `get_similar` | Raises says `ValueError` for missing file | Change to `DocumentNotFoundError` |
| `get_context` | Raises says `ValueError` for missing file | Change to `DocumentNotFoundError` |

### Icon Gaps

All 28 LLM-visible tools already have `icons=` wired and a corresponding SVG file in `static/icons/`. The 6 app-only `vault_*` tools have neither.

**Reuse existing icons (no new SVG files needed):**

| Tool | Icon key to reuse |
|------|------------------|
| `vault_context` | `get_context` |
| `vault_list` | `list_documents` |
| `vault_read` | `read` |
| `vault_search` | `search` |

**New SVG files required:**

| Tool | New icon file | Lucide source icon |
|------|--------------|-------------------|
| `vault_graph_neighborhood` | `static/icons/vault_graph_neighborhood.svg` | `network` |
| `vault_graph_hubs` | `static/icons/vault_graph_hubs.svg` | `git-fork` |

SVG files are fetched from the Lucide icon CDN (`unpkg.com/lucide-static/icons/<name>.svg`), stroked with `currentColor`, and saved as-is. The `_TOOL_ICONS` dict in `_icons.py` is extended with all 6 new keys. Each `@mcp.tool` decorator in `_server_apps.py` receives the corresponding `icons=_TOOL_ICONS["<key>"]` argument.

### Test Coverage

`tests/test_docstrings.py` gains a new test:

```python
def test_all_mcp_tools_have_icons():
    """Assert every @mcp.tool in _server_*.py passes icons=."""
```

Implementation: parse each `_server_*.py` source file with `ast`, find all `@mcp.tool(...)` decorator calls, and assert that every call includes an `icons` keyword argument.

## Components

### Task 1 — Fetch new SVG icons
Download `network.svg` and `git-fork.svg` from Lucide and save to `static/icons/vault_graph_neighborhood.svg` and `static/icons/vault_graph_hubs.svg`.

### Task 2 — Extend `_icons.py` and wire icons in `_server_apps.py`
Add 6 keys to `_TOOL_ICONS`. Add `icons=_TOOL_ICONS[...]` to all 6 `vault_*` tool decorators.

### Task 3 — Add icon coverage test
Add `test_all_mcp_tools_have_icons` to `tests/test_docstrings.py`.

### Task 4 — Fix docstrings in `_server_tools.py` (read-only tools)
Apply the schema to: `search`, `read`, `list_documents`, `list_folders`, `list_tags`, `stats`, `embeddings_status`, `get_backlinks`, `get_outlinks`, `get_broken_links`, `get_similar`, `get_recent`, `get_context`, `get_orphan_notes`, `get_most_linked`, `get_connection_path`, `get_history`, `get_diff`, `reindex`, `build_embeddings`.

### Task 5 — Fix docstrings in `_server_tools.py` (write tools)
Apply the schema to: `write`, `edit`, `delete`, `rename`, `fetch`, `create_download_link`.

### Task 6 — Fix docstrings in `_server_apps.py`
Apply the schema to: `browse_vault`, `show_context`, and the 6 `vault_*` tools.

## Error Handling

No runtime behavior changes. All changes are documentation only (docstrings) or additive (icons + test). The icon test will fail fast if a new tool is added without an icon.

## Testing

- `uv run pytest tests/test_docstrings.py -x -q` — existing docstring coverage + new icon coverage test
- `uv run pytest -x -q` — full suite (no behavior changes, all tests should pass)
- Manual review: read each updated docstring as an LLM would — does it answer "when to use this?", "what do I pass?", "what do I get back?", "what errors can occur?"
