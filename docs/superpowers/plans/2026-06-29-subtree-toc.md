# Folder/subtree TOC + per-note TOC tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a folder/subtree table-of-contents and expose the TOC as a tool (not just a resource), via one path-dispatched facet method (#773).

**Architecture:** A single `get_toc(path, *, max_level, max_notes)` method dispatches on the `.md` suffix: note paths keep today's flat `[{heading, level}]` output; folder paths return a nested-per-note object aggregated from the FTS `sections` table. The method is exposed through the existing `toc://vault/{path}` resource (defaults) and a new `get_toc` MCP tool (both knobs).

**Tech Stack:** Python 3.11+, SQLite FTS5, FastMCP, pytest, `uv`, `ruff`, `mypy`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-29-subtree-toc-design.md` (authoritative).
- Per-note shape is `{heading, level}` — **no** `text`/`anchor` fields are added.
- Folder match is on a path boundary via the precomputed `documents.folder`
  column: `folder = ? OR folder LIKE ? ESCAPE '\'` with `_escape_like(prefix)`,
  mirroring `FTSIndex.search` (`fts_index.py:1124-1129`). `Project` must never
  match `Projects/...`.
- Folder mode default `max_notes=200`; `max_level` default `None` (all levels).
- Empty/nonexistent folder → `{"path": ..., "notes": [], "truncated": False}`
  (no raise). Missing **note** → `ValueError` (unchanged).
- Gates (run before every commit that touches `src/`): `uv run ruff check --fix .`
  then `uv run ruff format .`, `uv run mypy src/ tests/`, `uv run pytest -x -q`.
- Docs ship in the same commit as the user-facing change (CLAUDE.md gate #5).
- Conventional commits.

---

### Task 1: FTS layer — `max_level` on `get_toc` + new `get_subtree_toc`

**Files:**
- Modify: `src/markdown_vault_mcp/fts_index.py` (`get_toc` ~line 1382; add `get_subtree_toc` adjacent to it)
- Test: `tests/test_fts_subtree_toc.py` (create)

**Interfaces:**
- Produces:
  - `FTSIndex.get_toc(self, path: str, *, max_level: int | None = None) -> list[dict[str, str | int]]`
  - `FTSIndex.get_subtree_toc(self, prefix: str, *, max_level: int | None = None, max_notes: int = 200) -> tuple[list[dict[str, Any]], bool]`
    where each list item is `{"path": str, "title": str, "headings": list[{"heading": str, "level": int}]}` (raw section headings, **no** synthetic H1), and the bool is `truncated`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fts_subtree_toc.py`:

```python
"""FTS-layer tests for subtree TOC aggregation and max_level filtering (#773)."""

from __future__ import annotations

from pathlib import Path

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import HeadingChunker, scan_directory


def _build_fts(root: Path) -> FTSIndex:
    fts = FTSIndex(db_path=":memory:")
    for note in scan_directory(root):
        fts.upsert_note(note)
    fts.resolve_vault_wikilinks()
    return fts


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "alpha.md").write_text(
        "---\ntitle: Alpha\n---\n# Alpha\n\n## Goals\n\nx\n\n### Detail\n\ny\n",
        encoding="utf-8",
    )
    (tmp_path / "Projects" / "beta.md").write_text(
        "---\ntitle: Beta\n---\n# Beta\n\n## Plan\n\nz\n",
        encoding="utf-8",
    )
    sub = tmp_path / "Projects" / "sub"
    sub.mkdir()
    (sub / "gamma.md").write_text(
        "---\ntitle: Gamma\n---\n# Gamma\n\n## Nested\n\nq\n",
        encoding="utf-8",
    )
    # A sibling folder whose prefix shares a leading substring with "Projects".
    (tmp_path / "Projectile.md").write_text(
        "---\ntitle: Projectile\n---\n# Projectile\n",
        encoding="utf-8",
    )
    return tmp_path


