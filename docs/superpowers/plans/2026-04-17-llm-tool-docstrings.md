# LLM-Facing Tool Docstring Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a consistent docstring schema across all 34 MCP tools, fix known correctness bugs, and add Lucide SVG icons to the 6 app-only `vault_*` tools.

**Architecture:** Documentation-only changes except for two new SVG files and the `_icons.py`/`_server_apps.py` icon wiring. No runtime behavior changes. A new AST-based test in `test_docstrings.py` enforces that every `@mcp.tool` decorator has `icons=` going forward.

**Tech Stack:** Python 3.10+, FastMCP, `ast` stdlib (icon coverage test), `mcp__universal-icons__get_icon` MCP tool (SVG fetch).

---

## File Map

- **Create:** `src/markdown_vault_mcp/static/icons/vault_graph_neighborhood.svg`
- **Create:** `src/markdown_vault_mcp/static/icons/vault_graph_hubs.svg`
- **Modify:** `src/markdown_vault_mcp/_icons.py` — add 6 keys to `_TOOL_ICONS`
- **Modify:** `src/markdown_vault_mcp/_server_apps.py` — add `icons=` to 6 `vault_*` decorators
- **Modify:** `src/markdown_vault_mcp/_server_tools.py` — fix docstrings for 11 tools
- **Modify:** `tests/test_docstrings.py` — add `test_all_mcp_tools_have_icons`

---

### Task 1: Fetch and save graph icon SVGs

**Files:**
- Create: `src/markdown_vault_mcp/static/icons/vault_graph_neighborhood.svg`
- Create: `src/markdown_vault_mcp/static/icons/vault_graph_hubs.svg`

- [ ] **Step 1: Fetch the `network` icon using the universal-icons MCP tool**

Call `mcp__universal-icons__get_icon` with `icon_name="network"`, `collection="lucide"`, `format="svg"`. The result is the SVG string (plus a trailing HTML comment — strip it). The SVG should look like the existing icons: single-line, `width="24" height="24"`, `stroke="currentColor"`, `stroke-width="2"`.

- [ ] **Step 2: Save the network icon**

Write the SVG content (strip the trailing `<!-- ... -->` comment line) to:
`src/markdown_vault_mcp/static/icons/vault_graph_neighborhood.svg`

- [ ] **Step 3: Fetch the `git-fork` icon**

Call `mcp__universal-icons__get_icon` with `icon_name="git-fork"`, `collection="lucide"`, `format="svg"`. Strip the trailing comment.

- [ ] **Step 4: Save the git-fork icon**

Write to: `src/markdown_vault_mcp/static/icons/vault_graph_hubs.svg`

- [ ] **Step 5: Verify both files load correctly**

```bash
python -c "
import importlib.resources, base64
d = importlib.resources.files('markdown_vault_mcp').joinpath('static/icons')
for name in ('vault_graph_neighborhood', 'vault_graph_hubs'):
    b = d.joinpath(f'{name}.svg').read_bytes()
    assert b.startswith(b'<svg'), f'{name}.svg does not start with <svg>'
    print(f'{name}.svg OK ({len(b)} bytes)')
"
```

Expected output:
```
vault_graph_neighborhood.svg OK (...bytes)
vault_graph_hubs.svg OK (...bytes)
```

- [ ] **Step 6: Commit**

```bash
git add src/markdown_vault_mcp/static/icons/vault_graph_neighborhood.svg \
        src/markdown_vault_mcp/static/icons/vault_graph_hubs.svg
git commit -m "feat(icons): add vault_graph_neighborhood and vault_graph_hubs SVG icons"
```

---

### Task 2: Wire icons for `vault_*` tools + icon coverage test

**Files:**
- Modify: `src/markdown_vault_mcp/_icons.py`
- Modify: `src/markdown_vault_mcp/_server_apps.py`
- Test: `tests/test_docstrings.py`

- [ ] **Step 1: Write the failing icon coverage test**

Add to `tests/test_docstrings.py`:

```python
def test_all_mcp_tools_have_icons():
    """Every @mcp.tool decorator in _server_*.py must include icons=."""
    import ast
    from pathlib import Path

    server_dir = Path("src/markdown_vault_mcp")
    missing = []
    for path in sorted(server_dir.glob("_server_*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"
                ):
                    continue
                has_icons = any(kw.arg == "icons" for kw in decorator.keywords)
                if not has_icons:
                    missing.append(f"{path.name}::{node.name}")
    assert not missing, f"@mcp.tool decorators missing icons=: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_docstrings.py::test_all_mcp_tools_have_icons -v
```

