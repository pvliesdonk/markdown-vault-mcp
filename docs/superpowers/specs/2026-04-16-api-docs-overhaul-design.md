# API Documentation Overhaul Design

**Date:** 2026-04-16
**Status:** Approved

## Problem

Several PRs since the initial documentation pass (primarily the collection-manager split in #376, plus the intelligence/link/history methods added in PRs #194–#218) have left the API documentation out of sync with the code. CLAUDE.md mandates that code without matching docs is incomplete; this spec documents the work needed to bring everything back into compliance.

Specific failures:
- `docs/api/collection.md` hardcodes a member list that is missing ~12 public methods (`get_backlinks`, `get_outlinks`, `get_similar`, `get_context`, `get_history`, `get_diff`, `get_orphan_notes`, `get_most_linked`, `get_connection_path`, `get_recent`, etc.)
- `docs/api/config.md` only exposes `to_collection_kwargs`, hiding all 20+ config fields
- `docs/api/git.md` and `docs/api/providers.md` also use restrictive hardcoded member lists
- No API pages for exceptions (7 exported) or types/dataclasses (20 exported)
- MCP interface docs have wrong counts: README says "25+ tools", "6 resources"; `mkdocs.yml` llmstxt block says "13 tools, 6 resources" (actual: 31 public tools, 9 resources)
- App-only tools (`vault_context`, `vault_list`, `vault_read`, `vault_search`, `vault_graph_neighborhood`, `vault_graph_hubs`) are undocumented entirely

## Design

### 1. Python API — Auto-discovery everywhere

All four existing `docs/api/` pages use mkdocstrings with hardcoded `members:` lists. Drop the `members:` restriction on every page, letting mkdocstrings auto-discover all public (non-`_`-prefixed) members. The existing `show_if_no_docstring: false` option in `mkdocs.yml` ensures methods without docstrings are silently excluded, so undocumented internals that happen to lack a leading `_` won't pollute the output.

**Files changed:**
- `docs/api/collection.md` — remove `members:` block; all ~30 public methods appear automatically
- `docs/api/git.md` — remove `members:` block from `GitWriteStrategy`
- `docs/api/config.md` — remove `members:` block from `CollectionConfig`; all config fields now visible
- `docs/api/providers.md` — remove `members:` block from `EmbeddingProvider`

**New pages:**
- `docs/api/types.md` — `:::  markdown_vault_mcp.types` auto-documents all 20 exported dataclasses (`NoteContent`, `SearchResult`, `NoteContext`, `CollectionStats`, etc.) with a brief intro on what these types represent and where they are returned
- `docs/api/exceptions.md` — `:::  markdown_vault_mcp.exceptions` auto-documents all 7 exported exceptions (`DocumentNotFoundError`, `ReadOnlyError`, `ConcurrentModificationError`, etc.) with guidance on when each is raised and how callers should handle them

### 2. Docstring audit — prerequisite for going live

Because `show_if_no_docstring: false` silently hides undocumented members, the audit must verify that every public method and class in the following modules has an accurate, complete docstring before the updated pages are deployed:

- `collection.py` — all public methods
- `git.py` — `GitWriteStrategy` methods
- `config.py` — `CollectionConfig` fields and `load_config`
- `providers.py` — `EmbeddingProvider` ABC and all three concrete providers
- `types.py` — all dataclasses and their fields
- `exceptions.py` — all exception classes

Any method lacking a docstring that should be visible must have one added. Any docstring that is stale (wrong parameter names, outdated return descriptions) must be corrected.

### 3. MCP interface docs — targeted manual pass

These pages cannot be auto-generated; they are prose with examples. Fix the specific gaps identified in the audit:

**`README.md`:**
- Update "25+ tools" to reflect the accurate split: 31 LLM-visible tools + 6 app-only tools
- Update "6 resources" to 9
- Add a brief "App-only tools" note explaining that 6 additional tools are available only in MCP Apps clients

**`mkdocs.yml` (llmstxt block):**
- Update description: "13 tools, 6 resources, and 6 prompts" → accurate counts

**`docs/tools/index.md`:**
- Verify all 31 LLM-visible tools are listed with current parameter descriptions
- Add an "App-only tools" section documenting the 6 `vault_*` tools with a note that they are only visible to MCP Apps clients (`visibility=["app"]`)

**`docs/resources.md`:**
- Add missing resources: `similar://vault/{path}`, `recent://vault`, `ui://vault/app.html`
- Verify all 9 resources are documented

**`docs/prompts.md`:**
- Verify all 6 prompts are documented (5 built-in + `create_from_template`)

### 4. `mkdocs.yml` nav update

Add the two new pages to the Python API section:

```yaml
- Python API:
    - Collection: api/collection.md
    - Git Integration: api/git.md
    - Configuration: api/config.md
    - Embedding Providers: api/providers.md
    - Types: api/types.md        # new
    - Exceptions: api/exceptions.md  # new
```

## Implementation Order

1. Docstring audit and fixes (prerequisite — ensures auto-discovered docs are complete)
2. Drop `members:` from the four existing API pages
3. Create `docs/api/types.md` and `docs/api/exceptions.md`
4. Update `mkdocs.yml` nav and llmstxt description
5. MCP interface docs pass (README, tools/index.md, resources.md, prompts.md)
6. Verify locally with `uv run mkdocs build --strict`

## Out of Scope

- The manager classes (`LinkManager`, `SearchManager`, `IndexManager`, `DocumentManager`) are internal implementation details, not in `__all__`, and should not get API pages
- Restructuring the prose content of existing guides
- Adding new examples or tutorials beyond what is needed to fix specific gaps
