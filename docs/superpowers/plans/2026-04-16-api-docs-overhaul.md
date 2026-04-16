# API Documentation Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring all Python API reference pages and MCP interface docs into sync with the current codebase by switching to auto-discovery and fixing content gaps.

**Architecture:** Drop hardcoded `members:` lists from all four existing `docs/api/` pages so mkdocstrings auto-discovers public methods; add two new pages (`types.md`, `exceptions.md`) for the 27 exported symbols currently undocumented; audit and fix docstrings so auto-discovered methods have accurate descriptions; fix counts and missing items in README and MCP interface docs.

**Tech Stack:** Python 3.10+, mkdocstrings (Google-style), MkDocs Material, `uv run mkdocs build --strict`

---

### Task 1: Write docstring coverage test

All public symbols in `__all__` must have docstrings — `show_if_no_docstring: false` in `mkdocs.yml` silently hides undocumented members. Write a test to enforce this before touching any pages.

**Files:**
- Create: `tests/test_docstrings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_docstrings.py
import inspect
import markdown_vault_mcp


def _public_members(obj):
    """Return (name, member) pairs for public non-dunder members."""
    return [
        (name, member)
        for name, member in inspect.getmembers(obj)
        if not name.startswith("_")
        and not inspect.ismodule(member)
    ]


def test_all_exported_symbols_have_docstrings():
    """Every symbol in __all__ must have a module-level docstring."""
    missing = []
    for name in markdown_vault_mcp.__all__:
        obj = getattr(markdown_vault_mcp, name)
        if obj.__doc__ is None:
            missing.append(name)
    assert not missing, f"Missing docstrings: {missing}"


def test_collection_public_methods_have_docstrings():
    """Every public Collection method must have a docstring."""
    from markdown_vault_mcp.collection import Collection
    missing = [
        name
        for name, member in _public_members(Collection)
        if callable(member) and member.__doc__ is None
    ]
    assert not missing, f"Collection methods missing docstrings: {missing}"


def test_gitwritestrategy_public_methods_have_docstrings():
    """Every public GitWriteStrategy method must have a docstring."""
    from markdown_vault_mcp.git import GitWriteStrategy
    missing = [
        name
        for name, member in _public_members(GitWriteStrategy)
        if callable(member) and member.__doc__ is None
    ]
    assert not missing, f"GitWriteStrategy methods missing docstrings: {missing}"
```

- [ ] **Step 2: Run test to verify it fails or passes baseline**

```bash
cd /mnt/code/markdown-mcp
uv run pytest tests/test_docstrings.py -v
```

Expected: note which tests pass/fail. Any failures indicate missing docstrings to fix in Tasks 2–4.

- [ ] **Step 3: Commit the test**

```bash
git add tests/test_docstrings.py
git commit -m "test: add docstring coverage tests for public API"
```

---

### Task 2: Audit and fix types.py docstrings

`types.py` exports 20 dataclasses but none have `Attributes:` sections, so mkdocstrings shows field names and types but no descriptions. Add `Attributes:` sections to the most important types.

**Files:**
- Modify: `src/markdown_vault_mcp/types.py`

- [ ] **Step 1: Read current types.py**

```bash
cat -n src/markdown_vault_mcp/types.py
```

- [ ] **Step 2: Add Attributes sections to key dataclasses**

Edit `src/markdown_vault_mcp/types.py`. Add Google-style `Attributes:` sections to these dataclasses (replace single-line docstrings):