Expected: FAIL — lists `_server_apps.py::vault_context`, `_server_apps.py::vault_graph_neighborhood`, `_server_apps.py::vault_graph_hubs`, `_server_apps.py::vault_list`, `_server_apps.py::vault_read`, `_server_apps.py::vault_search`.

- [ ] **Step 3: Extend `_TOOL_ICONS` in `_icons.py`**

In `src/markdown_vault_mcp/_icons.py`, replace:

```python
_TOOL_ICONS: dict[str, list[Icon]] = {
    name: _load_icon(name)
    for name in [
        "search",
        "read",
        "list_documents",
        "list_folders",
        "list_tags",
        "stats",
        "embeddings_status",
        "reindex",
        "build_embeddings",
        "write",
        "edit",
        "delete",
        "rename",
        "get_backlinks",
        "get_outlinks",
        "get_recent",
        "get_similar",
        "get_broken_links",
        "get_context",
        "get_orphan_notes",
        "get_most_linked",
        "get_connection_path",
        "fetch",
        "browse_vault",
        "show_context",
        "create_download_link",
        "get_history",
        "get_diff",
    ]
}
```

with:

```python
_TOOL_ICONS: dict[str, list[Icon]] = {
    name: _load_icon(name)
    for name in [
        "search",
        "read",
        "list_documents",
        "list_folders",
        "list_tags",
        "stats",
        "embeddings_status",
        "reindex",
        "build_embeddings",
        "write",
        "edit",
        "delete",
        "rename",
        "get_backlinks",
        "get_outlinks",
        "get_recent",
        "get_similar",
        "get_broken_links",
        "get_context",
        "get_orphan_notes",
        "get_most_linked",
        "get_connection_path",
        "fetch",
        "browse_vault",
        "show_context",
        "create_download_link",
        "get_history",
        "get_diff",
        "vault_graph_neighborhood",
        "vault_graph_hubs",
    ]
}

# App-only tools reuse existing icons rather than introducing new SVG files.
_TOOL_ICONS["vault_context"] = _TOOL_ICONS["get_context"]
_TOOL_ICONS["vault_list"] = _TOOL_ICONS["list_documents"]
_TOOL_ICONS["vault_read"] = _TOOL_ICONS["read"]
_TOOL_ICONS["vault_search"] = _TOOL_ICONS["search"]
```

- [ ] **Step 4: Add `icons=` to the 6 `vault_*` tool decorators in `_server_apps.py`**

Each of the 6 `@mcp.tool(...)` decorators that currently has no `icons=` needs one added. Find each decorator by the `async def` name that follows it. The decorator for `vault_context` currently reads:

```python
    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"fastmcp": {"app": "vault"}},
        app=AppConfig(resourceUri=_VAULT_APP_URI, visibility=["app"]),
    )
    async def vault_context(
```

Replace with:

```python
    @mcp.tool(
        icons=_TOOL_ICONS["vault_context"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"fastmcp": {"app": "vault"}},
        app=AppConfig(resourceUri=_VAULT_APP_URI, visibility=["app"]),
    )
    async def vault_context(
```

Apply the same pattern to the other 5 tools. The decorator for each tool is identical except for the `async def` name. Add `icons=_TOOL_ICONS["<tool_name>"]` as the first keyword in each decorator:

| `async def` | `icons=` key |
|---|---|
| `vault_context` | `"vault_context"` |
| `vault_graph_neighborhood` | `"vault_graph_neighborhood"` |
| `vault_graph_hubs` | `"vault_graph_hubs"` |
| `vault_list` | `"vault_list"` |
| `vault_read` | `"vault_read"` |
| `vault_search` | `"vault_search"` |

- [ ] **Step 5: Run tests to verify the icon test now passes**

```bash
uv run pytest tests/test_docstrings.py -v
```

Expected: all tests PASS including `test_all_mcp_tools_have_icons`.

- [ ] **Step 6: Run full suite**

