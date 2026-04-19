# LLM-Native Flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one new builtin MCP prompt (`propose-links`), refresh top-level documentation to surface ambient LLM workflows as first-class examples, and improve tool docstrings so the model can proactively suggest composable patterns. Closes [#386](https://github.com/pvliesdonk/markdown-vault-mcp/issues/386) and [#387](https://github.com/pvliesdonk/markdown-vault-mcp/issues/387).

**Architecture:** One file addition under `src/markdown_vault_mcp/static/prompts/`, one-line registration in `_server_prompts.py`, short-line docstring additions to eight tools in `_server_tools.py`, and prose edits to README + seven documentation files. No schema changes; no new tools; no new tests beyond the single prompt-registration test.

**Tech Stack:** Python 3.10+, FastMCP, mkdocs-material. Tests use `pytest` with `fastmcp.Client`.

**Reference spec:** [`docs/superpowers/specs/2026-04-19-llm-native-flows-design.md`](../specs/2026-04-19-llm-native-flows-design.md)

---

## Task 1: Add the `propose-links` builtin prompt

**Files:**
- Create: `src/markdown_vault_mcp/static/prompts/propose-links.md`
- Modify: `src/markdown_vault_mcp/_server_prompts.py:332` (the `for md_name in [...]` list)
- Modify: `tests/test_prompts.py` (add assertion in `test_all_builtins_present_without_prompts_folder` plus a new focused test)

- [ ] **Step 1: Write the failing test — assert `propose-links` is in the builtin set**

Add to `tests/test_prompts.py` inside `class TestNoPromptsFolder` at the end of `test_all_builtins_present_without_prompts_folder`:

```python
    async def test_all_builtins_present_without_prompts_folder(self) -> None:
        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        # Read-only mode: these built-ins should be present
        assert "summarize" in names
        assert "related" in names
        assert "compare" in names
        assert "propose-links" in names
```

The existing test already checks three other builtins; the new assertion extends it. This test covers registration; a separate test below covers description and arguments.

- [ ] **Step 2: Add a focused test for `propose-links` description and arguments**

Append this new class at the end of `tests/test_prompts.py`:

```python
class TestProposeLinks:
    """The propose-links builtin prompt is registered with the expected shape."""

    async def test_propose_links_registered(self) -> None:
        from markdown_vault_mcp.mcp_server import create_server

        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        propose_links = next(p for p in prompts if p.name == "propose-links")
        assert (
            propose_links.description is not None
            and "link" in propose_links.description.lower()
        )
        # Both arguments are optional (scope, per_note_limit).
        arg_names = {arg.name for arg in (propose_links.arguments or [])}
        assert arg_names == {"scope", "per_note_limit"}
        assert all(arg.required is False for arg in (propose_links.arguments or []))
```

If the `create_server` import pattern already exists elsewhere in the file (e.g., the `TestNoPromptsFolder` class imports it at module scope), move the import up to match the existing pattern rather than inlining inside the method. The existing file imports at the top; use the top-level import.

- [ ] **Step 3: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_prompts.py::TestNoPromptsFolder::test_all_builtins_present_without_prompts_folder tests/test_prompts.py::TestProposeLinks -v
```
Expected: both fail with `AssertionError` mentioning `propose-links` (the registered names set does not contain it yet).

- [ ] **Step 4: Create `src/markdown_vault_mcp/static/prompts/propose-links.md`**

```markdown
---
description: Propose meaningful new links between semantically-close notes that aren't already connected.
arguments:
  - name: scope
    description: "Candidate set. Accepts a folder path (e.g. '1-Projects'), the literal 'recent' (default; notes modified in the last 30 days), or the literal 'all'. No trailing slashes on folder paths."
    required: false
  - name: per_note_limit
    description: "Max candidates per note to evaluate (default 5)."
    required: false
tags:
  - write
icons: write
---

You are proposing meaningful new links between notes in a markdown vault. Focus on connections that a reader would genuinely benefit from, not notes that merely share vocabulary.

## Step 1: Resolve scope

Interpret `$scope`:

- Empty or `'recent'`: call `list_documents()`, then filter client-side for notes where `modified_at > <now - 30 * 86400>` (i.e., modified within the last 30 days). If the current Unix timestamp is not available, ask the user for today's date.
- `'all'`: call `list_documents()` unfiltered.
- Otherwise treat as a folder path. Strip any trailing `/` and call `list_documents(folder='<scope>')`.

