---
description: "Connect a note to the vault: find related notes, suggest links, and optionally create a MOC"
arguments:
  - name: path
    description: "Vault-relative path of the note to process (e.g., 'Notes/my-idea.md')"
    required: true
tags: ["write"]
---

You are helping build a Zettelkasten vault. Process the note at `$path`.

## Step 1: Read and understand the note

Call `read(path='$path')` and `get_conventions(path='$path')`. The conventions are the vault owner's rules for this folder (how notes link, where MOCs live); they override this prompt. Identify:
- The central claim or idea
- Key terms and concepts
- Note type (fleeting, literature, permanent, or MOC)

## Step 2: Survey the neighborhood

Call `get_context(path='$path')`. Review:
- **Backlinks**: notes that already link here — are they appropriate?
- **Outlinks**: notes this links to — are they still relevant?
- **Similar notes**: semantically related notes not yet linked

## Step 3: Discover broader connections

Call `search` with the central claim and key terms (mode='hybrid'). Look for permanent notes that should be linked to this one but aren't.

If you find two seemingly distant notes that might connect through this one, call `get_connection_path(source=<path1>, target='$path')` to check.

## Step 4: Suggest links

Present a prioritized list of suggested links:
- **Strong connections** (shared claim or evidence): suggest adding a `[[wikilink]]` or `[text](path.md)` in the note body
- **Weak connections** (thematic overlap): mention but don't suggest editing

For each suggestion, state: what the other note is about, why the connection is meaningful, and where in `$path` the link should be added.

## Step 5: Check for MOC opportunity

If `get_most_linked()` shows that `$path` is already highly linked, or if you found 5+ related permanent notes in Step 3, suggest creating a MOC using the `moc` template via `create_from_template(template_name='moc')`, in the folder the conventions name for MOCs. If they name none, ask the user where it goes.

## Constraints

- Do NOT edit the note without explicit user confirmation
- Present suggestions first, then ask "Shall I add these links?" before calling `edit`
- Keep suggestions concrete: quote the exact text where a link should be added
- Prefer `[[wikilinks]]` for internal notes (they're shorter and cleaner)
