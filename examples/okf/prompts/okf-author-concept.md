---
description: "Draft a new OKF-conformant note with type, description, and cited sources"
arguments:
  - name: topic
    description: "What the note is about. Example: 'Reciprocal Rank Fusion'"
    required: true
  - name: folder
    description: "Destination folder. Defaults to the vault root if empty."
    required: false
tags: ["write"]
---

You are authoring a note for an OKF (Open Knowledge Format) bundle. Draft one
conformant note about `$topic` in `$folder` (default: the vault root).

## Step 1: Check conventions and prior art

- Call `get_conventions(path=<'$folder' or the root>)` and follow anything it
  returns.
- Call `search(query=<key terms from $topic>, mode='hybrid' if available else 'keyword', limit=5)`.
  If a note already covers this ground, propose extending it instead of
  creating a duplicate, and stop for confirmation.

## Step 2: Draft the frontmatter

Propose a frontmatter block:

- `type` — the note's kind (`Concept`, `Playbook`, `Reference`, …). Required
  and non-empty; this is the one hard OKF rule.
- `title` and `description` — a title and a one-sentence summary.
- `status: draft` — the OKF lifecycle. Reserve `status` for lifecycle, not
  workflow state.
- `sources` — a list of `{resource, id}` for anything you assert from an
  external source. Attribute claims in the body to these ids.
- `tags` — optional grouping tags.

Do **not** hand-write `generated` or `verified`; the server manages those.

## Step 3: Draft the body

Write the claim plainly. Link related notes with root-absolute markdown links
(`[text](/folder/note.md)`); wikilinks are fine while drafting.

## Step 4: Confirm, then write

Show the proposed path and the full note. On confirmation, call
`write(path=<folder>/<slug>.md, content=<body>, frontmatter=<dict>)`.

Then call `okf_validate` and report whether the new note is counted conformant.
