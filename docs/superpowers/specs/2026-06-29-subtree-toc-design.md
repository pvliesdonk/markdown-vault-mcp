# Folder/subtree TOC + per-note TOC tool — Design (#773)

**Issue:** [#773](https://github.com/pvliesdonk/markdown-vault-mcp/issues/773)
**Date:** 2026-06-29
**Status:** Approved (brainstorm)

## Problem

There is no way to get a heading-level table of contents for a **folder/subtree**
of the vault — only for a single note. Separately, the per-note TOC is exposed
**only as an MCP resource** (`toc://vault/{path}`), so tool-only clients cannot
retrieve even a single note's outline.

## Goals

1. Extend the existing `toc://vault/{path}` **resource** so that when `path`
   names a folder it returns an aggregated subtree TOC; when it names a note it
   keeps today's behavior unchanged.
2. Add a **tool** `get_toc` with the same path-dispatch behavior, exposing the
   two bounding knobs the resource cannot take as parameters.

Non-goals (YAGNI): anchors, pagination/cursors, heading-hierarchy merging,
full-content retrieval (that is `read`).

## What exists today (verified)

- `FTSIndex.get_toc(path)` → `list[{heading, level}]` for one note, from the
  `sections` table (`document_id`, `heading`, `heading_level`), ordered by first
  appearance.
- `DocumentManager.get_toc(path)` validates the path, looks up the note (raises
  `ValueError` if missing), prepends the document **title** as a synthetic H1,
  and de-dups a leading H1 that already equals the title.
- `ReaderFacet.get_toc(path)` forwards to the manager.
- `toc://vault/{path}` resource (`_server_resources.py:vault_toc`) JSON-dumps the
  manager output and wraps it via `_stale_resource` for `_meta.index_stale`.
  **There is no TOC tool.**
- The per-note shape is `{heading, level}`. The resource docstring claiming
  `{level, text, anchor}` is **inaccurate** and will be corrected as part of this
  work (no `text`/`anchor` fields exist or are being added).

## Contract

### Path dispatch

A single rule, used identically by the resource and the tool:

- `path` **ends in `.md`** → **note mode** (existing behavior).
- otherwise → **folder mode** (new subtree behavior). A trailing `/` on the
  input is stripped. The subtree match is on a path **boundary**
  (`<folder> + "/"`), so `Project` never matches `Projects/...`.

### Note mode

Returns the existing flat list, now subject to the `max_level` filter:

```json
[ {"heading": "Title", "level": 1}, {"heading": "Intro", "level": 2}, ... ]
```

- Missing note → `ValueError` (unchanged).
- `max_level` (if set) drops entries with `level > max_level`. The synthetic H1
  title is always level 1, so it survives any `max_level >= 1`.
- `max_notes` is ignored in note mode.

### Folder mode

Returns a **nested-per-note** object:

```json
{
  "path": "Projects",
  "notes": [
    {
      "path": "Projects/alpha.md",
      "title": "Alpha",
      "headings": [ {"heading": "Goals", "level": 2}, ... ]
    },
    ...
  ],
  "truncated": false
}
```

- **Recursive** over the whole subtree (all descendants, not just direct
  children).
- Notes ordered by `path` ascending.
- Per-note `headings` use the same `{heading, level}` shape as note mode, with
  the synthetic H1 title prepended (the `title` is *also* carried on the note
  object for convenience; the synthetic H1 keeps note-mode and folder-mode
  per-note blocks structurally identical).
- `max_level` (optional, default `None` = all levels): drops headings with
  `level > max_level` within every note.
- `max_notes` (default `200`): caps the number of distinct notes. When more
  notes match than the cap, return the first `max_notes` (by path order) and set
  `truncated: true`. Detected by selecting `LIMIT max_notes + 1` and checking for
  overflow.
- Empty or nonexistent folder → `{"path": ..., "notes": [], "truncated": false}`
  (**not** an error — folders are not first-class entities; raising would punish
  legitimately empty folders). *(User-confirmed.)*

## Components & changes

All changes live in this repo (domain code), none in template-owned files.

### 1. `fts_index.py` — new subtree query

`FTSIndex.get_subtree_toc(prefix, *, max_level=None, max_notes=200)`:

1. Select documents whose `path` starts with `prefix + "/"`, ordered by `path`,
   `LIMIT max_notes + 1`. Capture `(id, path, title)`. Overflow of the `+1`
   row ⇒ `truncated = True`; drop the extra row.
2. For the selected document ids, select `heading, heading_level` from `sections`
   where `heading IS NOT NULL` (and `heading_level <= max_level` when set),
   grouped/ordered as in `get_toc`, keyed by `document_id`.
3. Return the per-note rows + `truncated` flag in a plain structure for the
   manager to assemble. (Two queries; both bounded by `max_notes`.)

`FTSIndex.get_toc` gains an optional `max_level` filter (push the predicate into
SQL, or filter in Python — implementation detail settled under TDD).

### 2. `managers/document.py` — dispatch + assembly

`DocumentManager.get_toc(path, *, max_level=None, max_notes=200)`:

- Note mode (`path.endswith(".md")`): current logic + `max_level` filter applied
  after the synthetic-H1 prepend.
- Folder mode: normalize prefix (strip trailing `/`), call `get_subtree_toc`,
  prepend each note's synthetic H1 title to its headings (reusing the same
  de-dup rule as note mode), assemble the nested object.