If the resolved set contains more than 100 notes, pause and ask the user whether to proceed or narrow the scope. Above 100 notes, the subsequent steps become slow and noisy.

## Step 2: Gather candidates per note

For each note in scope:

1. Call `get_similar(path=<note path>, limit=$per_note_limit)` (default limit: 5).
2. If `get_similar` returns an empty list (no embeddings configured), fall back to `search(query=<note title>, mode='keyword', limit=$per_note_limit)` and drop the note itself from the result.

## Step 3: Filter out already-linked pairs

For each scanned note, call `get_outlinks(path=<note>)` once. For each candidate pair (A, B), drop the candidate if A already links to B — compare the candidate path against each outlink's `raw_target` (also consider the wikilink form, e.g., `[[B-title]]` without the `.md` extension).

## Step 4: Apply LLM judgment

For each surviving candidate pair, `read(path=<B>)` and inspect B's title, first section heading, and opening paragraph. Keep only pairs where the content justifies an explicit link — the reader of A would benefit from knowing about B in context, not merely because the two share words.

## Step 5: Pick direction and placement

For each kept pair:

- **Direction:** one-way `A → B` as a wikilink from A. `get_backlinks` recovers the reverse; explicit bidirectional wikilinks are redundant. Pick A as the note where the link reads most naturally: atomic → hub, specific → general, newer → older.
- **Placement:** pick per note shape, using judgment:
  - Short atomic note → new `## Related` section (create if missing).
  - Prose note → inline citation in a relevant paragraph.
  - MOC / hub note → append under an existing thematic bullet list.
  - Reference note → `## See Also` or footnote-style link.

Do not prescribe a fixed placement. The shape of the host note dictates where the link belongs.

## Step 6: Batch preview

Render a preview of every proposed edit, numbered. Example:

```
1. 1-Projects/migrate-postgres.md — inline citation after line 23: mention [[postgres-16-json-path]]
2. 3-Resources/distributed-consensus.md — new `## Related` section with [[raft-paper-notes]] and [[paxos-made-simple]]
3. 2-Areas/team-hiring.md — append to existing `## Related` list: [[interview-rubric]]
```

Ask the user: "Apply all N? Apply specific ones? Skip all?"

## Step 7: Execute on confirmation

For each approved edit:

- Prefer `edit(path=<A>, old_text=<current>, new_text=<current with link added>)` for small in-place insertions (inline citations, bullet-list appends).
- Use `write(path=<A>, content=<full body>, frontmatter=<current frontmatter>)` when the change restructures the note (new `## Related` section at the end of a long note, or any change easier to express as a full-body rewrite). Always `read` the current state first to preserve unmodified content.
- If a write fails (for example, `ConcurrentModificationError`), skip that edit, record the reason, and continue with the rest.

## Step 8: Summary

Report the counts:

- Links added
- Notes updated
- Pairs skipped (and the reason for each: already linked, rejected by LLM judgment, write conflict)

## Constraints

- Never write without explicit user confirmation. The batch preview in Step 6 is the only gating point; per-edit confirmation is available on request.
- Never propose self-links.
- Do not propose MOC-to-MOC links by default. Mention them in the preview as observations but do not include them as edits unless the user explicitly asks.
- Budget guard: if the scope resolves to more than 100 notes, stop and ask before proceeding.
- Graceful degradation: if neither `get_similar` nor `search` produces candidates for a note, skip it silently. Do not warn per-note.
```

- [ ] **Step 5: Register the prompt in `_server_prompts.py`**

Modify the list at `src/markdown_vault_mcp/_server_prompts.py:332`. Current:

```python
    for md_name in ["summarize", "research", "discuss", "related", "compare"]:
```

Change to:

```python
    for md_name in ["summarize", "research", "discuss", "related", "compare", "propose-links"]:
```

Do not touch the derived-variables logic at lines 254-264 — `propose-links` has no derived variables like `research`'s `topic_slug`. The existing pattern handles parameter passthrough without modification.

- [ ] **Step 6: Run both tests — expect pass**

Run:
```bash
uv run pytest tests/test_prompts.py::TestNoPromptsFolder::test_all_builtins_present_without_prompts_folder tests/test_prompts.py::TestProposeLinks -v
```
Expected: both PASS.

- [ ] **Step 7: Run the full prompt test file to catch regressions**

Run:
```bash
uv run pytest tests/test_prompts.py -x -q
```
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/markdown_vault_mcp/static/prompts/propose-links.md \
  src/markdown_vault_mcp/_server_prompts.py \
  tests/test_prompts.py
git commit -m "feat: add propose-links builtin prompt"
```

