# PARA with markdown-vault-mcp

PARA (Projects, Areas, Resources, Archive) is Tiago Forte's framework for organizing digital information around action — what you are doing, responsible for, or referencing. Where a Zettelkasten is idea-centric and emergent, PARA is action-oriented and top-down: every note has a home determined by what you do with it. This guide shows how to use markdown-vault-mcp as a PARA backend, leveraging its frontmatter-aware search, linking, and templating to run the canonical Capture → Triage → Project Work → Weekly Review → Archive loop.

!!! note
    This is one of many ways to organize a vault with markdown-vault-mcp. The server is a generic markdown collection backend — PARA conventions are applied in this guide but not required or enforced by the server.

This guide assumes you're working with a PARA vault through Claude (or another MCP client) as the primary interface. The four PARA prompts — capture-from-chats, triage, kickoff, and weekly review — are where most of the value lives; Claude handles surfacing, classification, and batch operations you'd otherwise skip or defer. The Python and CLI examples scattered throughout are the scripting escape hatch, not the day-to-day pattern.

## Vault Setup

### Recommended folder structure

A canonical PARA vault with markdown-vault-mcp uses five top-level folders plus a templates folder:

```
vault/
  0-Inbox/        # quick capture, untyped
  1-Projects/     # active projects with outcomes
  2-Areas/        # ongoing responsibilities
  3-Resources/    # reference material
  4-Archive/      # status=archived items (any type)
  _templates/     # pointed at by MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER
```

The numeric prefixes (`0-`, `1-`, ...) keep the buckets sorting in the canonical PARA order in any file explorer. `0-Inbox/` is added because triage is a named workflow step and deserves a dedicated location — notes land here first and move out once they are classified.

**The server does not enforce this layout.** You can flatten it, rename folders, or skip folders entirely and rely on the `type` and `status` frontmatter fields to drive queries. The prompts shipped with this pack assume the canonical layout but fall back to asking you when they encounter a different structure.

### Frontmatter schemas

PARA has four note shapes. Each uses a distinct frontmatter block. The templates in `examples/para/templates/` provide these exactly:

**Inbox** — untyped quick capture. Triage assigns the `type` later:

```yaml
---
title: "{{title}}"
tags: []
created: "{{date}}"
---
```

**Project** — a concrete outcome with a deadline:

```yaml
---
title: "{{title}}"
type: project
status: active
outcome: "<one-sentence definition of done>"
deadline: YYYY-MM-DD
area: "<parent area name, or empty>"
tags: []
created: "{{date}}"
---
```

**Area** — an ongoing responsibility with no end state:

```yaml
---
title: "{{title}}"
type: area
status: active
standard: "<what 'healthy' looks like for this area>"
review_cadence: "<weekly | monthly | quarterly>"
tags: []
created: "{{date}}"
---
```

**Resource** — reference material, not actionable:

```yaml
---
title: "{{title}}"
type: resource
status: active
tags: []
created: "{{date}}"
---
```

**Field meanings:**

