---
description: "How the server recognizes an Open Knowledge Format bundle, what it adds to results, and the tools that audit and migrate one."
kind: how-to
---

# Open Knowledge Format (OKF) with markdown-vault-mcp

[Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog) (OKF) is a vendor-neutral convention for a folder of markdown files that serves as curated context for AI agents. A bundle is a directory of notes with YAML frontmatter: one concept per file, the file path as the concept identity, and a small set of frontmatter fields that describe each note's type, lifecycle, provenance, and trust. This guide shows how markdown-vault-mcp recognises OKF bundles, what it does with the metadata, and how to move an existing vault into the format.

!!! note
    OKF support is read-only by default. Recognising a bundle only ever adds annotations and advice; it never changes your files. The migration tools that do change files are explicit, and this guide covers them near the end.

!!! tip "Example pack"
    The [`examples/okf/`](https://github.com/pvliesdonk/markdown-vault-mcp/tree/main/examples/okf/) directory ships a declaration index, typed note templates (`Concept`, `Capture`), and a prompt pack (author a concept, verify a note, triage stale content, migrate a vault) that you can copy into your vault and prompt folders.

Live whole-note structured reads in an active bundle, or an undeclared vault with the legacy review alias enabled, include `okf.snapshot` with algorithm `mvmcp-concept-v1` and a SHA-256 digest. It identifies the body and frontmatter except `verified` and `x-mvmcp`; resolved YAML scalar text, including spellings of true and null, and JSON number spellings count; YAML string quoting style does not. Converting a header between YAML and JSON can change the snapshot. Optional TOML uses the numeric and timestamp precision of its parser. The etag distinguishes raw bytes. Pass this snapshot as `reviewed_snapshot` to `okf_verify` after reviewing the note. The digest alone does not prove who recorded a review. Historical and section reads and search hits omit it. In the CLI, use `--json` or `--agent` to see the snapshot.

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

Check the current state through `config://vault`, the `stats` tool, or `markdown-vault-mcp stats --json`. The resource's `okf_capabilities` and `stats.okf.capabilities` carry the same configured values, effective true/false values, reasons and folder scope, even when OKF is inactive. Reasons follow this order: `read_only` applies to every capability on a read-only vault, including disabled ones. On a writable vault a disabled capability has no reason. For enabled capabilities, `okf_inactive` comes next, except that legacy verification can remain effective in an undeclared vault when `OKF_WRITE` is enabled. Then `not_implemented` applies to event logging, and unrecognized library-supplied verify or log modes; finally, `not_designated` applies to index and write logging with an empty folder scope supplied through the library. Effective describes vault-level authority; identity, elicitation and etag checks still apply to individual calls. An effective index or write-log capability applies only within the reported folder scope; a nonempty scope does not mean every path is designated.

The resource's `okf_write_configured` and `okf_write_effective` report the legacy stamping layer; active `stats.okf` reports them as `write_configured` and `write_effective`. The layer is effective when configured, OKF is active, and the vault is not read-only. When inactive, `stats.okf` contains only `capabilities`. Detection reflects changes to the declaration on the next call.

## Authoring a concept

Enable `MARKDOWN_VAULT_MCP_OKF_AUTHOR=true` on a writable, active OKF vault to expose [`okf_author`](../reference/tools/writer.md#okf_author). Read the concept first and pass its `etag` as `if_match`, together with the editorial `fields` to change. The operation keeps the body, reviews, and fields omitted from the request. An explicit `producer` records `generated.by` and a server-generated UTC timestamp; omit it to leave existing generation metadata unchanged. Authentication identifies the caller for commits and does not identify the content producer.

In auto mode, list tools again after adding or removing the root bundle declaration to see the current authoring availability.

Fields and `generated` children omitted from the request retain their original text, including comments and formatting. Edited lists use flow form; comments inside an edited field move below it. Supply non-empty strings for `type`, `title`, and `description`, a list of strings for `tags`, and source entries with a non-empty `resource` string. `status` accepts `draft`, `stable`, or `deprecated`, and `stale_after` accepts an ISO date or offset datetime. Empty requests, invalid values, non-YAML or ambiguous frontmatter, and edits that cannot preserve the reader’s view are refused.

## What the metadata means

On a recognised bundle, the server reads a few OKF frontmatter families and surfaces them in `search`, `read`, and `get_context` results under an `okf` key:

- **Type**: the free-text `type` field, such as `Playbook`, `Metric`, or `Reference`.
- **Lifecycle**: `status` is one of `draft`, `stable`, or `deprecated`. A note with no `status` is treated as `stable`. `stale_after` marks a note stale from a date on (`2026-12-31`) or from an instant on (`2026-12-31T00:00:00Z`; the offset is required).
- **Recorded review tier**: `trust_tier` describes supplied review claims. A record with a usable `human:<id>` actor and an offset-aware `at` gives `human-reviewed`; otherwise, usable records give `machine-confirmed`, and no usable record gives `unverified`. Malformed entries do not promote the tier. The server does not certify the reviewer, truth, or applicability to the current revision.
- **Evidence**: `freshness.state` distinguishes `missing`, `malformed`, `unexpired`, and `expired`. `review` separates recorded and malformed entry counts. Whole reads include the parsed values under `recorded`; compact results keep the summary. `stale: false` includes absent or malformed expiry, so it does not establish freshness.
- **Review applicability**: `review_applicability` reports `snapshot: unbound` and `basis: no_snapshot_binding` for supplied entries, including malformed entries. Reads do not evaluate saved binding claims yet; `review` reports entry validity. No timestamp or extension establishes coverage of the current content in this assessment. Absent or empty reviews report `review: none`; malformed outer values report `review: unknown`. This assessment is separate from freshness and the recorded review tier.
- **Sources**: the `sources` provenance list, surfaced in full on `read` and as a count on search hits.

The CLI labels recorded review metadata and unknown applicability in human-readable search output. For whole-note text reads, it sends that notice to `stderr`, leaving the note content on `stdout` unchanged. The app's note and context views show the same uncertainty beside any supplied `verified` field, including empty or malformed values; the original metadata stays available. These notices change only the human-readable presentation. JSON and agent output retain their existing payloads.

You can filter on these dimensions. `search` and `list_documents` accept `status`, `stale`, `trust_tier`, and `type` filters, so `{"stale": "true"}` or `{"status": "deprecated"}` builds a triage listing. On an active OKF bundle, use `{"type": null}` with `search` or `list_documents` to find searchable notes without a non-empty string type; reserved `index.md` and `log.md` files are excluded. Non-string values such as `type: 42` count as missing a usable type, while the existing string equality filter `{"type": "42"}` still matches that value. The filter judges indexed metadata: an unquoted date such as `type: 2026-11-17` is indexed as the string `"2026-11-17"` and does not match null. The null selector is available only to `search` and `list_documents`; CLI and exact query filters accept strings. See [`list_documents`](../reference/tools/reader.md#list_documents) for the full filter set.

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
    at: "2026-08-01T09:14:00Z"
---
```

## Inspect citations in one note

Call `inspect_citations(path="research/note.md")` for an exact path you found with
`search` or `list_documents`. The library equivalent is
`vault.reader.inspect_citations("research/note.md")`. This opt-in read works
without a bundle declaration, including when OKF annotations are off.

It joins body markers such as `[^handbook]` to frontmatter `sources` entries whose
`id` is exactly `handbook`. The result includes the literal parsed source records,
unknown fields included. `source_indices` identifies matching records by their
zero-based positions in that response. Those positions can change when sources
are reordered; the exact string `id` is the stable key. Labels are not trimmed,
case-folded or coerced from numbers.

- `matched`: exactly one source entry has the label's ID, even if that entry has
  a separately reported source-shape issue.
- `ambiguous`: multiple entries have that ID, even if their resources are equal.
- `unmatched`: no entry has that ID. This may be an ordinary explanatory footnote,
  not a broken citation.

The report also lists duplicate IDs whether cited or not, entries without IDs,
and source-shape issues. An ID is optional; an entry without one is not invalid.
A resource may describe a scope rather than a location. URLs and paths are also
accepted. The inspector does not open resources or check whether they exist. It
does not assess whether a source supports a claim or whether the claim is true.

### Find notes citing a source

Call `find_citing_notes(resource="https://example.org/source")` to find indexed
notes with literal footnote labels matching a source record for that
exact resource. The result includes label occurrence counts and unresolved or
ambiguous labels, plus labels whose matched source has no valid resource, in notes
declaring the resource. Results are unbounded. When OKF is active, reserved
`index.md` and `log.md` files are excluded. Body links alone do not count.
On the first writer startup after upgrading from index semantics version 12, the
index rebuilds. `unconverted_notes` counts indexed notes that could not be re-read
with compatible citation rules. If ordinary reindex leaves the count above zero,
call the MCP tool `reindex(force=True)` or the library method
`build_index(force=True)` for a full rebuild.
The lookup does not fetch resources or assess whether they support a claim.

### Syntax coverage

The reader uses CommonMark block and inline parsing with a small literal marker
extension. It recognizes single-line labels with no whitespace, brackets or
backslashes. It skips code spans and fences, escaped markers, HTML blocks and
attributes, navigation links, images, wikilinks, and footnote definition blocks.
A citation beside inline code or a navigation link is still recognized. Footnote
definition prose never supplies the source join. Other Markdown dialect
extensions are not interpreted; an empty result is not an exhaustive citation
audit. The response's `syntax` and `scope` fields retain these limits.

The existing whole-note read limits apply. The source list and body are parsed
from one returned content string. `returned_text_sha256` hashes that complete
string after line endings are normalized to LF and it is encoded as UTF-8. It
is not the file's raw-byte etag or a portable file identity, and cannot be used
as `if_match`. No index refresh, traversal, metadata write or permission change
is part of this call. See the [tool reference](../reference/tools/reader.md#inspect_citations).

## Migrating an existing vault

Adoption is incremental. A note is either conformant or not, and the server tolerates a mix, so you can convert a vault gradually rather than in one step. The recommended order is a ratchet: declare early so new notes are written correctly, then converge the backlog.

### 1. Audit

Run the `okf_validate` tool to see where the vault stands. It reports conformance as a degree rather than a pass or fail. The report gives the conformant count out of the total, alongside a per-rule breakdown with example paths (notes missing a `type`, unknown `status` values, and the like). The audit reads from disk, so it works before you declare anything. Use it to decide whether to start.

The evidence findings report missing expiry, malformed expiry or review records, and unavailable assessments. An assessment is unavailable when a discovered concept cannot be read or parsed, or exceeds the read cap. Missing expiry is informational; malformed fields are format observations, not failures of a team's editorial policy. These findings leave the existing conformance ratio unchanged. The report covers discovered, non-excluded paths and does not certify complete traversal or current-review applicability.

The report also lists `index.md` files that carry frontmatter the format does not allow. That includes older generated index files containing seeded fields. Existing fields are preserved; reserved files remain indexed in an active OKF vault even without them. A declaration change reaches reserved files on the next indexing pass, and upgrading an older index rebuilds it once.

### 2. Declare

Add `okf_version: "0.2"` to the root `index.md` frontmatter. From this point the server annotates results, the filters work, and the agent receives guidance to keep OKF conventions when it edits. Declaring early means every new note is authored conformantly while you work through the backlog.

### 3. Enrich

Backfill the missing metadata. This is where most of the work is, and an agent can do it note by note. For each note it proposes a `type` from the content and fills in the `title` and `description`. Approve the changes in batches, then re-run `okf_validate` to watch the conformant count climb.

When using `write` to update an existing note or root index, read its current
content first and pass that read's etag as `if_match`. New files omit
`if_match`. A failed replacement leaves the existing file intact; report the
conflict before continuing with any dependent migration steps.

### 4. Mechanical transforms

Three tools handle the changes you should not make by hand. They are write tools, so they are hidden in read-only mode and when `OKF_MODE` is `off`, and on a git-backed vault each change is committed.

- **`okf_convert_links`** rewrites `[[wikilinks]]` as the root-absolute markdown links OKF recommends, such as `[text](/guides/note.md)`. Spaces in a note or folder name are percent-encoded, as Obsidian does when it writes a link, so `Project Notes/note.md` becomes `[text](/Project%20Notes/note.md)` and stays a link everywhere. Characters that would make the link mean something else are backslash-escaped: a `#` or an unbalanced parenthesis in the path, a bracket in the text. Only links whose target exists are converted, so your link graph is preserved exactly. Unresolvable wikilinks are left alone and reported. You can write in either link style day to day; run this before sharing the bundle.
- **`okf_generate_index`** writes a folder's `index.md` as a listing of the notes directly in that folder, plus a pointer into each subfolder's own `index.md`. A subfolder without an `index.md` gets one, at every level below, so no pointer leads to a missing file; a subfolder that already has one is left as it is. It draws the description from each note's frontmatter and lists one level at a time, so a nested bundle stays navigable rather than flattening into one long page. It preserves existing frontmatter, so regenerating the root `index.md` keeps your `okf_version` declaration.
- **`okf_seed_log`** creates a `log.md` change history from the vault's git commits, newest first. The `folder` argument both places the log and scopes its content: seeding a folder includes only the commits that touched that subtree, while seeding the bundle root includes the whole vault's history. It refuses to overwrite an existing `log.md`, so a hand-maintained history is safe.

### Reserved files on a vault with required fields

If you set `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS`, concept notes missing any listed field are excluded from the index. In an active OKF vault, reserved `index.md` and `log.md` files are indexed without those fields. Generators create reserved files without frontmatter and do not add fields during regeneration. Before generating in a vault with required fields, enable OKF scope with `MARKDOWN_VAULT_MCP_OKF_MODE=on`, or use `auto` mode with a root `okf_version` declaration; outside that scope the normal gate applies and `get_index_status` lists such files as skipped. Deliberate removal of old seeded fields is planned separately. If the root declaration cannot be read and no confirmed admission state is available, indexing fails safely; retry after the read error is resolved.

Anything already in the file wins, so a title you wrote yourself and the root `index.md`'s `okf_version` declaration are left alone. Existing headers, including legacy seeded fields, are retained byte-for-byte; the audit continues to report disallowed index fields.

## Writing and provenance

Ordinary writes preserve supplied `generated` and `verified` fields. Body-only edits and appends preserve the frontmatter bytes, including comments and line endings. A caller's credentials do not establish who authored a note. Use [`okf_author`](../reference/tools/writer.md#okf_author) with an explicit producer to record authorship. The deprecated `OKF_WRITE` alias enables authoring, review, index and log maintenance; explicit individual settings override it. See the [migration guide](okf-provenance-migration.md).

Enabling `OKF_WRITE` on a read-only deployment is valid, so a writable server and a read-only mirror can share an environment file. Authoring, review and reserved-file upkeep are inactive there; OKF read semantics remain available. Startup logs `okf_write_inert` at INFO. The `config://vault` resource reports the alias as `okf_write_configured` and `okf_write_effective`, which is true when the alias is set, OKF is active and the vault is writable. When OKF is active, the `stats` tool's `okf` section reports the same values as `write_configured` and `write_effective`. An effective alias stamps nothing.

### Recording a human review

Enabling review with `MARKDOWN_VAULT_MCP_OKF_VERIFY=elicit` or `trust-auth` exposes the [`okf_verify`](../reference/tools/writer.md#okf_verify) tool. Call it on a note you have reviewed and it appends a `{by: human:<subject>, at}` entry (`at` a UTC instant) to that note's `verified` list, which promotes the note's trust tier to `human-reviewed`. Independent review requires an active bundle; the legacy alias also permits review of an undeclared vault in auto mode.

One subtlety is worth understanding before you rely on the `human-reviewed` tier. The authenticated subject is *whose token* made the call, not proof that a person read the note. When an agent holds your token and attribution rests on the token alone, the model could promote a note to `human-reviewed` on its own, and the tier would mean nothing. `MARKDOWN_VAULT_MCP_OKF_VERIFY` controls how the tool guards against that. It can be configured independently of `OKF_WRITE`. Unset resolves to `off`, or to `elicit` when `OKF_WRITE` is enabled. Explicit `elicit` or `trust-auth` with `OKF_MODE=off` is a configuration error, including for configurations that previously accepted explicit `elicit`.

- **`elicit`** (the default with `OKF_WRITE` enabled) makes `okf_verify` ask you to confirm the review through an MCP [elicitation](https://gofastmcp.com/servers/elicitation), a prompt your client shows you, and it writes the `verified` entry only when you answer yes. It fails closed. On modern MCP connections the tool returns an input request and your client repeats the call with your answer; on older handshake connections it uses server-initiated elicitation. When your client cannot show the prompt or you decline it, the call fails and writes nothing. Use a client whose elicitation handler actually presents the question to a person: an automatically affirmative handler would defeat the human-review gate. The recorded subject is your token's `sub` when it identifies a person, or `local` when you use a service credential, a token the issuer marks as an application's, a token whose `sub` is listed in `MARKDOWN_VAULT_MCP_SERVICE_SUBJECTS`, or no auth.
- **`off`** hides the tool entirely. Use this when reviews are recorded only by tooling outside the server (a CLI step, a git hook, or a CI job that writes `verified` directly) and you want no in-session path to the tier at all.
- **`trust-auth`** attributes to a token's `sub` with no confirmation. It refuses static bearer credentials, other client-ID-only identities, OAuth tokens the issuer marks as an application's or whose `sub` is listed in `MARKDOWN_VAULT_MCP_SERVICE_SUBJECTS` (see above), and callers without auth. Choose it only when the sole caller of `okf_verify` is a human-driven interface rather than an agent.

**Upgrading bearer-based review clients:** deployments that used `trust-auth` with a static or mapped bearer token must switch `MARKDOWN_VAULT_MCP_OKF_VERIFY` to `elicit` and use a client that presents the confirmation to a person. Existing provenance and verification entries are not rewritten.

The verification mode controls how this tool records a review claim. The `human-reviewed` tier itself only reports a `human:` prefix in supplied metadata, which can also arrive from other editors or imports. It does not prove that a person reviewed the note or that its content is correct.

A `verified` entry records the reviewer and time in the portable format. `okf_verify` also appends a claim in `x-mvmcp.reviews` with the reviewed snapshot and check (default `human-review`). Its confirmation shows the path, digest prefix and check; a stale snapshot or an edit detected before the write is refused. Read the note again and review the current content before retrying. Other editors can supply or alter `x-mvmcp`, so the claim alone does not prove a local action. The server also stores a matching local receipt beside its observation ledger, outside the bundle. With in-memory state the receipt is lost on restart. Bound reviews require YAML frontmatter or a body-only note; convert JSON or TOML frontmatter to YAML before reviewing. The snapshot excludes `verified` and `x-mvmcp`. Review writes use the existing whole-note read, which normalizes line endings to LF. Receipt storage must be outside the vault. Back up the `.receipts.json` sibling of the configured state file with the observation ledger; each receipt retains the path at recording time, even if the note is later renamed. A receipt failure after the note write leaves the claim without local evidence; read the note before taking further action. Later edits retain the review; the tier does not establish that it covers current content.

### Keeping `log.md` and `index.md` current

With `OKF_INDEX=true` or `OKF_LOG=writes`, the server maintains the corresponding reserved files in designated folders as a side effect of writing into them. The deprecated `OKF_WRITE` alias enables both by default; explicit `OKF_INDEX` and `OKF_LOG` settings override it. `OKF_LOG=events` does not append entries for ordinary writes. `OKF_FOLDERS` defaults to the root (`.`); `**` designates every folder. Globs match full folder paths: `*`, `?` and character classes match within one path segment; a `**` segment matches recursively. Leading `./` or `/` and trailing `/` are ignored, so `docs/` and `./docs` designate `docs`. The pattern `.,*,*/*` designates the root and two folder levels. `a/**` does not include `a` itself. Designation follows the resolved vault-relative folder, including for `.` and `..` paths and symlink aliases. Automatic maintenance skips a reserved file when it points to a different file or folder; directory aliases still work. Explicit index generation remains unrestricted by this automatic maintenance scope. A write in a folder that is not designated does not trigger maintenance. After a successful `write` or `edit` on a note, it appends a dated `**Update**` bullet to that folder's `log.md` (creating the log, and the day's `## YYYY-MM-DD` section, when needed) and regenerates the folder's `index.md` listing so a new note shows up without a manual step. Index refresh traverses designated parent folders and stops at the first parent that is not designated. Eligible missing indexes are created along that contiguous path. Existing indexes in folders that are not designated are preserved and linked, while missing indexes in folders that are not designated are neither created nor linked. That covers the upkeep the advisory layer otherwise asks the agent to do by hand, on the write paths below and no others.

A `delete`, `rename` or `move_folder` refreshes eligible listings in designated folders, subject to the same parent traversal boundary. An ordinary folder move is refused if its source contains a designated index. After an otherwise permitted folder move, or a trusted library move with `allow_protected=True`, designated indexes that moved are regenerated for their new paths. These changes add no `log.md` entry. An allowed rename to or from `index.md` or `log.md` leaves the listings alone, so renaming a note onto a reserved name keeps its content.

After a folder move, the refresh skips folders inside a git submodule or nested repository, and their descendants. It still updates ordinary containing folders and their other descendants, without attempting to rewrite those listings. The [Git integration guide](git-integration.md#submodules-and-nested-repositories-in-the-vault) explains the read-only boundary.

A few boundaries keep this predictable, and they are also its limits. Maintenance runs only on an active bundle. A write whose target is itself a reserved file does not trigger it. The `okf_verify` tool and the one-shot migration transforms are exempt, so an attestation or a mechanical rewrite does not churn the reserved files. Only one listing is regenerated per write: that of the folder directly containing the note, or, when it has no `index.md`, that of the nearest folder above it with one. A folder's own listing is not otherwise refreshed when a note lands in one of its subfolders. A failure to update a reserved file is logged and skipped; it never fails or rolls back the note write that triggered it, so a listing can silently fall behind.

**Maintained indexes are generated.** Ordinary client mutations refuse `index.md` in designated folders while index maintenance is enabled on an active OKF vault. Use `okf_generate_index` to regenerate it. Edits made directly on disk or arriving through a git pull bypass this guard and may be replaced by later maintenance.

Folder paths and designation globs are normalized to NFC before matching, even when the disk keeps differently normalized names distinct. Case and index filename variants follow the disk's identity rules. On a case-folding disk, `[!a]*` also reserves `a` because `A` matches; `[!aA]*` excludes both. For ambiguous spellings, the server briefly creates and removes a hidden scratch directory in the nearest existing folder. A reserved leaf symlink to an authored note remains editable; an alias to a designated index stays protected. If the probe cannot run, the server assumes the disk folds both case and Unicode when checking the designation. Other indexes and every `log.md` remain editable. A library caller can opt in with `allow_protected=True`. Explicit protected paths still apply to generation and automatic maintenance.

One cost comes with this upkeep. Each `write` or `edit` waits for the search index to catch up before it regenerates `index.md`, so that a just-created note is listed; on a busy vault or with a slow embedding backend this adds latency to the write, bounded at 60 seconds. A refresh failure keeps the existing listing and logs a warning; the note write is not rolled back. Embedding jobs already in the queue can delay the refresh, but it does not wait for embeddings queued after it. On a git-backed vault the reserved-file updates are not extra commits: every write a tool call makes is committed together, so saving a note produces one commit naming the note, its `log.md` and its `index.md`.

To stop upkeep, set `MARKDOWN_VAULT_MCP_OKF_INDEX=false` and `MARKDOWN_VAULT_MCP_OKF_LOG=off`; explicit settings override the alias. Disabling the layer stops further upkeep without repairing anything already written. Stamps the layer added stay in their notes, and a `log.md` that collected repeated frontmatter blocks from a version before 4.2 keeps them. Ordinary writes leave those blocks alone. The deliberate repair below requires review because the server cannot distinguish a block it stacked from one the log quotes on purpose.

## Repairing legacy log frontmatter

Versions 4.1.0 and 4.2.0-rc.0 could stack identical frontmatter blocks at the
start of `log.md`. The [`admin repair-log`](../reference/cli.md#markdown-vault-mcp-admin-repair-log)
command repairs one such file at a time. Without `--apply` it only previews.
As an operator command rather than an MCP tool, it is out of every client's
reach.

Preview the affected log with its vault-relative path:

```bash
markdown-vault-mcp admin repair-log guides/log.md
```

The preview prints the exact bytes it would remove, with their byte offsets,
between `>>> removal begins` and `<<< removal ends`. Below them it prints the
command that applies the removal. It keeps the opening block and removes only the
identical copies directly after it. Matching blocks alone do not prove the old
defect wrote them: a log can quote its own frontmatter on purpose. Apply only
when nothing in the removal was written deliberately. `Nothing to repair`
means the file holds no supported repeated run: differing, interleaved or
malformed blocks are left for you to edit by hand.

To apply, run the printed command. It carries the `etag` of the log as you
previewed it:

```bash
markdown-vault-mcp admin repair-log guides/log.md --apply <etag>
```

If the log changed after the preview, apply refuses and changes nothing;
preview it again and review the new removal. Apply first keeps the complete
original as `log.md.frontmatter-repair.bak` beside the log, then removes only
the previewed repeats. The opening frontmatter, provenance fields, line endings
and the rest of the file stay byte for byte; no `log.md` entry or provenance
stamp is added. On systems that enforce Unix file modes, only the account
that ran the command can read the recovery file. It is not indexed as a note.
In a Git-backed vault both files are committed like any other write. Keep the
recovery file until you have checked the result. The same preview and apply
are available to library callers as `Vault.writer.okf_preview_log_repair` and
`Vault.writer.okf_apply_log_repair`. The library apply call refuses if either
the log or recovery path is protected. A trusted library caller can pass
`allow_protected=True`; the admin CLI passes it to preserve operator repair.

Any existing recovery entry blocks apply, even one with the same bytes. If
apply stops part-way, its error says whether the log may already have
changed. The recovery file may then be partial or missing, so inspect both
files. Apply never reuses or deletes the recovery file: move it somewhere safe
before a retry.

Stop the server before applying. Apply refuses while a server holds the
on-disk index (`MARKDOWN_VAULT_MCP_INDEX_PATH`), but a server with an
in-memory index, another editor, or a sync tool writing the same file is not
detected. Apply is not a transaction and gives no guarantee across a power
loss. Apply does not update the search index. The next `admin reindex`, or
the reindex the server runs when it starts, finds the changed log.

## Using OKF with PARA or Zettelkasten

OKF composes with the [PARA](para.md) and [Zettelkasten](zettelkasten.md) methods. They organise where notes live and how work flows; OKF describes what each note is. A PARA or Zettelkasten vault can also be an OKF bundle. Three points of overlap matter (each method's guide has a matching section from its own angle: [Using PARA with OKF](para.md#using-para-with-okf), [Using Zettelkasten with OKF](zettelkasten.md#using-zettelkasten-with-okf)):

- **Status vocabulary**: PARA uses `status` for workflow state (`active`, `archived`), while OKF uses it for lifecycle (`draft`, `stable`, `deprecated`). These mean different things, so keep PARA's workflow state in its own frontmatter key and reserve `status` for OKF lifecycle. OKF preserves any extra keys you add.
- **Untyped inbox notes**: PARA's inbox holds quick captures that are typed later. Give them a placeholder `type: Capture` so they are conformant from the start, and let triage set the real type.
- **Wikilinks**: write in whichever link style you prefer. The server resolves both, and `okf_convert_links` produces the OKF link style when you are ready to share the bundle.

## Exporting a bundle

When you want a copy to share or hand to other OKF tooling, download one through the standard `create_download_link` tool with a bundle reference rather than a file path:

- `create_download_link` with `ref="okf-bundle"` returns a one-time URL for a zip of the whole vault.
- `create_download_link` with `ref="okf-bundle:guides"` scopes the zip to a folder subtree.

The export reads from the live vault and never changes it. Wikilinks become the root-absolute markdown links OKF recommends. Convention files (`_conventions.md`) and the template folder are left out, while the reserved `index.md` and `log.md` stay in. Non-conformant notes appear as they are.

It is best-effort packaging rather than a certified publication, in three specific ways. It packages what the **index** currently holds. A note the index left out, whether excluded by pattern, missing a configured required field, or carrying frontmatter that does not parse, is absent from the archive without being reported. It reads the vault as it goes rather than from a point-in-time snapshot, so a write that lands mid-export can leave the archive internally inconsistent; quiesce writes if that matters. And nothing validates the result: the archive carries no conformance report, and building one does not run `okf_validate`. Run `okf_validate` yourself before you publish, and read `get_index_status.skipped_files` to see what the index is leaving out.

Bundle export needs an HTTP or SSE transport with `MARKDOWN_VAULT_MCP_BASE_URL` set, the same requirement as any transfer link, and it is unavailable when `OKF_MODE` is `off`. A git-backed vault also remains a shareable bundle on its own: the repository is an interchange format.

The independent `OKF_AUTHOR`, `OKF_VERIFY`, `OKF_INDEX`, `OKF_LOG`, and `OKF_FOLDERS`
settings control authoring, review and maintenance. `OKF_AUTHOR` does not enable
implicit stamping. `OKF_WRITE=true` enables these capabilities and logs a deprecation notice; explicit settings override its resolved defaults. See the [configuration reference](../reference/configuration.md).


Protected paths also prevent automatic index and log upkeep. A primary write
to an unprotected note still succeeds when upkeep is blocked. Explicit OKF
bulk transforms finish eligible writes before reporting protected paths they
skipped. The refusal repeats until the operator converts the protected notes or
changes the setting; see [protected paths](../reference/configuration.md).
Pulls through `git_sync`, CLI admin commands and edits outside the client bypass
the client write guard.

### Local observation state

A build or reindex records the latest observed state of Markdown files in the
inventory scope, including readable hidden paths and files excluded from search.
Internal server state, Git directories and inaccessible paths are outside that
scope. Symlinks are not followed, even when search can read their targets; a
previous observation hidden by a symlink or an inaccessible subtree is retained.
The ledger is instance-local state beside the change tracker. With an in-memory
index and no configured state path it stays in memory: a restart loses history,
including evidence of removals during downtime. Back up persistent state to
retain its history. One process must own a shared persistent state file;
independent indexes must not share a tracker or ledger. If a ledger is missing
or corrupt, the next sweep starts a new history from the files it can read. A
newer schema is refused rather than
overwritten. A ledger refusal or I/O failure fails the build or reindex before
search is updated; repair the ledger or its storage and retry. A failed build
leaves the on-disk completion marker intact if the ledger sweep failed; after
repair, a restart can reuse the index. An unforced warm build that fails its
sweep leaves an already-warm index queryable in the current process. A forced
rebuild may make it unavailable in that process.

The ledger compares states; it does not record an event for each edit, identify
an author, or change frontmatter or `log.md`. `last_change_observed_at` moves
when the file's bytes or presence change. A role that changes with
configuration, such as a new conventions file, is refreshed in place without
moving it. It hashes the full file and,
separately, the parser-recognised frontmatter block with surrounding whitespace
trimmed. An absent, unterminated or invalid UTF-8 header has no frontmatter
digest; invalid bytes in the body do not affect a valid header's digest. Reads
use bounded chunks. Ordinary notes use bounded memory, while a candidate header,
including an unterminated one, can use memory proportional to its length. The
version 1 ledger preserves digest meanings across parser upgrades; changing them
requires a new version and migration.

A build, reindex or successful pull observes external edits, even when the Git
HEAD does not change. The server reconciles after interactive pulls and webhooks,
as well as on periodic pull ticks. A webhook can sweep twice. When search
fails to reconcile, the server still sweeps the ledger and keeps the failed
result. A ledger failure on a pull tick or `git_sync` is logged and reported as
failed; the tick still retries a pending push.

Each pull tick reads the full observation scope, normally every 600 seconds.
Periodic pulls disable the file watcher. When the watcher runs, excluded
top-level folders wait for the next sweep until its scope is expanded. A readable empty root records previously observed files as removed,
even if search refuses to prune its own rows for an empty root.

The library's `Vault.force_pull()` sweeps after Git succeeds but does not
reconcile search. If the sweep fails, Git may already have changed the working
tree; the error propagates. A webhook reports failure so the delivery can be retried.

The independent `OKF_AUTHOR`, `OKF_VERIFY`, `OKF_INDEX`, `OKF_LOG`, and `OKF_FOLDERS`
settings control authoring, review and maintenance. `OKF_AUTHOR` does not enable
implicit stamping. `OKF_WRITE=true` retains the
existing behavior and logs a deprecation notice; explicit settings override
its resolved defaults. See the [configuration reference](../reference/configuration.md).

## Inspecting bundle inventory

Call `okf_inventory` with a vault-relative `root` (use `""` for the vault root) to inspect concept candidates, including notes excluded from search. Set `type_state` to `absent` to find untyped candidates; `parse_state` and `search_state` filter parsing and admission observations. Reserved `index.md` and `log.md` are not concept candidates. A blank, null or non-string `type` has `type_state="invalid"`, not `absent`.

Pass `next_cursor` into the next call, including after an empty filtered page. Each call scans up to 1,000 observations before filtering and returns at most the requested limit. A `not_evaluated` search state means the index was not available, not that a note is excluded. The read-only `okf-inventory` CLI command accepts the same selection and `--json` returns the structured report. Neither interface builds an index for inventory. The local CLI uses a usable configured index when the selected source resolves to the configured vault; otherwise search state is `not_evaluated`. An unusable or unsupported optional index also leaves search state `not_evaluated`; the index is not changed. Unknown type or parse state does not prove a mismatch. Search-state filters match note observations exactly.

An unavailable directory boundary with reason `depth_limit` remains in filtered results. Select its path as the root of another inventory call to inspect deeper entries.
