---
description: "What the vault explorer shows in a client that renders MCP Apps, and how to open each view."
kind: how-to
---

# Vault explorer

In a client that renders [MCP Apps](../deploy/mcp-apps.md), the server can show your vault as an interactive panel in the conversation. Ask Claude to "browse my vault" and it calls `browse_vault`; ask for "the context of `Projects/roadmap.md`" and it calls `show_context`. A client without MCP Apps gets the same request answered as text.

## The four views

The explorer has four tabs, and each note you click opens in one of them.

**Context card.** One note's surroundings:

- the notes that link to it, and the notes it links to, with links to missing notes marked;
- similar notes, when search by meaning is configured;
- its tags, the other notes in its folder, and when it last changed.

Click a linked note to open its card.

**Graph.** The link graph around a note, or the vault's most-linked notes:

- `browse_vault` with `view` set to `graph` opens it.
- The neighbourhood stops at a set number of notes and says so when it does.
- With search by meaning configured, a switch adds similarity edges, drawn dashed to set them apart from links.
- Far-out notes show as dots until you hover or zoom in. A chip in the corner counts the notes on screen, and a legend explains the styles.

**Browser.** The vault's folder tree, with a search box that runs the vault's own search. Click a note to preview it.

**Note.** The rendered note, its frontmatter shown once, collapsed. You can:

- open a table of contents built from its headings;
- copy the Markdown or the note's vault path;
- **send the note to Claude**, which puts its content into the conversation;
- jump to the same note's context card or graph.

## While you browse

Claude is told which note you are looking at, so "summarize this one" refers to the note on screen.

On a desktop or in a browser the panel opens full screen. On a phone it stays in the conversation.

The panel follows the client's light or dark theme.

The panel reads the vault with the same access as Claude's own tools. It changes nothing: every view only reads.
