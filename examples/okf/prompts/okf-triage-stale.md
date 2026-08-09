---
description: "Surface deprecated and stale OKF notes and propose refresh or retirement"
arguments:
  - name: folder
    description: "Restrict the triage to this folder. Defaults to the whole vault."
    required: false
tags: ["write"]
---

You are triaging the lifecycle of an OKF bundle. Find notes that are past their
prime and propose what to do with each. Scope to `$folder` if given, else the
whole vault.

## Step 1: List the stale and deprecated notes

Use the OKF filter dimensions (these work only on a detected bundle):

- `list_documents(folder=<$folder or omit>, filters={"stale": "true"})` — notes
  whose `stale_after` is in the past.
- `list_documents(folder=<$folder or omit>, filters={"status": "deprecated"})` —
  notes explicitly retired.

Combine the two lists, de-duplicating by path.

## Step 2: Assess each note

For each, `read(path=<note>)` and decide a disposition:

- **Refresh** — still relevant but out of date. Propose the specific edits and a
  new `stale_after`.
- **Deprecate** — superseded. Propose setting `status: deprecated` and, if there
  is a replacement, a pointer to it. Deprecated and stale notes are ranked
  lower in search on a detected bundle, so this quietly gets them out of the way
  without deleting history.
- **Delete** — genuinely obsolete with nothing worth keeping. Propose removal.

## Step 3: Present a table, then act on confirmation

Show one row per note: path, why it surfaced (stale / deprecated), proposed
disposition, and the concrete change. Wait for confirmation before any write.

On confirmation, apply each with `edit` / `write` (or `delete`). Do not verify a
refreshed note in the same pass — a content change clears prior verification;
re-review it separately (see `okf-verify-note.md`).