---

## Task 2: Improve tool docstrings to surface composable patterns

**Files:**
- Modify: `src/markdown_vault_mcp/_server_tools.py` (eight tools: `search`, `get_backlinks`, `get_outlinks`, `get_similar`, `get_context`, `write`, `delete`, `fetch`)

Each edit is a short sentence appended to the **Notes** or the trailing paragraph of the existing docstring — not a structural rewrite. Keep every addition to one sentence. Do not touch signature or Args sections.

- [ ] **Step 1: Run the test suite to establish a green baseline**

Run:
```bash
uv run pytest -x -q
```
Expected: all tests pass (propose-links was added in Task 1).

- [ ] **Step 2: Update `search` docstring (line 83)**

Find the `search` docstring. It ends with a `Raises:` section documenting `ValueError`. Insert a new paragraph **after** the `Returns:` block and **before** the `Raises:` section:

```
        Also useful for finding merge candidates during triage — if a
        close match exists for a new capture, prefer merging over
        creating a near-duplicate.
```

If the docstring has no blank line before `Raises:`, add one. Match the 8-space indentation of the surrounding docstring body.

- [ ] **Step 3: Update `get_backlinks` docstring (line 371)**

Find the `get_backlinks` docstring. Append a one-sentence paragraph at the very end of the docstring body (before the closing `"""`):

```
        Combine with ``get_similar`` to find connection gaps — notes that are
        semantically close to the target but not yet linked.
```

Use the 8-space indentation used elsewhere in the file.

- [ ] **Step 4: Update `get_outlinks` docstring (line 414)**

Find the `get_outlinks` docstring. Append the same one-sentence paragraph at the end:

```
        Combine with ``get_similar`` to find connection gaps — notes that are
        semantically close to the target but not yet linked.
```

- [ ] **Step 5: Update `get_similar` docstring (line 497)**

Find the `get_similar` docstring. Append a one-sentence paragraph at the end:

```
        Useful for finding link candidates that aren't yet wikilinked — the
        vault's organic graph is almost always denser than its explicit one.
        See the ``propose-links`` prompt for a full vault-wide sweep.
```

- [ ] **Step 6: Update `get_context` docstring (line 580)**

Find the `get_context` docstring. Append a one-sentence paragraph at the end:

```
        The ``similar`` field in the response surfaces notes that may warrant
        explicit links to the context note but don't yet — a common input to
        manual or automated link proposal.
```

- [ ] **Step 7: Update `write` docstring (line 977)**

Find the `write` docstring. Append a one-sentence paragraph at the very end:

```
        Supports split (write several new notes from one source) and merge
        (extend an existing note with content from another) when composed with
        ``read`` and ``delete``.
```

- [ ] **Step 8: Update `delete` docstring (line 1134)**

Find the `delete` docstring. Append a one-sentence paragraph at the end:

```
        Typically called after a split or merge to remove the source note once
        its content has been relocated.
```

- [ ] **Step 9: Update `fetch` docstring (line 1237)**

Find the `fetch` docstring. Append a one-sentence paragraph at the end:

```
        Primary building block for URL-to-note capture flows: call ``fetch`` to
        retrieve the source, summarize via the LLM, and ``write`` the result
        as a new note.
```

- [ ] **Step 10: Verify lint and types still pass**

Run:
```bash
uv run ruff check --fix . && uv run ruff format . && uv run mypy src/
```
Expected: no lint, format, or type errors. Docstring edits should not affect any of these gates, but run them as a safety net.

- [ ] **Step 11: Run the test suite**

Run:
```bash
uv run pytest -x -q
```
Expected: all tests pass. No tests assert on docstring content; this is just a regression check.

- [ ] **Step 12: Verify MkDocs picks up the docstring changes**

Run:
```bash
uv run mkdocs build --strict 2>&1 | tail -10
```
Expected: clean build (same pre-existing INFO notices as before about `design.md` exclusion and `examples/` relative links). The rendered `docs/tools/index.md` page is regenerated from the Python source docstrings, so the new sentences will appear there automatically on site build.

- [ ] **Step 13: Commit**

```bash
git add src/markdown_vault_mcp/_server_tools.py
git commit -m "docs: add composable-pattern hints to tool docstrings"
```

---

## Task 3: Add "What you can do with it" teaser sections to README and docs/index.md

**Files:**
- Modify: `README.md` (insert new section)
- Modify: `docs/index.md` (insert shorter mirror)