def test_subtree_toc_is_recursive_and_path_ordered(vault: Path) -> None:
    fts = _build_fts(vault)
    notes, truncated = fts.get_subtree_toc("Projects")
    assert truncated is False
    assert [n["path"] for n in notes] == [
        "Projects/alpha.md",
        "Projects/beta.md",
        "Projects/sub/gamma.md",
    ]
    alpha = notes[0]
    assert alpha["title"] == "Alpha"
    # Raw section headings only — no synthetic H1 prepended at this layer.
    assert {"heading": "Goals", "level": 2} in alpha["headings"]
    assert {"heading": "Detail", "level": 3} in alpha["headings"]


def test_subtree_prefix_is_boundary_matched(vault: Path) -> None:
    fts = _build_fts(vault)
    notes, _ = fts.get_subtree_toc("Project")
    assert notes == []  # neither "Projects/..." nor "Projectile.md" match


def test_subtree_max_level_filters_headings(vault: Path) -> None:
    fts = _build_fts(vault)
    notes, _ = fts.get_subtree_toc("Projects", max_level=2)
    alpha = next(n for n in notes if n["path"] == "Projects/alpha.md")
    levels = {h["level"] for h in alpha["headings"]}
    assert levels <= {1, 2}  # H3 "Detail" dropped


def test_subtree_max_notes_truncates(vault: Path) -> None:
    fts = _build_fts(vault)
    notes, truncated = fts.get_subtree_toc("Projects", max_notes=2)
    assert truncated is True
    assert len(notes) == 2
    assert [n["path"] for n in notes] == ["Projects/alpha.md", "Projects/beta.md"]


def test_get_toc_max_level_filter(vault: Path) -> None:
    fts = _build_fts(vault)
    toc = fts.get_toc("Projects/alpha.md", max_level=2)
    assert all(h["level"] <= 2 for h in toc)
    assert {"heading": "Detail", "level": 3} not in toc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fts_subtree_toc.py -q`
Expected: FAIL — `get_subtree_toc` missing / `get_toc()` got unexpected keyword `max_level`.

- [ ] **Step 3: Add `max_level` to `get_toc`**

In `fts_index.py`, change the `get_toc` signature and SQL. Current method:

```python
    def get_toc(self, path: str) -> list[dict[str, str | int]]:
```

Replace the signature line and inject the level predicate:

```python
    def get_toc(
        self, path: str, *, max_level: int | None = None
    ) -> list[dict[str, str | int]]:
```

In its SQL, after `AND heading IS NOT NULL`, add the optional clause. Replace the
`execute(...)` call body so it reads:

```python
        level_clause = "" if max_level is None else "AND heading_level <= ?"
        params: list[object] = [path]
        if max_level is not None:
            params.append(max_level)
        cur = self._conn().execute(
            f"""
            SELECT heading, heading_level
            FROM sections
            WHERE document_id = (SELECT id FROM documents WHERE path = ?)
              AND heading IS NOT NULL
              {level_clause}
            GROUP BY heading, heading_level
            ORDER BY MIN(rowid)
            """,
            params,
        )