```python
@dataclass
class NoteContent:
    """Full content of a document, returned by :meth:`~markdown_vault_mcp.collection.Collection.read`.

    Attributes:
        path: Relative path from the vault root (e.g. ``Journal/note.md``).
        title: Document title derived from the first H1 heading or filename.
        folder: Parent folder path (empty string for root-level documents).
        content: Raw markdown body including frontmatter.
        frontmatter: Parsed YAML frontmatter as a dict.
        modified_at: Last-modified time as a Unix timestamp float.
        etag: Opaque hash of file content for optimistic concurrency checks.
    """

    path: str
    title: str
    folder: str
    content: str
    frontmatter: dict[str, Any]
    modified_at: float
    etag: str | None = None


@dataclass
class SearchResult:
    """A search result from :meth:`~markdown_vault_mcp.collection.Collection.search`.

    Attributes:
        path: Relative path of the document containing this chunk.
        title: Document title.
        folder: Parent folder path.
        heading: Section heading this chunk falls under, or ``None`` for the intro.
        content: Matched chunk text (not the full document).
        score: Relevance score. Higher is better; not comparable across search types.
        search_type: ``"keyword"`` (BM25) or ``"semantic"`` (cosine similarity).
        frontmatter: Parsed YAML frontmatter of the parent document.
    """

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    search_type: Literal["keyword", "semantic"]
    frontmatter: dict[str, Any]


@dataclass
class NoteInfo:
    """Summary info for a document, returned by :meth:`~markdown_vault_mcp.collection.Collection.list`.

    Attributes:
        path: Relative path from the vault root.
        title: Document title.
        folder: Parent folder path.
        frontmatter: Parsed YAML frontmatter.
        modified_at: Last-modified time as a Unix timestamp float.
        kind: Always ``"note"`` for markdown documents; distinguishes from :class:`AttachmentInfo`.
    """

    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    kind: str = "note"


@dataclass
class CollectionStats:
    """Collection-wide statistics, returned by :meth:`~markdown_vault_mcp.collection.Collection.stats`.

    Attributes:
        document_count: Number of indexed markdown documents.
        chunk_count: Total number of indexed sections (chunks).
        folder_count: Number of distinct folder paths.
        semantic_search_available: ``True`` if a vector index is loaded and ready.
        indexed_frontmatter_fields: Frontmatter fields configured for tag indexing.
        attachment_extensions: File extensions recognised as attachments.
        link_count: Total number of links extracted from all documents.
        broken_link_count: Number of links whose target does not exist.
        orphan_count: Number of documents with no inbound or outbound links.
    """

    document_count: int
    chunk_count: int
    folder_count: int
    semantic_search_available: bool
    indexed_frontmatter_fields: list[str] = field(default_factory=list)
    attachment_extensions: list[str] = field(default_factory=list)
    link_count: int = 0
    broken_link_count: int = 0
    orphan_count: int = 0


@dataclass
class NoteContext:
    """Consolidated context for a document, returned by :meth:`~markdown_vault_mcp.collection.Collection.get_context`.

    Attributes:
        path: Relative path from the vault root.
        title: Document title.
        folder: Parent folder path.
        frontmatter: Parsed YAML frontmatter.
        modified_at: Last-modified time as a Unix timestamp float.
        backlinks: Documents that link to this document.
        outlinks: Links from this document with existence flags.
        similar: Up to ``similar_limit`` semantically similar notes (compact form).
        folder_notes: Paths of other notes in the same folder (up to 20).
        tags: Tag values for each indexed frontmatter field.
    """

    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    backlinks: list[BacklinkInfo]
    outlinks: list[OutlinkInfo]
    similar: list[SimilarItem]
    folder_notes: list[str]
    tags: dict[str, list[str]]


@dataclass
class IndexStats:
    """Statistics from :meth:`~markdown_vault_mcp.collection.Collection.build_index`.

    Attributes:
        documents_indexed: Number of documents successfully indexed.
        chunks_indexed: Total number of chunks indexed.
        skipped: Number of documents skipped due to parse errors.
    """

    documents_indexed: int
    chunks_indexed: int
    skipped: int


@dataclass
class ReindexResult:
    """Result of :meth:`~markdown_vault_mcp.collection.Collection.reindex`.

    Attributes:
        added: Documents added since the last index.
        modified: Documents that changed since the last index.
        deleted: Documents removed since the last index.
        unchanged: Documents with no changes.
    """

    added: int
    modified: int
    deleted: int
    unchanged: int
```

- [ ] **Step 3: Run docstring coverage test**

```bash
uv run pytest tests/test_docstrings.py -v
```

Expected: PASS (or only non-types.py failures remaining).

