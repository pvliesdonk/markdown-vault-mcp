---
type: Reference
title: Obsidian markdown dialect and link resolution
description: "Obsidian desktop (1.x): wikilink syntax, link resolution, aliases, and the Obsidian-only syntax that shares characters with links"
subject_version: "1.14 (help pages state a version only for properties: 1.4 / 1.9)"
valid_for: "Obsidian 1.x"
generated:
  by: process:researching-references
  at: 2026-09-06
verified:
  - by: process:researching-references-refute
    at: 2026-09-06
stale_after: 2027-03-06
status: stable
sources:
  - id: help-links
    title: Internal links - Obsidian Help
    resource: https://obsidian.md/help/links
    accessed: 2026-09-06
  - id: help-embeds
    title: Embed files - Obsidian Help
    resource: https://obsidian.md/help/embeds
    accessed: 2026-09-06
  - id: help-tags
    title: Tags - Obsidian Help
    resource: https://obsidian.md/help/tags
    accessed: 2026-09-06
  - id: help-callouts
    title: Callouts - Obsidian Help
    resource: https://obsidian.md/help/callouts
    accessed: 2026-09-06
  - id: help-properties
    title: Properties - Obsidian Help
    resource: https://obsidian.md/help/properties
    accessed: 2026-09-06
  - id: help-aliases
    title: Aliases - Obsidian Help
    resource: https://obsidian.md/help/aliases
    accessed: 2026-09-06
  - id: help-syntax
    title: Basic formatting syntax - Obsidian Help
    resource: https://obsidian.md/help/syntax
    accessed: 2026-09-06
  - id: help-advanced
    title: Advanced formatting syntax - Obsidian Help
    resource: https://obsidian.md/help/advanced-syntax
    accessed: 2026-09-06
  - id: help-ofm
    title: Obsidian Flavored Markdown - Obsidian Help
    resource: https://obsidian.md/help/obsidian-flavored-markdown
    accessed: 2026-09-06
  - id: help-formats
    title: Accepted file formats - Obsidian Help
    resource: https://obsidian.md/help/file-formats
    accessed: 2026-09-06
  - id: help-settings
    title: Settings - Obsidian Help (section "Files and links")
    resource: https://obsidian.md/help/settings
    accessed: 2026-09-06
  - id: api-dts
    title: obsidian-api obsidian.d.ts (master), the public API typings and their JSDoc
    resource: https://raw.githubusercontent.com/obsidianmd/obsidian-api/master/obsidian.d.ts
    accessed: 2026-09-06
  - id: api-parselinktext
    title: parseLinktext - Obsidian Developer Documentation
    resource: https://docs.obsidian.md/Reference/TypeScript+API/parseLinktext
    accessed: 2026-09-06
  - id: api-metadatacache
    title: MetadataCache - Obsidian Developer Documentation
    resource: https://docs.obsidian.md/Reference/TypeScript+API/MetadataCache
    accessed: 2026-09-06
  - id: api-cachedmetadata
    title: CachedMetadata - Obsidian Developer Documentation
    resource: https://docs.obsidian.md/Reference/TypeScript+API/CachedMetadata
    accessed: 2026-09-06
  - id: changelog
    title: Obsidian changelog
    resource: https://obsidian.md/changelog/
    accessed: 2026-09-06
---

# Obsidian markdown dialect and link resolution

What Obsidian adds to or changes in CommonMark/GFM, scoped to what the scanner,
the link index, the rename rewriter and the OKF converter depend on: the shape
of a wikilink, how its target is resolved to a file, aliases, titles, and the
Obsidian-only syntax whose characters (`[`, `|`, `#`, `^`, `%%`, `$`) can
confuse a regex-based scanner. Plain CommonMark/GFM rules are in
`commonmark-gfm.md`, not here. Obsidian itself could not be run for this
research, so every behaviour the help and API docs leave open is marked
`[unverified]` with the fixture that would settle it in a real vault.

The help site moved from `help.obsidian.md/...` to `obsidian.md/help/...`
(301); the source list carries the final URLs. There is no `comments` page and
no `settings/files-and-links` page (404): both are sections of the basic-syntax
page and the settings page respectively.

