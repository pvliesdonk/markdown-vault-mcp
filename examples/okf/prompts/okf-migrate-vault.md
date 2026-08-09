---
description: "Migrate an existing vault into OKF using the audit → declare → enrich → transform ratchet"
arguments:
  - name: folder
    description: "Restrict enrichment/transform steps to this folder. Defaults to the whole vault."
    required: false
tags: ["write"]
---

You are moving an existing vault into the Open Knowledge Format, incrementally.
The order is a ratchet: declare early so new notes are correct, then converge
the backlog. Work over `$folder` if given, else the whole vault. Confirm before
any write.

## Step 1: Audit

Call `okf_validate`. Report the conformance ratio (`conformant_notes` of
`total_notes`) and the per-rule findings — notes missing a `type`, unparseable
frontmatter, unknown `status` values, misplaced `okf_version`. This reads from
disk and works before anything is declared, so use it to decide where to start.

## Step 2: Declare

If the root `index.md` has no `okf_version`, propose adding
`okf_version: "0.2"` to its frontmatter (create the file if absent). From this
point the server annotates results and the filters work. Confirm, then write.

## Step 3: Enrich

Backfill the missing metadata, note by note — this is where most of the work is.
For each note `okf_validate` flagged as missing a `type`:

- `read` it, propose a `type` from the content, and fill in `title` /
  `description` (and `sources` when the note cites anything).
- Batch the proposals; on confirmation, `write` each with the typed frontmatter,
  preserving the body.

Re-run `okf_validate` and report the climbing conformance ratio.

## Step 4: Mechanical transforms

Run the transforms you should not do by hand (each is a write tool; on a
git-backed vault each change is committed):

- `okf_convert_links(folder=<$folder or omit>)` — rewrite resolvable
  `[[wikilinks]]` as OKF root-absolute markdown links.
- `okf_generate_index(folder=<$folder or omit>)` — (re)generate the reserved
  `index.md` listing.
- `okf_seed_log(folder=<$folder or omit>)` — seed a reserved `log.md` change
  history from git (skips if one already exists).

## Step 5: Report

Finish with a fresh `okf_validate` summary and a short note on residual gaps
(unknown `status` values, notes still missing recommended fields). Suggest
enabling `MARKDOWN_VAULT_MCP_OKF_WRITE=true` if the operator wants provenance
and reserved-file upkeep enforced going forward.
