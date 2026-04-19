---
description: "Run the PARA weekly review: list active projects, flag stale ones, audit areas, identify archive candidates, and write a dated review note"
arguments:
  - name: review_path
    description: "Where to write the review note. Defaults to '3-Resources/reviews/<YYYY-WW>.md'."
    required: false
tags: ["write"]
---

You are running a PARA weekly review. Produce a dated review note at `$review_path` (default: `3-Resources/reviews/<current-ISO-week>.md`).

## Step 1: Determine the review path

Use `$review_path` if provided, otherwise build it as `3-Resources/reviews/<current-ISO-week>.md` (e.g., `3-Resources/reviews/2026-W17.md`). Do not create the file yet — Step 6 does that.

## Step 2: Enumerate active projects with modification times

Call `list_documents(folder='1-Projects')`. The response includes `frontmatter` and `modified_at` (a Unix timestamp in seconds) for each note. Filter client-side for notes where `frontmatter.status == 'active'`.

Fallback for non-canonical vault layouts: call `list_documents()` (no folder) and filter for `frontmatter.type == 'project' and frontmatter.status == 'active'`. Note: if the vault is very large, prefer passing the canonical folder to reduce the payload and the model's context usage.

Record for each active project: path, title, `frontmatter.deadline`, `modified_at`.

## Step 3: Stale scan — find projects un-edited for 14+ days

Using the `modified_at` values from Step 2 (no additional calls needed), compute:

```
stale_threshold = <current Unix timestamp, ask user if unknown> - 14 * 86400   # 14 days in seconds
stale_projects = [p for p in active_projects if p.modified_at < stale_threshold]
```

Collect these as "Stale projects".

## Step 4: Area audit

Call `list_documents(folder='2-Areas')` and filter for `frontmatter.status == 'active'`.

For each active Area, count projects referencing it via the indexed `area` frontmatter field. Use the active projects list you already have from Step 2: `count = len([p for p in active_projects if p.frontmatter.get('area') == area.title])`.

Flag any Area with **zero** active projects as a candidate for archive or reassessment.

## Step 5: Archive candidates

Collect:

- Projects with `status=completed` still in `1-Projects/` (should be moved to `4-Archive/`)
- Projects that are stale for 30+ days (user may want to archive or revive)

## Step 6: Write the review note

Call `write(path=<review-path>, content=<assembled markdown body>, frontmatter={"title": "Weekly Review <today's ISO date, e.g. 2026-04-19>", "type": "resource", "tags": ["review"], "created": "<today's ISO date, e.g. 2026-04-19>"})`.

The body should have these sections, populated from Steps 2-5:

- `## Active projects` — from Step 2, one bullet per project with its deadline
- `## Stale projects (no edits in 14+ days)` — from Step 3
- `## Area audit` — from Step 4; Areas with their project counts, zero-project Areas called out
- `## Archive candidates` — from Step 5
- `## New projects / ideas` — left empty for the user to fill in during the review

Parent directories are created automatically by `write`.

If the target path already exists (running the review twice in the same week), ask the user whether to overwrite or pick a different path before calling `write`.

## Step 7: Offer next actions

Ask the user which archive candidates they want to move. For each confirmed candidate, perform:

1. `read(path=<project>)` to get the current body and frontmatter.
2. `write(path=<project>, content=<body>, frontmatter=<frontmatter with status='archived' and archived_at=<today>>)` — this is cleaner than a targeted `edit` for multi-field frontmatter updates.
3. `rename(old_path, '4-Archive/<basename>', update_links=True)` to move it to the archive folder.

## Constraints

- Do NOT archive, rename, or edit notes without explicit confirmation.
- Present all findings first, then ask what to act on. Do not interleave questions with data gathering.
- If the user confirms a batch ("archive all 5"), still perform each operation individually so failures are isolated.
