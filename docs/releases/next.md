# Next release

<!-- notes-range-end: 34837f61ef095c375684e0c500bf7c0fd2ea829c -->

<!-- RELEASE-SUMMARY NEXT START -->
This release changes where and how a vault's embeddings are kept, so search
by meaning stays usable as a vault grows and changes. Embeddings now live in
a SQLite database beside the index by default, written one change at a time
and searched without holding every vector in memory; the numpy files remain
an option, and a PostgreSQL schema with pgvector is a new one. Upgrading from
5.x copies your existing vectors across without calling the embedding
provider, and `markdown-vault-mcp embeddings import` moves them between
stores later. An edit now embeds only the sections whose text changed, a
damaged store is moved aside instead of deleted, and an unmounted vault
folder no longer reads as a vault whose notes were all deleted. Library
callers and anyone who runs `index` or `reindex` beside a running server
should read [Upgrading](#upgrading) first.
<!-- RELEASE-SUMMARY NEXT END -->

## Upgrading

1. **If you keep embeddings on disk (`MARKDOWN_VAULT_MCP_INDEX_PATH` or
   `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` set, as the shipped `compose.yml`
   does):** nothing is required. The first start copies every vector whose
   note text is unchanged from the 5.x `index-db-embeddings.json` and `.npy`
   files into the new default store, `index-db-embeddings.sqlite3`, with no
   provider calls, and embeds only the rest. While it runs it needs about
   twice the memory the vectors take, and until you delete the old files the
   vectors are on disk twice: about 82 MB for each copy at 20,000 chunks of
   1,024 dimensions. The log shows `embeddings_upgrade_started`, then
   `embeddings_donor_used`. The old files are never written, so you can roll
   back to 5.1 until you delete them; each start logs
   `embeddings_legacy_sidecar_unused` while they are there
   ([#1505](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1505),
   [#1940](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1940),
   [Upgrading from 5.x](../use/embedding-state.md#upgrading-from-5x)). To
   keep the previous behaviour, all vectors in memory and every save
   rewriting the files, set `MARKDOWN_VAULT_MCP_EMBEDDING_STORE=numpy`. A
   vault whose vectors live only in memory (no index path, as in the Claude
   Desktop bundle and the Claude Code plugin) is embedded at each start, as
   before.
2. **If a scheduled job runs `markdown-vault-mcp index` or `reindex` while a
   server serves the same index:** it now exits 1 and writes nothing, where
   it used to write as a second writer. Stop the server first, or let the
   server's own `reindex` tool do the work
   ([#1890](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1890),
   [#1924](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1924)). A
   second server started on the same index waits with the vault unavailable
   instead of writing beside the first.
3. **If you use the Python library:** `Vault.close()` now closes only what
   the vault opened. A `git_strategy` or `on_write` you passed in is yours
   to close; closing your `git_strategy` is what flushes its last push
   ([#1769](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1769)).
   An index that cannot be opened makes `Vault(...)` raise a
   `StoreOpenError` subclass instead of a `sqlite3` error
   ([#1773](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1773)).
   The undocumented vector classes (`VectorIndex`, `interfaces.VectorStore`,
   `VectorIndexUnusableError`) are gone
   ([#1782](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1782)).
4. **If you build your own image on top of this one:** the base is now
   `debian:trixie-slim` with a uv-managed Python 3.14 in `/opt/python`,
   where it was `python:3.14-slim`. The image has no `/usr/local/bin/python`
   or `pip`, so a layer that calls either before `uv sync` breaks
   ([#1922](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1922)).
5. **If you use `trust-auth` verification with client-credentials tokens:**
   see [Service principals](#service-principals). A token its issuer marks as
   an application's can no longer verify a note as a person.

**Clients:** nothing to do beyond reconnecting. No tool, prompt or argument
was renamed or removed. `embeddings_status` and `get_index_status` report
more fields, `rename` accepts an older `if_match` in one case, and some
writes are now refused with an error where they used to fail silently.

**State:** kept. The text index is not rebuilt. The embeddings move into
`index-db-embeddings.sqlite3` unless you choose another store, and the 5.x
files stay beside it until you delete them. New files appear next to the
index: `index.db.lock`, `index-db-embeddings.lock`, and, only if a store is
ever found damaged, `<name>.corrupt-<time>` copies.

**Security posture:** tightened. Tokens issued to applications no longer
count as people for OKF provenance and `trust-auth` verification, and a
second process is refused when it tries to write an index or a built-in
vector store a server is writing. In the Docker image, SQLite 3.53.1
replaces Debian's 3.46.1, which has the WAL-reset bug; the dependency locks
pick up the advisory fixes listed under [Smaller fixes](#smaller-fixes).

## Where embeddings live

A vault's embeddings used to live in one place: a JSON file and a numpy
matrix beside the index, loaded whole into memory and rewritten whole on
every save. Epic [#1499](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1499)
set out so that "Operators can keep semantic search usable as their vault
grows and changes, with less embedding persistence I/O and a supported way
to stay within the host's memory budget." Three stores now exist, chosen
with `MARKDOWN_VAULT_MCP_EMBEDDING_STORE`:

- `sqlite`, the default: one database beside the index,
  `index-db-embeddings.sqlite3`, that writes each change on its own and is
  durable once written
  ([#1497](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1497)).
- `numpy`: the files, now `index-db-embeddings.v2.json` and the matrix it
  names, rewritten at each save.
- `postgres`: a PostgreSQL schema with pgvector, described
  [below](#embeddings-in-postgresql).

`MARKDOWN_VAULT_MCP_EMBEDDING_SEARCH` picks how a search reads them
([#1377](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1377)).
`resident` holds every vector in memory and is the fastest; `streamed`
holds none and reads them from the store in batches of
`MARKDOWN_VAULT_MCP_EMBEDDING_SEARCH_WORKSPACE_MB` (1 MiB by default) for
each search. Unset, each store uses its own default: streamed for `sqlite`
and `postgres`, resident for `numpy`.

The choice is a trade between memory and search time, and it was measured
rather than guessed
([#1506](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1506),
[#1899](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1899)). At
32,520 notes (227,640 sections of 1,024 dimensions) on one host, a streamed
`sqlite` search added 25 MB of memory and answered 95 in 100 searches within
1.00 to 1.04 s; `numpy` added about 1,250 MB and answered within 81 to
107 ms. A one-note edit wrote 0.27 MB with `sqlite` and 1,058 MB with
`numpy`. [Choosing an embedding store](../use/embedding-stores.md) has the
full table, both vault sizes, what the figures leave out, and how to pick.

`embeddings_status` now says what the store costs: its `backend`,
`search_mode`, `search_workspace_bytes`, the size of its files
(`stored_bytes`) and the bytes of vectors it holds in memory
(`resident_vector_bytes`), beside its `identity` and `generation`
([#1808](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1808)). The
`numpy` store also stopped copying its matrix when it opens
([#1936](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1936)).

## Embeddings in PostgreSQL

For operators who already run a database service, the `postgres` store keeps
a vault's embeddings in a PostgreSQL schema with pgvector, so they leave the
server's disk and the database does the scoring
([#1504](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1504)).
Search is exact, and any model dimension works.

To enable it, an operator creates the extension, a role and one schema per
vault, then sets `MARKDOWN_VAULT_MCP_EMBEDDING_STORE=postgres`,
`MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_DSN` (a libpq connection string or
URI, treated as a secret and never logged or shown) and
`MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_SCHEMA`. The server creates only its
own tables in that schema. A pip or uv install needs the `postgres` extra;
the Docker image already has it. PostgreSQL 15 to 18 with pgvector 0.7.0 or
later is supported (0.7.4 or later on 17, 0.8.7 or later on 18).
[Embeddings in PostgreSQL](../use/postgresql.md) covers provisioning, TLS,
the role's privileges, PgBouncer and backups.

One server writes a schema at a time. A database that is unreachable or
refuses the connection makes semantic and hybrid search fail with a reason
while keyword search keeps working; it never reads as an empty store. At
each start the server checks the notes against the store 500 at a time
rather than one note at a time, so a restart at 227,640 sections came back
in 7.3 to 8.3 s, comparable to a local store
([#1937](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1937)).
The two passes that read every note's vectors, a re-token of many notes and
a rebuild that reuses the store's own rows, read 256 notes per query in
binary, which took the re-token's read at that size from 90.3 to 98.1 s down
to 6.4 to 6.8 s
([#1970](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1970)).

## An edit embeds only what changed

Before this release, a one-line edit re-embedded every section of the note.
[@AlexanderRyzhko](https://github.com/AlexanderRyzhko) measured what that
cost on their own vault and reported it in
[#1794](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1794): "editing
one line of a long note sends the embedding provider only the chunks whose
text changed. Actual: every chunk of the note is sent again." A note of
about 670 chunks, with a local CPU provider at 11.8 chunks a second, took
about 57 s for each one-line edit, though only 8 or 19 chunks had changed.
They then fixed it in
[#1795](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1795): only
sections whose embedding input changed go to the provider, and every other
section keeps its stored vector. On their 40-section test note, changing one
line sends 1 text instead of 40, and swapping two sections sends none.
Thank you.

Two related changes keep the provider idle when nothing it would compute has
changed. A frontmatter-only edit, such as a `modified:` stamp or a new tag,
republishes the stored vectors under the note's new version instead of
embedding again
([#1768](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1768)).
And a note's vectors are removed only when its absence is certain: an
exclusion pattern, or a file the source confirms is gone. A folder the
server cannot read keeps its vectors and logs one warning
([#1785](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1785)).

## Embeddings survive upgrades, switches and damage

Epic [#1505](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1505)
was about one risk: "Changing embedding storage risks unnecessary paid
re-embedding or loss of usable state when an upgrade, import or recovery is
interrupted."

- **Moving between stores.** `markdown-vault-mcp embeddings import --from
  numpy|sqlite|postgres` copies vectors from another store into the
  configured one, in any direction, and embeds only what the source lacks.
  `--dry-run` reports what it would copy and embed without writing or
  calling the provider. It reads the source only, and it refuses, with one
  `ERROR:` line, a source of another model, an empty source, or a store a
  running server is writing. The same command restores a backup
  ([#1870](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1870),
  [Embedding state](../use/embedding-state.md)).
- **A damaged store is moved aside, never deleted.** Its files are renamed
  to `<name>.corrupt-<time>`, the log shows `vector_store_moved_aside`, and
  the store is rebuilt, still reusing a 5.x sidecar's vectors if one is
  there. This now covers damage found after the store opened, too
  ([#1881](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1881)).
- **An unmounted vault folder removes nothing.** An empty folder used to
  look, in [#1791](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1791)'s
  words, like "an available source in which every note is gone." While the
  index holds notes, an empty vault folder now changes nothing: the log
  shows `vault_root_empty_removals_refused`, writes into it are refused, and
  `index` and `reindex` exit 1. `index --force` is the deliberate way to
  start over.
- **A note that stops parsing keeps its vectors.** A YAML typo or a missing
  required field takes the note out of search until it is fixed, and fixing
  it embeds only the sections that changed.
- **A model whose dimension changed under the same name** now rebuilds the
  store on the first search instead of failing every query
  ([#1904](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1904)).
- **A stopped server's `index.db` is complete on its own**, without its
  `-wal` file, so a plain copy of it is a valid backup.

[Embedding state](../use/embedding-state.md) covers what is kept where,
backing up each store while the server runs, restoring, and the log lines to
look for.

## Changes made while the server was stopped

A server that starts after notes changed on disk catches up by scanning the
vault. Three fixes make that catch-up trustworthy:

- `get_index_status` reports `offline_changes` as `reconciled`,
  `reconciling` or `not_reconciled`, so a status that looked healthy while
  serving stale results now says so
  ([#1542](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1542)).
  With `MARKDOWN_VAULT_MCP_BOOT_REINDEX=false`, the server's instructions
  tell the model that files changed while it was stopped stay out of the
  index until it calls `reindex`.
- A start that cannot reuse the index, because a setting it depends on
  changed or the last build never finished, rebuilds it and now drops the
  notes deleted in the meantime. Before, "search keeps returning a note that
  no longer exists"
  ([#1965](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1965)).
- A forced rebuild keeps the notes of folders it could not read, in keyword
  search and in their vectors, instead of dropping them and paying to embed
  them again
  ([#1929](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1929)).

## Service principals

OKF provenance and `trust-auth` verification treat the caller's token
subject as a person. A token issued to an application was read the same way,
so a service could stamp notes as `human:<sub>` and verify them
([#1480](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1480)). Now
a token its issuer marks as an application's (its `sub` equals its client
ID, or it carries `idtyp: app` or a client-credentials grant type) is a
service: its OKF writes stamp the tool actor, and `okf_verify` records its
review as `local` under `elicit` and refuses it under `trust-auth`
([#1972](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1972)).

Some issuers mark nothing. For a Keycloak, authentik or Zitadel service
account, list its `sub` in `MARKDOWN_VAULT_MCP_SERVICE_SUBJECTS`,
comma-separated; for Keycloak that is the service-account user's ID, not its
name ([#1973](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1973),
[OIDC providers](../deploy/oidc-providers.md)).

## Smaller fixes

- **No zombie processes in the Docker image.**
  [@joepadmiraal](https://github.com/joepadmiraal) found that with git sync
  on, each pull left a zombie `git` process: "After 14 days of uptime: There
  were 1,912 zombie (`Zs`) `git` processes", until the container could no
  longer fork
  ([#1959](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1959)).
  The image now runs tini as PID 1, which reaps them
  ([#1964](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1964)), and
  their [#1961](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1961)
  documents the fix for images up to 5.1.0: add `init: true` to the compose
  service, or `--init` to `docker run`
  ([Docker](../deploy/docker.md)). Thank you for the report and the research
  behind it.
- **`rename` survives the server's own link rewrites.** Renaming a note
  updates the links in other notes, which changed each one's etag and made a
  following conditional `rename` of one of them fail. It now accepts the
  older `if_match` when the only changes since are those rewrites, and says
  how many in `link_rewrites_since_etag`
  ([#1492](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1492)).
- **Writes into a git submodule are refused.** They used to land on disk
  uncommitted and unpushed while the tool reported success
  ([#1564](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1564),
  [Git integration](../use/git-integration.md)).
- **`get_history` under git 2.56** stops at a renamed note's birth again
  instead of listing the commits of the note that held its earlier name
  ([#1779](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1779)).
- **An index path in a folder that does not exist yet** works on every
  platform: the server creates the folder
  ([#1730](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1730),
  [#1956](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1956)).
  [@tstotzer](https://github.com/tstotzer) sent the first fix for this in
  [#1948](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1948); it
  overlapped with #1956, which landed first, but it covered the same cases.
  Thank you.
- **`index` and `reindex` on a vault without embeddings** no longer log an
  ERROR for the embedding step they skip
  ([#1733](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1733)).
- **The image's SQLite** is now 3.53.1, from the uv-managed Python, instead
  of Debian's 3.46.1, which has the WAL-reset corruption bug
  ([#1922](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1922)).
- **Dependency locks** pick up the fixed releases of pillow (13.0.0, eight
  advisories, [#1824](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1824)),
  opentelemetry-api
  ([#1859](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1859)) and
  filelock
  ([#1872](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1872)).
