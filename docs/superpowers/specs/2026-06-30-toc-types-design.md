# Type the TOC payloads as dataclasses — Design (#779)

**Issue:** [#779](https://github.com/pvliesdonk/markdown-vault-mcp/issues/779)
**Date:** 2026-06-30
**Status:** Approved (brainstorm)

## Problem

The table-of-contents read surface (`get_toc` / `get_subtree_toc`, shipped in #773 / PR #780) returns structurally-rich payloads typed only as `list[dict[str, Any]] | dict[str, Any]`. Field names and shape invariants live in docstrings, not the type system, so `mypy` cannot catch a key-name typo (`"heading"` vs `"headings"`) or a shape regression. Every other result-shaped method in the library returns a `@dataclass` from `types.py`; the TOC methods are the lone exception returning raw dicts.

## Goal

Express the TOC shapes as `@dataclass`es (consistent with the ~20 existing dataclasses in `types.py`; the codebase has **zero** `TypedDict`s), thread them through the FTS → manager → facet layers, and `asdict()` them at the MCP serialization boundary — the same pattern already used for `SearchResult`, `FTSResult`, etc.

**Hard constraint: the JSON wire shape is unchanged.** The MCP tool's structured content, the resource's JSON body, and the advertised output schema stay byte-identical. This is a typing/maintainability change only.

Out of scope: migrating the rest of `types.py` to `TypedDict` (tracked in a separate investigation issue); any behavior, bounds, or API change to the TOC feature itself.

## New types (in `types.py`)

```python
@dataclass
class TocEntry:
    heading: str
    level: int

@dataclass
class SubtreeNote:
    path: str
    title: str
    headings: list[TocEntry]

@dataclass
class SubtreeToc:
    path: str
    notes: list[SubtreeNote]
    truncated: bool
```

Placed alongside the existing dataclasses, with the same `@dataclass` style (no `slots`/`frozen` unless the surrounding types use them — match the file).

## Layer threading

| Symbol | Before | After |
|--------|--------|-------|
| `FTSIndex.get_toc(path, *, max_level)` | `list[dict[str, str \| int]]` | `list[TocEntry]` |
| `FTSIndex.get_subtree_toc(prefix, *, max_level, max_notes)` | `tuple[list[dict[str, Any]], bool]` | `tuple[list[SubtreeNote], bool]` |
| `DocumentManager._prepend_title_h1(title, headings)` | `list[dict] -> list[dict]` | `list[TocEntry] -> list[TocEntry]` |
| `DocumentManager._note_toc` | `list[dict[str, Any]]` | `list[TocEntry]` |
| `DocumentManager._subtree_toc` | `dict[str, Any]` | `SubtreeToc` |
| `DocumentManager.get_toc` | `list[dict[str, Any]] \| dict[str, Any]` | `list[TocEntry] \| SubtreeToc` |
| `ReaderFacet.get_toc` | `list[dict[str, Any]] \| dict[str, Any]` | `list[TocEntry] \| SubtreeToc` |

Notes on construction:
- `FTSIndex.get_toc` builds `TocEntry(heading=row["heading"], level=row["heading_level"])` in its list comprehension.
- `FTSIndex.get_subtree_toc` builds `SubtreeNote(path=…, title=…, headings=[TocEntry(...)])` with **raw** headings (no synthetic H1) — contract unchanged, just typed. The existing "manager prepends the synthetic H1" docstring note stays accurate.
- `DocumentManager._prepend_title_h1` takes/returns `list[TocEntry]`; the dedup predicate becomes `not (h.level == 1 and h.heading == title)` (attribute access instead of subscript).
- `DocumentManager._subtree_toc` assembles `SubtreeToc(path=prefix, notes=[SubtreeNote(path=n.path, title=n.title, headings=self._prepend_title_h1(n.title, n.headings)) for n in notes_raw], truncated=truncated)`.
- The `max_notes < 1` / `max_level < 1` guards in `get_toc` are unchanged.

## Serialization boundary

A single shared helper converts the dataclass union to the JSON-able dict shape (mirrors the existing `[asdict(r) for r in results]` convention for `get_similar`):

**New `src/markdown_vault_mcp/utils/serialization.py`:**
```python
from dataclasses import asdict
from typing import Any
from markdown_vault_mcp.types import SubtreeToc

def toc_payload(data: "list[TocEntry] | SubtreeToc") -> list[dict[str, Any]] | dict[str, Any]:
    """Convert a get_toc result (note list or folder SubtreeToc) to its JSON-able dict form."""
    if isinstance(data, SubtreeToc):
        return asdict(data)
    return [asdict(e) for e in data]
```
(Exact imports/typing finalized in the plan; `asdict` recurses nested dataclasses, so `SubtreeToc` → fully-nested dicts in one call.)

Consumers call it right before serialization:
- **Tool** (`_server_tools/reader.py` `get_toc`): `payload = toc_payload(data)` then `return _staleness_result(vault, payload, drained_on_request=…, gen_before=…, force_result_wrap=True)`. The tool's return annotation stays `list[dict[str, Any]] | dict[str, Any]` (still a union ⇒ `force_result_wrap=True` is still required, unchanged). `data` comes from `vault.reader.get_toc` and is now typed `list[TocEntry] | SubtreeToc`.
- **Resource** (`_server_resources.py` `vault_toc`): `payload = toc_payload(toc)` then `json.dumps(payload)` (was `json.dumps(toc)`).

## Error handling

No new error paths. `ValueError` (missing note / invalid folder / `max_* < 1`) and `IndexUnavailableError` (cold index) are unchanged — raised before any dataclass is constructed.

## Testing

The dataclass change moves the **library** return type from dict to dataclass; the **wire/MCP** output is unchanged. So:

- **Library-level tests** (`test_fts_subtree_toc.py`, `test_managers_document.py`, `test_facet_reader.py`): assertions comparing to raw dict literals (`== [{"heading": "Gamma", "level": 1}]`, `in`-membership of dict literals, `result["path"]`, `n["path"]`, `headings.index({...})`) update to the dataclass forms (`== [TocEntry("Gamma", 1)]`, `SubtreeToc`/`SubtreeNote` attribute access). `@dataclass` `__eq__` makes equality assertions clean. The `isinstance(result, dict)` narrowing asserts become `isinstance(result, SubtreeToc)`.
- **MCP-level tests** (`test_server.py`): assert on `_parse_tool_data(result)` / the resource JSON — both dict-shaped after `toc_payload`. These stay **unchanged** (regression guard that the wire shape didn't move). Keep the existing `structured_content == {"result": data}` assertions.
- **New test** for `utils/serialization.py:toc_payload`: a `SubtreeToc` round-trips to the exact nested dict; a `list[TocEntry]` round-trips to a list of dicts. One small unit test file or a section in an existing utils test.
- Full suite green; `mypy` now type-checks the construction sites (the point of the change); patch coverage ≥ 80% on changed files.

## Files touched

- `src/markdown_vault_mcp/types.py` — three dataclasses
- `src/markdown_vault_mcp/utils/serialization.py` — new `toc_payload` helper
- `src/markdown_vault_mcp/fts_index.py` — typed returns + construction
- `src/markdown_vault_mcp/managers/document.py` — typed returns + attribute access
- `src/markdown_vault_mcp/facets/reader.py` — typed return
- `src/markdown_vault_mcp/_server_tools/reader.py` — `toc_payload` at the tool boundary
- `src/markdown_vault_mcp/_server_resources.py` — `toc_payload` at the resource boundary
- `tests/test_fts_subtree_toc.py`, `tests/test_managers_document.py`, `tests/test_facet_reader.py` — dataclass assertions
- `tests/` — new `toc_payload` unit test
- `docs/design.md` — one-line note on the named TOC types

## Notes

- The `__init__.py` is PEP-562 lazy and import-light (#665); the new dataclasses live in `types.py` and are imported where needed, not surfaced at the package root — no change to the lazy root.
- `utils/` currently holds `text.py` and `links.py`; `serialization.py` is a sibling with one clear responsibility.