- [ ] **Step 1: Locate the insertion point in `README.md`**

Read `README.md` and find the end of the features list or tool-count block that currently precedes the Installation section. Exact line depends on current content — grep for the Installation heading to find the boundary:

```bash
grep -n "^## Installation" README.md
```

Insert the new section immediately above that `## Installation` heading.

- [ ] **Step 2: Add the "What you can do with it" section to `README.md`**

Insert the following block immediately before `## Installation`:

```markdown
## What you can do with it

With this server mounted in Claude, you can:

- **Capture a URL as a note.** "Fetch <url>, summarize as a Resource note under `3-Resources/`, and link any existing notes on the topic." — Claude composes `fetch` + `search` + `write`.
- **Research a topic into your vault.** "Research product security regulations, compare them, and create a set of interlinked notes — one per regulation, plus a map-of-content." — Claude composes web-search tools (client-side) + `write` with wikilinks.
- **Distill today's thinking.** "Summarize today's conversations into Inbox notes." — Claude.ai only; uses `conversation_search` + `recent_chats` + `write`. The [`para-capture-chats`](examples/para/prompts/para-capture-chats.md) prompt is the one-click version.
- **Find missing links.** Fire the [`propose-links`](https://pvliesdonk.github.io/markdown-vault-mcp/prompts/#propose-links) prompt from the `+` menu — it scans recently-modified notes, proposes meaningful connections, and writes them on confirmation.
- **Split or merge captures.** "Split this Inbox note into two." / "Merge this into `<existing note>` instead of duplicating." — Claude composes `read` + `write` + `delete`.

No external scheduler, no separate capture app — the vault sits behind your conversations and absorbs their output.
```

- [ ] **Step 3: Locate the insertion point in `docs/index.md`**

Read `docs/index.md` and find the main features bullet list (early in the file, before the deployment scenarios table). The mirror goes immediately after that list.

Grep the file to pick a stable anchor — the exact heading depends on current state:

```bash
grep -n "^##" docs/index.md | head -10
```

Insert the shorter mirror before the second major section heading (often `## Installation` or `## Deployment scenarios`).

- [ ] **Step 4: Add the shorter mirror to `docs/index.md`**

Insert the following block immediately before the second-section heading identified in Step 3:

```markdown
## What you can do with it

A few flows the server enables with an LLM on top — none of these require a bespoke prompt:

- **"Fetch <url> and summarize into a Resource note."** Claude composes `fetch` + `search` + `write`.
- **"Research <topic> and create a set of interlinked notes."** Claude composes web tools + `write` with wikilinks.
- **"Summarize today's conversations into Inbox notes."** Claude.ai composes `conversation_search` + `recent_chats` + `write`; the [`para-capture-chats`](guides/para.md#using-the-para-prompts) prompt is the one-click version.
- **Find missing links.** The [`propose-links`](prompts.md#propose-links) builtin prompt scans recently-modified notes and proposes meaningful connections.

See [MCP Prompts](prompts.md) for the codified workflows and the ambient-pattern reference.
```

- [ ] **Step 5: Verify MkDocs build is clean**

Run:
```bash
uv run mkdocs build --strict 2>&1 | tail -10
```
Expected: build succeeds; any new INFO notices (e.g., unresolvable link anchors) should be investigated and fixed. Anchors like `#propose-links` on `prompts.md` will work once Task 4 adds that heading; verify Task 4 writes the heading text exactly as `## `propose-links`` so this anchor resolves.