```bash
uv run pytest -x -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/markdown_vault_mcp/_icons.py \
        src/markdown_vault_mcp/_server_apps.py \
        tests/test_docstrings.py
git commit -m "feat(icons): add icons to vault_* app-only tools and enforce icon coverage in tests"
```

---

### Task 3: Fix read-only tool docstrings — `search`, `embeddings_status`, `get_similar`, `build_embeddings`

**Files:**
- Modify: `src/markdown_vault_mcp/_server_tools.py`

All changes are in `_server_tools.py`. No behavior changes — docstring text only.

- [ ] **Step 1: Fix `search` Returns — add missing `heading` and `search_type` fields, standardize format**

Find the Returns section inside `async def search`:

```
        Returns:
            List of result dicts ranked by relevance (higher score is better).
            Each contains: path, title, folder, content (matched chunk),
            score, frontmatter.
```

Replace with:

```
        Returns:
            List of result dicts ranked by relevance. Each contains:

            - path (str): Relative path of the document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - heading (str | None): Section heading of the matched chunk,
              or null for the document intro.
            - content (str): Matched chunk text (not the full document).
            - score (float): BM25 relevance score (keyword mode) or cosine
              similarity 0.0–1.0 (semantic/hybrid); higher = better match.
            - search_type (str): "keyword" or "semantic".
            - frontmatter (dict): Parsed YAML frontmatter of the document.
```

- [ ] **Step 2: Fix `embeddings_status` Returns — convert prose to bulleted list**

Find:

```
        Returns:
            Dict with available (bool), provider (str or null — provider class
            name when configured, e.g. "OllamaProvider"), chunk_count (int —
            embedded chunks in the vector index), and path (str or null —
            vector index file path when configured).
```

Replace with:

```
        Returns:
            Dict with the following fields:

            - available (bool): True if semantic search can be used in 'search'.
            - provider (str | None): Provider class name when configured
              (e.g. "OllamaProvider"), or null if not configured.
            - chunk_count (int): Number of chunks currently in the vector index.
            - path (str | None): Vector index file path when persisted, or null.
```

- [ ] **Step 3: Fix `get_similar` Returns — add score range; fix Raises — use `DocumentNotFoundError`**

Find the Returns section inside `async def get_similar`:

```
        Returns:
            List of result dicts ranked by similarity (higher score is
            more similar). Each contains: path, title, folder, content
            (most similar chunk), score, search_type ("semantic").
```

Replace with:

```
        Returns:
            List of result dicts ranked by similarity. Each contains:

            - path (str): Relative path of the similar document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - heading (str | None): Section heading of the most similar chunk.
            - content (str): Most similar chunk text.
            - score (float): Cosine similarity, 0.0–1.0; higher = more similar.
            - search_type (str): Always "semantic".
            - frontmatter (dict): Parsed YAML frontmatter.
```

Also find the Raises section inside `async def get_similar`:

```
        Raises:
            ValueError: If no document exists at the given path.
```

Replace with:

```
        Raises:
            DocumentNotFoundError: If no document exists at the given path.
```

- [ ] **Step 4: Fix `build_embeddings` Args — correct `force=False` description**

Find inside `async def build_embeddings`:

```
            force: When True, discards existing embeddings and rebuilds from
                scratch. Use only if the embedding model has changed.
                False (default) only embeds chunks not yet embedded.
```

Replace with:

```
            force: When True, discards existing embeddings and rebuilds from
                scratch. Use only if the embedding model has changed.
                When False (default), only embeds chunks not yet in the
                vector index (incremental — does not skip if any exist).
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/markdown_vault_mcp/_server_tools.py
git commit -m "docs(tools): fix search/embeddings_status/get_similar/build_embeddings docstrings"
```

---

### Task 4: Fix read-only tool docstrings — `get_backlinks`, `get_outlinks`, `get_context`, `get_history`

**Files:**
- Modify: `src/markdown_vault_mcp/_server_tools.py`

- [ ] **Step 1: Fix `get_backlinks` Raises — use `DocumentNotFoundError`**

Find inside `async def get_backlinks`:

```
        Raises:
            ValueError: If no document exists at the given path.
```

Replace with:

```
        Raises:
            DocumentNotFoundError: If no document exists at the given path.
```

- [ ] **Step 2: Fix `get_outlinks` Raises — use `DocumentNotFoundError`**

Find inside `async def get_outlinks`:

```
        Raises:
            ValueError: If no document exists at the given path.
```

