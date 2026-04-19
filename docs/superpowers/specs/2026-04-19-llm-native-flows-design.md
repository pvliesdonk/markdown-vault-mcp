# LLM-Native Flows — Design

**Date:** 2026-04-19
**Status:** Approved design; plan pending
**Audience:** Contributors adding a new builtin prompt and refreshing docs to surface LLM-native workflows

## Summary

This change has three threads under one theme — **surface the LLM-native workflows this server already enables**:

1. Add one new builtin prompt, `propose-links`, that scans a bounded set of notes and proposes meaningful links between semantically-close pairs that aren't already connected.
2. Refresh the docs to showcase *ambient* LLM flows (URL capture, research into interlinked notes, conversation distillation, split/merge) as examples — without adding prompts for them, because the LLM handles them well from prose intent.
3. Tweak tool docstrings to surface composable patterns, so the model itself can propose these flows before the user asks.

Closes [#386](https://github.com/pvliesdonk/markdown-vault-mcp/issues/386) (connector `+` menu affordance) and [#387](https://github.com/pvliesdonk/markdown-vault-mcp/issues/387) (split/merge as generic note operations) as part of the agnostic docs work.

## Goals

1. Make it obvious to a first-time reader that this server enables workflows beyond "search and read markdown" — ambient Claude flows are the differentiator.
2. Ship exactly *one* new prompt, chosen because a vault-wide link-proposal sweep is genuinely non-obvious (the LLM won't do it proactively from prose intent alone).
3. Document the ambient patterns in prose, not codified prompts — avoid prompt sprawl for things the LLM already handles naturally.
4. Use tool docstrings as affordances for the model itself, not just reference material for humans.

## Non-Goals

- New prompts for URL summarization, external-doc ingestion, or research-into-interlinked-notes. These work well from conversational intent and don't benefit from codification.
- Example-pack-specific versions of `propose-links`. The prompt is pack-agnostic.
- Any server-side feature changes (no new tools, no new resources, no behavior changes to existing tools).

## Design Decision: Why Only One New Prompt

Prompts add value when one or more of the following is true:

1. **One-click `+` menu shortcut** — the flow is frequent enough that a menu item beats prose invocation.
2. **Non-obvious structure** — heuristics, thresholds, or sequencing that a casual "ask Claude" won't produce (e.g., staleness windows, bucket classification rules, filter-out-already-linked-pairs sweeps).
3. **Platform-specific tool dance** — the flow has to name specific client tools the LLM wouldn't guess (e.g., `conversation_search`, `recent_chats`).

By that test:

| Flow | Passes? | Reasoning |
|------|---------|-----------|
| `propose-links` | ✓ | Non-obvious sweep; LLM won't enumerate vault-wide, run `get_similar` per note, filter out already-linked pairs, and propose the remainder without being explicitly prompted to do so. |
| Capture today's chats (`para-capture-chats`, shipped in PR #385) | ✓ | Platform dance; frequent. |
| Summarize a URL into a note | ✗ | "Fetch <url>, summarize as a Resource note" works from prose. No structure worth codifying. |
| Research topic → interlinked notes | ✗ | Confirmed ambient: "research X, compare, create interlinked notes" works in Claude.ai without scaffolding. |
| Split / merge captures | ✗ (as standalone prompt) | Already codified inside `para-triage` Step 3. Generic version doesn't need its own prompt; surfaces via tool docstrings and documentation. |

## The `propose-links` Prompt

**File:** `src/markdown_vault_mcp/static/prompts/propose-links.md`
**Registration:** `src/markdown_vault_mcp/_server_prompts.py` — add `"propose-links"` to the list of builtin prompt names registered at startup (alongside `summarize`, `research`, `discuss`, `related`, `compare`).

### Frontmatter

```yaml
---
description: Propose meaningful new links between semantically-close notes that aren't already connected.
arguments:
  - name: scope
    description: "Candidate set. Accepts a folder path (e.g. '1-Projects'), the literal 'recent' (default; notes modified in the last 30 days), or the literal 'all'. No trailing slashes on folder paths."
    required: false
  - name: per_note_limit
    description: "Max candidates per note to evaluate (default 5)."
    required: false
tags: [write]
icons: write
---
```

### Body

1. **Resolve scope.** Interpret `$scope`:
   - Empty or `'recent'`: `list_documents()`, filter client-side on `modified_at > now - 30*86400`.
   - `'all'`: `list_documents()` unfiltered.
   - Otherwise treat as a folder path (strip trailing `/`): `list_documents(folder=$scope)`.

   If the resolved set contains more than 100 notes, pause and ask the user whether to proceed or narrow the scope.

2. **Gather candidates per note.** For each note in scope:
   - Call `get_similar(path=<note>, limit=$per_note_limit)` (default 5).
   - If `get_similar` returns empty (no embeddings configured), fall back to `search(query=<note title>, mode='keyword', limit=$per_note_limit)` and filter the results to exclude the note itself.

3. **Filter out already-linked pairs.** For each candidate pair (A, B), check whether A already links to B. Call `get_outlinks(path=A)` once per scanned note and compare `raw_target` against the candidate B's path (also compare the wikilink text form — `[[B-title]]` without the `.md` extension).

4. **LLM judgment pass.** For each surviving candidate pair, `read(path=B)` and inspect the title, first section heading, and opening paragraph. Keep only pairs where the content justifies an explicit link — meaning the reader of A would genuinely benefit from knowing about B in context, not merely that they share vocabulary.

5. **Pick direction and placement per kept pair.**
   - Direction: one-way (`A → B` as a wikilink from A). The reverse is recoverable via `get_backlinks`; explicit bidirectional edits are redundant.
   - Direction heuristic: atomic → hub, specific → general, newer → older; whichever reads most naturally in context. Break ties in favor of the direction that produces the more meaningful in-context citation.
   - Placement: pick per note shape, as LLM judgment. Examples:
     - Short atomic note → new `## Related` section (create if missing).
     - Prose note → inline citation at a relevant paragraph.
     - MOC / hub note → append under an existing thematic bullet list.
     - Reference note → `## See Also` or footnote-style link.
   - The prompt does not prescribe a fixed placement. Let the model judge per note.

6. **Batch preview.** Render a preview showing each proposed edit:

   ```
   1. `1-Projects/migrate-postgres.md` — inline citation after line 23: mention [[postgres-16-json-path]]
   2. `3-Resources/distributed-consensus.md` — new `## Related` section with [[raft-paper-notes]] and [[paxos-made-simple]]
   3. `2-Areas/team-hiring.md` — append to existing `## Related` list: [[interview-rubric]]
   ```

   Ask the user: "Apply all N? Apply specific ones? Skip all?"

7. **Execute on confirmation.** For each approved edit:
   - Prefer `edit(path=..., old_text=..., new_text=...)` for small in-place insertions (inline citations, bullet-list appends).
   - Use `write(path=..., content=..., frontmatter=...)` when the change restructures the file (new `## Related` section at the bottom of a long note, or the placement is easier to express as a full-body rewrite).
   - If a write fails (e.g., `ConcurrentModificationError`), skip that edit, record the reason, and continue.

8. **Summary.** Report the counts: links added, notes updated, pairs skipped (with reasons: already linked, rejected by LLM judgment, write conflict).

### Constraints

- **Never write without user confirmation.** The batch preview (Step 6) is the only gating point; per-edit confirmation is available on request.
- **Don't propose self-links.**
- **Don't propose MOC-to-MOC links by default.** Hub-to-hub connections are usually noise. The LLM may surface them as "worth mentioning" in the preview text but should not propose them as edits unless the user explicitly asks.
- **Budget guard.** If scope resolves to > 100 notes, ask before proceeding.
- **Graceful degradation.** If neither `get_similar` nor `search` produces candidates for a note, skip it silently — do not warn per-note.
- **No silent writes on skipped edits.** If the batch contains 10 edits and the user approves 7, execute 7 and report which 3 were skipped.

## Documentation Structure

### `README.md` — new "What you can do with it" section

Positioned after the features list / tool count block, before Installation. Aim: ~25 lines.

Content template (exact wording tuned during implementation):

```markdown
## What you can do with it

With this server mounted in Claude, you can:

- **Capture a URL as a note:** "Fetch <url>, summarize as a Resource note under `3-Resources/`, and link any existing notes on the topic." — Claude uses `fetch` + `search` + `write`.
- **Research a topic into your vault:** "Research product security regulations, compare them, and create a set of interlinked notes, one per regulation, plus a MOC." — Claude uses web-search tools (client-side) + `write` with cross-linking.
- **Distill today's thinking:** "Summarize today's conversations into Inbox notes." — Claude.ai only; uses `conversation_search` + `recent_chats` + `write`. Or invoke the `para-capture-chats` prompt for a one-click version.
- **Find missing links:** Invoke `propose-links` from the `+` menu — it scans recently modified notes, proposes meaningful connections, and writes them on confirmation.
- **Split or merge captures:** "Split this Inbox note into two" or "Merge this capture into `<existing note>` instead of duplicating." — Claude uses `read` + `write` + `delete`.

No external scheduler, no separate capture app — the vault sits behind your conversations and absorbs their output.
```

### `docs/index.md` — shorter mirror

Two or three of the five examples above, with a pointer to `docs/prompts.md` for the full list. Goal: orient readers without duplicating the README in full.

### `docs/prompts.md` — new entry + new section

1. Add `propose-links` to the existing prompts table / per-prompt subsections (same format as `summarize`, `research`, etc.).
2. New section **"Ambient patterns without prompts"** — extends the README teaser with depth. For each example, list which tools the LLM composes, and explain why the pattern doesn't need its own MCP prompt:

   - URL capture: `fetch` + `search` + `write` — prose is sufficient because target folder is the only knob, and the user expresses it in the ask.
   - Research into interlinked notes: web-search + `write` with wikilinks — the LLM already composes cross-links naturally when asked for "a set of interlinked notes."
   - Split / merge: `read` + `write` + `delete` — codified as part of `para-triage` for the Inbox case; generic form is just prose.

3. New section **"How to invoke"** — unifies the invocation mechanics across platforms:
   - Prose conversation ("ask Claude: ...")
   - MCP prompt in the Claude Code `/` menu
   - Claude.ai `+` → connectors → pick server → pick prompt

   This is the canonical home for the #386 content (the connector `+` menu paragraph).

### Tool docstring improvements

Edits in `src/markdown_vault_mcp/_server_tools.py`:

| Tool | Addition |
|------|----------|
| `get_similar` | "Useful for finding link candidates that aren't yet wikilinked — the vault's organic graph is always denser than its explicit one. Combine with `propose-links` for a sweep, or use ad-hoc per-note." |
| `get_context` | "The `similar` field surfaces notes that may warrant explicit links to the context note but don't yet — a common input to manual or automated link proposal." |
| `get_backlinks` / `get_outlinks` | "Combine with `get_similar` to find connection gaps — notes that are semantically close to the target but not yet linked." |
| `search` | One-sentence hint about split/merge: "Also useful for finding merge candidates when triaging a new capture — if a close match exists, prefer merging over creating a near-duplicate." |
| `write` | One-sentence hint: "Supports split (write new notes from one source) and merge (extend an existing note with content from another) when combined with `delete`." |
| `delete` | One-sentence hint: "Used after split or merge to remove the source note once its content has been relocated." |
| `fetch` | One-sentence hint: "Primary path for URL-to-note capture flows: fetch, summarize via the LLM, and `write` as a new note." |

**Rationale:** tool docstrings are the canonical source for MCP tool schemas shown to the model at runtime. A hint about composable patterns in the docstring increases the chance the model proposes the pattern proactively before the user asks.

### Guide cross-references

- `docs/guides/zettelkasten.md`:
  - New subsection in "Using the Zettelkasten prompt" area documenting the connector `+` menu affordance (#386 content, reusable paragraph).
  - New tip on split/merge (#387 content), parallel to the one in the PARA guide.
  - New "See also: ambient patterns" bullet in Next Steps, pointing at `docs/prompts.md#ambient-patterns-without-prompts`.
- `docs/guides/para.md`:
  - "See also: ambient patterns" bullet added to Tips or Next Steps.
  - Existing split/merge coverage stays as-is.
- `docs/guides/claude-desktop.md`, `docs/guides/obsidian-everywhere.md`, `docs/guides/mcp-apps.md`:
  - Each gets the connector `+` menu paragraph (#386 content) in the relevant "how to invoke prompts/tools" area.

### Navigation

The `propose-links` prompt is indexed by `docs/prompts.md`. No `mkdocs.yml` nav change needed — `prompts.md` already appears under MCP Interface.

## Testing Strategy

Content-only change plus one new prompt file. Testing:

1. **Unit tests** — `tests/test_prompts.py` currently tests that each builtin prompt registers and renders. Add `propose-links` to the test list.
2. **Prompt YAML validation** — `static/prompts/propose-links.md` must parse; existing test covers this pattern.
3. **Docstring rendering** — `uv run mkdocs build --strict` must pass; `tools/index.md` is auto-generated via mkdocstrings and will pick up the docstring edits.
4. **Manual walkthroughs** — against a small seed vault:
   - `propose-links()` with default scope (recent), with `scope='1-Projects'`, with `scope='all'`.
   - Verify the batch preview, verify confirmation gate, verify skipped-edits reporting.
   - Verify graceful fallback when embeddings are not configured.

No additional integration tests required.

## Risks / Tradeoffs

- **Propose-links noise on dense vaults.** On a vault with lots of tangentially-related notes, the LLM-judgment pass may keep too many pairs. Mitigation: the user sees the full batch preview and can reject individually. Future mitigation: add a similarity-score threshold knob.
- **`get_similar` required for best experience.** The prompt falls back to keyword search but performs notably worse without embeddings. Documentation should call this out in the prompt's `description` field and in `prompts.md`.
- **Ambient-pattern examples may age.** The README's ambient examples reference flows that depend on client tools (`conversation_search`) and Claude.ai UI (`+` menu). If these change, the examples need updating. Mitigation: keep the examples concise; use footnotes for platform caveats.
- **Tool docstring edits affect a file in `src/`.** This is the only server-code change in an otherwise docs-only PR. Keep the edits to one-sentence additions per tool to minimize review surface.

## Future Work

- **Similarity threshold knob** on `propose-links` (minimum cosine; skip anything below).
- **Periodic mode** — schedule `propose-links` via a cron/systemd timer, notify when new links are suggested.
- **Split / merge prompts** if the `para-triage` codification proves valuable enough to standalone-ize.
- **URL-capture prompt** if the ambient flow proves too verbose to re-type across sessions (reverse of this spec's decision).

## Acceptance Criteria

- `src/markdown_vault_mcp/static/prompts/propose-links.md` exists, passes YAML frontmatter parse, matches the frontmatter/body structure in this spec.
- `_server_prompts.py` includes `propose-links` in the builtin prompt list; unit test confirms registration.
- `README.md` has a new "What you can do with it" section (~25 lines) with the five ambient-pattern examples.
- `docs/index.md` has a shorter mirror with 2-3 examples.
- `docs/prompts.md` has an entry for `propose-links`, an "Ambient patterns without prompts" section, and a "How to invoke" section covering the connector `+` menu affordance.
- Tool docstrings for `get_similar`, `get_context`, `get_backlinks`, `get_outlinks`, `search`, `write`, `delete`, `fetch` updated per the table in §Documentation Structure.
- `docs/guides/zettelkasten.md` has the connector `+` menu paragraph, a split/merge tip, and a Next Steps cross-link to the ambient-patterns section.
- `docs/guides/para.md` has a cross-link to the ambient-patterns section.
- `docs/guides/claude-desktop.md`, `docs/guides/obsidian-everywhere.md`, `docs/guides/mcp-apps.md` each have the connector `+` menu paragraph.
- `uv run mkdocs build --strict` passes with no new warnings.
- `uv run pytest -x -q` passes.
- Issues #386 and #387 closed by this PR.