- `_validate_path` still guards traversal for both modes.

### 3. `facets/reader.py` — forward kwargs

`ReaderFacet.get_toc(path, *, max_level=None, max_notes=200)` forwards to the
manager. Return type widens to `list[...] | dict[str, Any]`.

### 4. `_server_resources.py` — extend the resource

`vault_toc` calls `vault.reader.get_toc(path)` (defaults). Note path → bare list
(unchanged); folder path → nested object. JSON-dump + `_stale_resource` as today.
Fix the inaccurate `{level, text, anchor}` docstring to describe both shapes.

### 5. `_server_tools/reader.py` — new tool

`get_toc(path, max_level=None, max_notes=200, vault=Depends(get_vault))`:

- `@mcp.tool(...)` with `readOnlyHint=True`, `idempotentHint=True`,
  `destructiveHint=False`, title e.g. `"Table of Contents"`, plus a tool icon.
- `@needs_queryable()` — requires a built index (bucket 3), like the resource.
- `asyncio.to_thread(vault.reader.get_toc, path, max_level=..., max_notes=...)`,
  wrapped with the standard staleness helper so `_meta.index_stale` is reported.
- Docstring documents both return shapes and cross-references the
  `toc://vault/{path}` resource.

## Error handling

| Case | Behavior |
|------|----------|
| Note path, missing note | `ValueError` (unchanged) |
| Note path, no headings | `[{title, 1}]` (synthetic H1 only) |
| Folder path, no matching notes | `{path, notes: [], truncated: false}` |
| Folder path, > `max_notes` notes | first `max_notes`, `truncated: true` |
| `max_level` filters out all body headings | note still carries its synthetic H1 |
| Path traversal attempt | `_validate_path` raises (unchanged) |
| Index not built | `needs_queryable` / resource gating (unchanged) |

## Testing (failure modes to cover)

Library level (`tests/` against `FTSIndex` / `DocumentManager` / `ReaderFacet`):

- Note mode unchanged: existing per-note tests stay green.
- `max_level` in note mode drops deep headings, keeps synthetic H1.
- Folder mode: nested shape, notes path-ordered, recursive across nested
  subfolders.
- Folder prefix boundary: `Project` does **not** match `Projects/...`.
- Trailing-slash input normalized.
- `max_notes` truncation: exactly-at-cap (no truncation) vs over-cap
  (`truncated: true`, count == cap).
- `max_level` in folder mode filters within every note.
- Empty/nonexistent folder → empty `notes`, no raise.
- Note with no body headings inside a folder → synthetic-H1-only block.

MCP level (`async with Client(server)`):

- New `get_toc` tool: note path and folder path return the two shapes; knobs
  honored; `_meta.index_stale` present.
- Resource `toc://vault/{path}`: note path bare list (unchanged), folder path
  nested object.
- `get_toc` registered in the full tool registry with a title (registry
  enforcement test).
- Bucket-3 gating: tool/resource on a cold (unbuilt) index behaves like peers.

## Documentation impact

- `docs/tools/index.md` — new `get_toc` tool.
- `docs/resources.md` — extended `toc://vault/{path}` (both shapes) + cross-ref
  to the tool.
- `README.md` — tool/resource list update.
- `docs/design.md` — TOC surface (note + subtree) and bounds.

## Out of scope

Anchors, pagination/continuation cursors, heading-hierarchy nesting, full-content
retrieval. Each can be a follow-up issue if a real need appears.
