# Open Knowledge Format (OKF) with markdown-vault-mcp

[Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog) (OKF) is a vendor-neutral convention for a folder of markdown files that serves as curated context for AI agents. A bundle is a directory of notes with YAML frontmatter: one concept per file, the file path as the concept identity, and a small set of frontmatter fields that describe each note's type, lifecycle, provenance, and trust. This guide shows how markdown-vault-mcp recognises OKF bundles, what it does with the metadata, and how to move an existing vault into the format.

!!! note
    OKF support is read-only by default. Recognising a bundle only ever adds annotations and advice; it never changes your files. The migration tools that do change files are explicit, and this guide covers them near the end.

!!! tip "Example pack"
    The [`examples/okf/`](../../examples/okf/) directory ships a declaration index, typed note templates (`Concept`, `Capture`), and a prompt pack (author a concept, verify a note, triage stale content, migrate a vault) that you can copy into your vault and prompt folders.

## How the server recognises a bundle

The server treats a vault as an OKF bundle when the root `index.md` declares a version in its frontmatter:

```yaml
---
okf_version: "0.2"
---
# My bundle
```

This declaration travels with the vault, so the same bundle behaves the same way here and in other OKF tools. The `MARKDOWN_VAULT_MCP_OKF_MODE` environment variable controls how the server responds to it:

| Value | Behaviour |
|-------|-----------|
| `auto` (default) | Apply OKF read semantics when the root `index.md` declares `okf_version`. |
| `off` | Never apply OKF semantics, and hide the OKF tools. Use this if your frontmatter happens to use keys such as `status` for an unrelated purpose. |
| `on` | Force OKF semantics even without a declaration. Use this for a bundle you consume but do not own. |

Check the current state at any time through the `okf` section of the `stats` tool, or the `config://vault` resource.

## What the metadata means

On a recognised bundle, the server reads a few OKF frontmatter families and surfaces them in `search`, `read`, and `get_context` results under an `okf` key:

- **Type**: the free-text `type` field, such as `Playbook`, `Metric`, or `Reference`.
- **Lifecycle**: `status` is one of `draft`, `stable`, or `deprecated`. A note with no `status` is treated as `stable`. `stale_after: YYYY-MM-DD` marks a note stale once that date passes.
- **Trust tier**: derived from the `verified` list. A note verified by a person (`by: human:...`) is `human-reviewed`; a note verified only by a process is `machine-confirmed`; an unverified note is `unverified`.
- **Sources**: the `sources` provenance list, surfaced in full on `read` and as a count on search hits.

You can filter on these dimensions. `search` and `list_documents` accept `status`, `stale`, `trust_tier`, and `type` filters, so `{"stale": "true"}` or `{"status": "deprecated"}` builds a triage listing. See the [tools reference](../tools/index.md) for the full filter set.

## Frontmatter fields

A conformant note needs only a non-empty `type`. Everything else is optional but recommended:

```yaml
---
type: Playbook
title: Onboarding a new teammate
description: Step-by-step setup for the first week.
tags: [process, people]
status: stable
stale_after: 2027-01-01
sources:
  - resource: https://example.com/handbook
    id: handbook
verified:
  - by: human:alex
    at: 2026-08-01
---
```

## Migrating an existing vault

Adoption is incremental. A note is either conformant or not, and the server tolerates a mix, so you can convert a vault gradually rather than in one step. The recommended order is a ratchet: declare early so new notes are written correctly, then converge the backlog.

### 1. Audit

Run the `okf_validate` tool to see where the vault stands. It reports conformance as a degree rather than a pass or fail. The report gives the conformant count out of the total, alongside a per-rule breakdown with example paths (notes missing a `type`, unknown `status` values, and the like). The audit reads from disk, so it works before you declare anything. Use it to decide whether to start.

### 2. Declare

Add `okf_version: "0.2"` to the root `index.md` frontmatter. From this point the server annotates results, the filters work, and the agent receives guidance to keep OKF conventions when it edits. Declaring early means every new note is authored conformantly while you work through the backlog.

### 3. Enrich

Backfill the missing metadata. This is where most of the work is, and an agent can do it note by note. For each note it proposes a `type` from the content and fills in the `title` and `description`. Approve the changes in batches, then re-run `okf_validate` to watch the conformant count climb.

### 4. Mechanical transforms

