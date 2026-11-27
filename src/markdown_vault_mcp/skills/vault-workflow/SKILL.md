---
name: vault-workflow
description: Use when the user asks you to search, read, or reason about notes in their markdown vault; says which vault tool to use when, and how to chain them for good results.
---

# Working effectively with the markdown vault

When the user mentions "my vault" or "my notes", or asks a question their
knowledge base could answer, use this server's tools as follows.

## Search strategy

- **Call `search` without `mode` by default**: the server picks the best mode
  the vault serves, which is hybrid (keyword plus meaning) when semantic
  search is available.
- Pass `mode="keyword"` when the user gives an exact phrase, tag, filename
  or proper noun they want matched literally; embeddings miss exact names.
- Pass `"semantic"` or `"hybrid"` only when `stats` reports
  `semantic_search_available`.

## Reading and context

- After `search` finds a relevant note, call `get_context` on the top hit
  before reading the full body. It returns the note's metadata, backlinks,
  outlinks, similar notes, folder neighbours and tags in one call, which is
  often enough to answer without reading the file.
- Call `read` when the user asks for the text itself, or when `get_context`
  does not give enough to answer.

## Link-graph questions

- "What notes reference X?" → `get_backlinks`.
- "What does this note link to?" → `get_outlinks`.
- "How are these two notes connected?" → `get_connection_path`.
- "What is orphaned?" → `get_orphan_notes`.
- "What is most cited?" → `get_most_linked`.

## Writes (only when the write tools are available)

- Prefer `edit` over `write` for a targeted change: `edit` replaces only the
  text it matches, and `write` replaces the whole note.
- Move a note with `rename(update_links=True)`, never `write` to a new path
  plus `delete` of the old one: `update_links` repairs the links that point
  at it.
- Do not call `reindex` after writes; the server updates its index as it
  writes.

## Do not

- Do not use `list_documents` in place of `search`: it lists, it does not
  rank.
- Do not search again for a note you have already found; keep the path from
  the first result.