Version: the changelog's newest desktop entry on the accessed date is 1.14.0
(2026-09-02) and none of the 1.13.x–1.14.0 entries touch link resolution,
wikilinks, aliases, tags or the parser [source: changelog]. The help pages
name a version only for properties (1.4 deprecates `alias`/`tag`/`cssclass`,
1.9 drops them) [source: help-properties].

## Scope

- Covers: wikilink and embed syntax, fragments (headings, blocks), how a link
  target maps to a file, aliases and the reserved properties, title sources,
  tags, callouts, comments, footnotes, math, table pipe escaping, markdown
  links as Obsidian writes them, file-name constraints.
- Does not cover: CommonMark/GFM proper (`commonmark-gfm.md`), Obsidian
  Publish/Sync/Bases/Canvas, plugin APIs beyond `MetadataCache` and the link
  helpers, rendering.
- Depended on by: `src/markdown_vault_mcp/scanner.py` (`_RE_WIKILINK`,
  `_extract_wikilinks`, `_is_external_target`, `_resolve_title`),
  `src/markdown_vault_mcp/fts_index.py` (`FTSIndex.resolve_vault_wikilinks`,
  `FTSIndex._insert_aliases`), `src/markdown_vault_mcp/okf.py`
  (`convert_wikilinks_to_markdown`), `src/markdown_vault_mcp/utils/links.py`
  (`compute_new_raw_target`, `apply_link_replacement`);
  `docs/design/design.md` § "Link Extraction" (Wikilink resolution).

## Claims

### Wikilink shape

- A wikilink is `[[Three laws of motion]]` or, with the extension,
  `[[Three laws of motion.md]]`; the help shows no other bracket form and no
  nested or multi-line example. [source: help-links]
  [pins: tests/test_links.py::TestExtractWikilinks::test_wikilink_basic, tests/test_links.py::TestExtractWikilinks::test_wikilink_md_extension_not_doubled]
- Display text follows a vertical bar: "Use a vertical bar (`|`) to change the
  display text. `[[Example|Custom name]]`". [source: help-links]
  [pins: tests/test_links.py::TestExtractWikilinks::test_wikilink_with_alias]