Replace with:

```
        Raises:
            DocumentNotFoundError: If no document exists at the given path.
```

- [ ] **Step 3: Fix `get_context` Returns — convert to bulleted list; fix Raises**

Find the Returns section inside `async def get_context`:

```
        Returns:
            Dict with: path, title, folder, frontmatter (dict),
            modified_at (Unix timestamp), backlinks (list), outlinks (list),
            similar (list of {path, title, score}).
            folder_notes (list[str]): Paths of other notes in the same folder
            (max 20). Plain strings, not dicts — unlike backlinks/outlinks/similar.
            tags (dict[str, list[str]]): Indexed frontmatter field → list of values.
            backlinks and outlinks are empty if link tracking is not
            available. similar is empty if semantic search is not configured
            or similar_limit is 0.
```

Replace with:

```
        Returns:
            Dict with the following fields:

            - path (str): Relative path of the document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - frontmatter (dict): Parsed YAML frontmatter.
            - modified_at (float): Unix timestamp of last modification.
            - backlinks (list): Documents linking to this note. Each entry
              has source_path, source_title, link_text, link_type, fragment,
              raw_target. Empty if link tracking is not yet built.
            - outlinks (list): Links from this note. Each entry has
              target_path, link_text, link_type, fragment, raw_target, exists
              (bool). Empty if link tracking is not yet built.
            - similar (list): Semantically similar notes. Each entry has
              path, title, score (cosine similarity 0.0–1.0). Empty if
              semantic search is not configured or similar_limit=0.
            - folder_notes (list[str]): Paths of other notes in the same
              folder (up to 20). Plain strings, not dicts.
            - tags (dict[str, list[str]]): Indexed frontmatter field →
              distinct values for this note.
```

Find the Raises section inside `async def get_context`:

```
        Raises:
            ValueError: If no document exists at the given path.
```

Replace with:

```
        Raises:
            DocumentNotFoundError: If no document exists at the given path.
```

- [ ] **Step 4: Fix `get_history` `paths_changed` description — remove confusing explanation**

Find inside `async def get_history` Returns:

```
            - paths_changed (list[str]): Files touched by the commit.
              Populated for vault-wide queries; always empty for single-note
              queries because the path is already determined by the query
              arguments — callers know which file the commit touched without
              needing it echoed back.
```

Replace with:

```
            - paths_changed (list[str]): Files touched by the commit.
              Populated for vault-wide queries. Always [] for single-note
              queries (the queried note path is implicit).
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/markdown_vault_mcp/_server_tools.py
git commit -m "docs(tools): fix get_backlinks/get_outlinks/get_context/get_history docstrings"
```

---

### Task 5: Fix write tool docstrings — `delete`, `rename`, `create_download_link`

**Files:**
- Modify: `src/markdown_vault_mcp/_server_tools.py`

- [ ] **Step 1: Fix `delete` Raises — use `DocumentNotFoundError`**

Find inside `async def delete`:

```
        Raises:
            ValueError: If no file exists at the given path.
            McpError: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
```

Replace with:

```
        Raises:
            DocumentNotFoundError: If no file exists at the given path.
            McpError: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
```

- [ ] **Step 2: Fix `rename` Raises — split `ValueError` into `DocumentNotFoundError` + `DocumentExistsError`**

Find inside `async def rename`:

```
        Raises:
            ValueError: If old_path does not exist, new_path already exists,
                or the path fails traversal validation.
            McpError: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
```

Replace with:

```
        Raises:
            DocumentNotFoundError: If old_path does not exist.
            DocumentExistsError: If new_path already exists.
            ValueError: If the path fails traversal validation.
            McpError: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
```

- [ ] **Step 3: Fix `create_download_link` Returns — clarify it returns a JSON *string*, not a dict**

Find inside `async def create_download_link`:

```
        Returns:
            JSON with ``download_url``, ``expires_in_seconds``,
            ``path``, and ``content_type``.
```

Replace with:

```
        Returns:
            JSON-encoded string with the following fields:

            - download_url (str): One-time HTTP URL to download the file.
            - expires_in_seconds (int): Link lifetime (equals ttl_seconds).
            - path (str): Vault-relative path of the served file.
            - content_type (str): MIME type of the file.
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest -x -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/markdown_vault_mcp/_server_tools.py
git commit -m "docs(tools): fix delete/rename/create_download_link docstrings"
```

