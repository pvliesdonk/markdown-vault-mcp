---
description: Summarize a folder subtree or a set of notes with the client's own model, processing notes in batches so note bodies stay out of the retained conversation context.
arguments:
  - name: paths
    description: "One or more note paths and/or folder prefixes (e.g. 'projects/alpha' or 'notes/a.md, notes/b.md'), separated by commas."
    required: true
  - name: focus
    description: "Optional free-text steer, e.g. 'extract action items'. Empty produces a general summary."
    required: false
icons: summarize
---

You are producing a faithful summary of multiple notes from a markdown vault. Only the vault's read tools (`get_toc`, `list_documents`, `read`) are needed; never write.

Target: $paths
Focus: "$focus" (empty means a general summary; otherwise steer every step toward it)

${route_note}

## Core rule

Do not accumulate note bodies in the context you retain. Work batch by batch: what survives each batch is a partial summary, never the notes themselves. With subagents, confine every `read` to a subagent; without them, read one batch at a time and carry only the partial summaries forward.

## Step 1: Plan

Enumerate and partition the target set before reading any note:

1. Split the target list ($paths) on commas into individual entries.
2. Keep each entry ending in `.md` as a single note path. Treat any other entry as a folder prefix and call `get_toc(path=<entry>)`; folder mode returns `{path, notes, truncated}` where each note carries `path`, `title`, and `headings`. When `truncated` is true the listing is incomplete — retry with a higher cap (`get_toc(path=<entry>, max_notes=<comfortably above the expected count>)`) or call `list_documents(folder=<entry>)`, which lists the entire subtree without a cap; never plan from a truncated listing, since notes sorted past the cutoff would be silently omitted.
3. De-duplicate, keep the enumeration order, and pack the note paths into batches of about 8 notes each. The toc carries no note sizes; use heading counts as a rough proxy (many headings usually means a long note — put fewer of those in one batch).

The plan is path lists plus a total note count, nothing more. Delegate this step to a subagent when you can; the toc is compact (paths, titles, and headings, never bodies), so doing it yourself is also fine. If the plan spans hundreds of notes, report the count and confirm scope with the user before continuing.

## Step 2: Map (one partial summary per batch)

Apply these instructions to every batch, plus its path list:

> You summarize notes from a markdown vault. Call `read(path=...)` for each assigned path. The notes provided are one part of a larger collection; other parts are summarized separately. Produce a detailed partial summary of these notes that preserves concrete specifics, and reference each note by its path (e.g. `folder/note.md`) so the partial summaries can be combined without losing attribution. Be faithful to the notes and do not invent details. If a note is unreadable, skip it and name it at the end. You may `read` a directly referenced note when needed to resolve an otherwise-unclear reference, but summarize only your assigned batch. Output only the partial summary — no meta-commentary.

If a focus was given, append: "Focus specifically on: $focus".

Run the map phase with whatever your host supports:

- Parallel subagents: fan out one mapper subagent per batch from the main conversation (subagents typically cannot spawn subagents, so the fan-out must happen here).
- Sequential subagents: run the same mappers one at a time.
- No subagents: process the batches yourself, one at a time, writing down each batch's partial summary before reading the next batch.

## Step 3: Reduce

When all partial summaries are collected, combine them under these instructions:

> You combine partial summaries, each covering a different subset of notes from one markdown vault, into a single cohesive summary that reads as one text. Merge overlapping points, keep the note-path references intact, prefer prose over bullet dumps, and do not invent details beyond the partial summaries. Output only the summary — no meta-commentary.

Delegate the reduction to a single subagent when one is available and the partials are long; otherwise do it yourself. Fold the same focus line in if a focus was given.

## Step 4: Deliver

Present the final summary, then a short coverage note: how many notes were summarized and which, if any, were skipped as unreadable or matched nothing.

## Constraints

- Read-only: `get_toc`, `list_documents`, and `read` are the only vault tools this recipe needs. Never write.
- Path attribution must survive every stage: plan → partial summaries → final summary.
- Never paste note bodies into the final answer or retain them past their batch.