- `title` — The note's heading. Used for display in lists and searches.
- `type` — One of `project`, `area`, `resource`. Stable across the note's lifetime.
- `status` — Lifecycle state: `active`, `on-hold`, `completed`, or `archived`. Flipped as the note transitions.
- `outcome` — Project-only. A specific, one-sentence definition of done. "Ship v2 with feature Y by date Z", not "improve v2".
- `deadline` — Project-only. Target date. Stored as ISO `YYYY-MM-DD` but not indexed for range queries (see [Future Enhancements](#future-enhancements)).
- `area` — Project-only. Names the parent Area for rollups (e.g., `"Health"`). This field is the authoritative signal for area→project linkage; wikilinks between them are supplementary.
- `standard` — Area-only. Describes what "healthy" looks like for the area. Used during the Weekly Review.
- `review_cadence` — Area-only. Informational hint about how often this area should be reviewed (`weekly`, `monthly`, `quarterly`). The `para-weekly-review` prompt does not yet act on this field; it audits all active areas each run. Tracked as a Future Enhancement.
- `tags` — Free-form list. Searchable via `filters={"tags": "value"}`.
- `created` — ISO date the note was written. Useful for reviewing note age.

!!! note "Archive is a status, not a type"
    Forte's original PARA treats Archive as the fourth top-level category. Mechanically this is awkward: an archived project is still a project; archiving is a lifecycle transition, not a reclassification. This pack models archive as `status=archived`, with `type` preserved across the lifecycle. Queries like `type=project status=archived` remain meaningful, and the Weekly Review filters cleanly on `status=active` without union queries across types. The `4-Archive/` folder is a convenience location for browsing — the authoritative "is this archived" signal is the `status` field.

### Make frontmatter fields searchable

Configure `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` so that `type`, `status`, `tags`, and `area` become structured filters in the search API:

```bash
export MARKDOWN_VAULT_MCP_INDEXED_FIELDS=type,status,tags,area
```

Then you can run targeted queries via the Python API:

```python
# All active projects
results = collection.search(
    "project",
    filters={"type": "project", "status": "active"},
    limit=50,
)

# All active projects in the Health area
results = collection.search(
    "project",
    filters={"type": "project", "status": "active", "area": "Health"},
    limit=50,
)
```

The server treats each indexed field as an equality filter. Ranges (e.g., deadlines within the next week) are not yet supported — see [Future Enhancements](#future-enhancements).

### Filename conventions

The server uses the file's path (relative to the vault root) as its identity. Any naming scheme works, but for PARA a title-based lowercase-hyphen form is clearest:

- `1-Projects/ship-v2-launch.md`
- `2-Areas/health.md`
- `3-Resources/postgres-connection-pooling.md`

Do not embed dates in filenames — PARA uses the `deadline` and `created` frontmatter fields for dates, and the folder for lifecycle. A project's filename should describe its identity, not its moment in time.

## Workflow

The canonical PARA loop: **Capture → Triage → Project Work → Weekly Review → Archive**. Each stage has a corresponding prompt or manual action.

### 1. Capture (Inbox)

Capture whatever's in your head into `0-Inbox/` without worrying about classification. The `inbox.md` template exists for this — Claude can create notes from it on request ("create an inbox note titled X with content Y"), or you can drop files directly into the folder via Obsidian, the CLI, or a scripting run. Review the inbox daily or every few days by invoking the triage prompt on the whole folder; Claude walks each note, classifies it, and proposes target paths.

Capture doesn't have to be something you type. If your MCP client exposes chat-history tools (Claude.ai has `conversation_search` and `recent_chats`), the vault can ingest ideas you've already discussed. The `para-capture-chats` prompt does this in one pass — it distills recent conversations into topic-scoped Inbox notes ready for triage. On Claude.ai you can fire it directly from the compose area's `+` menu once the MCP server is added as a connector — no typing required.

**Via Claude:**

```
Use the create_from_template prompt with template_name="inbox"
```

**Programmatically (scripting escape hatch):**

```python
from markdown_vault_mcp import Collection

collection = Collection(source_dir="/path/to/vault")
collection.write(
    "0-Inbox/migrate-postgres.md",
    content="We should look into migrating to Postgres 16.",
    frontmatter={"tags": [], "created": "2026-04-19"},
)
```

**Goals for this stage:**

- Capture without deciding type or folder
- One idea per note
- Review the Inbox at least once per week

### 2. Triage (Inbox → P/A/R)

Triage is the decision step: every Inbox note either becomes a Project, Area, or Resource, or gets deleted. The `para-triage` prompt codifies the decision procedure. Give Claude the entire `0-Inbox/` folder and it processes each note sequentially, pausing for your call on anything ambiguous — what would take an hour of manual review runs in minutes.

**What Claude does autonomously:** classify unambiguous notes into their bucket, slug the filename from the title, fill type-specific frontmatter fields (`outcome`, `deadline`, `area`, `standard`) from the note body, and execute the rename and write on confirmation.

**What Claude asks about:** anything that could fit two buckets ("is this a one-off project or an ongoing responsibility?"), picking a slug when the title is generic ("thoughts"), and two shape decisions worth calling out:

- **Split.** When an Inbox note contains two distinct ideas, tell Claude to split. It creates two notes — one per idea — and triages each separately. Don't pre-split before triage; Claude handles it in one pass.
- **Merge.** Before classifying, Claude can search for existing notes covering the same ground. If a strong match exists, it proposes merging the inbox content into the existing note as a new section (or extending an existing section), deleting the inbox note after. This prevents the vault from accumulating near-duplicates.

The prompt walks through five steps:

1. **Load the note(s).** If given a folder, enumerate with `list_documents` and iterate. If given a single note path, read it directly.
2. **Read and extract the central intent.** Call `read(path=note)` and identify the central subject — is it a concrete deliverable, an ongoing responsibility, or reference material?
3. **Classify** into one of three buckets:
   - **Project** — concrete outcome plus a plausible deadline; something that can be completed.
   - **Area** — ongoing responsibility with no end state (e.g., "Health", "Team Management").
   - **Resource** — reference material, not actionable.
   - **Ambiguous** — ask the user. Do not guess.
4. **Propose the move.** Present the proposed target path (`1-Projects/<slug>.md`, `2-Areas/<slug>.md`, `3-Resources/<slug>.md`) and the typed frontmatter block filled in from the note body.
5. **Execute on confirmation.**
    1. `rename(old, new, update_links=True)` preserves backlinks from other notes.
    2. `read(new)` loads the body at the new path.
    3. `write(new, content=<body from read>, frontmatter=<typed frontmatter dict>)` rewrites the file with the typed frontmatter, preserving the body.

See the full prompt body in `examples/para/prompts/para-triage.md`.

The prompt enforces a simple safety rule: **never rename or edit without explicit user confirmation**. When the note is ambiguous — a task that could be either a one-off Project or a recurring Area, for example — the prompt asks the user rather than picking.

### 3. Project Work (`para-project-kickoff`)

Once a note has been classified as a Project, the `para-project-kickoff` prompt turns it into an actionable plan. This is the resurface work you'd otherwise skip: most people defining a project don't go back through archived work to check what's been tried, don't look across resources to find relevant reference material, don't re-read similar past projects for lessons. Claude does. You give Claude the project note; Claude does the homework and surfaces what's relevant; you approve the links to add.

The prompt walks through six steps:

1. **Verify the project is well-defined.** Read the note, check that `outcome` is a specific one-sentence definition of done (not a placeholder) and that `deadline` is a real date. If either is missing, ask the user.
2. **Survey the existing neighborhood.** Call `get_context(path=project)` to see existing backlinks, outlinks, similar notes, and tags.
3. **Resurface related material.** Run two searches:
   - If `stats()` reports `semantic_search_available=True`, call `search(query=outcome_text, mode='hybrid', limit=15)`. Otherwise fall back to `search(query=outcome_text, mode='keyword', limit=15)`.
   - `get_similar(path=project, limit=10)` — returns an empty list when embeddings aren't configured, so it is always safe to call.
4. **Classify results into three buckets.** For each result, use the **folder prefix fast path** when the vault follows the canonical layout:
   - `3-Resources/` → Resource
   - `4-Archive/` → archived project (similar past work)
   - `2-Areas/` → linkable Area
   Folder-prefix classification is one step; reading each note's frontmatter is N steps. Prefer the fast path, fall back to a targeted `search(filters={"type": "resource"})` per bucket when the layout is non-canonical.
5. **Propose links.** Present a `## Related` section grouped by bucket, with a one-sentence "why this is relevant" per link. Prefer `[[wikilinks]]`.
6. **Apply on confirmation.** On confirmation, `write` the note back with the `## Related` section added (robust to trailing whitespace), or use `edit` if you prefer to avoid rewriting the full file and can match the section heading precisely.

See the full prompt body in `examples/para/prompts/para-project-kickoff.md`.

The kickoff prompt isn't just for project start. Re-invoke it mid-project when you're stuck — Claude will resurface whatever's changed in the vault since kickoff and propose additional links as context evolves.

!!! note "Hybrid mode needs embeddings"
    `search(mode='hybrid')` combines BM25 keyword ranking with vector similarity via Reciprocal Rank Fusion. It requires embeddings to be built. If embeddings aren't configured, the prompt falls back to `mode='keyword'`, which still works but misses semantic matches (paraphrases, conceptual neighbors). Check `stats().semantic_search_available` before picking the mode.

### 4. Weekly Review (`para-weekly-review`)

The Weekly Review is a 10-minute guided session, not a manual audit. Invoke the prompt, answer Claude's questions as they come, and decide on the archive candidates it surfaces. The stale-scan, area audit, and archive candidate list are what a manual review would take an hour to produce — the prompt does it in minutes; you just make the calls.

Invoke it in Claude with no arguments — it figures out the current ISO week and defaults everything:

```
In Claude, call: para-weekly-review()
```

Here is Claude's internal process across seven steps:

1. **Determine the review path.** Default is `3-Resources/reviews/<YYYY-WW>.md` (ISO week), or a user-supplied `review_path`.
2. **Enumerate active projects** with `list_documents(folder='1-Projects')` and filter client-side for `frontmatter.status == 'active'`. Record each project's path, title, deadline, and `modified_at` timestamp.
3. **Stale scan.** Compute a threshold of `now - 14 * 86400` seconds and flag any active project whose `modified_at` is older. These are the stale projects that need attention.
4. **Area audit.** `list_documents(folder='2-Areas')`, filter for `status=active`. For each Area, count active projects whose `area` frontmatter field matches the Area's title. Any Area with **zero** active projects is flagged as a candidate for archive or reassessment. The `area` frontmatter field is the authoritative signal; wikilinks between projects and areas are supplementary.
5. **Archive candidates.** Projects with `status=completed` still sitting in `1-Projects/` (should be moved to `4-Archive/`), plus projects stale for 30+ days (user may want to archive or revive).
6. **Write the review note** to the path from Step 1 via a direct `write()` call. The frontmatter matches the `weekly-review` template (Resource type, tagged `review`) and the body contains the seeded sections: `## Active projects`, `## Stale projects`, `## Area audit`, `## Archive candidates`, `## New projects / ideas`. The review note's section structure matches the `weekly-review.md` template — for consistency and for users who want to create reviews manually via `create_from_template` — but the prompt writes the note directly.
7. **Offer next actions.** Ask the user which archive candidates to act on. For each confirmed one: `read` the project to get the current body and frontmatter, `write` it back with `status=archived` and `archived_at=<today>` added, then `rename` it into `4-Archive/`.

See the full prompt body in `examples/para/prompts/para-weekly-review.md`.

The prompt presents all findings before asking for any action. It never archives, renames, or edits notes without explicit confirmation, and when the user confirms a batch ("archive all 5"), it still performs each operation individually so failures are isolated.

### 5. Archive (lifecycle transition)

Archiving is a two-step operation: flip the `status` to `archived` (and set `archived_at`), then move the file to `4-Archive/`. The order matters — the `status` field is what matters for queries; the folder is for browsing.

The easiest path is to use `para-weekly-review` Step 7, which handles the full lifecycle (surface candidates, flip status, move) with user confirmation. You can also invoke archiving ad-hoc outside the review cadence: "Claude, list everything with `status=completed` in `1-Projects/` and archive them." Claude runs `list_documents(folder='1-Projects')`, filters client-side on the frontmatter, and proposes each move for your confirmation (or batch confirmation if you prefer).

For scripting outside the prompt:

```python
from datetime import date
from markdown_vault_mcp import Collection

collection = Collection(source_dir="/path/to/vault")

today = date.today().isoformat()

# Flip status and add archived_at. One targeted replacement each.
collection.edit(
    "1-Projects/ship-v2.md",
    old_text="status: active",
    new_text=f"status: archived\narchived_at: {today}",
)

# Move to the archive folder, preserving backlinks.
collection.rename(
    "1-Projects/ship-v2.md",
    "4-Archive/ship-v2.md",
    update_links=True,
)
```

After this sequence, `search(filters={"type": "project", "status": "archived"})` surfaces the note, and `search(filters={"type": "project", "status": "active"})` excludes it — regardless of what folder it physically lives in.

## Using Templates

Templates accelerate note creation. The five PARA templates (`inbox`, `project`, `area`, `resource`, `weekly-review`) live in `examples/para/templates/`.

**Configure the templates folder:**

```bash
export MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER=/path/to/examples/para/templates
markdown-vault-mcp serve
```

**Use in Claude via the `create_from_template` prompt:**

The prompt will:

1. List available templates
2. Ask you to choose one
3. Gather required values (title, outcome, deadline, area, etc.)
4. Create the note with filled-in frontmatter

**Invoke via MCP prompt:**

`create_from_template` is an MCP prompt, not a Python API method. Invoke it through your MCP client (e.g., Claude):

```
Use the create_from_template prompt with template_name="project"
```

The prompt calls `list_documents` on the templates folder to enumerate choices, `read`s the chosen template, substitutes placeholders, and `write`s the filled note — all through vault tools.

## Using the PARA Prompts

The four PARA prompts live in `examples/para/prompts/` and are loaded via `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER`:

```bash
export MARKDOWN_VAULT_MCP_PROMPTS_FOLDER=/path/to/examples/para/prompts
markdown-vault-mcp serve
```

Then in Claude, the `para-capture-chats`, `para-triage`, `para-project-kickoff`, and `para-weekly-review` prompts are available. On Claude.ai they also appear in the compose area's `+` menu after adding the MCP server as a connector.

### `para-capture-chats`

**When to use:** at the end of a day or week, when you want ideas from your recent conversations captured into the vault without retyping them. Requires a client that exposes chat-history tools (e.g. Claude.ai's `conversation_search` and `recent_chats`); the prompt stops gracefully if they're not available.

```
In Claude, call: para-capture-chats()
```

Or with an explicit window:

```
In Claude, call: para-capture-chats(window='this week')
```

The prompt gathers relevant conversations, distills them topic by topic, and proposes one Inbox note per topic for your confirmation. It ends by suggesting you run `para-triage` on the fresh Inbox entries to classify them.

### `para-triage`

**When to use:** after capturing one or more notes in `0-Inbox/`, or any time you want to classify an untyped note into Project, Area, or Resource. Run it as part of a daily or weekly inbox sweep.

```
In Claude, call: para-triage(path='0-Inbox/')
```

Or for a single note:

```
In Claude, call: para-triage(path='0-Inbox/migrate-postgres.md')
```

The prompt will iterate over the folder (or act on the single note), propose a classification and target path for each, and move them on confirmation.

### `para-project-kickoff`

**When to use:** right after creating a new Project note, or any time you want to define the outcome and pull in related context before starting work. Run it once per project — it's the onboarding step for the project itself.

```
In Claude, call: para-project-kickoff(path='1-Projects/ship-v2.md')
```

The prompt will verify the outcome and deadline are real, resurface related Resources and past archived Projects, and add a `## Related` section on confirmation.

### `para-weekly-review`

**When to use:** weekly (or whenever you run your review cadence). Produces a dated review note and flags stale projects, zero-project Areas, and Archive candidates.

```
In Claude, call: para-weekly-review()
```

Or with an explicit path:

```
In Claude, call: para-weekly-review(review_path='3-Resources/reviews/2026-W17.md')
```

The review note is written as a Resource under `3-Resources/reviews/`, so it is searchable and linkable from other notes. Past reviews form a trail of how the vault has evolved.

## Tips and Best Practices

### Status over folder

The authoritative signal for "is this archived" is `status=archived`. The `4-Archive/` folder is convenience — nice for browsing in a file explorer, nice for ambient separation — but a query that only checks the folder will miss archived items that haven't been moved yet and will mis-flag active items that happen to sit in an archive-adjacent folder. Always filter on `status`, not path, when writing automations.

### Archive by flipping status, then moving

When archiving a note, update `status=archived` and set `archived_at` **first**, then `rename` into `4-Archive/`. If you do the rename first, anyone querying `status=active` between the two steps will still see it as active. The weekly-review prompt enforces this order; if you archive manually, follow the same sequence.

### Link projects to areas via the `area` frontmatter field

The `area` field on a project is indexed (via `MARKDOWN_VAULT_MCP_INDEXED_FIELDS`) and makes area rollups reliable: `search(filters={"type": "project", "area": "Health", "status": "active"})` is precise and fast. Wikilinks from the project note to the area note (e.g., `[[health]]`) are optional and supplementary — they show up in `get_context(path)` as outlinks and help visual exploration, but the `area` field is the source of truth.

### Deadlines aren't indexed

You can store `deadline: YYYY-MM-DD` in project frontmatter, and the date round-trips cleanly, but the server does not yet support range queries like "projects with `deadline <= next week`". To spot upcoming deadlines, either:

- Filter the active-project list from `list_documents(folder='1-Projects')` client-side by parsing the `deadline` field
- Sort results by title if you use a date prefix (not recommended — complicates renames)
- Run the `para-weekly-review` prompt, which surfaces project deadlines in the review note

Date-range filters are tracked as a planned follow-up — see [Future Enhancements](#future-enhancements).

### Link aggressively within `## Related`

Same spirit as Zettelkasten: over-linking is better than under-linking. If a Resource touches on a Project's outcome, link it. If an Area has three projects that reference a common book, link the book from each. `get_context(path)` will surface these connections later when you're editing, and `para-project-kickoff` will resurface them when you start a new related project. Don't agonize — the tools will help you rediscover what you linked.

### Let Claude split or merge captures

Two shape operations the triage prompt handles that manual workflows usually skip:

- **Split** — when an Inbox note contains two ideas, tell the triage prompt to split. Claude creates two notes, one per idea, and classifies each separately.
- **Merge** — when an Inbox note extends or restates something already in the vault, Claude proposes merging it into the existing note rather than creating a duplicate. This prevents near-duplicate accumulation, which is the main failure mode of long-lived PARA vaults.

Don't pre-split or pre-merge before triage; Claude handles both in one pass.

### Ask Claude for area assignments in batches

After a triage session produces 5-10 new projects, ask Claude to propose an `area` value for each based on the project's outcome. It's a one-shot operation that keeps the rollup queries useful and saves you from clicking through each note.

## Future Enhancements

**Date-range filter support** would enable deadline queries like `filters={"deadline": {"lte": "2026-05-01"}}`. This requires extending the FTS query layer to parse range operators on indexed date fields. Tracked as a planned follow-up issue.

**Review-cadence-aware weekly review.** The `review_cadence` frontmatter on Areas is currently informational. A future version of the `para-weekly-review` prompt could skip areas whose cadence is `monthly` or `quarterly` and it isn't their week.

**Consolidated triage-and-create prompt.** Today, triage classifies an existing Inbox note and moves it; `create_from_template` creates a new note from a template. A combined "triage a thought and create a typed note directly" prompt — with auto-suggested target folder based on classification — is possible as a `create_from_template` extension. It's a usability improvement, not a capability gap.

## Next Steps

- **Read the design document** for details on the linking system and search algorithms: [`docs/design.md`](../design.md)
- **Explore the MCP tools** to understand the full API: [`tools/index.md`](../tools/index.md)
- **Review the examples** for templates and prompts: [`examples/para/`](../../examples/para/)
- **Prefer idea-centric knowledge management?** See the alternative workflow: [`docs/guides/zettelkasten.md`](zettelkasten.md)
