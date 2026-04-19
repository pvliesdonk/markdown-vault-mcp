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

- Empty or `'recent'`: call `get_recent(limit=100)` — it returns the 100 most-recently-modified notes pre-sorted by `modified_at`. Then filter client-side to keep only notes where `modified_at > <now - 30 * 86400>` (within the last 30 days). If the current Unix timestamp is not available, ask the user for today's date. Using `get_recent` here is strictly better than `list_documents()`: it bounds the payload and pre-sorts by recency in one tool call.
- `'all'`: call `list_documents()` unfiltered.
- Otherwise treat as a folder path. Strip any trailing `/` and call `list_documents(folder='<scope>')`.

If the resolved set contains more than 100 notes, pause and ask the user whether to proceed or narrow the scope. Above 100 notes, the subsequent steps become slow and noisy.

## Step 2: Gather candidates per note

For each note in scope:

1. Call `get_similar(path=<note path>, limit=$per_note_limit)` (default limit: 5).
2. If `get_similar` returns an empty list (no embeddings configured), fall back to `search(query=<note title>, mode='keyword', limit=$per_note_limit)` and drop the note itself from the result.

## Step 3: Filter out already-linked pairs

For each scanned note, call `get_outlinks(path=<note>)` once. For each candidate pair (A, B), drop the candidate if A already links to B — compare the candidate path `B` against each outlink's `target_path` (the resolved vault-relative path of the link target, which is what you want for equality checks). `raw_target` is the literal link text as written (and may be a wikilink without `.md`) — only fall back to comparing against `raw_target` when `target_path` is empty (e.g., the link hasn't been resolved).

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
