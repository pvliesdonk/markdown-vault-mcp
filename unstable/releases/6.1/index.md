# 6.1

This release brings the vault to the terminal. The `markdown-vault-mcp` command can now search, read and explore a vault that a server publishes over MCP, with a bearer token and no copy of the vault or its index on the machine you type on; the same commands still work against a local vault. Clients and scripts also get exact answers ranked search could not give, from the number of notes that match to the path a mistyped filename meant. The commands that write the index move under `admin`, and their old names keep working. A build that fails to index a note now retries it on the next run. Nothing needs to change on upgrade; see [Upgrading](#upgrading).

## Upgrading

No step is required. The new commands, tools and settings are additions.

- **If a script, cron job or unit runs `markdown-vault-mcp index`, `reindex` or `embeddings import`:** it keeps working, and prints a note on stderr naming the new command. Switch it to `admin index`, `admin reindex` or `admin embeddings import` when convenient ([#2005](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2005)).

**Clients:** nothing beyond reconnecting. Clients discover four new read-only tools: `query_documents`, `count_documents`, `aggregate_documents` and `search_paths`.

**State:** kept. The index and the embeddings are used as they are; nothing is rebuilt.

**Security posture:** none on the server. The four new tools are read-only and answer from the same index the existing reader tools read. The new terminal client sends its bearer token only to the endpoint you select, over HTTP(S), with certificate verification on ([CLI reference](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/cli/#search-a-hosted-vault)).

## Query a vault from the terminal

Until now the `search` command built a local vault before every query. As [#1831](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1831) put it: "A person with access to an already hosted vault cannot search it from this CLI without also making its data and local configuration available on the client." The command now reaches a hosted vault through the MCP endpoint it already serves. The idea, the issues and all nine pull requests of [epic #1829](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1829) come from [@salmonumbrella](https://github.com/salmonumbrella), who proposed "a terminal workflow for a hosted Markdown vault" that never copies the vault or its index to the client.

Install the command without extras on the machine you type on, then point a profile at the server:

```
uv tool install markdown-vault-mcp
```

```
# ~/.config/markdown-vault-mcp/client.toml
[remote]
url = "https://vault.example/mcp"
token_env = "VAULT_ACCESS_TOKEN"
```

`url` is the server's complete MCP endpoint, and `token_env` names the environment variable that holds the bearer token, so the profile never stores it. `--server` or `MARKDOWN_VAULT_MCP_SERVER_URL` selects an endpoint without a profile, `MARKDOWN_VAULT_MCP_CLIENT_TOKEN` supplies a token directly, and `--local` reads the local vault instead. A remote read never falls back to the local vault. The command doesn't sign in interactively; an OIDC access token you already hold works as the bearer token.

The read commands, all of which accept the connection options above:

- `search`, now with `--mode auto`, repeated `--filter KEY=VALUE`, `--chunks-per-file` and `--snippet-words` ([#1837](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1837), [#1844](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1844));
- `read`, `list`, `folders`, `tags`, `stats`, `recent`, `toc`, `similar` and `context` ([#1845](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1845));
- `links to`, `links from`, `links broken`, `links orphans`, `links hubs` and `links path` ([#1847](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1847));
- `query metadata`, `query body`, `query note`, `query count` and `query aggregate` ([#1851](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1851));
- `query path`, with `--fuzzy` for a one-letter typo ([#1849](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1849)).

Output is plain text, `--json` for scripts, or `--agent` for one escaped record per line. On the Docker host you can also run the same commands inside the server's container, as the `appuser` user, against the server's own index with no address or token. Running them as root fails on a vault that is a git checkout ([#1999](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1999)).

[Query a vault from the terminal](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/terminal/index.md) walks through both routes, and the [command reference](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/cli/index.md) lists every option.

## Exact counts, groups and path discovery

Search ranks a bounded set of hits, so its length says nothing about how many notes match. [#1842](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1842) describes the gap: "MCP clients receive bounded ranked search results, so they cannot use a result count as the total number of matching notes or count the full population by folder, tag or month." Four read-only tools answer those questions ([#1839](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1839), [#1846](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1846), [#1848](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1848)):

- `query_documents` pages through every matching note with its exact total (20 notes per page by default, up to 1,000);
- `count_documents` counts every matching note without reading content;
- `aggregate_documents` groups the whole population by folder, tag or UTC month;
- `search_paths` finds an indexed path from literal text, and with `fuzzy` also one a letter off: "A remembered filename with one wrong letter is hard to recover through content search" ([#1848](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1848)).

The first three share their predicates: a keyword query scoped to metadata, body or both, a folder, a path or glob, modification bounds, and `filters`. `filters` works on the fields listed in `indexed_frontmatter_fields`, which `stats` reports; computed OKF fields stay with `search`. The [reader tools reference](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/reader/#query_documents) has every parameter and default.

Library callers get the same through `vault.reader`: `query_documents`, `count_documents_matching`, `aggregate_documents` and `search_paths`, with the request types in `markdown_vault_mcp.query_types`. A custom keyword index written against an earlier release keeps working. Exact queries are an optional capability, `RunsExactQueries`, rather than new required methods on `KeywordIndex`, so an index without it answers search as before. The exact queries then raise `ExactQueriesNotSupportedError`, which points the caller at `search` ([per the maintainer](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1846#issuecomment-6573006023); [storage reference](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/api/storage/#exact-keyword-queries)).

## A note that fails to index is retried

When a build failed to index a note, it logged `build_index_index_failed` and still recorded the note's new content hash. The next build then treated the note as unchanged and never retried it until the file changed again. A normal build kept serving the note's previous content, and a forced build left it out of search ([#1982](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1982)). A failed note is now recorded so that the next build indexes it again ([#1991](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1991), by [@tstotzer](https://github.com/tstotzer)).

## Commands that write move under `admin`

`index`, `reindex` and `embeddings import` change the shared index or the stored vectors, and they now sit in one group: `admin index`, `admin reindex` and `admin embeddings import`. Every other command except `serve` only reads, so the help screen shows what can change a vault. The old names still run the same commands and name the new form on stderr ([#2005](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2005), [#2006](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2006), [#2007](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2007)).

The [command reference](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/cli/#markdown-vault-mcp-admin-embeddings-import) now documents `admin embeddings import` with every option. Before, it listed the `embeddings` group's description only. The fix comes from the project template's reference generator, which now walks a command group's sub-commands ([fastmcp-server-template#823](https://github.com/pvliesdonk/fastmcp-server-template/issues/823), [fastmcp-server-template#850](https://github.com/pvliesdonk/fastmcp-server-template/pull/850), [#1998](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1998)).

## v6.1.1

This release fixes how the server finds the headings in a note. The outline that `get_toc` returns now lists every heading the note has, a `#` line inside fenced code is no longer read as a heading, and a note that opens with indented code keeps it as code instead of taking its first line as the title. Nothing needs to change on upgrade. The first start rebuilds the text index once; the release's Upgrading notes say what that keeps.

### Upgrading

No step is required.

**Clients:** nothing beyond reconnecting. No tool, parameter or payload shape changes. An outline can list more headings than before, a heading that occurs twice in a note is listed twice, and a note whose title came from a `#` line in code gets its real title.

**State:** kept. The first start after upgrading rebuilds the text index once, automatically, because this release changes how headings are derived from the notes. The embeddings are kept: only a note whose sections came out different is embedded again, and only its changed sections. Those are notes with a `#` line inside fenced code and notes whose first line is indented. A reader that opens an index read-only, such as the terminal client on a local index, keeps the old outline for an index an older version built until the server has rebuilt it, and logs `toc_heading_table_missing` once.

**Security posture:** none.

### The outline lists every heading

[`get_toc`](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/reader/#get_toc) and the `toc://vault/{path}` resource return the heading outline of a note, or of every note under a folder. Until this release the outline was read from the index's search chunks, and a chunk carries at most one heading. The report in [#2033](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2033) was that it "omits headings that `read` returns and that `read(path, section=...)` finds". The outline missed:

- every heading in a note of 30 lines or fewer;
- headings below H2 inside a section short enough to stay one chunk;
- a heading with no text under it;
- in a long section, a heading followed directly by a deeper one.

The index now stores every heading of a note in document order, found by the same scan that `read(path, section=...)` uses, so each heading the outline lists is one a section read accepts, and the other way round ([#2035](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2035)). Search chunks are unchanged.

### Code is not a heading

A `#` line inside fenced code is a comment or a prompt, not a heading. As [#2034](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2034) put it, `read(path, section="Setup")` "stops at a `# comment` line inside a fenced code block". The same line showed in the outline, split a search chunk inside the code, and could become the note's title. Lines between an opening and a closing fence of three or more backticks or tildes are now never headings, for section reads, the outline, chunking and the title alike ([#2037](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2037)).

A note that opens with indented code had the same problem one step earlier: the body was read with its first line's indentation stripped, so, in the words of [#2036](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2036), "the code line loses its indentation, and a `#` line in it becomes the note's title and an H1 in the outline". The first line now keeps its indentation; every other note reads exactly as before ([#2038](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2038)).

## v6.1.2

This release closes gaps where a change looked saved but wasn't, or a failure came back as an empty answer. In a Git-backed vault, a plain Git repository nested inside the vault is now read-only, like a submodule. Upload links, `fetch` and the OKF refresh refuse such a destination up front. A search by meaning after an embedding model change reports a failed rebuild instead of returning nothing. Wikilinks with a `]` in the target resolve as in Obsidian. Commit subjects name the note when OKF maintenance is on. Local terminal reads work as root and stop printing startup logs. The package ships its type hints. The first start rebuilds the text index once; see [Upgrading](#upgrading).

### Upgrading

No step is required for most deployments.

- **If your Git-backed vault holds a folder with its own `.git` that is not a registered submodule, and something writes notes into it:** those writes now fail with an error naming the nested repository. Before this release they reported success but were never committed. Change those files in their own repository, or move the destination outside it ([#1949](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1949)).
- **If you used `-e MARKDOWN_VAULT_MCP_LOG_LEVEL=WARNING` to keep startup logs out of `docker exec` reads:** you can drop it. Pass `-v` when you want the routine logs back.
- **If your Python code type-checks against `markdown_vault_mcp`:** mypy now reads the package's type hints, so code that passed against an untyped package can report new type errors. Nothing changes at runtime.

**Clients:** nothing beyond reconnecting; the tool surface is unchanged. Two cases that looked like success now return errors: a write into a nested repository and a search by meaning after a failed model-change rebuild.

**State:** kept. The first start after upgrading rebuilds the text index once, automatically, because this release changes which wikilinks are read from a note. The embeddings are kept: the rebuild re-embeds only notes whose sections changed, and this release changes no sections.

**Security posture:** none. Nested repositories become read-only for the server's write tools. Reads, search and download links still reach them.

### Nested repositories are read-only

Since 6.0 the server refuses to change files inside a Git submodule of a Git-backed vault: a change there never reaches the vault's own commits. That check missed a plain repository nested in the vault: a folder with its own `.git` that isn't a registered submodule. A write there reported success but "is never committed" ([#1949](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1949)).

The server now refuses writes into a nested repository as it does for a submodule, including a repository created after the server started ([#2088](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2088)). The error names the argument and the nested repository. Reading and searching those files still work. A vault without Git refuses nothing.

Three callers reached that refusal only after doing other work ([#1950](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1950)). They now refuse up front:

- [`create_upload_link`](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/transfer/#create_upload_link) refuses a protected `ref` before it creates a link, instead of handing out a link whose upload then failed ([#2101](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2101)).
- `fetch` refuses a protected `path` before it downloads anything ([#2103](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2103)).
- After `move_folder`, the OKF index refresh skips these folders instead of logging `okf_convention_index_failed` with a traceback ([#2104](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2104)).

The [Git integration guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/git-integration/#submodules-and-nested-repositories-in-the-vault) describes the boundary.

### Search by meaning reports a failed model change

When the embedding model or its vector size changes, the server rebuilds the embeddings on the next search by meaning. If every batch of that rebuild failed because the provider was down, "the search returns `[]` and so does every later search. The caller gets no error" ([#1946](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1946)).

Semantic and hybrid searches now fail with an error that says the rebuild failed, that the request was fine, and to retry later or search by keyword. Each later search tries the rebuild again and recovers once the provider does. `admin index` and `admin embeddings import` print the same failure and exit with status 1 ([#2081](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2081)). The [embeddings guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/embeddings/#common-to-all-providers) covers model changes.

### Links and indexing

- **A `]` inside a wikilink target.** "A single `]` inside a wikilink target ends the link for the scanner, but not for Obsidian, where only `]]` does" ([#1384](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1384)). `[[Sq]uare]]` now links to `Sq]uare.md`, for backlinks, broken-link reports and renames alike ([#2089](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2089)). This is the change behind the one-time index rebuild.
- **Slow indexing of long backtick or space runs.** Link extraction took time growing with the square of an unmatched run of backticks, or of spaces before a link title: "Doubling the run roughly quadruples the time" ([#1749](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1749)). Both cases now run in linear time with the same results. For a 16,000-character backtick run the PR measured 3.359 s before and 0.00256 s after ([#2084](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2084)).

### Git history

- **Commit subjects name the note.** With OKF maintenance on, a single-note write, edit or append committed as `edit: 3 files`, because the folder's `index.md` and `log.md` were counted. The owner's report in [#1675](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1675): "Every commit is as an edit for 3 files." The subject now names the note, and the `index.md` and `log.md` updates still land in the same commit ([#2091](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2091)).
- **Carriage returns in filenames.** A note whose name contains a carriage return, which can only arrive from outside the server, "comes back from `get_history` with that byte rewritten to a newline, so the path names a file that does not exist" ([#1290](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1290)). History, rename tracking, revision reads and conflict recovery now keep the name as Git wrote it ([#2092](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2092), [guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/git-integration/index.md)).

### Terminal reads in a container

Running `markdown-vault-mcp search` through `docker exec` as root on a Git checkout owned by the service user failed with "Managed mode requires SOURCE_DIR to be empty or a git repository" ([#1999](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1999)). Local `search` now skips the managed Git checks, as `read`, `list` and the other read commands already did. It never clones or syncs ([#2097](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2097)).

The same issue reported that "every successful call also writes the server's startup log lines (JSON, INFO) to stderr, interleaved with the results." Local read commands now print only warnings and errors; `-v` or `MARKDOWN_VAULT_MCP_LOG_LEVEL=DEBUG` brings the routine lines back ([#2100](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2100)). The [terminal guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/terminal/index.md) shows the updated alias.

### Type hints for library users

The package now ships a `py.typed` marker, so mypy checks code that uses `markdown_vault_mcp` against its type hints instead of skipping it with "module is installed, but missing library stubs or py.typed marker" ([#1911](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1911)). Thanks to [@tstotzer](https://github.com/tstotzer) for [#2026](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2026).

### Vault explorer

Screen readers now announce the search box in the [vault explorer](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/vault-explorer/index.md) as "Search vault" and the context heading as "Context" while the first note loads, where it used to be empty ([#1754](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1754), [#2096](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2096)).

### Contributors

Every fix in this release except the type marker came from [@salmonumbrella](https://github.com/salmonumbrella), who sent the pull requests for issues the maintainer had filed.