---

### Task 6: Fix `_server_apps.py` docstrings — `browse_vault`, `show_context`, `vault_*`

**Files:**
- Modify: `src/markdown_vault_mcp/_server_apps.py`

- [ ] **Step 1: Fix `browse_vault` Returns — standardize field format**

Find inside `async def browse_vault`:

```
        Returns:
            - ``path``: the requested path (or ``null``)
            - ``view``: the requested view
            - ``summary``: text summary for non-Apps clients
```

Replace with:

```
        Returns:
            - path (str | None): The requested note path, or null if none given.
            - view (str): The active view ("context", "graph", "browse", or "note").
            - summary (str): Text summary of vault or note state for non-Apps clients.
```

- [ ] **Step 2: Fix `show_context` Returns — standardize field format**

Find inside `async def show_context`:

```
        Returns:
            - ``path``: the note path
            - ``view``: ``"context"``
            - ``summary``: text summary with relationship counts
```

Replace with:

```
        Returns:
            - path (str): The note path.
            - view (str): Always "context".
            - summary (str): Text summary with backlink, outlink, and similarity counts.
```

- [ ] **Step 3: Fix `vault_context` Returns — standardize**

Find inside `async def vault_context`:

```
        Returns:
            NoteContext as a JSON-serializable dict.
```

Replace with:

```
        Returns:
            Dict with path, title, folder, frontmatter, modified_at, backlinks,
            outlinks, similar, folder_notes, and tags — see 'get_context' for
            field details. Returns {"error": "..."} if the note is not found.
```

- [ ] **Step 4: Fix `vault_graph_neighborhood` Returns — standardize**

Find inside `async def vault_graph_neighborhood`:

```
        Returns:
            ``{nodes: [{id, label, group, folder, backlink_count}], edges: [{from, to, type}]}``
```

Replace with:

```
        Returns:
            Dict with:

            - nodes (list): Each node has id (str), label (str), group
              ("note" or "orphan"), folder (str), backlink_count (int).
            - edges (list): Each edge has from (str), to (str), type
              ("markdown", "wikilink", "reference", or "semantic").
```

- [ ] **Step 5: Fix `vault_graph_hubs` Returns — standardize**

Find inside `async def vault_graph_hubs`:

```
        Returns:
            ``{nodes: [{id, label, group, backlink_count}], edges: [{from, to, type}]}``
```

Replace with:

```
        Returns:
            Dict with:

            - nodes (list): Each node has id (str), label (str), group
              ("hub" or "note"), folder (str), backlink_count (int).
            - edges (list): Each edge has from (str), to (str), type
              ("markdown", "wikilink", or "reference").
```

- [ ] **Step 6: Fix `vault_list` Returns — standardize**

Find inside `async def vault_list`:

```
        Returns:
            ``{folders: [str], notes: [{path, title, kind}]}``
```

Replace with:

```
        Returns:
            Dict with:

            - folders (list[str]): Direct child folder paths.
            - notes (list): Notes directly inside this folder. Each has
              path (str), title (str), kind ("note" or "attachment").
```

- [ ] **Step 7: Fix `vault_read` Returns — standardize**

Find inside `async def vault_read`:

```
        Returns:
            ``{path, title, frontmatter, content, modified_at}`` or ``null``.
```

Replace with:

```
        Returns:
            Dict with path, title, frontmatter, content (markdown body), and
            modified_at (Unix timestamp), or null if the note is not found.
```

- [ ] **Step 8: Fix `vault_search` Returns — standardize**

Find inside `async def vault_search`:

```
        Returns:
            List of ``{path, title, snippet, score}``.
```

Replace with:

```
        Returns:
            List of result dicts, each with path (str), title (str),
            snippet (str, first 200 chars of matched chunk), and
            score (float). Returns [{"error": "..."}] on search failure.
```

- [ ] **Step 9: Run full test suite**

```bash
uv run pytest -x -q
```

Expected: all tests pass.

- [ ] **Step 10: Run lint and type check**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run ruff format --check . && uv run mypy src/
```

Expected: no errors.

- [ ] **Step 11: Commit**

```bash
git add src/markdown_vault_mcp/_server_apps.py
git commit -m "docs(tools): standardize _server_apps.py tool docstrings"
```
