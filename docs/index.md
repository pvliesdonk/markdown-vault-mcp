---
description: "What Markdown Vault MCP is, what it does, and where to start reading."
kind: explanation
---

# Markdown Vault MCP

Generic markdown vault MCP with hybrid search

What the server can reach, what it changes and who gets in is set out in the [security model](security-model.md).

## Where to go

- [Get started](get-started/index.md): a first success with the client you use.
- [Deploy](deploy/index.md): run it for real, in a container and behind authentication.
- [Use](use/index.md): get more out of its features.
- [Reference](reference/configuration.md): configuration, [tools](reference/tools/index.md), resources, prompts and the command line.
- [Upgrade](upgrade/index.md): release channels and what changes when you move.
- [Contribute](contribute/index.md): change the project.

<!-- DOMAIN-INDEX-FEATURES-START -->
Point it at a folder of Markdown notes, an Obsidian vault included, and it gives Claude or any MCP client tools to search, read and write them. Whether it fits your notes, what it assumes and what to use instead is under [Does it fit?](https://github.com/pvliesdonk/markdown-vault-mcp#does-it-fit) in the README.

## Features

Each line links to the page that covers it.

- **Hybrid search**: SQLite FTS5 keyword search, plus search by meaning once an embedding provider is configured; Reciprocal Rank Fusion merges the two. [Embeddings](use/embeddings.md).
- **Frontmatter as data**: YAML frontmatter fields become search filters, and required fields can be enforced. [Configuration](reference/configuration.md).
- **Writes**: create, edit, append to, delete, rename and move notes and attachments, with the index kept current. Replacing a file takes the etag from reading it, and per-folder `_conventions.md` rules reach the client as it writes. [Write tools](reference/tools/writer.md).
- **Links**: backlinks, outlinks, broken links, orphans and the path between two notes. [Graph tools](reference/tools/graph.md).
- **Git**: an optional commit per write, a delayed push, pull or webhook sync, and history and diffs. [Git integration](use/git-integration.md).
- **Open Knowledge Format**: recognizes OKF bundles, adds each note's type, status, staleness and trust tier to results, and audits and migrates a vault. [Open Knowledge Format](use/okf.md).
- **Summaries**: an optional `summarize` tool with a model you configure, or the `summarize-subtree` prompt with the client's own model. [Prompts](reference/prompts.md#summarize-subtree).
- **Vault explorer**: interactive views in clients that render MCP Apps. [Vault explorer](use/vault-explorer.md).
- **Transfer links**: one-time URLs that move a file into or out of the vault over HTTP. [Transfer links](deploy/transfer-links.md).
- **A Python library**: the same engine for your own code. [Vault API](reference/api/vault.md).
<!-- DOMAIN-INDEX-FEATURES-END -->

<!-- DOMAIN-INDEX-USE-CASES-START -->
## What you can do with it

A few flows the server enables with an LLM on top; none needs a bespoke prompt:

- **"Fetch <url> and summarize into a Resource note."** Claude composes `fetch` + `search` + `write`.
- **"Research <topic> and create a set of interlinked notes."** Claude composes web tools + `write` with wikilinks. See [Research workflows](use/research-workflows.md) for the full loop.
- **"Summarize today's conversations into Inbox notes."** Claude.ai composes `conversation_search` + `recent_chats` + `write`; the [`para-capture-chats`](use/para.md#using-the-para-prompts) prompt is the one-click version.
- **Find missing links.** The built-in [`propose-links`](reference/prompts.md#propose-links) prompt scans recently modified notes and proposes connections between them.

The [prompts reference](reference/prompts.md) has the codified workflows.
<!-- DOMAIN-INDEX-USE-CASES-END -->