- The API confirms the split: a `Reference` has `link` ("Link destination"),
  `original` ("the text as it's written in the document") and optional
  `displayText` ("in the case of `[[page name|display name]]` this will
  return `display name`"); `LinkCache` and `EmbedCache` are both
  `ReferenceCache`. [source: api-dts] [source: api-cachedmetadata]
- The help lists characters a link target may not carry: "A string which
  contains the following characters may not work as a link:
  `# | ^ : %% [[ ]]`". This is the closest the help comes to a grammar; it
  implies `]`, `|` and `#` cannot be literal target characters, which is what
  `_RE_WIKILINK` assumes, but it is a warning, not a parse rule.
  [source: help-links]
- Whether a `]` inside the target terminates the link, whether whitespace or a
  newline inside `[[...]]` is tolerated, and how `[[a [[b]] c]]` parses are
  not documented. [unverified] A fixture note with each of those, opened in
  Obsidian, then read through `metadataCache.getFileCache(file).links` would
  settle all three.

### Fragments: headings and blocks

- A heading link puts `#` after the destination: "add a hash (`#`) at the
  end of the link destination, followed by the heading text. For example,
  `[[About Obsidian#Links are first-class citizens]]`". Subheadings chain:
  "You can add multiple hash symbols for each subheading. For example,
  `[[Help and support#Questions and advice#Report bugs and request features]]`".
  [source: help-links]
  [pins: tests/test_links.py::TestExtractWikilinks::test_wikilink_with_fragment, tests/test_links.py::TestExtractWikilinks::test_wikilink_dotmd_with_fragment]
- The API models a linktext as path plus subpath split at the first `#`:
  "Linktext is any internal link that is composed of a path and a subpath,
  such as 'My note#Heading'. Linkpath (or path) is the path part of a
  linktext. Subpath is the heading/block ID part of a linktext."
  `parseLinktext(linktext)` returns `{ path, subpath }` ("subpath can refer
  either to a block id, or a heading"); `getLinkpath(linktext)` returns "the
  name of the file that is being linked to". [source: api-dts]
  [source: api-parselinktext]
- The scanner's split at the first `#` matches the API's; a chained `#H1#H2`
  subpath is stored whole as the fragment. [source: api-dts]
- Block references use `#^`: "`#^` at the end of your link destination,
  followed by a unique block identifier. For example: `[[2023-01-01#^37066d]]`".
  "Block identifiers can only consist of Latin letters, numbers, and dashes."
  A block is defined by " ^" plus the identifier at the end of a paragraph
  line, on its own line (blank line before and after) for tables and code
  blocks, or directly on a list item. The help calls block references
  "specific to Obsidian and not part of the standard Markdown format".
  [source: help-links] The `^id` marker at the end of a block is an
  Obsidian extension listed on the OFM page. [source: help-ofm]
- The display alias comes after the fragment: the embed page shows
  `![[Internal links#Link to a heading in a note|headings]]`, so the order is
  `[[note#heading|alias]]`, never `[[note|alias#heading]]`.
  [source: help-embeds]
  [pins: tests/test_links.py::TestExtractWikilinks::test_wikilink_escaped_pipe_with_fragment]

### Same-note headings and external targets

- A same-note heading link is `[[#Heading]]`: "type `[[#` to get a list of
  headings within the note to link to. For example, `[[#Preview a linked
  file]]`". The help presents it as intra-note navigation, not a link to a
  file. [source: help-links]
  [pins: tests/test_links.py::TestExtractWikilinks::test_fragment_only_wikilink_skipped, tests/test_links.py::TestExtractWikilinks::test_fragment_only_wikilink_with_alias_skipped, tests/test_links.py::TestResolveVaultWikilinks::test_same_note_heading_wikilink_is_not_broken]
- Whether Obsidian records `[[#Heading]]` in `resolvedLinks` as a self-edge
  (source → source) is not documented. [unverified] A one-note fixture and a
  dump of `metadataCache.resolvedLinks` would show whether Obsidian's own
  backlink/graph counts include it; the project's choice to skip it (#1107)
  is a design decision either way.
- The help does not document `[[https://...]]`; external links are written
  as `[text](url)`, with spaces escaped as `%20` or the URL wrapped in
  `< >`. The `:` in the "may not work as a link" list implies a schemed
  wikilink is not a valid file link. [source: help-syntax] [source: help-links]
  [pins: tests/test_links.py::TestExternalUriSchemes::test_schemed_wikilink_target_is_external]
- How Obsidian renders `[[https://example.com]]` (as a URL, as an unresolved
  note, or as a file named `https:`) is not documented. [unverified] Fixture:
  the wikilink, then `unresolvedLinks` for the note.

### Extension, non-markdown targets, embeds

- `.md` may be omitted or written; both forms link the same note.
  [source: help-links]
  [pins: tests/test_links.py::TestExtractWikilinks::test_wikilink_dotmd_raw_target_keeps_extension, tests/test_utils_links.py::TestComputeNewRawTarget::test_wikilink_with_md_extension, tests/test_utils_links.py::TestComputeNewRawTarget::test_wikilink_without_md_extension]
- Whether the extension comparison is case-insensitive (`[[Note.MD]]`) is not
  documented. [unverified] `compute_new_raw_target` assumes it is
  [pins: tests/test_utils_links.py::TestComputeNewRawTarget::test_wikilink_case_insensitive_md].
- Non-markdown targets keep their extension: "links to file formats other than
  Markdown needs to include a file extension, such as `[[Figure 1.png]]`".
  So `[[Figure 1.png]]` and `![[image.png]]` name a file `Figure 1.png`, not
  `Figure 1.png.md`; the scanner appended `.md` unconditionally until #1333,
  and now records no link for a target with an allowlisted extension (see
  the departures section). [source: help-links]
  [pins: tests/test_links_attachment_references.py::TestWikilinkSite::test_an_embed_of_an_attachment_is_not_a_link, tests/test_links_attachment_references.py::TestWikilinkSite::test_a_plain_wikilink_to_an_attachment_is_not_a_link]
- Accepted formats: `.md`, `.base`, `.canvas`; images `.avif .bmp .gif .jpeg
  .jpg .png .svg .webp`; audio `.flac .m4a .mp3 .ogg .wav .webm .3gp`; video
  `.mkv .mov .mp4 .ogv .webm`; `.pdf`. "Show all file types" lets any
  extension be linked. [source: help-formats] [source: help-settings]
- Embeds are `![[...]]` with the same target grammar: `![[Internal links]]`,
  `![[Internal links#Link to a heading in a note|headings]]`,
  `![[Internal links#^b15695]]`, `![[Engelbart.jpg|100x145]]` and
  `![[Engelbart.jpg|100]]` (the alias slot carries the size),
  `![[Document.pdf#page=3]]`, `![[Document.pdf#height=400]]`,
  `![[My note#^my-list-id]]`. [source: help-embeds]
- `_RE_WIKILINK` has no `!` lookbehind, so a note embed is indexed as a
  link; an attachment embed is skipped by its target's extension, not by the
  `!` (#1333). [source: help-embeds]
  [pins: tests/test_links_attachment_references.py::TestWikilinkSite::test_a_note_embed_is_still_a_link, tests/test_links_attachment_references.py::TestWikilinkSite::test_an_embed_of_an_attachment_is_not_a_link, tests/test_links_attachment_references.py::TestWikilinkSite::test_a_plain_wikilink_to_an_attachment_is_not_a_link]

### Folder-qualified targets and resolution

- "To link to a note in a folder, include the folder path before the note
  name. Folder paths start at the vault root and use forward slashes (`/`),
  even on Windows: `[[Projects/Three laws of motion]]`". So a folder-qualified
  wikilink is vault-root-relative, not relative to the source note, and a
  bare name is looked up vault-wide. [source: help-links]
  [pins: tests/test_links.py::TestExtractWikilinks::test_wikilink_path_stored_as_is, tests/test_links.py::TestResolveVaultWikilinks::test_bare_wikilink_resolves_vault_wide, tests/test_links.py::TestResolveVaultWikilinks::test_wikilink_with_path_separator_resolves_vault_wide]
- `[[./note]]` and `[[../note]]` are not documented as wikilink forms. The
  API's `normalizePath` "resolv[es] . and .. references" and `fileToLinktext`
  takes a `sourcePath` "used to compute relative links", so relative
  spellings exist in Obsidian's model, but the help never shows one inside
  `[[ ]]`. [source: api-dts]
  [pins: tests/test_links.py::TestExtractWikilinks::test_wikilink_explicit_relative_resolved_against_source]
- Whether `[[../note]]` resolves relative to the source note or is treated as
  a literal name is [unverified]. Fixture: `a/x.md` containing `[[../y]]`
  with `y.md` at root, then `resolvedLinks`.
- Resolution is a "best match": `getFirstLinkpathDest(linkpath, sourcePath)`
  is documented only as "Get the best match for a linkpath"; the `sourcePath`
  parameter is present but its role is not described. [source: api-dts]
  [source: api-metadatacache]
- Link *writing* is documented: `fileToLinktext` "If file name is unique, use
  the filename. If not unique, use full path", and the "New link format"
  setting offers "Shortest path when possible" ("Uses the shortest unique
  path to the linked file"), "Relative path to file" ("Uses a path relative
  to the current file") and "Absolute path in vault" ("Uses the full path
  from the vault root"). [source: api-dts] [source: help-settings]
- The design doc's "ends with `/target`" suffix match is not documented by
  Obsidian; the help shows only full vault-root paths. Whether `[[b/Note]]`
  resolves `a/b/Note.md` is [unverified]. Fixture: `a/b/Note.md` only, a note
  containing `[[b/Note]]`, then `unresolvedLinks`.

#### The tie-break (#1350)

- What the help documents: a *written* link is the shortest **unique** path
  (a bare name when unique, else the full path), so Obsidian itself never
  writes an ambiguous bare name when the user accepts its suggestion.
  [source: help-settings] [source: api-dts]
- What it leaves open: how `getFirstLinkpathDest` picks among several files
  that all match an ambiguous bare name typed by hand or left behind by a
  later duplicate. Neither "shortest path", "fewest components", "closest to
  the source", nor "first in file-tree order" appears in any source read;
  the `sourcePath` parameter hints that the source note's location may
  matter. [unverified]
- The project's two rules (code `min(candidates, key=len)`, design doc
  "fewest path components") both claim to mirror Obsidian, and neither can be
  sourced. The pinned tests use fixtures where the two rules agree
  (`a/Note.md` vs `a/b/Note.md`; `javascript.md` vs
  `deep/nested/javascript.md`), so they do not discriminate.
  [pins: tests/test_links.py::TestResolveVaultWikilinks::test_bare_wikilink_shortest_path_wins, tests/test_links.py::TestAliasResolution::test_alias_resolution_shortest_path_wins]
- A settling fixture, in one vault: `Note.md` at root, `zzzz/Note.md` (one
  component, longer string), `a/b/Note.md` (two components, shorter string),
  and a source note containing `[[Note]]` at root, in `zzzz/`, and in `a/b/`;
  `resolvedLinks` then shows (1) whether root wins, (2) with `Note.md`
  removed, whether string length or depth decides, and (3) whether the
  source note's folder changes the answer.

### Case, aliases, properties

- Case-insensitive file-name matching for links is not stated on any help or
  API page read. [unverified] A forum thread (secondary, not a source) reports
  `[[log]]` and `[[LOG]]` both resolving to `Log.md` on macOS; a fixture with
  `Log.md` and `[[log]]` on a case-sensitive filesystem plus `resolvedLinks`
  would verify. The project matches paths case-sensitively (#235).
- Aliases are declared in the `aliases` property, "always [...] formatted as
  a list in YAML"; when an alias is chosen from suggestions "Obsidian creates
  the link with the alias as its custom display text, for example
  `[[Artificial Intelligence|AI]]`". [source: help-aliases]
  [pins: tests/test_links.py::TestAliasResolution::test_wikilink_resolves_via_alias]
- Whether a hand-typed `[[AI]]` (alias as target) resolves to the aliased
  note, whether that match is case-insensitive, and what happens when two
  notes share an alias are not documented; the help only says an alias
  "shows up in the list of suggestions". [unverified] Fixture: `[[AI]]` and
  `[[ai]]` with `aliases: [AI]` on one note, then on two, and
  `resolvedLinks`.
  [pins: tests/test_links.py::TestAliasResolution::test_alias_resolution_case_insensitive, tests/test_links.py::TestAliasResolution::test_path_match_takes_priority_over_alias, tests/test_links.py::TestAliasResolution::test_alias_with_fragment]
- Properties are YAML between `---` lines at the top of the file, name and
  value separated by `: `, each name unique. Types: Text, List, Number,
  Checkbox, Date (`2020-08-21`), Date & time (`2020-08-21T10:30:00`), Tags.
  [source: help-properties]
- Default properties: `tags` (List), `aliases` (List), `cssclasses` (List);
  `publish`, `permalink`, `description`, `image`, `cover` are Publish-only.
  [source: help-properties]
- `tag`, `alias`, `cssclass` are "Deprecated alias[es]" for the list forms:
  "These properties were deprecated in Obsidian 1.4 and should be replaced
  with their modern equivalents. Support for them as Default properties is
  dropped in Obsidian 1.9." [source: help-properties]
  [pins: tests/test_links.py::TestAliasResolution::test_alias_singular_key]
- Whether a scalar string in `aliases:` (not a list) is honoured is not
  stated; the help says lists only. [unverified] Fixture: `aliases: AI`.

### Titles

- Obsidian has no title property: the help pages read here never describe a
  `title` frontmatter key, the first H1, or any title derivation; the note's
  name is its file name (`fileToLinktext` uses "the filename"), and
  `HeadingCache` records headings with `heading` text and `level` 1–6.
  [source: api-dts] [source: help-properties] The project's own title
  derivation is a departure, pinned under "Where this project departs".
- Whether a heading that contains a wikilink (`# See [[Other]]`) is stored
  with the raw `[[Other]]` text in `HeadingCache.heading`, and therefore how
  `[[Note#See [[Other]]]]` would have to be spelled, is [unverified].

### Syntax that shares characters with links

- Tables: "If you want to use aliases, or to resize an image in your table,
  you need to add a `\` before the vertical bar", so `[[note\|alias]]` inside
  a cell is Obsidian's own escape and departs from GFM, where `\|` is only a
  literal pipe. [source: help-advanced]
  [pins: tests/test_links.py::TestExtractWikilinks::test_wikilink_escaped_pipe_alias_in_table, tests/test_links.py::TestExtractWikilinks::test_wikilink_escaped_pipe_dotmd_explicit_extension, tests/test_links.py::TestExtractWikilinks::test_wikilinks_mixed_escaped_and_plain_same_line, tests/test_links.py::TestExtractWikilinks::test_wikilink_mid_path_backslash_not_stripped, tests/test_links.py::TestFTSIndexLinks::test_escaped_pipe_wikilink_not_broken_and_backlinked]
- Footnotes: `[^1]` references with `[^1]: text` definitions, inline
  `^[This is an inline footnote.]` ("the caret goes outside the brackets"),
  continuation lines indented two spaces. Single-bracket, so they cannot
  match `_RE_WIKILINK`, but they do match reference-link regexes (#1104).
  [source: help-syntax] [source: help-ofm]
- Comments: "You can add comments by wrapping text with `%%`", inline
  (`%%inline%%`) or spanning lines, "only visible in Editing view". A link
  inside a comment is still text the scanner sees; Obsidian's treatment of it
  in `resolvedLinks` is [unverified]. [source: help-syntax]
- Math: inline `$...$` and block `$$...$$` (MathJax/LaTeX). `|` and `[` are
  common inside math and are not link syntax there. [source: help-advanced]
- Callouts: `> [!type]`, `> [!type] Custom Title`, foldable `> [!type]+` /
  `> [!type]-`, nestable; "The type identifier is case-insensitive"; thirteen
  built-in types (note, abstract, info, todo, tip, success, question, warning,
  failure, danger, bug, example, quote) plus aliases. A callout is a GFM
  blockquote whose first line is `[!type]`; single bracket, so not a
  wikilink. [source: help-callouts]
- Tags: `#` followed by letters, numbers, `_`, `-`, `/` (nesting, e.g.
  `#inbox/to-read`) and "Commonly accepted Unicode characters"; "Tags must
  contain at least one non-numerical character" (`#1984` is not a tag,
  `#y1984` is); no spaces; "Tags are case-insensitive". In properties,
  "Tags in YAML should always be formatted as a list" without `#`.
  [source: help-tags] A tag is not a heading (`#tag` has no space after `#`;
  headings need one), which is the CommonMark distinction the scanner's title
  regex `^#\s+` already relies on. [source: help-syntax]
- Obsidian "supports CommonMark, GitHub Flavored Markdown, and LaTeX" and
  "does not render Markdown syntax inside HTML elements". [source: help-ofm]

### Markdown links Obsidian writes

- With "Use Wikilinks" off, Obsidian writes
  `[Three laws of motion](Three%20laws%20of%20motion.md)`: URL-encoded, with
  the `.md` extension present, folder paths from the vault root
  (`[Three laws of motion](Projects/Three%20laws%20of%20motion.md)`).
  [source: help-links] [source: help-settings]
- The path style follows "New link format" (shortest unique / relative /
  absolute), and "Automatically update internal links" rewrites links on
  rename. [source: help-settings]
- Whether Obsidian percent-decodes a markdown destination when resolving it,
  and whether a bare `Note.md` markdown link is looked up vault-wide or only
  relative to the source, is [unverified]. Fixture: `[x](Sub/My%20Note.md)`
  from root and from `Sub/`, then `resolvedLinks`. The scanner resolves
  markdown links source-relative after percent-decoding (#1332).

### File names

- Characters that "may not work as a link": `# | ^ : %% [[ ]]`.
  [source: help-links] Forum threads (secondary) report Obsidian Sync and
  mobile rejecting `* " / < > : | ?`; no help page read states a
  desktop-wide forbidden set. [unverified]
- Unicode normalisation (NFC/NFD) of file names is not mentioned by any
  source read. [unverified] Fixture: `Café.md` saved NFD, a note with
  `[[Café]]` typed NFC, then `resolvedLinks`.

## Where this project departs from the subject

Each entry names the function and the design section that decides it.

- `_RE_WIKILINK` (scanner): the grammar `[[target]]` / `[[target|alias]]`,
  no `]` or `|` in the target, is narrower than anything Obsidian documents
  (the help gives a "may not work" list, not a grammar). Unverifiable rather
  than contrary. design.md § Link Extraction.
- `_extract_wikilinks` / `_extract_inline_links` / `_extract_reference_links`:
  a target with an allowlisted attachment extension is not a link at all
  (#1333), where Obsidian resolves `[[Figure 1.png]]` to the file and shows
  it in the graph. A deliberate carve-out — the link graph is notes-only
  until attachments become graph nodes (#1359). design.md § Link Extraction
  ("The link graph is notes-only").
- `_extract_wikilinks`: no `![[` discrimination, so a note embed is a link.
  Not modelled; the help defines embeds as a distinct syntax.
- `_extract_wikilinks`: `./` and `../` opt-out is a project convention; the
  help shows no relative wikilink form. Unverifiable.
- `FTSIndex.resolve_vault_wikilinks`: suffix match "ends with `/stem.md`" is
  wider than the help's vault-root folder paths. Unverifiable.
- `FTSIndex.resolve_vault_wikilinks`: `min(candidates, key=len)` versus the
  design doc's "fewest path components" — #1350 open; neither is sourced,
  and Obsidian only documents the *writing* rule (shortest unique path).
  design.md § Link Extraction (Wikilink resolution).
- `FTSIndex.resolve_vault_wikilinks`: path matching case-sensitive — #235
  asked for case-insensitivity; Obsidian's behaviour is unverified.
- `FTSIndex.resolve_vault_wikilinks` / `_insert_aliases`: `[[Alias]]` as a
  target, case-insensitive alias match, path-beats-alias, shortest path among
  alias holders — all project rules; the help documents aliases only as
  suggestion entries that expand to `[[Note|Alias]]`. Unverifiable.
- `_insert_aliases`: honours the scalar `alias` key that Obsidian deprecated
  in 1.4 and dropped in 1.9. Wider than current Obsidian; harmless for
  vaults that predate 1.9. design.md § Link Extraction (Alias resolution).
- `_resolve_title`: frontmatter `title` → first H1 → file stem is a project
  rule; Obsidian's note name is the file name only. design.md § Data model
  (`title`). The departure, not Obsidian's behaviour, is what these tests
  assert: [pins: tests/test_scanner.py::test_title_from_h1, tests/test_scanner.py::test_title_from_filename, tests/test_scanner.py::TestTitleField::test_falls_back_to_h1_then_stem]
- `_extract_wikilinks` (#1107) and `_is_external_target` (#1335): skipping
  `[[#Heading]]` and schemed targets is consistent with the help but chosen by
  the project. design.md § Link Extraction.
- `convert_wikilinks_to_markdown` (okf) and `apply_link_replacement`
  (utils/links): match `[[raw_target` literally, so the `\|` table escape
  and interior whitespace variants are not rewritten (documented limitation
  in both docstrings).
- Not modelled at all: block identifiers `^id` as anchors, callouts, `%%`
  comments (links inside them are still indexed), inline `#tags`, property
  types, `cssclasses`, embed sizes, PDF `#page=`, `.base`/`.canvas` targets.

## Not covered

- Everything marked `[unverified]` above; the single most valuable fixture is
  the #1350 vault (three `Note.md` files, three source folders) read through
  `metadataCache.resolvedLinks`.
- Block-fragment links (`[[note#^id]]`): stored as a plain fragment, no test.
- Embeds of notes (`![[Note]]`) as distinct from links: the scanner treats
  both as links; whether Obsidian's graph does is unknown.
- Same-note `[[#Heading]]` inside Obsidian's own graph: unknown whether it is
  a self-edge there.
- A wikilink inside a code span, `%%` comment, callout title, math, or
  footnote body: the scanner strips code only; Obsidian's treatment unknown.
- Percent-decoding of markdown destinations, chained `#H1#H2` subpaths, and
  headings containing a link: no test either way.
