---
description: "Review a vault note and edit it: factual corrections, clarity, structure and completeness, shown to you first."
arguments:
  - name: path
    description: Path to the document to analyze.
    required: true
tags:
  - write
icons: edit
---
Step 1: Call `read` with path='$path', and `get_conventions` with path='$path'. Review the document against the vault owner's conventions; they override your own preferences.
Step 2: Identify specific improvements: factual corrections, clarity, structure, completeness.
Step 3: Present your proposed changes to the user before editing. Then apply each change using `edit`. `edit` requires an exact `old_text` substring from the document returned in Step 1 — do not paraphrase. Each `edit` call changes one location; use multiple calls for multiple changes.
Do not use `write` — it overwrites the entire file including frontmatter.
If `read` fails, report the error and stop.
