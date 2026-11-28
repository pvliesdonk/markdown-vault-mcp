# Next release

<!-- notes-range-end: 39faddf6de5dcc96b19734a02ac592cf019fce20 -->

<!-- RELEASE-SUMMARY NEXT START -->
This release reads and writes Open Knowledge Format bundles the way OKF 0.3
specifies. A review now counts toward the trust tier only while it covers the
current content. Dates follow the spec's grammar, and the server stamps
provenance on concepts and nothing else. Every vault also reads frontmatter
more strictly: by the YAML 1.2 rules and only from an exact `---` first line.
Two new read-only tools join a note's footnotes to its sources and find the
notes citing a source. Long tasks started from Claude keep their results for
two hours, and the vault-workflow skill is now served over MCP. Several
changes alter what a vault reads or writes, and the index rebuilds once; see
[Upgrading](#upgrading).
<!-- RELEASE-SUMMARY NEXT END -->

## Upgrading

Read the steps that match your deployment. Most vaults need none of them, but
each one describes a note or setting that reads differently after the
upgrade.

- **If a note's frontmatter does not start on the first line of the file**, or is
  fenced by `----` or `--- ` (with a trailing space), move it to start with a
  line that is exactly `---` and end with another. Such a block is now body
  text, so its keys are no longer read, indexed or filtered on. A leading
  UTF-8 byte order mark still reads as before
  ([#2452](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2452)).
- **If your frontmatter relies on YAML 1.1 values** such as `draft: yes`, `on`
  or `1_000`, quote or rewrite them. Frontmatter is now read by the YAML 1.2
  core schema: `yes` and `on` stay strings, and dates arrive as the strings
  you wrote, also in the metadata the Python library returns
  ([#2415](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2415)).
- **If you set `MARKDOWN_VAULT_MCP_FTS_WEIGHTS` or
  `MARKDOWN_VAULT_MCP_FOLDER_WEIGHTS`**, check that at least one search column
  weighs more than 0 and that no key appears twice with different weights.
  The server now refuses to start on either, where it used to rank silently
  wrong ([#2205](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2205)).
- **If a client or script passes git-style dates to `get_history` or
  `get_diff`**, such as `June 1 2020` or `last friday`, switch to an ISO date,
  `now`, `today`, `yesterday` or `N days ago`. Other forms are refused instead
  of being guessed at
  ([#1631](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1631)).
- **If tooling searches notes for `conflict_with` or `conflict_date`**, also
  match `x-markdown-vault-mcp-conflict-with` and
  `x-markdown-vault-mcp-conflict-date`. Git conflict marking writes the new
  names from now on; notes marked earlier keep the old ones
  ([#2455](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2455)).
- **If you run with `MARKDOWN_VAULT_MCP_OKF_WRITE=true`**, read
  [What the server writes into a bundle](#what-the-server-writes-into-a-bundle):
  files that are not concepts are no longer stamped, a write no longer clears
  `verified`, and a write whose frontmatter does not parse now lands instead
  of being refused.
- **If your bundle keeps notes under a folder whose name starts with `.`**,
  such as `.drafts/`, expect those notes to drop out of the audit, the
  export and the OKF filters. Search still finds them
  ([#2451](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2451)).
- **If you used the Claude Code plugin's `vault-workflow` skill**, it now
  comes from the server instead, so Claude loads it only on a client that
  supports MCP skills
  ([#1552](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1552),
  [Claude Code setup](../get-started/claude-code.md)).

**Clients:** reconnect to pick up two new read-only tools, `inspect_citations`
and `find_citing_notes`, the `vault-workflow` skill, and new members of the
`okf` annotation. `get_server_info` no longer reports `core_version`.

**State:** the search index rebuilds once, on the first start of a writing
server after the upgrade, because the index semantics version moves from 12
to 13. The rebuild also fills the new citations table that `find_citing_notes`
reads. Notes on disk are not rewritten, so old conflict keys and old link
spellings stay until a note is next written.

**Security posture:** unchanged. The new tools and the `skill://` resources are
read-only. No route, authentication default or write tool was added.

## OKF bundles read as OKF 0.3 specifies

The [Open Knowledge Format](https://github.com/open-knowledge-format/open-knowledge-format)
is a spec for knowledge bundles that agents can read: Markdown files whose
frontmatter records what each note is, where it came from and who checked it.
This server reads a bundle when its root `index.md` declares `okf_version`,
and annotates each note it returns with what that metadata means. As
recorded on [epic #2401](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2401),
the account holder set the bar for this release: "Spec needs to match
*exactly*, that is what it's a spec/standard for". A bundle is now read by the
OKF 0.3 rules whatever version it declares, and an unknown version is read
best-effort with a warning
([#2422](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2422)).

What changes for a bundle you serve:

- **Reviews count only while they are current.** A `verified` event counts
  toward the trust tier only when its `at` is at or after the note's
  `generated.at`, so a human review recorded before the last content change no
  longer makes the note `human-reviewed`. The event stays in the file as
  history ([#2409](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2409)).
- **Malformed evidence no longer reads as good evidence.** A `stale_after` the
  server cannot parse now reads as stale, never fresh. A bare date means the
  start of that day in UTC. A date-time must follow RFC 3339, seconds and a
  `T` included ([#2410](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2410),
  [#2404](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2404),
  [#2428](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2428)).
  A `status` outside `draft`, `stable` and `deprecated`, such as `Stable` or a
  number, is not stable
  ([#2420](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2420)).
  A null or empty field counts as absent, and a single item counts as a list
  of one ([#2411](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2411),
  [#2412](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2412)).
- **The annotation says what the metadata supports.** Each note's `okf`
  annotation now reports `freshness`, `review`, `attribution` and
  `generation` as a `state` with its `basis`: a `stale_after` can be
  missing, malformed, unexpired or expired. A separate
  `review_applicability` says that no recorded review is bound to the content
  it covered ([#1444](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1444),
  [#2378](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2378),
  [#2059](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2059)).
  A deprecated note's annotation also names its replacement as a vault path
  you can pass to `read`
  ([#2421](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2421)).
- **`okf_validate` measures conformance as OKF defines it.** A file without a
  `type`, such as a `README.md`, is a warning, not a failure, so it no longer
  lowers the conformance ratio. Frontmatter in `log.md` and in a folder
  `index.md` is reported, and the root `index.md` may carry `title`,
  `description`, `license` and your own keys
  ([#2406](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2406),
  [#2413](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2413),
  [#2414](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2414)).
- **Dot paths are not part of the bundle.** A file or folder whose name starts
  with `.` stays in the vault and in search, but the audit, the export and the
  generated listings leave it out, and its results carry no `okf`
  annotation. The account holder framed the rule on
  [#2451](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2451#issuecomment-7236574276):
  "What matters here is that the server presents a bundle to the client, and
  for that it doesn't matter how the backend works." The export guard comes
  from [@salmonumbrella](https://github.com/salmonumbrella)'s
  [#2525](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2525).

The [OKF guide](../use/okf.md) describes the annotation and the audit as they
now stand.

## What the server writes into a bundle

These changes apply when `MARKDOWN_VAULT_MCP_OKF_WRITE=true` and the vault is
an active OKF bundle.

- **Only concepts are stamped.** The server adds `generated: {by, at}` to a
  `.md` file other than `index.md` and `log.md` whose frontmatter, as written,
  has a non-empty `type`. Any other file lands exactly as sent: a `README.md`,
  a `SKILL.md`, an Agent Skill's resource files, or a `log.md` gain no
  frontmatter ([#2486](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2486),
  [#2478](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2478)).
  A write whose frontmatter does not parse has no `type` the server can read,
  so it now lands unstamped where it used to be refused; `okf_validate`
  reports the block.
- **Reviews survive your writes.** A `write`, `edit` or `append` keeps the
  stored `verified` events as history instead of deleting them. After a
  change a reader can see, they no longer count toward the tier, so call
  `okf_verify` to record a review of the new content
  ([#2405](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2405)).
- **A formatting-only edit keeps `generated.at`.** OKF says a trivial edit does
  not change it. The server treats an edit as trivial only when the
  frontmatter is otherwise equal and the body renders to the same Markdown,
  so changed emphasis markers or blank lines keep an earlier review current,
  while a reworded line does not. The account holder chose this definition on
  [#2418](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2418#issuecomment-7066767930).
- **Frontmatter keeps its bytes.** The stamp and `okf_verify` rewrite only the
  keys they change, so comments and quoting elsewhere in the block survive,
  and keys the server does not know inside `generated`, such as `x-model`,
  are kept ([#2419](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2419),
  [#2484](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2484)).
  Regenerating the root `index.md` keeps its frontmatter byte for byte, so
  `okf_version: "0.3"` is no longer rewritten as `'0.3'`
  ([#2485](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2485)).
- **No case-only twins.** The server never creates an `index.md` or `log.md`
  beside a file whose name differs only by case, such as your own `Index.md`;
  on macOS or Windows the new file would overwrite it. `okf_generate_index`
  and `okf_seed_log` refuse that folder, and automatic upkeep skips it
  ([#2454](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2454)).

## Frontmatter and links in every vault

These changes apply with or without OKF.

- **Frontmatter is read strictly.** A block opens only on a first line that is
  exactly `---` and closes on the next such line, and its values follow the
  YAML 1.2 core schema. Both match what OKF 0.3 §4 requires. The account
  holder chose to apply them to every vault, and to keep reading a leading
  byte order mark as an encoding signature, which is how OKF's own tools
  read it
  ([research on #2452](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2452#issuecomment-7235848251)).
- **Links resolve across folders.** A Markdown link whose relative target is
  missing now finds the note elsewhere in the vault, by exact path and then by
  the closest match, the way Obsidian resolves it. Explicit `/`, `./` and `../`
  prefixes keep their meaning, and rename and folder moves rewrite these links
  ([#1383](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1383)).
  A target that climbs above the vault root is external and reported broken,
  where it used to be clamped onto a note at the root
  ([#2416](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2416)).
- **Written links are URI references.** Links the server writes, during a
  rename with link updates, `okf_convert_links` or an export, are
  percent-encoded as RFC 3986 requires: `50% done.md` becomes
  `50%25%20done.md` ([#2417](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2417)).
- **Conflict keys carry the server's prefix.** OKF 0.3 asks producers to name
  their own keys `x-<producer>-<name>`, so git conflict marking now writes
  `x-markdown-vault-mcp-conflict-with` and `x-markdown-vault-mcp-conflict-date`
  ([#2455](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2455)).

## Join footnotes to their sources

Two read-only tools answer a question
[#2379](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2379) put this
way: "there is no way to ask which source records a note's footnote labels
resolve to, or which notes cite a given source resource."

- `inspect_citations(path)` joins each `[^label]` footnote in a note to the
  `sources` entry whose `id` is exactly that label, and reports labels that
  match nothing or more than one entry. It works without a bundle
  declaration.
- `find_citing_notes(resource)` lists the notes whose `sources` name that exact
  resource, with how often each cites it. It reads the citations table that
  the upgrade's index rebuild fills.

Neither tool opens a source. See
[Inspect citations in one note](../use/okf.md#inspect-citations-in-one-note).
Most of the evidence and citation work in this release, nine of the thirteen
pull requests of package `024`, came from
[@salmonumbrella](https://github.com/salmonumbrella).

## Search and history refuse what they used to misread

- `MARKDOWN_VAULT_MCP_FTS_WEIGHTS` and `MARKDOWN_VAULT_MCP_FOLDER_WEIGHTS` are
  checked at startup. As
  [#2205](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2205) put
  it: "An operator with a degenerate or colliding map gets wrong ranking with
  no error." A map where every column weighs 0, or where a key repeats with a
  different weight, now stops the server with a configuration error
  ([configuration reference](../reference/configuration.md)).
- The `since` and `until` filters of `get_history`, and `since_timestamp` of
  `get_diff`, accept ISO dates and date-times from 1970 to 2099, `now`,
  `today`, `yesterday` and `N units ago`. Before, "a mistyped or malformed date
  returns an answer filtered by a date git made up, not a refusal"
  ([#1631](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1631)).
  `today` means local midnight, so `until="today"` leaves out today's
  commits ([git tools](../reference/tools/git.md)).

## Working with Claude

- **Task results last two hours.** A tool that runs as an MCP task now keeps
  its result for two hours from creation; before, the server forgot it
  15 minutes after the call started. Claude Code's guidance for server authors, quoted in
  [#2529](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2529), asks
  servers to "Keep a finished task's result for at least an hour, and longer
  if you can." Set `FASTMCP_DOCKET_EXECUTION_TTL` (an ISO 8601 duration,
  default `PT2H`) to change it. The other `FASTMCP_DOCKET_*` durations are
  now documented as ISO 8601 too; the bare seconds the reference used to give
  stopped the server from starting
  ([#2531](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2531)).
- **The vault-workflow skill comes from the server.** The server serves its
  usage guidance as the `vault-workflow` skill over MCP, at
  `skill://vault-workflow/SKILL.md`, so any client that supports MCP skills
  can load it on demand. Clients without that support see it as a plain
  resource. The Claude Code plugin keeps `vault-setup` and `vault-summarize`
  ([#1552](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1552),
  [Claude Code setup](../get-started/claude-code.md)).
- **Collected jobs free their slot.** When `get_job_result` returns a job's
  final outcome, the job no longer counts against
  `MARKDOWN_VAULT_MCP_JOBS_MAX_PER_SUBJECT` (default 256). Before, "a caller
  that promotes more than 256 calls within one retention window is refused,
  even when it has collected every result"
  ([#2530](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2530)).

## One package to install

The server no longer depends on `fastmcp-pvl-core`: the shared code it used
came into this repository
([epic #2241](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2241)).
Every setting, environment variable and default stays the same. Installing
the package now pulls `fastmcp[tasks]` 4.2.6 or later directly. Unexpected
errors inside a resource read now reach the client as a generic message,
with the detail kept in the server log
([template #888](https://github.com/pvliesdonk/fastmcp-server-template/pull/888),
applied in [#2043](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2043)).

## Known issues

- A busy or broken index read through the `toc://` or `similar://` resources
  now reaches the client as a generic error, so it cannot tell that a retry
  could work ([#2256](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2256)).
- A note reached through a symlink to a file or folder outside the vault is
  listed and searchable, but `read` refuses it
  ([#2545](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2545)).
- Four departures from OKF 0.3 remain open:
  [#1443](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1443) (the
  stamp replaces a supplied `generated.by`),
  [#1438](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1438)
  (seeded frontmatter on reserved files),
  [#2423](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2423) (the
  root `okf_version` upgrade path) and
  [#2488](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2488) (the
  audit honours exclude patterns).
