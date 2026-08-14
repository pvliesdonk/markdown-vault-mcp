---
description: Summarize a folder subtree or a set of notes with the client's own model, delegating note reading to subagents so note bodies never enter the main context.
arguments:
  - name: paths
    description: "One or more note paths and/or folder prefixes (e.g. 'projects/alpha' or 'notes/a.md, notes/b.md'), separated by commas."
    required: true
  - name: focus
    description: "Optional free-text steer, e.g. 'extract action items'. Empty produces a general summary."
    required: false
icons: summarize
---

You are producing a faithful summary of multiple notes from a markdown vault, using your own model rather than a server-side backend. Only the vault's read tools (`get_toc`, `list_documents`, `list_folders`, `read`) are needed; never write.

Target: $paths
Focus: "$focus" (empty means a general summary; otherwise steer every step toward it)

## Core constraint

Note bodies must never enter your main context. Hold only the partition plan (lists of note paths), the partial summaries, and the final summary; every `read` of a note body happens inside a subagent. This is what keeps large subtrees summarizable without blowing out the conversation.

## Step 0: Consider the single-call alternative

If this vault server exposes a `summarize` tool (it is only registered when the operator configured a summarization backend), that tool is the single-call alternative: `summarize(paths=[...], focus=...)` partitions and combines server-side. Prefer it when it is available and sending note content to the operator's configured external provider is acceptable. Use this recipe instead when the tool is absent, when note content must stay with your own model, or when a faithful summary requires following links between notes mid-summary — the server backend receives frozen text and cannot do that; your subagents can.

## Step 1: Plan (one subagent)

Delegate planning to a single subagent so the enumeration happens outside your context. Instruct it to:

1. Split the target list ($paths) on commas into individual entries.
2. Keep each entry ending in `.md` as a single note path. Treat any other entry as a folder prefix and call `get_toc(path=<entry>)`; folder mode returns `{path, notes, truncated}` where each note carries `path`, `title`, and `headings`. When `truncated` is true, enumerate subfolders (`list_folders`, then per-subfolder `get_toc`) until the full set is known.
3. De-duplicate, keep the enumeration order, and pack the note paths into batches of about 8 notes each. The toc carries no note sizes; use heading counts as a rough proxy (many headings usually means a long note — put fewer of those in one batch).
4. Return ONLY the plan: the numbered batches as lists of note paths, plus the total note count and any entries that matched nothing. No note bodies, no commentary.

If the plan spans hundreds of notes, report the count and confirm scope with the user before fanning out.

(If your host cannot run subagents at all, do this step yourself — the toc is compact: paths, titles, and headings only, never bodies.)

## Step 2: Map (parallel subagents, one per batch)

Fan out one mapper subagent per batch, in parallel, from the main conversation — subagents typically cannot spawn subagents, so the fan-out must happen here. Give each mapper its batch's path list plus these instructions:

> You summarize notes from a markdown vault. Call `read(path=...)` for each assigned path. The notes provided are one part of a larger collection; other parts are summarized separately. Produce a detailed partial summary of these notes that preserves concrete specifics, and reference each note by its path (e.g. `folder/note.md`) so the partial summaries can be combined without losing attribution. Be faithful to the notes and do not invent details. If a note is unreadable, skip it and name it at the end. You may `read` a directly referenced note when needed to resolve an otherwise-unclear reference, but summarize only your assigned batch. Output only the partial summary — no meta-commentary.

If a focus was given, append to the mapper instructions: "Focus specifically on: $focus".

On a host without parallel subagents, run the same mappers sequentially — slower, same result.

## Step 3: Reduce

When all partial summaries are back:

- One or two short partials: combine them yourself.
- Otherwise, pass all partials to a single reducer subagent with these instructions:

> You combine partial summaries, each covering a different subset of notes from one markdown vault, into a single cohesive summary that reads as one text. Merge overlapping points, keep the note-path references intact, prefer prose over bullet dumps, and do not invent details beyond the partial summaries. Output only the summary — no meta-commentary.

Fold the same focus line into the reducer instructions if a focus was given.

## Step 4: Deliver

Present the final summary, then a short coverage note: how many notes were summarized and which, if any, were skipped as unreadable or matched nothing.

## Constraints

- Read-only: `get_toc`, `list_documents`, `list_folders`, and `read` are the only vault tools this recipe needs. Never write.
- Path attribution must survive every stage: plan → partial summaries → final summary.
- Never paste note bodies into the main conversation, and never ask a subagent to return full note text.
