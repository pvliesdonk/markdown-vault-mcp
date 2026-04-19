# Obsidian Everywhere

A reference architecture for keeping one Obsidian vault available on desktop, mobile, and Claude, with git as the source of truth.

## Overview

This setup gives you one vault, accessible everywhere:

- Obsidian Desktop writes to git
- Obsidian Mobile syncs the same git repo
- markdown-vault-mcp serves the same repo to Claude
- OIDC (Authelia) protects access to the MCP endpoint

```text
┌──────────────┐     git push      ┌─────────────────┐
│  Obsidian    │ ──────────────►   │                 │
│  Desktop     │     obsidian-git  │  Private GitHub │
│  (laptop)    │ ◄────────────────  │  Repo           │
└──────────────┘     git pull      │                 │
                                   │                 │
┌──────────────┐     git sync      │                 │
│  Obsidian    │ ◄────────────────► │                 │
│  Mobile      │     GitSync app   │                 │
│  (Android)   │                   │                 │
└──────────────┘                   └────────┬────────┘
                                            │
                                   git pull │ (automated)
                                            ▼
                                   ┌─────────────────┐
                                   │  Home Lab Server │
                                   │  ┌─────────────┐ │
                                   │  │ markdown-   │ │
                                   │  │ vault-mcp   │ │
                                   │  └──────┬──────┘ │
                                   │         │ MCP    │
                                   │  ┌──────┴──────┐ │
                                   │  │  Authelia   │ │
                                   │  │  (OIDC)     │ │
                                   │  └─────────────┘ │
                                   └────────┬────────┘
                                            │
                                   MCP over │ SSE/HTTP
                                            ▼
                                   ┌─────────────────┐
                                   │  Claude          │
                                   │  (Desktop/Web)   │
                                   │  read/write/     │
                                   │  search vault    │
                                   └─────────────────┘
```

## Prerequisites

- Private GitHub repository for your vault
- Obsidian Desktop on at least one machine
- Android phone (if using GitSync mobile workflow)
- Home lab server or VPS running Docker
- Domain + HTTPS for OIDC-protected MCP over HTTP/SSE

## Step 1: Set up the GitHub repo

1. Create a private repository for your vault
2. Push your existing vault, or initialize a new vault and commit it
3. Confirm you can clone and pull from another machine
4. Add a `.gitignore` suitable for Obsidian workspace state (see Step 2)

## Step 2: Obsidian Desktop with obsidian-git

1. Install the `obsidian-git` plugin in Obsidian Desktop
2. Configure auto-pull and auto-push intervals
3. Set commit message patterns you can recognize later
4. Ensure workspace-local files are ignored (example):

```gitignore
.obsidian/workspace*.json
.obsidian/cache/
.trash/
```

## Step 3: Obsidian Mobile with GitSync

1. Install a GitSync-compatible Android app
2. Add an SSH key to your GitHub account (or app-specific auth)
3. Clone the same private vault repository to mobile
4. Configure periodic sync and test a note edit round trip

## Step 4: Server with markdown-vault-mcp

Follow [Docker](docker.md) for deployment details. For this topology, enable git write and server-side pull automation.

Current stable pull setting:

```bash
MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S=600
```

Recommended related variables:

```bash
MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S=30
MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME=markdown-vault-mcp
MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL=noreply@markdown-vault-mcp
```

## Step 5: Protect with Authelia

Use [OIDC Providers](oidc-providers.md#authelia) for provider-specific setup, then [Docker OIDC setup](docker.md#step-4-add-oidc-authentication) for container wiring.

Target result:

- Public endpoint requires OIDC login
- Only authenticated users can access MCP tools
- Callback URI and base URL match your deployment path

## Step 6: Connect Claude

Use [Claude Desktop](claude-desktop.md) to configure the MCP endpoint and verify tools. If you want semantic retrieval quality beyond keyword search, add embeddings via [Embeddings](embeddings.md).

Verification checklist:

1. `search` returns recent notes from git-synced vault content
2. `write` creates a new note
3. Server commits and pushes that note to the repository
4. Desktop/mobile clients pull and see the same note

### Firing prompts from Claude.ai's `+` menu

When Claude.ai is part of your setup, every MCP prompt this server exposes can be fired from the compose area's `+` menu once the server is added as a connector. Click `+`, select **connectors**, pick the server, pick a prompt — Claude opens with the invocation scaffolded. This is the recommended way to invoke multi-step prompts like `propose-links` or the PARA / Zettelkasten workflow prompts. See [How to invoke prompts](../prompts.md#how-to-invoke-prompts) for the full invocation reference.

## Limitations & troubleshooting

- Diverged branches: when both Obsidian and MCP commit on different files, the server rebases local commits onto upstream automatically. When the same file is edited on both sides, the server resolves the conflict by accepting the upstream version and saving the MCP version as a `*.conflict-mcp-YYYYMMDD-HHMMSS.md` file with `conflict_with` frontmatter on both files. Neither version is authoritative — reconcile manually or let the AI merge them.
- Squash/cherry-pick detection limitation: rewritten commit history may not map cleanly for duplicate detection.
- Git LFS: large binary attachments may need explicit LFS setup on every client and server.
- iOS: no equivalent GitSync workflow is documented here for private repositories.

## What's next

- Add semantic search for better recall: [Embeddings](embeddings.md)
- Tune git modes and policies: [Git Integration](git-integration.md)
- Add note templates to standardize AI-generated notes via the `create_from_template` prompt (see [MCP Prompts](../prompts.md#create_from_template))
