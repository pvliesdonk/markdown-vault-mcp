# Next release

<!-- notes-range-end: 1f81a24876ad99561572c5a408b47953f977a28a -->

<!-- RELEASE-SUMMARY NEXT START -->
This release closes gaps where a change looked saved but wasn't, or a failure
came back as an empty answer. In a Git-backed vault, a plain Git repository
nested inside the vault is now read-only, like a submodule. Upload links,
`fetch` and the OKF refresh refuse such a destination up front. A search by
meaning after an embedding model change reports a failed rebuild instead of
returning nothing. Wikilinks with a `]` in the target resolve as in Obsidian.
Commit subjects name the note when OKF maintenance is on. Local terminal
reads work as root and stop printing startup logs. The package ships its type
hints. The first start rebuilds the text index once; see
[Upgrading](#upgrading).
<!-- RELEASE-SUMMARY NEXT END -->

## Upgrading

No step is required for most deployments.

- **If your Git-backed vault holds a folder with its own `.git` that is not a
  registered submodule, and something writes notes into it:** those writes
  now fail with an error naming the nested repository. Before this release
  they reported success but were never committed. Change those files in
  their own repository, or move the destination outside it
  ([#1949](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1949)).
- **If you used `-e MARKDOWN_VAULT_MCP_LOG_LEVEL=WARNING` to keep startup logs
  out of `docker exec` reads:** you can drop it. Pass `-v` when you want the
  routine logs back.
- **If your Python code type-checks against `markdown_vault_mcp`:** mypy now
  reads the package's type hints, so code that passed against an untyped
  package can report new type errors. Nothing changes at runtime.

**Clients:** nothing beyond reconnecting; the tool surface is unchanged.
Two cases that looked like success now return errors: a write into a nested
repository and a search by meaning after a failed model-change rebuild.

**State:** kept. The first start after upgrading rebuilds the text index
once, automatically, because this release changes which wikilinks are read
from a note. The embeddings are kept: the rebuild re-embeds only notes whose
sections changed, and this release changes no sections.

**Security posture:** none. Nested repositories become read-only for the
server's write tools. Reads, search and download links still reach them.

## Nested repositories are read-only

Since 6.0 the server refuses to change files inside a Git submodule of a
Git-backed vault: a change there never reaches the vault's own commits. That
check missed a plain repository nested in the vault: a folder with its own
`.git` that isn't a registered submodule. A write there reported
success but "is never committed"
([#1949](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1949)).

The server now refuses writes into a nested repository as it does for a
submodule, including a repository created after the server started
([#2088](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2088)).
The error names the argument and the nested repository. Reading and
searching those files still work. A vault without Git refuses nothing.

Three callers reached that refusal only after doing other work
([#1950](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1950)).
They now refuse up front:

- [`create_upload_link`](../reference/tools/transfer.md#create_upload_link) refuses a protected
  `ref` before it creates a link, instead of handing out a link whose upload
  then failed ([#2101](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2101)).
- `fetch` refuses a protected `path` before it downloads anything
  ([#2103](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2103)).
- After `move_folder`, the OKF index refresh skips these folders instead of
  logging `okf_convention_index_failed` with a traceback
  ([#2104](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2104)).

The [Git integration guide](../use/git-integration.md#submodules-and-nested-repositories-in-the-vault)
describes the boundary.

## Search by meaning reports a failed model change

When the embedding model or its vector size changes, the server rebuilds the
embeddings on the next search by meaning. If every batch of that rebuild
failed because the provider was down,
"the search returns `[]` and so does every later search. The caller gets no
error"
([#1946](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1946)).

Semantic and hybrid searches now fail with an error that says the rebuild
failed, that the request was fine, and to retry later or search by keyword.
Each later search tries the rebuild again and recovers once the provider
does. `admin index` and `admin embeddings import` print the same failure and
exit with status 1
([#2081](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2081)). The
[embeddings guide](../use/embeddings.md#common-to-all-providers) covers
model changes.

## Links and indexing

- **A `]` inside a wikilink target.** "A single `]` inside a wikilink target
  ends the link for the scanner, but not for Obsidian, where only `]]` does"
  ([#1384](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1384)).
  `[[Sq]uare]]` now links to `Sq]uare.md`, for backlinks, broken-link
  reports and renames alike
  ([#2089](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2089)).
  This is the change behind the one-time index rebuild.
- **Slow indexing of long backtick or space runs.** Link extraction took
  time growing with the square of an unmatched run of backticks, or of
  spaces before a link title: "Doubling the run roughly quadruples the time"
  ([#1749](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1749)).
  Both cases now run in linear time with the same results. For a
  16,000-character backtick run the PR measured 3.359 s before and
  0.00256 s after
  ([#2084](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2084)).

## Git history

- **Commit subjects name the note.** With OKF maintenance on, a single-note
  write, edit or append committed as `edit: 3 files`, because the folder's
  `index.md` and `log.md` were counted. The owner's report in
  [#1675](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1675):
  "Every commit is as an edit for 3 files." The subject now names the note,
  and the `index.md` and `log.md` updates still land in the same commit
  ([#2091](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2091)).
- **Carriage returns in filenames.** A note whose name contains a carriage
  return, which can only arrive from outside the server, "comes back from
  `get_history` with that byte rewritten to a newline, so the path names a
  file that does not exist"
  ([#1290](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1290)).
  History, rename tracking, revision reads and conflict recovery now keep
  the name as Git wrote it
  ([#2092](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2092),
  [guide](../use/git-integration.md)).

## Terminal reads in a container

Running `markdown-vault-mcp search` through `docker exec` as root on a Git
checkout owned by the service user failed with "Managed mode requires
SOURCE_DIR to be empty or a git repository"
([#1999](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1999)).
Local `search` now skips the managed Git checks, as `read`, `list` and the
other read commands already did. It never clones or syncs
([#2097](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2097)).

The same issue reported that "every successful call also writes the server's
startup log lines (JSON, INFO) to stderr, interleaved with the results."
Local read commands now print only warnings and errors; `-v` or
`MARKDOWN_VAULT_MCP_LOG_LEVEL=DEBUG` brings the routine lines back
([#2100](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2100)). The
[terminal guide](../use/terminal.md) shows the updated alias.

## Type hints for library users

The package now ships a `py.typed` marker, so mypy checks code that uses
`markdown_vault_mcp` against its type hints instead of skipping it with
"module is installed, but missing library stubs or py.typed marker"
([#1911](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1911)).
Thanks to [@tstotzer](https://github.com/tstotzer) for
[#2026](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2026).

## Vault explorer

Screen readers now announce the search box in the
[vault explorer](../use/vault-explorer.md) as "Search vault" and the context
heading as "Context" while the first note loads, where it used to be empty
([#1754](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1754),
[#2096](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2096)).

## Contributors

Every fix in this release except the type marker came from
[@salmonumbrella](https://github.com/salmonumbrella), who sent the pull
requests for issues the maintainer had filed.