If `--strict` fails due to the unresolvable `#propose-links` anchor, reorder: complete Task 4 first, then redo Step 5. Alternatively, use a non-anchor link for now (`[propose-links](prompts.md)`) and refine in Task 4. The subagent should choose one and proceed.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/index.md
git commit -m "docs: add 'what you can do with it' teaser to README and docs index"
```

---

## Task 4: Extend `docs/prompts.md` with `propose-links` entry, ambient-patterns section, and invocation-mechanics section

**Files:**
- Modify: `docs/prompts.md`

- [ ] **Step 1: Add `propose-links` to the Quick Reference table**

Find the table at the top of `docs/prompts.md`. Add a new row after the existing `related` row (preserve alphabetic-by-name ordering isn't followed in the current table — match the existing ordering which seems roughly read-then-write). Insert just before the closing of the table body. The updated table should contain, in order: `summarize`, `research`, `discuss`, `create_from_template`, `related`, `compare`, `propose-links`. The new row:

```markdown
| [`propose-links`](#propose-links) | `scope`, `per_note_limit` (both optional) | Write | Propose meaningful new links between semantically-close notes that aren't already connected |
```

- [ ] **Step 2: Add the `## `propose-links`` section**

Append a new section at the end of the per-prompt listings (after the `compare` section). Exact content:

```markdown
## `propose-links`

Scan a bounded set of notes for semantically close pairs that aren't already linked, apply LLM judgment to keep only the meaningful connections, and write them to the vault on confirmation.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `scope` | string \| null | Candidate set: a folder path (e.g. `"1-Projects"`), the literal `"recent"` (default; notes modified in the last 30 days), or the literal `"all"`. No trailing slashes. |
| `per_note_limit` | integer \| null | Max candidates per note to evaluate. Defaults to 5. |

**Workflow:**

1. Resolve `scope` to a list of notes, warning if more than 100 notes match.
2. For each note, gather candidates via `get_similar` (with a `search(mode='keyword')` fallback when embeddings aren't configured).
3. Filter out pairs where A already links to B (via `get_outlinks`).
4. LLM judgment: read each candidate's title and opening to confirm the connection is meaningful, not merely lexical.
5. Pick direction (one-way `A → B`) and placement per note shape (inline citation, `## Related` section, hub bullet list, footnote — whichever fits).
6. Show a batch preview of every proposed edit.
7. Write approved edits; skip failures (e.g., `ConcurrentModificationError`) and report reasons.

!!! note "Write prompt"
    This prompt modifies documents and is only available when `READ_ONLY=false`.

!!! note "Embeddings recommended"
    `propose-links` falls back to keyword search without embeddings, but the quality of candidates is noticeably better when `get_similar` is available. See [Embeddings](guides/embeddings.md) for setup.
```

- [ ] **Step 3: Add the "Ambient patterns without prompts" section**

Append after the `propose-links` section:

```markdown
## Ambient patterns without prompts

Not every LLM-native workflow needs a codified MCP prompt. With a capable model and the server's tools, several high-value flows work from prose intent alone. The examples below document these composable patterns — which tools the model orchestrates and why each pattern doesn't need its own prompt.

### Capture a URL as a note

> "Fetch https://example.com/article, summarize as a Resource note under `3-Resources/`, and link any existing notes on the topic."

**Tools composed:** `fetch` → LLM summarization → `search` (to find related existing notes) → `write` (with frontmatter and wikilinks).

**Why no codified prompt:** the only knob is the target folder, and the user expresses it in the ask. No structure worth pre-specifying.

### Research a topic into interlinked notes

> "Research product security regulations, compare the major frameworks, and create a set of interlinked notes — one per regulation, plus a map-of-content."

**Tools composed:** client-side web search → LLM synthesis → multiple `write` calls with `[[wikilinks]]` connecting the resulting notes.

**Why no codified prompt:** modern LLMs cross-link naturally when asked for "a set of interlinked notes." The single-note version (`research` prompt) handles the simpler case where one note is enough.

### Distill conversations into Inbox notes

> "Summarize today's conversations into Inbox notes — one per topic."

**Tools composed:** `conversation_search` + `recent_chats` (Claude.ai client-side) → LLM distillation → `write` per topic.

**Why (partially) codified:** the [`para-capture-chats`](guides/para.md#using-the-para-prompts) prompt exists as the one-click version because it has platform-specific tool names to call out and constraints on what to skip (pure Q&A, debugging). Outside the PARA pack, the ambient ask works fine.

### Split and merge captures

> "Split this Inbox note into two — one for the Postgres upgrade, one for the CRA compliance work."
>
> "Merge this into `3-Resources/distributed-consensus.md` instead of creating a duplicate."

**Tools composed:** `read` + `search` (to find merge target) + `write` (new notes or extended target) + `delete` (the source note).

**Why no codified prompt:** the split/merge heuristic is codified inside `para-triage` where it's most useful. Outside triage, the ambient ask is a direct one-sentence instruction.

### Ad-hoc link proposal for a single note

> "Get the context for `1-Projects/migrate-postgres.md` and identify any notes we haven't linked yet."

**Tools composed:** `get_context` (surfaces the `similar` field) → LLM filtering → `edit` or `write` to add selected links.

**Why no codified prompt:** [`related`](#related) covers the find-candidates case read-only; the per-note write case is a natural extension and doesn't add enough structure to warrant a standalone prompt. The vault-wide sweep *is* codified, as [`propose-links`](#propose-links).
```

- [ ] **Step 4: Add the "How to invoke" section**

Append after the "Ambient patterns without prompts" section:

```markdown
## How to invoke prompts

Three invocation affordances, roughly in order of convenience:

### 1. Claude.ai: the `+` menu (recommended)

On Claude.ai, once the server is added as a connector, every prompt appears in the compose area's `+` menu. Click `+`, select **connectors**, pick the server, pick a prompt — Claude opens with the invocation scaffolded. No typing; no remembering argument names.

This is the best UX for frequent prompts (`propose-links`, `summarize`, the PARA / Zettelkasten workflow prompts).

### 2. Claude Code: the `/` menu

In Claude Code, MCP prompts appear in the slash-command menu after the server is configured in the workspace's MCP settings. Same effect as the Claude.ai `+` menu — the prompt is pre-scaffolded.

### 3. Plain conversation

Every prompt can be invoked from prose ("use the propose-links prompt with scope='1-Projects'"). The model resolves the name and calls the prompt. This is the fallback — more typing, but works in any MCP client.

The ambient-pattern flows above *only* use plain conversation; they don't have a prompt name to invoke. The trade-off: no menu shortcut, but no prompt to maintain either.
```

- [ ] **Step 5: Verify MkDocs build is clean with the new anchors**

Run:
```bash
uv run mkdocs build --strict 2>&1 | tail -10
```
Expected: build succeeds. The anchors `#propose-links`, `#ambient-patterns-without-prompts`, `#how-to-invoke-prompts` should exist and match any links from Task 3 (`docs/index.md`) and Tasks 5–7.

- [ ] **Step 6: Commit**

```bash
git add docs/prompts.md
git commit -m "docs: add propose-links entry, ambient patterns, and invocation sections to prompts.md"
```

---

## Task 5: Update `docs/guides/zettelkasten.md` — connector menu, split/merge tip, cross-link

**Files:**
- Modify: `docs/guides/zettelkasten.md`

- [ ] **Step 1: Locate the Zettelkasten prompt section**

Find the "Using the Zettelkasten Prompt" section (approximately line 395-420 based on current content). The connector-menu paragraph goes right after the description of how to invoke the prompt.

- [ ] **Step 2: Add the connector-menu paragraph**

After the existing explanation of how to run the Zettelkasten prompt via `PROMPTS_FOLDER`, insert the following paragraph:

```markdown
**Fire the prompt from Claude.ai's `+` menu.** Once the server is added as a connector on Claude.ai, every MCP prompt — including `zettelkasten` — appears in the compose area's `+` menu. Click `+`, select **connectors**, pick the server, pick the prompt. Claude opens with the invocation scaffolded, so you don't need to remember the arguments. See [How to invoke prompts](../prompts.md#how-to-invoke-prompts) for other clients.
```

- [ ] **Step 3: Find the existing Tips section**

Find the "Tips and Best Practices" section (near the end of the file, before Next Steps). The new split/merge tip goes after the existing "Tag for grouping, not taxonomy" tip.

- [ ] **Step 4: Add a split/merge tip**

Insert this new subsection after the last existing tip and before the Next Steps section:

```markdown
### Let Claude split or merge fleeting notes

Two shape operations that an LLM handles cleanly but manual workflows usually skip:

- **Split.** When a fleeting note contains two ideas — one literature reference and one nascent permanent claim — ask Claude to split it into two notes. Each is then developed independently.
- **Merge.** When a fleeting note restates or extends an existing permanent note, ask Claude to merge it (add as a new paragraph or `## Extension` section) rather than letting near-duplicates accumulate. The [`search`](../tools/index.md#search) + `read` + `write` + `delete` composition handles this in a single prompt turn.

Resist pre-splitting or pre-merging before review. Claude does both in one pass.
```

- [ ] **Step 5: Add an ambient-patterns cross-link to Next Steps**

Find the "Next Steps" section at the end of the file. It currently contains three or four bullet items. Append this bullet at the end:

```markdown
- **Ambient patterns**: [`docs/prompts.md`](../prompts.md#ambient-patterns-without-prompts) — flows the LLM handles from prose alone (URL capture, research, split/merge, ad-hoc link proposal)
```

- [ ] **Step 6: Verify MkDocs build is clean**

Run:
```bash
uv run mkdocs build --strict 2>&1 | tail -10
```
Expected: clean build; no new broken-link warnings.

- [ ] **Step 7: Commit**

```bash
git add docs/guides/zettelkasten.md
git commit -m "docs(zettelkasten): add connector + menu, split/merge tip, ambient-patterns cross-link"
```

---

## Task 6: Update `docs/guides/para.md` — cross-link to ambient patterns

**Files:**
- Modify: `docs/guides/para.md`

The PARA guide already covers connector menu, split/merge, and the PARA prompts. Only the ambient-patterns cross-link needs adding.

- [ ] **Step 1: Find the Next Steps section**

Open `docs/guides/para.md` and find the Next Steps section at the end of the file.

- [ ] **Step 2: Append the ambient-patterns cross-link**

Append this bullet at the end of the Next Steps list:

```markdown
- **Ambient patterns**: [`docs/prompts.md`](../prompts.md#ambient-patterns-without-prompts) — flows the LLM handles from prose alone (URL capture, research, ad-hoc link proposal)
```

- [ ] **Step 3: Verify MkDocs build is clean**

Run:
```bash
uv run mkdocs build --strict 2>&1 | tail -10
```
Expected: clean build.

- [ ] **Step 4: Commit**

```bash
git add docs/guides/para.md
git commit -m "docs(para): cross-link to ambient patterns section"
```

---

## Task 7: Add connector-menu paragraph to other guides

**Files:**
- Modify: `docs/guides/claude-desktop.md`
- Modify: `docs/guides/obsidian-everywhere.md`
- Modify: `docs/guides/mcp-apps.md`

Three small, identical edits. The target location in each file is "wherever invocation of tools or prompts is first discussed" — typically after the initial setup steps.

- [ ] **Step 1: Identify the insertion point in `docs/guides/claude-desktop.md`**

Grep for invocation-related headings:

```bash
grep -n "^##" docs/guides/claude-desktop.md | head -20
```

Pick the heading most closely related to "invoking prompts" or "using the server from Claude Desktop." If the file doesn't have a dedicated invocation section, insert after the "Verify" / "Test" section near the end.

- [ ] **Step 2: Add the connector-menu paragraph to `docs/guides/claude-desktop.md`**

Insert this new subsection at the chosen insertion point:

```markdown
### Firing prompts from Claude.ai's `+` menu

This guide covers Claude Desktop. If you also use Claude.ai with the same server, its prompts can be fired from the compose area's `+` menu once the server is added as a connector. Click `+`, select **connectors**, pick the server, pick a prompt — Claude opens with the prompt scaffolded. See [How to invoke prompts](../prompts.md#how-to-invoke-prompts).
```

- [ ] **Step 3: Identify the insertion point in `docs/guides/obsidian-everywhere.md`**

Grep for section headings:

```bash
grep -n "^##" docs/guides/obsidian-everywhere.md | head -20
```

This guide likely has a "Claude.ai" or "Using with Claude" section — insert the paragraph there. If not, insert near the end before any closing Next-Steps block.

- [ ] **Step 4: Add the connector-menu paragraph to `docs/guides/obsidian-everywhere.md`**

Insert the same subsection (adjust surrounding heading level to match the rest of the file — likely `###`):

```markdown
### Firing prompts from Claude.ai's `+` menu

When Claude.ai is part of your setup, every MCP prompt this server exposes can be fired from the compose area's `+` menu once the server is added as a connector. Click `+`, select **connectors**, pick the server, pick a prompt — Claude opens with the invocation scaffolded. This is the recommended way to invoke multi-step prompts like `propose-links` or the PARA / Zettelkasten workflow prompts. See [How to invoke prompts](../prompts.md#how-to-invoke-prompts) for the full invocation reference.
```

- [ ] **Step 5: Identify the insertion point in `docs/guides/mcp-apps.md`**

Grep for section headings:

```bash
grep -n "^##" docs/guides/mcp-apps.md | head -20
```

Insert the paragraph where the guide first discusses invoking server functionality (tools, resources, or prompts from the client side).

- [ ] **Step 6: Add the connector-menu paragraph to `docs/guides/mcp-apps.md`**

Insert the same subsection:

```markdown
### Firing prompts from Claude.ai's `+` menu

MCP Apps surface interactive views, but this server also ships MCP *prompts* — `summarize`, `research`, `propose-links`, and the workflow prompts from the PARA and Zettelkasten packs. On Claude.ai, every prompt appears in the compose area's `+` menu once the server is added as a connector. Click `+`, select **connectors**, pick the server, pick a prompt — Claude opens with the invocation scaffolded. See [How to invoke prompts](../prompts.md#how-to-invoke-prompts).
```

- [ ] **Step 7: Verify MkDocs build is clean**

Run:
```bash
uv run mkdocs build --strict 2>&1 | tail -15
```
Expected: clean build, no new warnings about broken anchors.

- [ ] **Step 8: Commit**

```bash
git add docs/guides/claude-desktop.md docs/guides/obsidian-everywhere.md docs/guides/mcp-apps.md
git commit -m "docs: add Claude.ai connector + menu paragraph to three guides"
```

---

## Task 8: End-to-end verification and issue closure

**Files:** (none modified directly)

- [ ] **Step 1: Run full pre-commit**

Run:
```bash
uv run pre-commit run --all-files
```
Expected: all hooks pass (ruff, ruff format, mypy, vendored SPA, trailing whitespace, EOF, YAML, large files, JSON).

- [ ] **Step 2: Run the full test suite**

Run:
```bash
uv run pytest -x -q
```
Expected: all tests pass, including the new `TestProposeLinks` class and the updated `test_all_builtins_present_without_prompts_folder`.

- [ ] **Step 3: Run MkDocs `--strict`**

Run:
```bash
uv run mkdocs build --strict 2>&1 | tail -20
```
Expected: clean build. The only remaining INFO notices should be the pre-existing ones (`design.md` excluded, `examples/` relative links in both zettelkasten and para guides, the `oidc-providers.md` anchor).

Sanity-check that the ambient-patterns anchor resolves: open `site/guides/zettelkasten/index.html` in a browser and click the "Ambient patterns" Next-Steps link; it should jump to the right place in `site/prompts/index.html`. If any internal link fails, fix it in the originating file.

- [ ] **Step 4: Smoke-test `propose-links` against a seed vault**

Set up a minimal vault and verify the prompt is callable:

```bash
mkdir -p /tmp/propose-vault/Notes
cat > /tmp/propose-vault/Notes/a.md <<'EOF'
---
title: "Distributed consensus"
tags: [systems]
---

# Distributed consensus

Overview of consensus algorithms in distributed systems.
EOF

cat > /tmp/propose-vault/Notes/b.md <<'EOF'
---
title: "Raft paper notes"
tags: [systems, reading]
---

# Raft paper notes

Notes from Ongaro and Ousterhout's Raft paper.
EOF

MARKDOWN_VAULT_MCP_SOURCE_DIR=/tmp/propose-vault \
MARKDOWN_VAULT_MCP_READ_ONLY=false \
uv run python -c "
import asyncio
from fastmcp import Client
from markdown_vault_mcp.mcp_server import create_server

async def main():
    server = create_server()
    async with Client(server) as client:
        prompts = await client.list_prompts()
        names = sorted(p.name for p in prompts)
        print('Registered prompts:', names)
        assert 'propose-links' in names
        result = await client.get_prompt('propose-links', {'scope': 'Notes'})
        print('Prompt body length:', sum(len(m.content.text) for m in result.messages if hasattr(m.content, 'text')))

asyncio.run(main())
"
rm -rf /tmp/propose-vault
```

Expected: the script prints the registered prompts list (includes `propose-links`), invokes `get_prompt('propose-links', {'scope': 'Notes'})`, and prints a non-zero body length. If the body length is zero, the substitution is broken.

- [ ] **Step 5: Review the branch commit history**

Run:
```bash
git log --oneline main..HEAD
```
Expected: seven commits from Tasks 1–7 (the verification task has no commit).

- [ ] **Step 6: No commit for this task**

This task is verification-only. If any step failed, diagnose, fix in the relevant source file, amend the appropriate task's commit (or create a fix-up commit with an explicit message referencing what was missed), and re-run the entire verification sequence from Step 1.

- [ ] **Step 7: Note issue closures for the PR description**

When opening the PR, include in the body:

```
Closes #386
Closes #387
```

so both issues auto-close on merge.

---

## Out of Scope (Future Work — do not implement now)

- Similarity-score threshold knob for `propose-links` (currently relies on LLM judgment over `get_similar`'s top-N; a cosine-threshold parameter would give deterministic-ish filtering at a tuning cost).
- Scheduled / periodic execution of `propose-links` (cron, systemd timer, or in-server scheduler).
- Dedicated prompts for split, merge, URL capture, research, or summarize-URL. These are ambient in the current design; revisit only if real usage shows the prose form is too verbose.
- Restructuring `docs/prompts.md` into multiple pages. Current size is manageable; revisit after two or three more prompts are added.
