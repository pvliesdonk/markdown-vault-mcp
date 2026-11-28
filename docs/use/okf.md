---
description: "How the server recognizes an Open Knowledge Format bundle, what it adds to results, and the tools that audit and migrate one."
kind: how-to
---

# Open Knowledge Format (OKF) with markdown-vault-mcp

[Open Knowledge Format](https://github.com/open-knowledge-format/open-knowledge-format) (OKF) is a vendor-neutral convention for a folder of markdown files that serves as curated context for AI agents. A bundle is a directory of notes with YAML frontmatter: one concept per file, the file path as the concept identity, and a small set of frontmatter fields that describe each note's type, lifecycle, provenance, and trust. This guide shows how markdown-vault-mcp recognises OKF bundles, what it does with the metadata, and how to move an existing vault into the format.

!!! note
    OKF support is read-only by default. Recognising a bundle only ever adds annotations and advice; it never changes your files. The migration tools that do change files are explicit, and this guide covers them near the end.

!!! tip "Example pack"
    The [`examples/okf/`](https://github.com/pvliesdonk/markdown-vault-mcp/tree/main/examples/okf/) directory ships a declaration index, typed note templates (`Concept`, `Capture`), and a prompt pack (author a concept, verify a note, triage stale content, migrate a vault) that you can copy into your vault and prompt folders.

## How the server recognises a bundle

The server treats a vault as an OKF bundle when the root `index.md` declares a version in its frontmatter:

```yaml
---
okf_version: "0.3"
---
# My bundle
```

This declaration travels with the vault, so the same bundle behaves the same way here and in other OKF tools. The server reads every bundle by the rules of OKF 0.3, whatever version it declares, so a bundle that still declares `0.2` is read as 0.3. For a version it does not know, such as a later release, the server logs an `okf_unknown_version` warning and reads the bundle anyway. The `MARKDOWN_VAULT_MCP_OKF_MODE` environment variable controls how the server responds to it:

Files and folders whose names start with `.`, such as `.drafts/` or `.notes.md`, are not part of the bundle, as OKF 0.3 says. They remain vault files that search can find. The server never treats them as bundle content, though: their results carry no `okf` annotation and match no OKF filter, and the `stats` tool leaves them out of its `okf` counts. `okf_validate` and the export pass over them. Generated `index.md` listings never link or create them, and the write layer neither stamps them nor keeps a `log.md` or `index.md` for them. To keep a file out of a bundle, give it, or a folder above it, a name starting with `.`.

| Value | Behaviour |
|-------|-----------|
| `auto` (default) | Apply OKF read semantics when the root `index.md` declares `okf_version`. |
| `off` | Never apply OKF semantics, and hide the OKF tools. Use this if your frontmatter happens to use keys such as `status` for an unrelated purpose. |
| `on` | Force OKF semantics even without a declaration. Use this for a bundle you consume but do not own. |

Check the current state through the `config://vault` resource. Its `okf_write_configured` field reports the configured `OKF_WRITE` flag, and `okf_write_effective` is true when that flag is enabled, OKF is active, and the vault is not read-only. When OKF is active, the `stats` tool's `okf` section reports the same values as `write_configured` and `write_effective`. The section is absent when OKF is inactive; use `config://vault` to inspect that state. Detection reflects changes to the declaration on the next call.

The effective write state describes the existing provenance stamping and reserved-file maintenance layer. It does not report whether `okf_verify` is available: `OKF_VERIFY=off` can hide that tool while the write layer remains effective. Independent capability reporting is planned in [#1412](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1412).

## What the metadata means

On a recognised bundle, the server reads a few OKF frontmatter families and surfaces them in `search`, `read`, and `get_context` results under an `okf` key:

- **Type**: the free-text `type` field, such as `Playbook`, `Metric`, or `Reference`.
- **Lifecycle**: `status` is one of `draft`, `stable`, or `deprecated`. A note with no `status` is treated as `stable`. `stale_after` marks a note stale from an instant on (`2026-12-31T09:00:00+01:00`; a date-time needs its seconds and its offset, and a `T` between date and time) or from a date on (`2026-12-31`, which means the start of that day in UTC, as do the dates in `generated.at` and `verified[].at`). A value the server cannot parse, such as an instant without an offset, also marks the note stale. A deprecated note's annotation also carries `superseded_by`: the vault path of its replacement, resolved the way OKF resolves a path field (from the note's own folder, or from the bundle root when it starts with `/`), or `null` when the note names none or it points outside the bundle. Pass that path to `read` to follow it; filtering never follows it for you.
- **Recorded review tier**: `trust_tier` describes supplied review claims (a single entry may be a bare mapping). Only current records count: a record is current when its `at` is equal to or later than `generated.at`, or when the note has no `generated`, as OKF 0.3 requires. A current record with a usable `human:<id>` actor and an `at` that is a date or a date-time with an offset gives `human-reviewed`; otherwise, current records from any other actor, `team:<id>` and unknown schemes included, give `machine-confirmed`, and no current record gives `unverified`. A review older than the last content change stays in the file as history but no longer counts, and a note whose `generated` has no usable `at` has no current record, so even an `okf_verify` entry does not promote it. Malformed entries do not promote the tier. The server does not certify the reviewer, truth, or applicability to the current revision. The `stats` trust breakdown counts these same classifications.
- **Evidence**: `freshness.state` distinguishes `missing`, `malformed`, `unexpired`, and `expired`. `review` separates recorded and malformed entry counts, and splits the recorded ones into `current_entries` and `earlier_entries`, with `currency` naming how they were judged (`no_generation`, `generated_at`, or `no_generation_time`). Whole reads include the parsed values under `recorded`; compact results keep the summary. `stale: false` includes absent or malformed expiry, so it does not establish freshness.
- **Review applicability**: `review_applicability.state` is `unknown` whenever the note's `verified` value has entries or is malformed, because no review is bound to the content it covered. Its `basis` is `no_snapshot_binding`. `review` reports entry validity. No timestamp or extension establishes coverage of the current content. An absent `verified`, or one that is null, empty or an empty list, reports `state: none`. This assessment is separate from freshness and the recorded review tier.
- **Sources**: the `sources` provenance list, surfaced in full on `read` and as a count on search hits.
- **Provenance**: `attribution` reports the `sources` list in the same shape as `review`: `state` is `missing`, `malformed`, or `recorded`, with counts of entries that carry a `resource`, which the OKF spec requires in each entry (recorded), and of other entries (malformed), and `basis: supplied_metadata_only`. As OKF 0.3 asks, a single source written without the list dash is a list of one, and a null or empty `sources`, `verified`, `generated` or `stale_after` counts as absent. `generation` reports `generated` as `{state, basis}` with `state` `missing`, `malformed`, or `recorded`. It follows the OKF spec: `by` is required and is an actor (`<scheme>:<id>` such as `human:<id>`, `team:<id>` or `process:<id>`, or `<tool>/<version>`; any other scheme is accepted as non-human), and `at` is optional but, when present, must be a date or a date-time with an offset. A `verified` entry needs both. The server's own stamps meet it. Neither object repairs or hides the raw values, so mixed source lists stay intact on reads and keep their full count on hits.

The CLI labels recorded review metadata and unknown applicability in human-readable search output. For whole-note text reads, it sends that notice to `stderr`, leaving the note content on `stdout` unchanged. In both, a note without a review, where `review_applicability` reports `none` (no `verified` field or an empty one), gets no notice; a malformed or recorded `verified` field keeps it. The app's note and context views follow the same rule on an OKF bundle: a malformed or recorded `verified` field gets the notice, while an absent or empty one does not. On a vault without OKF they show no notice. The original metadata stays visible in all cases. These notices change only the human-readable presentation. JSON and agent output retain their existing payloads.

You can filter on these dimensions. `search` and `list_documents` accept `status`, `stale`, `trust_tier`, and `type` filters, so `{"stale": "true"}` or `{"status": "deprecated"}` builds a triage listing. See [`list_documents`](../reference/tools/reader.md#list_documents) for the full filter set.

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
are reordered; the exact string `id` is the stable key. A `sources` written as
one mapping rather than a list is read as a one-entry list, at position 0, and a
null or empty `sources` as none, as OKF 0.3 §11.2 asks. Labels are not trimmed,
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
index rebuilds. `unconverted_notes` counts indexed notes whose citation rows were
not derived with the current rules, such as a note that could not be re-read
during that rebuild; they are left out of the result. An ordinary reindex skips
unchanged notes, so if the count stays above zero, call the MCP tool
`reindex(force=True)` or the library method `build_index(force=True)` for a full
rebuild.
The lookup does not fetch resources or assess whether they support a claim.

### Syntax coverage

The reader uses CommonMark block and inline parsing with a small literal marker
extension. It recognizes single-line labels with no whitespace, brackets or
backslashes. It skips code spans and fences, escaped markers, HTML blocks and
attributes, navigation links, images, wikilinks, and footnote definition blocks.
A citation right after inline code, an inline link or a wikilink is still
recognized. A marker right after other bracketed text, such as the reference-style
link in `[text][ref][^a]`, is read as part of that link and skipped. Footnote
definition prose never supplies the source join. Other Markdown dialect
extensions are not interpreted; an empty result is not an exhaustive citation
audit. The response's `syntax` and `scope` fields retain these limits.

The existing whole-note read limits apply. The source list and body are parsed
from one returned content string. `returned_text_sha256` hashes that complete
string after line endings are normalized to LF and it is encoded as UTF-8. It
is not the file's raw-byte etag or a portable file identity, and cannot be used
as `if_match`. No index refresh, traversal, metadata write or permission change
is part of this call. See the [tool reference](../reference/tools/reader.md#inspect_citations).

## Reading the specification

The server serves the text of the one OKF version it follows, 0.3, as the `okf-spec` skill, so an assistant that reaches your vault only through this server can read the format it is asked to write. The specification doesn't have to live in your vault as a note. A client that supports skills over MCP, such as Claude or Claude Code, lists the skill by name and description and loads it when the assistant needs it; to other clients its files are ordinary resources, such as `skill://okf-spec/SPEC.md`. The skill disappears with the OKF tools when `MARKDOWN_VAULT_MCP_OKF_MODE` is `off`.

The file is the specification exactly as the [Open Knowledge Format project](https://github.com/open-knowledge-format/open-knowledge-format) published it, under the Apache License 2.0. The skill carries that license and a note naming the exact revision. A server release that moves to a newer OKF version replaces it.

## Migrating an existing vault

Adoption is incremental. A note is either conformant or not, and the server tolerates a mix, so you can convert a vault gradually rather than in one step. The recommended order is a ratchet: declare early so new notes are written correctly, then converge the backlog.

### 1. Audit

Run the `okf_validate` tool to see where the vault stands. It reports conformance as a degree rather than a pass or fail. The report gives the count of concepts (notes with a non-empty `type`) out of the total, alongside a per-rule breakdown with example paths (frontmatter that does not parse, unknown `status` values, and the like). A note without a `type` is not a concept: it does not count against conformance, and the report lists it under `missing_type` so you can decide whether it should become one. The audit reads from disk, so it works before you declare anything. Use it to decide whether to start.

The evidence findings report missing expiry, malformed expiry, review, source or `generated` records, and unavailable assessments. An assessment is unavailable when a discovered concept cannot be read or parsed, or exceeds the read cap. Missing expiry is informational; malformed fields are format observations, not failures of a team's editorial policy. These findings leave the existing conformance ratio unchanged. The report covers discovered, non-excluded paths and does not certify complete traversal or current-review applicability.

The report also lists folder `index.md` files and `log.md` files that carry frontmatter, which the format does not allow there; the root `index.md` may carry the bundle's `okf_version`, `title`, `description`, `license` and your own keys. That includes reserved files the server generated: with `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` set, the server writes the required fields into them so they stay indexed. Leave those fields in place, because an index without them drops out of search and document listings.

The evidence findings `malformed_sources` and `malformed_generated` count notes whose `attribution` reports a malformed entry or whose `generation` is `malformed`, using the same rules as the read annotation. Like the other evidence findings, they are withheld for a note counted under `evidence_unavailable`. They leave the conformant count unchanged, and affected notes remain readable and searchable. Reading and auditing preserve your files; they never repair these fields.

### 2. Declare

Add `okf_version: "0.3"` to the root `index.md` frontmatter. From this point the server annotates results, the filters work, and the agent receives guidance to keep OKF conventions when it edits. Declaring early means every new note is authored conformantly while you work through the backlog.

### 3. Enrich

Backfill the missing metadata. This is where most of the work is, and an agent can do it note by note. For each note it proposes a `type` from the content and fills in the `title` and `description`. Approve the changes in batches, then re-run `okf_validate` to watch the conformant count climb.

When using `write` to update an existing note or root index, read its current
content first and pass that read's etag as `if_match`. New files omit
`if_match`. A failed replacement leaves the existing file intact; report the
conflict before continuing with any dependent migration steps.

### 4. Mechanical transforms

Three tools handle the changes you should not make by hand. They are write tools, so they are hidden in read-only mode and when `OKF_MODE` is `off`, and on a git-backed vault each change is committed.

- **`okf_convert_links`** rewrites `[[wikilinks]]` as the root-absolute markdown links OKF recommends, such as `[text](/guides/note.md)`. Spaces in a note or folder name are percent-encoded, as Obsidian does when it writes a link, so `Project Notes/note.md` becomes `[text](/Project%20Notes/note.md)` and stays a link everywhere. Every destination is written as the URI reference OKF requires, so a `%`, `#`, `?`, bracket or accented letter in a name is percent-encoded too (`50% done.md` becomes `50%25%20done.md`), and an unbalanced parenthesis is encoded as well. A bracket in the link text is backslash-escaped. Only links whose target exists are converted, so your link graph is preserved exactly. Unresolvable wikilinks are left alone and reported. You can write in either link style day to day; run this before sharing the bundle.
- **`okf_generate_index`** writes a folder's `index.md` as a listing of the notes directly in that folder, plus a pointer into each subfolder's own `index.md`. A subfolder without an `index.md` gets one, at every level below, so no pointer leads to a missing file; a subfolder that already has one is left as it is. It draws the description from each note's frontmatter and lists one level at a time, so a nested bundle stays navigable rather than flattening into one long page. It preserves existing frontmatter byte for byte, comments and quoting included, so regenerating the root `index.md` keeps your `okf_version` declaration exactly as you wrote it. It never creates an `index.md` beside a file whose name differs from it only by case, such as your own `Index.md`: OKF says two such paths should not exist, and on macOS or Windows the new file would overwrite yours. It refuses that folder, and in a parent listing it leaves that subfolder out. Automatic upkeep and `okf_seed_log` treat `log.md` the same way.
- **`okf_seed_log`** creates a `log.md` change history from the vault's git commits, newest first. The `folder` argument both places the log and scopes its content: seeding a folder includes only the commits that touched that subtree, while seeding the bundle root includes the whole vault's history. It refuses to overwrite an existing `log.md`, so a hand-maintained history is safe.

### Reserved files on a vault with required fields

If you set `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS`, documents missing any listed field are excluded from the index. The generated `index.md` and `log.md` are ordinary documents to the indexer, so the generators give them the fields your vault requires. Your title field is filled in from the file's heading; any other required field is written as `null`, for you to fill in. Without this the bundle's own listing and change history would be absent from `search` and `list_documents` while still opening through `read`.

Anything already in the file wins, so a title you wrote yourself and the root `index.md`'s `okf_version` declaration are left alone. On a vault that sets no required fields nothing changes: the reserved files carry no frontmatter, exactly as before.

## The enforced write layer

The steps above keep a bundle conformant by convention. The enforced write layer makes the server maintain two of those fields on the writes that go through it. Turn it on by setting `MARKDOWN_VAULT_MCP_OKF_WRITE=true`. It requires `OKF_MODE` to be `auto` or `on`; pairing it with `OKF_MODE=off` is a configuration error, since there is nothing to enforce. When the flag is off the write path is untouched, so an ordinary vault behaves exactly as before.

Enabling `OKF_WRITE` on a read-only deployment is valid, so a writable server and a read-only mirror can share an environment file. Stamping, reserved-file upkeep, and `okf_verify` are inactive regardless of `OKF_VERIFY`; OKF read semantics remain available. Startup logs `okf_write_inert` at INFO, and `config://vault` reports `okf_write_effective=false`.

With the layer on, and only while the vault is an active OKF bundle, every `write` and `edit` of a concept stamps the note that lands and keeps its review history. A concept is a `.md` file other than `index.md` and `log.md` whose frontmatter, as written, has a non-empty `type`. Every other file is written exactly as you send it, with no `generated` added and no frontmatter created: `index.md` and `log.md`, which OKF says carry no frontmatter of their own, and files such as `README.md` or `SKILL.md` that have no `type`. A write that gives a file a `type` makes it a concept, so it is stamped.

- **Stamps provenance.** The server writes `generated: {by, at}` describing the bytes it just saved. `at` is the instant of the write, in UTC (`2026-09-07T05:32:29Z`), as the OKF spec requires of every timestamp. `by` is `human:<subject>` when the token carries a usable `sub` claim the issuer does not mark as an application's, and a tool actor such as `markdown-vault-mcp/1.4.0` otherwise. Static bearer credentials, including custom and mapped subjects, use the tool actor, as does an OAuth token the issuer marks as an application's rather than a person's: its `sub` equals the client ID (Amazon Cognito, Authelia, and Okta in its usual configuration), or it carries `gty: client-credentials` (Auth0) or `idtyp: app` (Microsoft Entra ID, when the API is set up to receive that optional claim). Keycloak, authentik and Zitadel issue client-credentials tokens to a service account the token does not mark. List those accounts' `sub` values in `MARKDOWN_VAULT_MCP_SERVICE_SUBJECTS` so their writes use the tool actor too; an unlisted one receives human attribution. It records who called the write, which is not the same as who wrote the words, so an agent writing under your token stamps your identity. Any existing `generated.by` and `generated.at` are replaced, including ones another tool wrote; any other key inside `generated`, such as `x-model`, keeps its value, as OKF 0.3 §4.1 requires. The field tracks the last write this server saw rather than the note's history. A write that changes only formatting or whitespace (the same frontmatter apart from `generated` and `verified`, and a body that renders the same) keeps the existing `generated`, as OKF says a trivial edit does.
- **Keeps recorded reviews.** The server carries the stored note's `verified` across every write, as OKF 0.3 §5.3 asks: earlier events stay in the file as history. The value comes from the note on disk, not from the content you send, so `verified` in the content of a write has no effect, and a new note is written without it. Use `okf_verify` to record a review. Because the write moves `generated.at`, a review recorded before it no longer counts toward the trust tier, which only events at or after `generated.at` set. The entry remains, so a reader can still show it as verified before the last change. A new `okf_verify` call records a review of the current content. A `rename` may mechanically rewrite links in the moved note when link updates are enabled; this rewrite leaves both fields alone.

Both fields describe what this server observed on its own write path. A note changed by anything else (a teammate's editor, a git pull, a script) carries whatever its author left there, and the layer neither notices nor corrects that.

The one-shot migration transforms above (`okf_convert_links`, `okf_generate_index`, `okf_seed_log`) are exempt: a mechanical rewrite does not re-stamp provenance, so a human attestation stays current.

A write whose frontmatter block cannot be parsed has no `type` the server can read, so it is not a concept: it lands as you sent it, unstamped, as it does with the layer off. The note is left out of the index until its frontmatter parses, and `okf_validate` reports the block as unparseable.

### Recording a human review

Enabling the layer also exposes the [`okf_verify`](../reference/tools/writer.md#okf_verify) tool. Call it on a note you have reviewed and it appends a `{by: human:<subject>, at}` entry (`at` a UTC instant) to that note's `verified` list, which promotes the note's trust tier to `human-reviewed` (unless the note's `generated` has no usable `at`; see [What the metadata means](#what-the-metadata-means)). The verification write is exempt from the stamp above: recording a review does not move `generated.at`, so the entry you just added counts.

One subtlety is worth understanding before you rely on the `human-reviewed` tier. The authenticated subject is *whose token* made the call, not proof that a person read the note. When an agent holds your token and attribution rests on the token alone, the model could promote a note to `human-reviewed` on its own, and the tier would mean nothing. `MARKDOWN_VAULT_MCP_OKF_VERIFY` controls how the tool guards against that. It applies only when `OKF_WRITE` is on. Setting it to a non-default value with the layer off is a configuration error, because the tool is hidden and the setting would have no effect.

- **`elicit`** (the default) makes `okf_verify` ask you to confirm the review through an MCP [elicitation](https://gofastmcp.com/servers/elicitation), a prompt your client shows you, and it writes the `verified` entry only when you answer yes. It fails closed. On modern MCP connections the tool returns an input request and your client repeats the call with your answer; on older handshake connections it uses server-initiated elicitation. When your client cannot show the prompt or you decline it, the call fails and writes nothing. Use a client whose elicitation handler actually presents the question to a person: an automatically affirmative handler would defeat the human-review gate. The recorded subject is your token's `sub` when it identifies a person, or `local` when you use a service credential, a token the issuer marks as an application's, a token whose `sub` is listed in `MARKDOWN_VAULT_MCP_SERVICE_SUBJECTS`, or no auth.
- **`off`** hides the tool entirely. Use this when reviews are recorded only by tooling outside the server (a CLI step, a git hook, or a CI job that writes `verified` directly) and you want no in-session path to the tier at all.
- **`trust-auth`** attributes to a token's `sub` with no confirmation. It refuses static bearer credentials, other client-ID-only identities, OAuth tokens the issuer marks as an application's or whose `sub` is listed in `MARKDOWN_VAULT_MCP_SERVICE_SUBJECTS` (see above), and callers without auth. Choose it only when the sole caller of `okf_verify` is a human-driven interface rather than an agent.

**Upgrading bearer-based review clients:** deployments that used `trust-auth` with a static or mapped bearer token must switch `MARKDOWN_VAULT_MCP_OKF_VERIFY` to `elicit` and use a client that presents the confirmation to a person. Existing provenance and verification entries are not rewritten.

The verification mode controls how this tool records a review claim. The `human-reviewed` tier itself only reports a `human:` prefix in supplied metadata, which can also arrive from other editors or imports. It does not prove that a person reviewed the note or that its content is correct.

A `verified` entry records a review claim without identifying the reviewed snapshot. This server keeps it when its own write path changes the note and moves `generated.at`, so the entry stays as history but no longer counts toward the tier. A change that arrives another way (a teammate's editor, a git pull, a script) moves neither field. Applicability to current content remains unknown, and `review_applicability` reports it as `unknown`. Review timestamps do not establish applicability. Neither do `generated.at`, missing history or changed bytes. The trust tier does compare them: a review older than `generated.at` is not current and no longer promotes the tier. The presentation layer leaves write behavior unchanged. Snapshot-bound reviews and change-history assessment remain separate work.

### Keeping `log.md` and `index.md` current

With the layer on, the server also maintains a folder's reserved files as a side effect of writing into it. After a successful `write` or `edit` on a note, it appends a dated `**Update**` bullet to that folder's `log.md` (creating the log, and the day's `## YYYY-MM-DD` section, when needed) and regenerates the folder's `index.md` listing so a new note shows up without a manual step. A note written into a folder with no `index.md` is listed from the nearest folder above that has one, and each level in between gets its own `index.md`. That covers the upkeep the advisory layer otherwise asks the agent to do by hand, on the write paths below and no others.

A `delete`, `rename` or `move_folder` keeps the listings current too. A deleted note drops out of its folder's `index.md`. A renamed note leaves its old folder's listing and appears in its new folder's, as a write there would. After a folder move, every `index.md` that moved with it is regenerated for its new paths, and so are the listings of the old and the new parent folder. When a note or folder lands several levels below the nearest folder with an `index.md`, that folder's listing is regenerated and each level in between gets one, so no listing points at a missing file. These changes add no `log.md` entry. A rename to or from `index.md` or `log.md` leaves the listings alone, so renaming a note onto a reserved name keeps its content.

After a folder move, the refresh skips folders inside a git submodule or nested repository, and their descendants. It still updates ordinary containing folders and their other descendants, without attempting to rewrite those listings. The [Git integration guide](git-integration.md#submodules-and-nested-repositories-in-the-vault) explains the read-only boundary.

A few boundaries keep this predictable, and they are also its limits. Maintenance runs only on an active bundle. A write whose target is itself a reserved file does not trigger it. The `okf_verify` tool and the one-shot migration transforms are exempt, so an attestation or a mechanical rewrite does not churn the reserved files. Only one listing is regenerated per write: that of the folder directly containing the note, or, when it has no `index.md`, that of the nearest folder above it with one. A folder's own listing is not otherwise refreshed when a note lands in one of its subfolders. A failure to update a reserved file is logged and skipped; it never fails or rolls back the note write that triggered it, so a listing can silently fall behind.

**Do not hand-edit a maintained `index.md`.** The write itself is left alone, because the maintainer skips a write whose target is a reserved file. But the next content write into that folder regenerates the listing from the note list, and an edit you made is gone with nothing reporting it. The same applies to anyone else editing the vault. A teammate's change to a maintained `index.md` in Obsidian, or one arriving through a git pull, survives only until the next write into that folder. Put anything you want to keep in a note, not in the generated navigation. `log.md` is appended to rather than regenerated, so hand-written entries there are not overwritten, but a conflicting edit from another editor can still collide with the server's append.

One cost comes with this upkeep. Each `write` or `edit` waits for the search index to catch up before it regenerates `index.md`, so that a just-created note is listed; on a busy vault or with a slow embedding backend this adds latency to the write, bounded at 60 seconds. A refresh failure keeps the existing listing and logs a warning; the note write is not rolled back. Embedding jobs already in the queue can delay the refresh, but it does not wait for embeddings queued after it. On a git-backed vault the reserved-file updates are not extra commits: every write a tool call makes is committed together, so saving a note produces one commit naming the note, its `log.md` and its `index.md`.

To stop the upkeep, set `MARKDOWN_VAULT_MCP_OKF_WRITE=false` and maintain the reserved files yourself. Two things to know before you do. If you had set `MARKDOWN_VAULT_MCP_OKF_VERIFY` to anything but its default `elicit`, reset it as well. Startup fails otherwise, because that setting only applies while the layer is on. And disabling the layer stops further upkeep without repairing anything already written. Stamps the layer added stay in their notes, and a `log.md` that collected repeated frontmatter blocks from a version before 4.2 keeps them. Ordinary writes leave those blocks alone. The deliberate repair below requires review because the server cannot distinguish a block it stacked from one the log quotes on purpose.

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
`Vault.writer.okf_apply_log_repair`.

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

The export reads from the live vault and never changes it. Wikilinks become the root-absolute markdown links OKF recommends. Convention files (`_conventions.md`), the template folder and every path that is not part of the bundle (a file or folder whose name starts with `.`) are left out, while the reserved `index.md` and `log.md` stay in. Selecting a dot folder, such as `ref="okf-bundle:.drafts"`, downloads an empty zip. Non-conformant notes appear as they are.

It is best-effort packaging rather than a certified publication, in three specific ways. It packages what the **index** currently holds. A note the index left out, whether excluded by pattern, missing a configured required field, or carrying frontmatter that does not parse, is absent from the archive without being reported. It reads the vault as it goes rather than from a point-in-time snapshot, so a write that lands mid-export can leave the archive internally inconsistent; quiesce writes if that matters. And nothing validates the result: the archive carries no conformance report, and building one does not run `okf_validate`. Run `okf_validate` yourself before you publish, and read `get_index_status.skipped_files` to see what the index is leaving out.

Bundle export needs an HTTP or SSE transport with `MARKDOWN_VAULT_MCP_BASE_URL` set, the same requirement as any transfer link, and it is unavailable when `OKF_MODE` is `off`. A git-backed vault also remains a shareable bundle on its own: the repository is an interchange format.
