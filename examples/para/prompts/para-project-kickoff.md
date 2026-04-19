---
description: "Kick off a project note: define outcome and deadline, then resurface related Resources, Areas, and past Archived projects"
arguments:
  - name: path
    description: "Vault-relative path to the project note (e.g., '1-Projects/ship-v2.md')"
    required: true
tags: ["write"]
---

You are helping kick off a project in a PARA vault. Process `$path`.

## Step 1: Verify the project is well-defined

Call `read(path='$path')`. Check the frontmatter:

- `outcome` must be a specific, one-sentence definition of done (not "improve X" — "ship v2 with feature Y by date Z").
- `deadline` must be a real date (not the placeholder `YYYY-MM-DD`).

If either is missing or still a placeholder, prompt the user to provide values before continuing.

## Step 2: Survey the existing neighborhood

Call `get_context(path='$path')`. Note:

- Existing backlinks and outlinks
- Similar notes already surfaced by the context call
- Any tags or frontmatter already present

## Step 3: Resurface related material

Run two queries to gather context the user may not have linked yet:

1. If `stats()` reports `semantic_search_available=True`, call `search(query=<outcome text>, mode='hybrid', limit=15)` for the richest results. Otherwise fall back to `search(query=<outcome text>, mode='keyword', limit=15)`. The hybrid mode fuses keyword BM25 with vector similarity; keyword mode is still useful when embeddings aren't configured.
2. `get_similar(path='$path', limit=10)` — semantic near-neighbors (returns an empty list when embeddings are unavailable, so it is always safe to call)

## Step 4: Classify results into three buckets

For each result, classify using the folder prefix when available (fast path), otherwise by a targeted `search` with `filters={"type": "resource"}` etc.:

- **Relevant Resources** — from `3-Resources/` or `type=resource`
- **Similar past Projects (archived)** — from `4-Archive/` or `type=project status=archived`
- **Linkable Areas** — from `2-Areas/` or `type=area`

Prefer folder-prefix classification (one step) over per-note reads (N steps) when the vault follows the canonical layout.

## Step 5: Propose links

Present a `## Related` section to add to the project note, grouped by bucket. For each suggested link, state why it's relevant in one sentence.

Prefer `[[wikilinks]]` for internal references.

## Step 6: Apply on confirmation

On user confirmation, add the `## Related` section. Call `read(path='$path')` to get the current body and frontmatter, assemble the new body (existing body + new `## Related` section, or existing body with the `## Related` section extended if one exists), then call `write(path='$path', content=<new body>, frontmatter=<current frontmatter>)`. Using `write` is more robust than `edit` for appending — a trailing blank line or whitespace on the note would break an `edit(old_text=<last line>, ...)` match. If you do prefer `edit` to avoid rewriting the full file, match the existing `## Related` section heading precisely.

## Constraints

- Do NOT edit the note without explicit user confirmation.
- If Step 1 finds the note is not yet well-defined, stop and ask the user rather than guessing outcome/deadline.
- Keep the `## Related` section focused — 5-10 strong links beats 30 weak ones.