- [ ] **Step 4: Run lint**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run ruff format --check .
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/markdown_vault_mcp/types.py
git commit -m "docs: add Attributes sections to key dataclass docstrings"
```

---

### Task 3: Audit and fix exceptions.py docstrings

`EditConflictError` and `ConcurrentModificationError` have constructor parameters with no docstring. Add them so mkdocstrings shows what each field means.

**Files:**
- Modify: `src/markdown_vault_mcp/exceptions.py`

- [ ] **Step 1: Replace exceptions.py content**

```python
"""Exception types for markdown-vault-mcp."""


class MarkdownMCPError(Exception):
    """Base exception for all markdown-vault-mcp errors."""


class DocumentNotFoundError(MarkdownMCPError):
    """Raised when the requested document path does not exist on disk."""


class ReadOnlyError(MarkdownMCPError):
    """Raised when a write operation is attempted on a read-only collection."""


class EditConflictError(MarkdownMCPError):
    """Raised when ``old_text`` is not found or appears more than once in a document.

    Attributes:
        closest_match_line: 1-based line number of the nearest fuzzy match, if any.
        first_diff_char: Character offset of the first difference from the nearest match.
        expected_snippet: The ``old_text`` that was searched for (truncated).
        found_snippet: The nearest match found in the document (truncated).
    """

    def __init__(
        self,
        message: str,
        *,
        closest_match_line: int | None = None,
        first_diff_char: int | None = None,
        expected_snippet: str | None = None,
        found_snippet: str | None = None,
    ) -> None:
        super().__init__(message)
        self.closest_match_line = closest_match_line
        self.first_diff_char = first_diff_char
        self.expected_snippet = expected_snippet
        self.found_snippet = found_snippet


class DocumentExistsError(MarkdownMCPError):
    """Raised when the target path already exists (e.g. rename destination)."""


class ConcurrentModificationError(MarkdownMCPError):
    """Raised when an ``if_match`` etag does not match the current file state.

    Attributes:
        path: Relative path of the document that was modified concurrently.
        expected: The etag value the caller provided.
        actual: The etag value found on disk.
    """

    def __init__(self, path: str, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Concurrent modification on {path}: "
            f"expected etag {expected!r}, actual {actual!r}"
        )


class ConfigurationError(MarkdownMCPError):
    """Raised for invalid or unsupported configuration at startup."""
```

- [ ] **Step 2: Run docstring coverage test**

```bash
uv run pytest tests/test_docstrings.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite to check for regressions**

```bash
uv run pytest -x -q
```

Expected: all tests pass (exception class structure unchanged).

- [ ] **Step 4: Commit**

```bash
git add src/markdown_vault_mcp/exceptions.py
git commit -m "docs: add Attributes sections to exception docstrings"
```

---

### Task 4: Audit collection.py, config.py, git.py, providers.py docstrings

Read each module and verify every public method/class has a complete, accurate docstring. `show_if_no_docstring: false` silently hides undocumented members — anything missing a docstring will be invisible in the generated docs.

**Files:**
- Modify as needed: `src/markdown_vault_mcp/collection.py`, `src/markdown_vault_mcp/config.py`, `src/markdown_vault_mcp/git.py`, `src/markdown_vault_mcp/providers.py`

- [ ] **Step 1: Read collection.py public methods**

```bash
grep -n 'def \|"""' src/markdown_vault_mcp/collection.py | grep -v '^\s*//' | head -120
```

For each public method (no leading `_`): verify the docstring is present, mentions the return type, and lists key parameters. Flag any that are single-line only where more context would help.

- [ ] **Step 2: Fix any missing or stub docstrings in collection.py**

Pay particular attention to methods added in later PRs that may have brief docstrings:
- `get_backlinks(path, *, limit)` — document the `limit` parameter
- `get_outlinks(path, *, limit)` — document the `limit` parameter
- `get_context(path, *, similar_limit, link_limit)` — document both keyword params
- `get_diff(path, ref, *, per_commit)` — document what `ref` accepts (commit SHA, timestamp, branch)
- `get_history(path, *, limit)` — document `path=None` for vault-wide history

Read the actual docstrings for those methods and verify they mention the above:

```bash
sed -n '/def get_backlinks/,/def get_outlinks/p' src/markdown_vault_mcp/collection.py
sed -n '/def get_context/,/def get_orphan/p' src/markdown_vault_mcp/collection.py
sed -n '/def get_diff/,/def stats/p' src/markdown_vault_mcp/collection.py
```