Three tools handle the changes you should not make by hand. They are write tools, so they are hidden in read-only mode and when `OKF_MODE` is `off`, and on a git-backed vault each change is committed.

- **`okf_convert_links`** rewrites `[[wikilinks]]` as the root-absolute markdown links OKF recommends, such as `[text](/guides/note.md)`. Only links whose target exists are converted, so your link graph is preserved exactly. Unresolvable wikilinks are left alone and reported. You can write in either link style day to day; run this before sharing the bundle.
- **`okf_generate_index`** writes a folder's `index.md` as a listing of the notes directly in that folder, plus a pointer into each subfolder's own `index.md`. It draws the description from each note's frontmatter and lists one level at a time, so a nested bundle stays navigable rather than flattening into one long page. It preserves existing frontmatter, so regenerating the root `index.md` keeps your `okf_version` declaration.
- **`okf_seed_log`** creates a `log.md` change history from the vault's git commits, newest first. The `folder` argument both places the log and scopes its content: seeding a folder includes only the commits that touched that subtree, while seeding the bundle root includes the whole vault's history. It refuses to overwrite an existing `log.md`, so a hand-maintained history is safe.

### Reserved files on a vault with required fields

If you set `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS`, documents missing any listed field are excluded from the index. The generated `index.md` and `log.md` are ordinary documents to the indexer, so the generators give them the fields your vault requires. Your title field is filled in from the file's heading; any other required field is written empty, for you to complete. Without this the bundle's own listing and change history would be absent from `search` and `list_documents` while still opening through `read`.

Anything already in the file wins, so a title you wrote yourself and the root `index.md`'s `okf_version` declaration are left alone. On a vault that sets no required fields nothing changes: the reserved files carry no frontmatter, exactly as before.

## The enforced write layer

The steps above keep a bundle conformant by convention. The enforced write layer makes the server keep two of those fields correct on its own. Turn it on by setting `MARKDOWN_VAULT_MCP_OKF_WRITE=true`. It requires `OKF_MODE` to be `auto` or `on`; pairing it with `OKF_MODE=off` is a configuration error, since there is nothing to enforce. When the flag is off the write path is untouched, so an ordinary vault behaves exactly as before.

With the layer on, and only while the vault is an active OKF bundle, every `write` and `edit` does two things to the note that lands:

- **Stamps provenance.** The server writes `generated: {by, at}` describing the bytes it just saved. `at` is the write date. `by` is `human:<subject>` when the caller is authenticated, and a tool actor such as `markdown-vault-mcp/1.4.0` otherwise. Any existing `generated` value is replaced.
- **Invalidates prior review.** A content change means an earlier human review no longer describes the current note, so the server clears `verified`. This fires on any content-changing write, including an edit that touches only a frontmatter line. A `rename` moves the file without changing its content, so it leaves both fields alone.

The one-shot migration transforms above (`okf_convert_links`, `okf_generate_index`, `okf_seed_log`) are exempt: a mechanical rewrite does not re-stamp provenance or discard a human attestation.

### Recording a human review

