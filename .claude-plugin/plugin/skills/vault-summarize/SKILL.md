---
name: vault-summarize
description: Use when the user asks to summarize, digest, or get an overview of a folder, project, or set of notes in their markdown vault — runs the vault's batched map-reduce recipe so note bodies stay out of the retained conversation context.
---

# Summarizing vault folders and note sets

When the user asks for a summary, digest, or overview spanning more than one
vault note ("summarize my projects folder", "what's in my meeting notes from
March"), do not read note after note into the conversation — long vaults
overflow the context and the summary degrades. Route the request instead:

## 1. Check scope

Call `get_toc(path=<folder>)` first. It returns paths, titles, and headings —
never bodies — so it is always safe. A single short note needs no workflow:
just `read` it and summarize directly.

## 2. Prefer the server's one-call route when it is available

If the `summarize` tool is registered, it produces the whole summary
server-side in one call: `summarize(paths=[...], focus=...)`. Use it unless
the user asks to keep note content away from external providers (the tool
sends note text to its configured backend).

## 3. Otherwise run the canonical client-side recipe

The server ships the full recipe as its `summarize-subtree` MCP prompt — that
prompt is the single source of truth for the workflow (plan from the toc,
map in batches of ~8 notes, reduce, deliver with a coverage note). Follow it
with this host's strengths:

- Fan out the map phase as **parallel subagents**, one per batch, using the
  `vault-mapper` agent this plugin ships — it carries the map-phase
  instructions and only the vault read tools. Pass each mapper its batch's
  path list (plus the user's focus, if any).
- Subagents cannot spawn subagents: do the planning and the reduce step in
  the main conversation, keeping only partial summaries — never note
  bodies — in the retained context.

## Do not

- Do not `read` more than one batch of notes in the main conversation.
- Do not re-summarize notes a mapper already covered; merge the partials.
- Do not write anything to the vault while summarizing.