- [ ] **Step 3: Read and verify config.py CollectionConfig fields**

```bash
sed -n '/class CollectionConfig/,/def to_collection_kwargs/p' src/markdown_vault_mcp/config.py
```

`CollectionConfig` is a dataclass. Verify it has an `Attributes:` section covering the most important fields: `source_dir`, `read_only`, `indexed_fields`, `exclude_patterns`, `embedding_provider`, `git_repo_url`, `git_token`, `bearer_token`. Add the section if missing.

- [ ] **Step 4: Run docstring coverage test and lint**

```bash
uv run pytest tests/test_docstrings.py -v
uv run ruff check --fix . && uv run ruff format . && uv run ruff format --check .
```

Expected: PASS on both.

- [ ] **Step 5: Commit any docstring fixes**

```bash
git add src/markdown_vault_mcp/collection.py src/markdown_vault_mcp/config.py \
        src/markdown_vault_mcp/git.py src/markdown_vault_mcp/providers.py
git commit -m "docs: fix and complete public API docstrings"
```

(Skip this commit if no changes were needed.)

---

### Task 5: Drop hardcoded member lists from all four existing API pages

Remove the `members:` blocks so mkdocstrings auto-discovers all public members.

**Files:**
- Modify: `docs/api/collection.md`, `docs/api/git.md`, `docs/api/config.md`, `docs/api/providers.md`

- [ ] **Step 1: Update collection.md**

Replace the full `:::` block (lines 28–56) with:

```markdown
## API Reference

::: markdown_vault_mcp.collection.Collection
```

- [ ] **Step 2: Update git.md**

Replace the `GitWriteStrategy` block:

```markdown
## API Reference

::: markdown_vault_mcp.git.GitWriteStrategy

::: markdown_vault_mcp.git.git_write_strategy
```

- [ ] **Step 3: Update config.md**

Replace the `CollectionConfig` block:

```markdown
## API Reference

::: markdown_vault_mcp.config.CollectionConfig

::: markdown_vault_mcp.config.load_config
```

- [ ] **Step 4: Update providers.md**

Replace the `EmbeddingProvider` block:

```markdown
## API Reference

::: markdown_vault_mcp.providers.EmbeddingProvider

::: markdown_vault_mcp.providers.OllamaProvider

::: markdown_vault_mcp.providers.OpenAIProvider

::: markdown_vault_mcp.providers.FastEmbedProvider

::: markdown_vault_mcp.providers.get_embedding_provider
```

- [ ] **Step 5: Verify the docs build without errors**

```bash
uv run mkdocs build --strict 2>&1 | tail -30
```

Expected: `INFO - Documentation built in X.X seconds` with no warnings or errors. If mkdocstrings warns about a missing member, check whether the method has a docstring (Task 4) or if the method name changed.

- [ ] **Step 6: Commit**

```bash
git add docs/api/collection.md docs/api/git.md docs/api/config.md docs/api/providers.md
git commit -m "docs: switch API pages to auto-discovery (drop hardcoded member lists)"
```

---

### Task 6: Create docs/api/types.md

**Files:**
- Create: `docs/api/types.md`

- [ ] **Step 1: Write the file**

```markdown
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

::: markdown_vault_mcp.types.WriteCallback
```

- [ ] **Step 2: Verify build**

```bash
uv run mkdocs build --strict 2>&1 | tail -10
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add docs/api/types.md
git commit -m "docs: add types API reference page"
```

---

### Task 7: Create docs/api/exceptions.md

**Files:**
- Create: `docs/api/exceptions.md`

- [ ] **Step 1: Write the file**

```markdown
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
```

- [ ] **Step 2: Verify build**

```bash
uv run mkdocs build --strict 2>&1 | tail -10
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add docs/api/exceptions.md
git commit -m "docs: add exceptions API reference page"
```

---

### Task 8: Update mkdocs.yml — nav + llmstxt description

**Files:**
- Modify: `mkdocs.yml`

- [ ] **Step 1: Add types and exceptions to nav**

