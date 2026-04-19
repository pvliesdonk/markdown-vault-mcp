---
description: "Distill recent chat conversations into PARA Inbox notes for later triage"
arguments:
  - name: window
    description: "Time window to cover. Examples: 'today', 'this week', 'since 2026-04-15'. Defaults to 'today'."
    required: false
  - name: target_folder
    description: "Vault folder for the inbox notes. Defaults to '0-Inbox'."
    required: false
tags: ["write"]
---

You are capturing recent chat conversations into a PARA vault's Inbox for later triage. The vault is a downstream beneficiary of conversations you've already had; this prompt bridges that gap in one pass.

## Step 1: Check your chat-history tools

Look for client-provided tools that expose conversation history. Common examples are `conversation_search` and `recent_chats` (Claude.ai). If no such tools are available in this session, stop and tell the user — this prompt cannot proceed without them. Do not fabricate content from the current conversation's context alone.

## Step 2: Gather relevant conversations

Use the chat-history tools to retrieve conversations within `$window` (default: `today`). Keep conversations that contain:

- Ideas or concepts the user might want to think about again
- Decisions made or alternatives considered
- Open questions or things left unresolved
- References cited (URLs, book titles, person names)
- Action items or follow-ups

Skip:

- Pure factual Q&A with no follow-up value
- One-off debugging or troubleshooting sessions
- Chats that were fully resolved with no durable takeaway

## Step 3: Distill into topics

Group the retained conversations by topic. **One note per distinct topic** is preferred over a single daily mega-note — Inbox triage classifies note-by-note, and smaller topic-scoped notes classify more reliably into Project / Area / Resource.

For each topic, extract:

- The core idea or question (1-2 sentences, in the user's voice)
- Specific decisions, references, action items
- A title — use one from the conversation if natural, otherwise synthesise

## Step 4: Propose note paths and content

Before writing, present the proposed notes to the user:

- **Path:** `$target_folder/<slug>.md` (default folder: `0-Inbox`); one note per topic
- **Frontmatter:** `{title: "<title>", tags: [], created: "<today's ISO date>"}` — no `type` field; Inbox is untyped by convention, triage assigns the type later
- **Body:** the distilled summary, in the user's voice, with any URLs from the source conversations preserved as links

## Step 5: Write on confirmation

On user confirmation, call `write(path=..., content=..., frontmatter=...)` for each note. If a target path already exists (re-running for the same window), ask whether to overwrite, merge, or pick a different slug.

## Step 6: Suggest next action

After writing, suggest running the `para-triage` prompt on `$target_folder` to classify the new notes into Projects / Areas / Resources.

## Constraints

- Do NOT write without explicit user confirmation.
- If no chat-history tools are available in the client, stop — do not fabricate content from the current conversation's context alone.
- Capture ideas, decisions, references, and action items — not transient Q&A or debugging.
- One topic per note. Resist the urge to write a single daily-log mega-note.
- Use `$target_folder` as the target folder (default `0-Inbox`); strip any trailing `/`.
- Preserve the user's voice. These are their ideas from their conversations — write in first person where natural.