Enabling the layer also exposes the [`okf_verify`](../tools/index.md#okf_verify) tool. Call it on a note you have reviewed and it appends a `{by: human:<subject>, at}` entry to that note's `verified` list, which promotes the note's trust tier to `human-reviewed`. The verification write is exempt from the invalidation above, so attesting a note does not immediately clear the attestation you just added.

One subtlety is worth understanding before you rely on the `human-reviewed` tier. The authenticated subject is *whose token* made the call, not proof that a person read the note. When an agent holds your token and attribution rests on the token alone, the model could promote a note to `human-reviewed` on its own, and the tier would mean nothing. `MARKDOWN_VAULT_MCP_OKF_VERIFY` controls how the tool guards against that. It applies only when `OKF_WRITE` is on. Setting it to a non-default value with the layer off is a configuration error, because the tool is hidden and the setting would have no effect.

- **`elicit`** (the default) makes `okf_verify` ask you to confirm the review through an MCP [elicitation](https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation), a prompt your client shows you, and it writes the `verified` entry only when you answer yes. It fails closed. When your client cannot show the prompt or you decline it, the call returns a tool error and writes nothing. A model cannot answer the prompt on your behalf, so a headless agent with no human present can never produce a `human-reviewed` entry. The recorded subject is your authenticated identity when the server has auth, or `local` when it does not.
- **`off`** hides the tool entirely. Use this when reviews are recorded only by tooling outside the server (a CLI step, a git hook, or a CI job that writes `verified` directly) and you want no in-session path to the tier at all.
- **`trust-auth`** is the original behaviour: attribute to the authenticated caller with no confirmation, and refuse when the server runs with no auth. Choose it only when the sole caller of `okf_verify` is a human-driven interface rather than an agent.

Whichever mode you pick, `human-reviewed` means a person deliberately confirmed the review, not that the note is provably correct. Someone can still rubber-stamp a note. The tier promises a deliberate human act rather than diligence, so treat it as one signal instead of a guarantee.

### Keeping `log.md` and `index.md` current

With the layer on, the server also maintains a folder's reserved files as a side effect of writing into it. After a successful `write` or `edit` on a note, it appends a dated `**Update**` bullet to that folder's `log.md` (creating the log, and the day's `## YYYY-MM-DD` section, when needed) and regenerates the folder's `index.md` listing so a new note shows up without a manual step. These are the guaranteed versions of the upkeep the advisory layer otherwise asks the agent to do by hand.

A few boundaries keep this predictable. Maintenance runs only for content writes on an active bundle; a `rename` or `delete` does not trigger it, and neither does a write whose target is itself a reserved file (so editing `index.md` by hand is left alone). The `okf_verify` tool and the one-shot migration transforms are exempt, so an attestation or a mechanical rewrite does not churn the reserved files. Only the folder directly containing the written note is refreshed: if a write creates a brand-new subfolder, its pointer in the parent `index.md` appears on the next write into that parent. A failure to update a reserved file is logged and skipped; it never fails or rolls back the note write that triggered it.

Two costs come with this upkeep, and they are the price of the guarantee rather than bugs. Each `write` or `edit` now waits for the search index to catch up before it regenerates `index.md`, so that a just-created note is listed; on a busy vault or with a slow embedding backend this adds latency to the write, bounded at ten seconds before it proceeds with a best-effort listing. And because the `log.md` and `index.md` updates are ordinary writes, a git-backed vault records them as their own commits, so one logical "save a note" call can produce up to three commits: one for the note and one for each reserved file it refreshes. Turn `OKF_WRITE` off if you want the plain single-write behaviour and prefer to maintain the reserved files yourself.

## Using OKF with PARA or Zettelkasten

OKF composes with the [PARA](para.md) and [Zettelkasten](zettelkasten.md) methods. They organise where notes live and how work flows; OKF describes what each note is. A PARA or Zettelkasten vault can also be an OKF bundle. Three points of overlap matter (each method's guide has a matching section from its own angle: [Using PARA with OKF](para.md#using-para-with-okf), [Using Zettelkasten with OKF](zettelkasten.md#using-zettelkasten-with-okf)):

- **Status vocabulary**: PARA uses `status` for workflow state (`active`, `archived`), while OKF uses it for lifecycle (`draft`, `stable`, `deprecated`). These mean different things, so keep PARA's workflow state in its own frontmatter key and reserve `status` for OKF lifecycle. OKF preserves any extra keys you add.
- **Untyped inbox notes**: PARA's inbox holds quick captures that are typed later. Give them a placeholder `type: Capture` so they are conformant from the start, and let triage set the real type.
- **Wikilinks**: write in whichever link style you prefer. The server resolves both, and `okf_convert_links` produces the OKF link style when you are ready to share the bundle.

## Exporting a bundle

When you want a conformant copy to share or hand to other OKF tooling, download one through the standard `create_download_link` tool with a bundle reference rather than a file path:

- `create_download_link` with `ref="okf-bundle"` returns a one-time URL for a zip of the whole vault.
- `create_download_link` with `ref="okf-bundle:guides"` scopes the zip to a folder subtree.

The export reads from the live vault and never changes it. Wikilinks become the root-absolute markdown links OKF recommends. Convention files (`_conventions.md`) and the template folder are left out, while the reserved `index.md` and `log.md` stay in. Non-conformant notes appear as they are, so the archive is a faithful snapshot. Run `okf_validate` for the residual conformance gaps; the archive itself carries no gap report.

Bundle export needs an HTTP or SSE transport with `MARKDOWN_VAULT_MCP_BASE_URL` set, the same requirement as any transfer link, and it is unavailable when `OKF_MODE` is `off`. A git-backed vault also remains a shareable bundle on its own: the repository is an interchange format.
