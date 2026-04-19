---
description: "Triage an Inbox note (or any untyped note) into a Project, Area, or Resource"
arguments:
  - name: path
    description: "Vault-relative path to a note or folder. Defaults to '0-Inbox/' if empty. Examples: '0-Inbox/my-note.md', '0-Inbox/'"
    required: false
tags: ["write"]
---

You are helping triage notes into a PARA vault. Process `$path` (default: `0-Inbox/`).

## Step 1: Load the note(s)

If `$path` is empty or ends with `/`, treat it as a folder (default: `0-Inbox`). Call `list_documents(folder=<'$path' with any trailing `/` stripped, or '0-Inbox' if empty>)` and iterate over each note. Example: `$path='0-Inbox/'` → call `list_documents(folder='0-Inbox')`. If `$path` is a single note path (ends with `.md`), skip to Step 2.

## Step 2: Read and extract the central intent

Call `read(path=<note>)`. Identify:

- The central intent or subject
- Whether it describes a concrete outcome, an ongoing responsibility, or reference material
- Any existing tags or signals in the body

## Step 3: Classify

Assign one of:

- **Project** — concrete outcome + a plausible deadline. Something that can be completed.
- **Area** — ongoing responsibility with no end state (e.g., "Health", "Team Management").
- **Resource** — reference material, not actionable (e.g., "Postgres connection pooling notes").

If the note is ambiguous, **ask the user** — do not guess.

## Step 4: Propose the move

Present:

- Proposed target path (`1-Projects/<slug>.md`, `2-Areas/<slug>.md`, `3-Resources/<slug>.md`)
- Proposed frontmatter block (type, status=active, any other relevant fields pre-filled from the note body)

Wait for confirmation.

## Step 5: Execute on confirmation

On user confirmation:

1. `rename(old_path, new_path, update_links=True)` — preserves backlinks from other notes.
2. `read(path=new_path)` to get the current body content.
3. `write(path=new_path, content=<existing body>, frontmatter=<typed frontmatter dict>)` — overwrites the file with the typed frontmatter while preserving the body. Include `type`, `status=active`, `tags`, `created` (from the original note), and any type-specific fields you inferred (`outcome`, `deadline`, `area` for projects; `standard`, `review_cadence` for areas).

## Constraints

- Do NOT rename or edit without explicit user confirmation.
- If the user has a non-canonical folder layout (no `1-Projects/` etc.), ask where the note should go rather than hard-coding.
- When in doubt between Project and Area, ask: "Is there a concrete 'done' state?" Yes → Project. No → Area.
