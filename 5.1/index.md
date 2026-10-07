# Markdown Vault MCP

Generic markdown vault MCP with hybrid search

What the server can reach, what it changes and who gets in is set out in the [security model](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/security-model/index.md).

## Where to go

- [Get started](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/get-started/index.md): a first success with the client you use.
- [Deploy](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/index.md): run it for real, in a container and behind authentication.
- [Use](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/index.md): get more out of its features.
- [Reference](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/configuration/index.md): configuration, [tools](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/index.md), resources, prompts and the command line.
- [Upgrade](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/upgrade/index.md): release channels and what changes when you move.
- [Contribute](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/contribute/index.md): change the project.

Point it at a folder of Markdown notes, an Obsidian vault included, and it gives Claude or any MCP client tools to search, read and write them. Whether it fits your notes, what it assumes and what to use instead is under [Does it fit?](https://github.com/pvliesdonk/markdown-vault-mcp#does-it-fit) in the README.

## Features

Each line links to the page that covers it.

- **Hybrid search**: SQLite FTS5 keyword search, plus search by meaning once an embedding provider is configured; Reciprocal Rank Fusion merges the two. [Embeddings](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/embeddings/index.md).
- **Frontmatter as data**: YAML frontmatter fields become search filters, and required fields can be enforced. [Configuration](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/configuration/index.md).
- **Writes**: create, edit, append to, delete, rename and move notes and attachments, with the index kept current. Replacing a file takes the etag from reading it, and per-folder `_conventions.md` rules reach the client as it writes. [Write tools](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/writer/index.md).
- **Links**: backlinks, outlinks, broken links, orphans and the path between two notes. [Graph tools](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/graph/index.md).
- **Git**: an optional commit per write, a delayed push, pull or webhook sync, and history and diffs. [Git integration](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/git-integration/index.md).
- **Open Knowledge Format**: recognizes OKF bundles, adds each note's type, status, staleness and trust tier to results, and audits and migrates a vault. [Open Knowledge Format](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/okf/index.md).
- **Summaries**: an optional `summarize` tool with a model you configure, or the `summarize-subtree` prompt with the client's own model. [Prompts](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/prompts/#summarize-subtree).
- **Vault explorer**: interactive views in clients that render MCP Apps. [Vault explorer](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/vault-explorer/index.md).
- **Transfer links**: one-time URLs that move a file into or out of the vault over HTTP. [Transfer links](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/transfer-links/index.md).
- **A Python library**: the same engine for your own code. [Vault API](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/api/vault/index.md).

## What you can do with it

A few flows the server enables with an LLM on top; none needs a bespoke prompt:

- **"Fetch and summarize into a Resource note."** Claude composes `fetch` + `search` + `write`.
- **"Research and create a set of interlinked notes."** Claude composes web tools + `write` with wikilinks. See [Research workflows](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/research-workflows/index.md) for the full loop.
- **"Summarize today's conversations into Inbox notes."** Claude.ai composes `conversation_search` + `recent_chats` + `write`; the [`para-capture-chats`](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/para/#using-the-para-prompts) prompt is the one-click version.
- **Find missing links.** The built-in [`propose-links`](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/prompts/#propose-links) prompt scans recently modified notes and proposes connections between them.

The [prompts reference](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/prompts/index.md) has the codified workflows.