Find the `Python API:` section in `mkdocs.yml` (around line 143) and replace it with:

```yaml
  - Python API:
      - Collection: api/collection.md
      - Git Integration: api/git.md
      - Configuration: api/config.md
      - Embedding Providers: api/providers.md
      - Types: api/types.md
      - Exceptions: api/exceptions.md
```

- [ ] **Step 2: Fix the stale llmstxt description**

Find the `markdown_description:` block in the `llmstxt:` plugin section (around line 49) and update the counts:

```yaml
      markdown_description: >
        markdown-vault-mcp is a generic markdown collection MCP server with
        FTS5 full-text search, semantic search via embeddings, frontmatter-aware
        indexing, and incremental reindexing. It exposes 31 LLM-visible tools
        (plus 6 app-only tools), 9 resources, and 6 prompts over the Model
        Context Protocol.
```

- [ ] **Step 3: Verify build**

```bash
uv run mkdocs build --strict 2>&1 | tail -10
```

Expected: no errors and new pages appear in `site/api/types/` and `site/api/exceptions/`.

- [ ] **Step 4: Commit**

```bash
git add mkdocs.yml
git commit -m "docs: add types/exceptions to nav; fix stale tool/resource counts in llmstxt"
```

---

### Task 9: Fix README.md counts and missing items

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Fix the feature bullet counts (lines 23–25)**

Find:
```markdown
- **MCP tools** — 25+ tools including search, read, write, edit, delete, rename, git history, and admin operations
- **MCP resources** — 6 resources exposing vault configuration, statistics, tags, folders, and document outlines
- **MCP prompts** — 6 prompt templates including template-driven note creation
```

Replace with:
```markdown
- **MCP tools** — 31 LLM-visible tools including search, read, write, edit, delete, rename, git history, and admin operations; plus 6 app-only tools for MCP Apps clients
- **MCP resources** — 9 resources exposing vault configuration, statistics, tags, folders, document outlines, similar notes, recent notes, and an interactive SPA
- **MCP prompts** — 6 prompt templates including template-driven note creation
```

- [ ] **Step 2: Add missing tools to the MCP Tools table**

Find the tools table (around line 312). The table is missing `browse_vault` and `show_context`. Add them at the end of the table before the "Write tools" note:

```markdown
| `browse_vault` | Open the vault explorer SPA in a supporting MCP Apps client |
| `show_context` | Open the Context Card for a specific note in a supporting MCP Apps client |
```

- [ ] **Step 3: Add ui://vault/app.html to the Resources table**

Find the resources table (around line 348). Add the missing resource:

```markdown
| `ui://vault/app.html` | Interactive vault explorer SPA for MCP Apps clients |
```

- [ ] **Step 4: Add app-only tools note after the tools table**

After the "Write tools ... are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`" line, add:

```markdown

App tools (`browse_vault`, `show_context`) use `visibility="app"` and only appear in MCP clients that support the MCP Apps protocol. Six additional internal tools (`vault_context`, `vault_list`, `vault_read`, `vault_search`, `vault_graph_neighborhood`, `vault_graph_hubs`) are used by the SPA and are never visible to the LLM.
```

- [ ] **Step 5: Verify build**

```bash
uv run mkdocs build --strict 2>&1 | tail -10
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: fix tool/resource counts and add missing items in README"
```

---

### Task 10: Verify full build and run test suite

Final gate before declaring done.

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest -x -q
```

Expected: all tests pass.

- [ ] **Step 2: Run lint**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run ruff format --check .
```

Expected: no errors.

- [ ] **Step 3: Run type checker**

```bash
uv run mypy src/
```

Expected: no errors.

- [ ] **Step 4: Run full docs build**

```bash
uv run mkdocs build --strict
```

Expected: `Documentation built in X.X seconds` with no warnings.

- [ ] **Step 5: Spot-check key pages exist in site/**

```bash
ls site/api/
```

Expected output includes: `collection/`, `config/`, `exceptions/`, `git/`, `index.html`, `providers/`, `types/`

- [ ] **Step 6: Final commit if any cleanup needed**

```bash
git add -p
git commit -m "docs: final cleanup for API docs overhaul"
```
