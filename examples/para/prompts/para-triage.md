---
description: "Triage an Inbox note (or any untyped note) into a Project, Area, or Resource"
arguments:
  - name: path
    description: "Vault-relative path to a note or folder. Defaults to the inbox folder the vault's conventions name. Examples: 'Inbox/my-note.md', 'Inbox/'"
    required: false
tags: ["write"]
---

You are helping triage notes into a PARA vault. Process `$path` (default: the `<inbox>` folder).

## Step 0: Read the vault's folder layout

Call `get_conventions(path='')`. The vault owner's root conventions say which folders hold the inbox, projects, areas and resources; use those folders wherever this prompt says `<inbox>`, `<projects>`, `<areas>` or `<resources>`. If the conventions name no folder for a role this prompt needs, ask the user for it now, before anything else, rather than assuming a name. Before writing into a folder, call `get_conventions(path=<that folder>)` and follow its rules as well; the owner's conventions override this prompt.

## Step 1: Load the note(s)

If `$path` is empty or ends with `/`, treat it as a folder (default: `<inbox>`). Call `list_documents(folder=<'$path' with any trailing `/` stripped, or `<inbox>` if empty>)` and iterate over each note. Example: `$path='Inbox/'` → call `list_documents(folder='Inbox')`. If `$path` is a single note path (ends with `.md`), skip to Step 2.

## Step 2: Read and extract the central intent

Call `read(path=<note>)`. Identify:

- The central intent or subject
- Whether it describes a concrete outcome, an ongoing responsibility, or reference material
- Any existing tags or signals in the body

## Step 3: Classify — and consider split / merge

Before picking a bucket, sanity-check the note's shape:

- **Split.** If the note contains two or more distinct ideas, classify each independently. Propose splitting into separate notes (one per idea) and triage each.
- **Merge.** Run `search(query=<key terms from the note>, mode='hybrid' if available else 'keyword', limit=5)` to find existing notes that cover the same ground. If a strong match exists (same topic, overlapping content), propose **merging** the inbox note's content into the existing note instead of creating a new standalone file. Show the user the target note and the specific section where the new content would go.

Otherwise assign one of:

- **Project** — concrete outcome + a plausible deadline. Something that can be completed.
- **Area** — ongoing responsibility with no end state (e.g., "Health", "Team Management").
- **Resource** — reference material, not actionable (e.g., "Postgres connection pooling notes").

If the note is ambiguous between buckets, **ask the user** — do not guess.

## Step 4: Propose the move (or split / merge)

**For a classification:**

- Proposed target path (`<projects>/<slug>.md`, `<areas>/<slug>.md`, `<resources>/<slug>.md`), named the way the target folder's conventions ask
- Proposed frontmatter block (type, status=active, any field the conventions require, and other relevant fields pre-filled from the note body)

**For a split:** list the two (or more) target notes with their individual paths and frontmatter, then treat each as its own triage in Step 5.

**For a merge:** show the target note path, the section heading the new content will extend (or "new section at end"), and a preview of the merged content.

Wait for confirmation.

## Step 5: Execute on confirmation

**For a classification**, on user confirmation:

1. `rename(old_path, new_path, update_links=True)` — preserves backlinks from other notes.
2. `read(path=new_path)` to get the current body content, frontmatter, and etag.
3. `write(path=new_path, content=<existing body>, frontmatter=<typed frontmatter dict>, if_match=<etag from that read>)` — overwrites the file with the typed frontmatter while preserving the body. Include `type`, `status=active`, `tags`, `created` (from the original note, or today's ISO date if missing), and any type-specific fields you inferred (`outcome`, `deadline`, `area` for projects; `standard`, `review_cadence` for areas).

**For a split:** `read` the inbox note once, then for each resulting note choose an unused target path and call `write(path=<target>, content=<portion of body>, frontmatter=<typed frontmatter>)`. Delete the original inbox note with `delete(path=old_path, if_match=<etag from the source read>)` only after every split is written successfully.

**For a merge:** `read(path=<target>)` to get the current body, frontmatter, and etag, append the new content (preserving the target's structure), then `write(path=<target>, content=<merged body>, frontmatter=<target's existing frontmatter>, if_match=<etag from the target read>)`. Delete the original inbox note with `delete(path=old_path, if_match=<etag from the source read>)` only after the merge is written successfully and confirmed.

If a write or delete conflicts, stop processing that note and report its current location and any completed steps. Preserve the source after a failed split or merge; do not retry with a blind overwrite or delete and recreate a destination.

## Constraints

- Do NOT rename or edit without explicit user confirmation.
- Never assume a folder name the conventions do not give; ask (Step 0).
- When in doubt between Project and Area, ask: "Is there a concrete 'done' state?" Yes → Project. No → Area.