```

- [ ] **Step 4: Add `get_subtree_toc`**

Immediately after `get_toc`, add (carry the same `@_retry_on_locked` decorator
that the surrounding read methods use):

```python
    @_retry_on_locked
    def get_subtree_toc(
        self,
        prefix: str,
        *,
        max_level: int | None = None,
        max_notes: int = 200,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Return per-note headings for every document under a folder prefix.

        Matches documents whose ``folder`` equals *prefix* or is a descendant
        of it (boundary match, so ``"Project"`` never matches ``"Projects"``).

        Args:
            prefix: Folder path with no trailing slash (e.g. ``"Projects"``).
            max_level: If set, drop headings with ``level`` greater than this.
            max_notes: Cap on distinct notes returned (default 200).

        Returns:
            ``(notes, truncated)`` where *notes* is a list of
            ``{"path", "title", "headings": [{"heading", "level"}]}`` ordered
            by path, *headings* are raw section headings (no synthetic H1),
            and *truncated* is True when more than ``max_notes`` notes matched.
        """
        escaped = _escape_like(prefix)
        doc_rows = self._conn().execute(
            """
            SELECT id, path, title
            FROM documents
            WHERE (folder = ? OR folder LIKE ? ESCAPE '\\')
            ORDER BY path ASC
            LIMIT ?
            """,
            (prefix, escaped + "/%", max_notes + 1),
        ).fetchall()
        truncated = len(doc_rows) > max_notes
        doc_rows = doc_rows[:max_notes]
        if not doc_rows:
            return [], truncated

        ids = [row["id"] for row in doc_rows]
        placeholders = ",".join("?" * len(ids))
        level_clause = "" if max_level is None else "AND heading_level <= ?"
        params: list[object] = [*ids]
        if max_level is not None:
            params.append(max_level)
        section_rows = self._conn().execute(
            f"""
            SELECT document_id, heading, heading_level
            FROM sections
            WHERE document_id IN ({placeholders})
              AND heading IS NOT NULL
              {level_clause}
            GROUP BY document_id, heading, heading_level
            ORDER BY document_id, MIN(rowid)
            """,
            params,
        ).fetchall()

        by_doc: dict[int, list[dict[str, Any]]] = {}
        for row in section_rows:
            by_doc.setdefault(row["document_id"], []).append(
                {"heading": row["heading"], "level": row["heading_level"]}
            )

        notes = [
            {
                "path": row["path"],
                "title": row["title"],
                "headings": by_doc.get(row["id"], []),
            }
            for row in doc_rows
        ]
        return notes, truncated
```

Confirm `Any` is imported in `fts_index.py` (it is — `get_backlinks` uses it).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_fts_subtree_toc.py -q`
Expected: PASS (5 tests). Then regression: `uv run pytest tests/test_fts_index_retry.py tests/test_managers_document.py -q` → PASS.

- [ ] **Step 6: Lint + typecheck + commit**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run mypy src/ tests/
git add src/markdown_vault_mcp/fts_index.py tests/test_fts_subtree_toc.py
git commit -m "feat: FTS get_subtree_toc + max_level filter on get_toc (#773)"
```

---

### Task 2: Library dispatch — `DocumentManager.get_toc` + `ReaderFacet.get_toc`

**Files:**
- Modify: `src/markdown_vault_mcp/managers/document.py` (`get_toc` ~line 495)
- Modify: `src/markdown_vault_mcp/facets/reader.py` (`get_toc` ~line 175)
- Test: `tests/test_managers_document.py` (extend `TestGetToc`), `tests/test_facet_reader.py`

**Interfaces:**
- Consumes: `FTSIndex.get_toc(path, *, max_level)`, `FTSIndex.get_subtree_toc(prefix, *, max_level, max_notes)` (Task 1); existing `self._validate_dir_path` (`document.py`).
- Produces:
  - `DocumentManager.get_toc(self, path: str, *, max_level: int | None = None, max_notes: int = 200) -> list[dict[str, Any]] | dict[str, Any]`
  - `ReaderFacet.get_toc(self, path: str, *, max_level: int | None = None, max_notes: int = 200) -> list[dict[str, Any]] | dict[str, Any]`
  - Folder-mode dict shape: `{"path": str, "notes": [{"path", "title", "headings": [{"heading", "level"}]}], "truncated": bool}`. Each note's `headings` has the title prepended as a synthetic H1 (deduped against an existing leading H1 equal to the title) — structurally identical to note mode.

- [ ] **Step 1: Write the failing tests**

In `tests/test_managers_document.py`, extend `class TestGetToc` (the `doc_vault`
fixture has `alpha.md`, `beta.md`, `sub/gamma.md` with `# Gamma` / `## Section One`):

```python
    def test_get_toc_max_level_note_mode(self, doc_mgr: DocumentManager) -> None:
        toc = doc_mgr.get_toc("sub/gamma.md", max_level=1)
        # Only the synthetic H1 title survives an H1-only cap.
        assert toc == [{"heading": "Gamma", "level": 1}]

    def test_get_toc_folder_mode_nested(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.get_toc("sub")
        assert isinstance(result, dict)
        assert result["path"] == "sub"
        assert result["truncated"] is False
        paths = [n["path"] for n in result["notes"]]
        assert paths == ["sub/gamma.md"]
        gamma = result["notes"][0]
        assert gamma["title"] == "Gamma"
        # Synthetic H1 prepended, body headings follow.
        assert gamma["headings"][0] == {"heading": "Gamma", "level": 1}
        assert {"heading": "Section One", "level": 2} in gamma["headings"]

    def test_get_toc_folder_trailing_slash(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.get_toc("sub/")
        assert result["path"] == "sub"
        assert [n["path"] for n in result["notes"]] == ["sub/gamma.md"]

    def test_get_toc_empty_folder_returns_empty(
        self, doc_mgr: DocumentManager
    ) -> None:
        result = doc_mgr.get_toc("does-not-exist")
        assert result == {"path": "does-not-exist", "notes": [], "truncated": False}

    def test_get_toc_folder_max_level(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.get_toc("sub", max_level=1)
        gamma = result["notes"][0]
        assert gamma["headings"] == [{"heading": "Gamma", "level": 1}]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_managers_document.py::TestGetToc -q`
Expected: FAIL — folder mode not implemented / unexpected keyword `max_level`.

- [ ] **Step 3: Implement dispatch in `DocumentManager.get_toc`**

Replace the existing `get_toc` body in `managers/document.py` with:

```python
    def get_toc(
        self,
        path: str,
        *,
        max_level: int | None = None,
        max_notes: int = 200,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Return a table of contents for a note or a folder subtree.

        When *path* ends in ``.md`` the result is a single note's flat
        outline: a list of ``{"heading", "level"}`` with the document title
        prepended as a synthetic H1. Otherwise *path* is treated as a folder
        prefix and the result is a nested-per-note object aggregating the
        subtree.

        Args:
            path: Note path (``"a/b.md"``) or folder prefix (``"a/b"``).
            max_level: If set, drop headings with ``level`` above this. The
                synthetic H1 title always survives ``max_level >= 1``.
            max_notes: Folder mode only — cap on distinct notes (default 200).

        Returns:
            Note mode: ``list[{"heading", "level"}]``.
            Folder mode: ``{"path", "notes": [...], "truncated": bool}`` where
            each note is ``{"path", "title", "headings": [...]}``.

        Raises:
            ValueError: Note mode, if no document exists at *path*; or if a
                folder *path* is empty/root or escapes the vault.
        """
        if path.endswith(".md"):
            return self._note_toc(path, max_level=max_level)
        return self._subtree_toc(path, max_level=max_level, max_notes=max_notes)

    def _note_toc(
        self, path: str, *, max_level: int | None
    ) -> list[dict[str, Any]]:
        self._validate_path(path)
        row = self._fts.get_note(path)
        if row is None:
            raise ValueError(f"Document not found: {path}")
        title: str = row["title"]
        headings = self._fts.get_toc(path, max_level=max_level)
        toc: list[dict[str, Any]] = [{"heading": title, "level": 1}]
        toc.extend(
            h for h in headings if not (h["level"] == 1 and h["heading"] == title)
        )
        return toc

    def _subtree_toc(
        self, path: str, *, max_level: int | None, max_notes: int
    ) -> dict[str, Any]:
        prefix = path.rstrip("/")
        self._validate_dir_path(prefix)
        notes_raw, truncated = self._fts.get_subtree_toc(
            prefix, max_level=max_level, max_notes=max_notes
        )
        notes: list[dict[str, Any]] = []
        for note in notes_raw:
            title = note["title"]
            headings: list[dict[str, Any]] = [{"heading": title, "level": 1}]
            headings.extend(
                h
                for h in note["headings"]
                if not (h["level"] == 1 and h["heading"] == title)
            )
            notes.append(
                {"path": note["path"], "title": title, "headings": headings}
            )
        return {"path": prefix, "notes": notes, "truncated": truncated}
```

Note: `_note_toc` keeps the synthetic H1 (level 1) regardless of `max_level`
because it is prepended *after* the FTS filter — matching the spec rule that the
title survives `max_level >= 1`.

- [ ] **Step 4: Forward kwargs in `ReaderFacet.get_toc`**

In `facets/reader.py`, replace the `get_toc` method:

```python
    def get_toc(
        self,
        path: str,
        *,
        max_level: int | None = None,
        max_notes: int = 200,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Return a table of contents for a note or a folder subtree.

        Note paths (ending in ``.md``) return a flat ``[{"heading", "level"}]``
        outline with the title as a synthetic H1. Folder paths return a
        nested-per-note object ``{"path", "notes", "truncated"}``. The result
        depends on the FTS index, so cold-start callers must build the index
        first (bucket 3).

        Args:
            path: Note path or folder prefix.
            max_level: Drop headings with ``level`` above this (both modes).
            max_notes: Folder mode cap on distinct notes (default 200).

        Returns:
            ``list`` for a note path, ``dict`` for a folder path.

        Raises:
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not been called.
            ValueError: Note path with no document; invalid folder path.
        """
        self._require_built()
        return self._doc_mgr.get_toc(path, max_level=max_level, max_notes=max_notes)
```

- [ ] **Step 5: Add a facet-level folder test**

In `tests/test_facet_reader.py`, near `test_get_toc_returns_list`:

```python
    def test_get_toc_folder_returns_dict(self, built: Vault) -> None:
        result = built.reader.get_toc("subfolder")
        assert isinstance(result, dict)
        assert "notes" in result and "truncated" in result
```

(The `built` vault indexes `tests/fixtures/`, which contains
`subfolder/nested.md` and `subfolder/deep/doc.md`.)

- [ ] **Step 6: Run tests + gates + commit**

Run: `uv run pytest tests/test_managers_document.py tests/test_facet_reader.py -q`
Expected: PASS.

```bash
uv run ruff check --fix . && uv run ruff format . && uv run mypy src/ tests/
git add src/markdown_vault_mcp/managers/document.py src/markdown_vault_mcp/facets/reader.py tests/test_managers_document.py tests/test_facet_reader.py
git commit -m "feat: path-dispatched get_toc (note + folder subtree) in library (#773)"
```

---

### Task 3: MCP resource extension + docstring fix + docs

**Files:**
- Modify: `src/markdown_vault_mcp/_server_resources.py` (`vault_toc` ~line 180-194)
- Modify: `docs/resources.md`
- Test: `tests/test_server.py` — class `TestResources` (~line 2201; existing `test_toc_resource` at ~2267)

**Interfaces:**
- Consumes: `ReaderFacet.get_toc(path)` (Task 2), now returning list **or** dict.
- The resource passes only defaults (no knobs).

- [ ] **Step 1: Write the failing test**

In `tests/test_server.py`, add to class `TestResources` (next to the existing
`test_toc_resource`, which uses `Client(make_server())` and `json` — both already
imported in this file). `tests/fixtures/` ships `subfolder/` with two notes:

```python
    async def test_toc_resource_folder_returns_nested(self) -> None:
        async with Client(make_server()) as client:
            result = await client.read_resource("toc://vault/subfolder")
            data = json.loads(result[0].text)
            assert isinstance(data, dict)
            assert "notes" in data and data["path"] == "subfolder"
```

(Match the `usefixtures` / decorators on the neighboring `TestResources` tests.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_server.py -q -k toc`
Expected: FAIL — folder path currently raises (note-only `get_toc`).

- [ ] **Step 3: Fix the resource docstring (no behavior change needed in body)**

The body already calls `vault.reader.get_toc(path)` and JSON-dumps the result,
so list-or-dict passes through unchanged. Only correct the inaccurate docstring.
Replace the `vault_toc` docstring:

```python
        """Table of contents for a note or a folder subtree.

        A note path (ending in ``.md``) returns a flat ordered list of
        ``{heading, level}`` headings (the title as a synthetic H1). A folder
        path returns a nested-per-note object
        ``{path, notes: [{path, title, headings}], truncated}`` aggregating the
        subtree (default cap 200 notes). Useful for navigating structure
        without reading full content. See the ``get_toc`` tool for the same
        data with ``max_level`` / ``max_notes`` controls.

        Index freshness is reported in _meta.index_stale.
        """
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_server.py -q -k toc`
Expected: PASS (folder + existing note cases).

- [ ] **Step 5: Update `docs/resources.md`**

Update the `toc://vault/{path}` entry to document both shapes (note → list;
folder → nested `{path, notes, truncated}` object) and cross-reference the new
`get_toc` tool. Match the surrounding entry format in that file.

- [ ] **Step 6: Gates + commit**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run mypy src/ tests/
git add src/markdown_vault_mcp/_server_resources.py docs/resources.md tests/test_server.py
git commit -m "feat: extend toc:// resource to folder subtree; fix docstring (#773)"
```

---

### Task 4: New `get_toc` MCP tool + registry tests + docs

**Files:**
- Modify: `src/markdown_vault_mcp/_server_tools/reader.py` (add tool after `get_similar`, ~line 558)
- Modify: `tests/test_server.py` (manifest list ~line 254; add tool behavior tests)
- Modify: `docs/tools/index.md`, `README.md`, `docs/design.md`
- Modify: `src/markdown_vault_mcp/_icons.py` only if a `get_toc` icon key is desired (optional — reuse `_TOOL_ICONS["read"]`)

**Interfaces:**
- Consumes: `ReaderFacet.get_toc(path, *, max_level, max_notes)` (Task 2); `_maybe_wait_for_drain`, `_staleness_result`, `needs_queryable`, `_TOOL_ICONS` (already imported in `reader.py`).
- Produces: MCP tool `get_toc(path, max_level=None, max_notes=200, wait_for_pending_writes=False)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_server.py`, add `"get_toc"` to the expected manifest list in
`test_register_tools_registers_exact_manifest` (alphabetical: after
`"get_similar"`, before `"git_sync"`):

```python
            "get_similar",
            "get_toc",
            "git_sync",
```

Then add a behavior test class (mirror the existing `Client(make_server())`
style used elsewhere in the file):

```python
class TestGetTocTool:
    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_get_toc_tool_note(self) -> None:
        async with Client(make_server()) as client:
            result = await client.call_tool("get_toc", {"path": "simple.md"})
            assert isinstance(result.data, list)
            assert result.data[0]["level"] == 1

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_get_toc_tool_folder(self) -> None:
        async with Client(make_server()) as client:
            result = await client.call_tool("get_toc", {"path": "subfolder"})
            assert isinstance(result.data, dict)
            assert "notes" in result.data and "truncated" in result.data
```

(Use the same `make_server` import / fixtures the other tool tests in this file
use. `tests/fixtures/` ships `simple.md` and `subfolder/`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_server.py -q -k "manifest or GetTocTool"`
Expected: FAIL — manifest mismatch / unknown tool `get_toc`.

- [ ] **Step 3: Implement the tool**

In `_server_tools/reader.py`, add after the `get_similar` tool block:

```python
    @mcp.tool(
        icons=_TOOL_ICONS["read"],
        annotations={
            "title": "Table of Contents",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    @needs_queryable()
    async def get_toc(
        path: str,
        max_level: int | None = None,
        max_notes: int = 200,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Heading outline for a single note or a whole folder subtree.

        If 'path' ends in '.md' it is a note: returns a flat ordered list of
        {heading, level} (the title as a synthetic H1). Otherwise 'path' is a
        folder: returns {path, notes, truncated} where 'notes' is an ordered
        list of {path, title, headings} aggregating every note under the
        subtree. Mirrors the 'toc://vault/{path}' resource, adding the
        max_level / max_notes controls below.

        Args:
            path: Note path ("a/b.md") or folder prefix ("a/b").
            max_level: Drop headings deeper than this level (e.g. 2 keeps
                H1-H2). The synthetic H1 title always survives. Default None
                returns all levels.
            max_notes: Folder mode only — cap on distinct notes (default 200).
                When more notes match, the first max_notes (by path) are
                returned and 'truncated' is True.
            wait_for_pending_writes: When True, wait until recent
                write/edit/delete/rename operations are applied to the index
                before answering. Default False answers from the current
                index; inspect '_meta.index_stale' to tell whether a write was
                still in flight. Bounded by a server timeout (default 60s).

        Returns:
            Note mode: list of {heading (str), level (int)}.
            Folder mode: {path (str), notes (list[{path, title, headings}]),
            truncated (bool)}. Empty/nonexistent folder → empty 'notes'.

            Index freshness is reported out-of-band in '_meta.index_stale'.

        Raises:
            ValueError: Note path with no document; invalid folder path.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_toc"
        )
        gen_before = vault.index.write_generation()
        data = await asyncio.to_thread(
            vault.reader.get_toc,
            path,
            max_level=max_level,
            max_notes=max_notes,
        )
        return _staleness_result(
            vault,
            data,
            drained_on_request=drained,
            gen_before=gen_before,
        )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_server.py -q -k "manifest or title or GetTocTool"`
Expected: PASS — manifest matches, `get_toc` has the unique title
"Table of Contents", both behavior tests pass.

- [ ] **Step 5: Update docs**

- `docs/tools/index.md`: add a `get_toc` entry (params `path`, `max_level`,
  `max_notes`, `wait_for_pending_writes`; both return shapes; cross-ref the
  `toc://vault/{path}` resource). Match the surrounding entry format.
- `README.md`: add `get_toc` to the tools list.
- `docs/design.md`: document the TOC surface (note + subtree), the path-dispatch
  rule, and the `max_notes` bound, in the reader/TOC section.

- [ ] **Step 6: Full gate run + commit**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run ruff format --check .
uv run mypy src/ tests/
uv run pytest -x -q
git add -A
git commit -m "feat: add get_toc MCP tool for note + subtree TOC (#773)"
```

---

### Task 5: Final verification + PR prep

- [ ] **Step 1: Patch-coverage check**

Run: `uv run pytest --cov=markdown_vault_mcp.fts_index --cov=markdown_vault_mcp.managers.document --cov=markdown_vault_mcp.facets.reader --cov=markdown_vault_mcp._server_tools.reader --cov-report=term-missing -q`
Expected: new lines exercised; patch coverage ≥ 80%. Add tests for any uncovered
branch (notably folder-with-headings-filtered and the truncation path).

- [ ] **Step 2: Pre-commit + preflight**

Run: `uv run pre-commit run --all-files` → green.
Then invoke the `preflight-circus` skill against `origin/main..HEAD` before pushing.

- [ ] **Step 3: Push + open PR**

```bash
git push -u origin feat/773-subtree-toc
```
Open PR with `Closes #773`, a summary of the new tool + extended resource, the
two return shapes, and the bounds. End the PR body with the required generation
trailer and the agent-attribution signature line.

---

## Self-Review

**Spec coverage:**
- Path dispatch (`.md` → note, else folder) — Tasks 1-2. ✓
- Note mode `{heading, level}` + `max_level` — Tasks 1-2. ✓
- Folder nested-per-note `{path, notes, truncated}`, recursive, path-ordered — Tasks 1-2. ✓
- Boundary prefix match (`Project` ≠ `Projects`) — Task 1 test. ✓
- Trailing slash normalized — Task 2 test. ✓
- `max_notes` cap + truncation flag — Tasks 1-2 tests. ✓
- Empty/nonexistent folder → empty, no raise — Task 2 test. ✓
- Resource extended + docstring fix — Task 3. ✓
- New `get_toc` tool, `needs_queryable`, staleness — Task 4. ✓
- Registry title + exact-manifest enforcement — Task 4. ✓
- Docs (resources, tools, README, design) — Tasks 3-4. ✓

**Placeholder scan:** No TBD/TODO; all code steps carry full code. Two
deliberate "match the surrounding file's style" instructions (resource test
import names, docs entry format) point at a single concrete file each — resolve
by reading that file, not by judgment.

**Type consistency:** `get_toc(path, *, max_level=None, max_notes=200)` and the
`list | dict` return are identical across FTS-helper boundary (Task 1 returns the
raw `(notes, truncated)` tuple; manager assembles the dict), manager, facet, and
tool. Folder dict keys (`path`, `notes`, `truncated`) and note dict keys
(`path`, `title`, `headings`) are used identically everywhere.
