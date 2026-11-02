# Next release

<!-- notes-range-end: 550922f6be474bff6520bf990a5edbd6ed16e793 -->

<!-- RELEASE-SUMMARY NEXT START -->
This release brings the vault to the terminal. The `markdown-vault-mcp`
command can now search, read and explore a vault that a server publishes over
MCP, with a bearer token and no copy of the vault or its index on the machine
you type on; the same commands still work against a local vault. Clients and
scripts also get exact answers ranked search could not give, from the number
of notes that match to the path a mistyped filename meant. The commands that
write the index move under `admin`, and their old names keep working. A build
that fails to index a note now retries it on the next run. Nothing needs to
change on upgrade; see [Upgrading](#upgrading).
<!-- RELEASE-SUMMARY NEXT END -->

## Upgrading

No step is required. The new commands, tools and settings are additions.

- **If a script, cron job or unit runs `markdown-vault-mcp index`, `reindex` or
  `embeddings import`:** it keeps working, and prints a note on stderr naming the
  new command. Switch it to `admin index`, `admin reindex` or
  `admin embeddings import` when convenient
  ([#2005](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2005)).

**Clients:** nothing beyond reconnecting. Clients discover four new read-only
tools: `query_documents`, `count_documents`, `aggregate_documents` and
`search_paths`.

**State:** kept. The index and the embeddings are used as they are; nothing is
rebuilt.

**Security posture:** none on the server. The four new tools are read-only and
answer from the same index the existing reader tools read. The new terminal
client sends its bearer token only to the endpoint you select, over HTTP(S),
with certificate verification on
([CLI reference](../reference/cli.md#search-a-hosted-vault)).

## Query a vault from the terminal

Until now the `search` command built a local vault before every query. As
[#1831](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1831) put it:
"A person with access to an already hosted vault cannot search it from this
CLI without also making its data and local configuration available on the
client." The command now reaches a hosted vault through the MCP endpoint it
already serves. The idea, the issues and all nine pull requests of
[epic #1829](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1829) come
from [@salmonumbrella](https://github.com/salmonumbrella), who proposed "a
terminal workflow for a hosted Markdown vault" that never copies the vault or
its index to the client.

Install the command without extras on the machine you type on, then point a
profile at the server:

```bash
uv tool install markdown-vault-mcp
```

```toml
# ~/.config/markdown-vault-mcp/client.toml
[remote]
url = "https://vault.example/mcp"
token_env = "VAULT_ACCESS_TOKEN"
```

`url` is the server's complete MCP endpoint, and `token_env` names the
environment variable that holds the bearer token, so the profile never stores
it. `--server` or `MARKDOWN_VAULT_MCP_SERVER_URL` selects an endpoint without a
profile, `MARKDOWN_VAULT_MCP_CLIENT_TOKEN` supplies a token directly, and
`--local` reads the local vault instead. A remote read never falls back to the
local vault. The command doesn't sign in interactively; an OIDC access token
you already hold works as the bearer token.

The read commands, all of which accept the connection options above:

- `search`, now with `--mode auto`, repeated `--filter KEY=VALUE`,
  `--chunks-per-file` and `--snippet-words`
  ([#1837](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1837),
  [#1844](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1844));
- `read`, `list`, `folders`, `tags`, `stats`, `recent`, `toc`, `similar` and
  `context` ([#1845](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1845));
- `links to`, `links from`, `links broken`, `links orphans`, `links hubs` and
  `links path` ([#1847](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1847));
- `query metadata`, `query body`, `query note`, `query count` and
  `query aggregate` ([#1851](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1851));
- `query path`, with `--fuzzy` for a one-letter typo
  ([#1849](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1849)).

Output is plain text, `--json` for scripts, or `--agent` for one escaped
record per line. On the Docker host you can also run the same commands inside
the server's container, as the `appuser` user, against the server's own index
with no address or token. Running them as root fails on a vault that is a git
checkout ([#1999](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1999)).

[Query a vault from the terminal](../use/terminal.md) walks through both
routes, and the [command reference](../reference/cli.md) lists every option.

## Exact counts, groups and path discovery

Search ranks a bounded set of hits, so its length says nothing about how many
notes match. [#1842](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1842)
describes the gap: "MCP clients receive bounded ranked search results, so they
cannot use a result count as the total number of matching notes or count the
full population by folder, tag or month." Four read-only tools answer those
questions
([#1839](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1839),
[#1846](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1846),
[#1848](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1848)):

- `query_documents` pages through every matching note with its exact total
  (20 notes per page by default, up to 1,000);
- `count_documents` counts every matching note without reading content;
- `aggregate_documents` groups the whole population by folder, tag or UTC
  month;
- `search_paths` finds an indexed path from literal text, and with `fuzzy`
  also one a letter off: "A remembered filename with one wrong letter is hard
  to recover through content search"
  ([#1848](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1848)).

The first three share their predicates: a keyword query scoped to metadata,
body or both, a folder, a path or glob, modification bounds, and `filters`.
`filters` works on the fields listed in `indexed_frontmatter_fields`, which
`stats` reports; computed OKF fields stay with `search`. The
[reader tools reference](../reference/tools/reader.md#query_documents) has
every parameter and default.

Library callers get the same through `vault.reader`: `query_documents`,
`count_documents_matching`, `aggregate_documents` and `search_paths`, with the
request types in `markdown_vault_mcp.query_types`. A custom keyword index
written against an earlier release keeps working. Exact queries are an
optional capability, `RunsExactQueries`, rather than new required methods on
`KeywordIndex`, so an index without it answers search as before. The exact
queries then raise `ExactQueriesNotSupportedError`, which points the caller at
`search` ([per the maintainer](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1846#issuecomment-6573006023);
[storage reference](../reference/api/storage.md#exact-keyword-queries)).

## A note that fails to index is retried

When a build failed to index a note, it logged `build_index_index_failed` and
still recorded the note's new content hash. The next build then treated the
note as unchanged and never retried it until the file changed again. A normal
build kept serving the note's previous content, and a forced build left it out
of search ([#1982](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1982)).
A failed note is now recorded so that the next build indexes it again
([#1991](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1991), by
[@tstotzer](https://github.com/tstotzer)).

## Commands that write move under `admin`

`index`, `reindex` and `embeddings import` change the shared index or the
stored vectors, and they now sit in one group: `admin index`, `admin reindex`
and `admin embeddings import`. Every other command except `serve` only reads,
so the help screen shows what can change a vault. The old names still run the
same commands and name the new form on stderr
([#2005](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2005),
[#2006](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2006),
[#2007](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2007)).

The [command reference](../reference/cli.md#markdown-vault-mcp-admin-embeddings-import)
now documents `admin embeddings import` with every option. Before, it listed
the `embeddings` group's description only. The fix comes from the project
template's reference generator, which now walks a command group's
sub-commands
([fastmcp-server-template#823](https://github.com/pvliesdonk/fastmcp-server-template/issues/823),
[fastmcp-server-template#850](https://github.com/pvliesdonk/fastmcp-server-template/pull/850),
[#1998](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1998)).
