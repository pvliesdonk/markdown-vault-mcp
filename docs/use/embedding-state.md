---
description: "Upgrade from 5.x, switch embedding stores, and back up or restore the index and embeddings without paying to embed unchanged notes again."
kind: how-to
---

# Upgrading, switching and restoring embeddings

The server derives its search index and its embeddings from your notes. Losing them never loses a note, but rebuilding the embeddings means asking the embedding provider again for every section of every note, which a paid provider bills. This page is for the operator moving to a new release or another store, or bringing back a backup: the steps below pay only for text that changed.

The rule behind every step below: the server keeps a stored vector for as long as the text it was made from is unchanged and the configured model is the one that made it. A restore, an import or an upgrade only finds out which vectors are still good; it embeds the rest.

## What is kept where

The files sit beside `MARKDOWN_VAULT_MCP_INDEX_PATH` (here `index.db`), or at `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` when that is set. Under Docker Compose that is `/data/state` on the `state-data` volume.

| File or schema | Written by | Losing it costs |
|---|---|---|
| `index.db` (and `index.db-wal`, `index.db-shm` while the server runs) | the search index | a rebuild of the index from the notes, with no provider calls |
| `index-db-embeddings.v2.json` and the `index-db-embeddings.v2.m<N>.npy` it names | the `numpy` store | every note embedded again |
| `index-db-embeddings.sqlite3` (and its `-wal`, `-shm` while the server runs) | the `sqlite` store, the default | every note embedded again |
| the schema named by `MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_SCHEMA` | the `postgres` store | every note embedded again |
| `index-db-embeddings.lock` | the server, or `index`, `reindex` and `embeddings import`, while one of them writes the `numpy` or `sqlite` store | nothing; safe to leave or delete while no server runs |
| `index-db-embeddings.json` and `.npy` | a 5.0 or 5.1 server | nothing once the upgrade below has run; going back to 5.1 then embeds everything again |
| `*.corrupt-<time>` | the server, when it set a damaged store aside | nothing; they are yours to inspect or delete |

## Upgrading from 5.x

A 5.0 or 5.1 server kept its vectors in `index-db-embeddings.json` and `.npy`. On the first start of the new version, the configured `numpy` or `sqlite` store is built from those files: every vector whose note text is unchanged is copied, and only the rest is embedded. With no `MARKDOWN_VAULT_MCP_EMBEDDING_STORE` set, the new version's store is `sqlite`, so a 5.x install that set none moves into `index-db-embeddings.sqlite3` this way; set `MARKDOWN_VAULT_MCP_EMBEDDING_STORE=numpy` to keep the files a numpy store writes instead. Nothing needs setting; the log says `embeddings_upgrade_started`, then `embeddings_donor_used` with how many vectors were copied (`reused`) and how many embedded. Files from another model, or the oldest layout that records no model, are refused (`embeddings_donor_refused`), and every note is embedded again.

What the upgrade costs, once:

- **Memory:** about twice the vectors while it runs, since the old files sit in memory beside the new vectors being built.
- **Disk:** the vectors exist twice until you delete the old files, each copy about `4 × chunks × dimensions` bytes. A vault of 20,000 chunks at 1,024 dimensions needs about 82 MB for each.
- **Search by meaning before the server's first start:** `markdown-vault-mcp search` run against the new version before the server has started once finds no stored vectors, because the new store does not exist yet. Start the server first; the upgrade runs in the background and searches by meaning answer once it finishes.

Going back:

- **Before the upgrade finishes**, the new store does not exist yet. Stopping, crashing or going back to 5.1 leaves the old files as 5.1 wrote them, and the next start of the new version tries again.
- **After it finishes**, the old files are still there, untouched. 5.1 reads them and embeds only the notes changed since the upgrade. This is the expected outcome, not a guarantee: keep a backup if you rely on going back.
- **Upgrading again after going back** finds the store the first upgrade built and embeds only the notes changed in between. It does not read the old files again.

Each start logs the old files as unused (`embeddings_legacy_sidecar_unused`, with their size). Delete `index-db-embeddings.json`, `index-db-embeddings.npy` and any `index-db-embeddings.m<N>.npy` once you no longer want to go back to 5.1.

## Switching stores

A switch between `numpy`, `sqlite` and `postgres` starts the new store empty, so on its own it embeds every note again. Run `markdown-vault-mcp embeddings import` with the server stopped to copy the vectors across first; it embeds only what the old store lacks and never changes the old store. [Moving vectors between stores](embeddings.md#moving-vectors-between-stores) has the options and the refusals.

## Backing up

Back up the embeddings: they cost money to rebuild. The index is optional, since a lost one rebuilds from the notes with no provider calls.

| What | With the server stopped | While the server runs |
|---|---|---|
| `index.db` | copy `index.db` alone: a server that stopped cleanly folds everything into it | `sqlite3 index.db ".backup index-copy.db"` |
| `numpy` store | copy `index-db-embeddings.v2.json` and the matrix it names | stop the server first: a save replaces the matrix |
| `sqlite` store | copy `index-db-embeddings.sqlite3`, and its `-wal` file if one is left | `sqlite3 index-db-embeddings.sqlite3 ".backup embeddings-copy.sqlite3"` |
| `postgres` store | `pg_dump -n <schema>` ([Backup and restore](postgresql.md#backup-and-restore)) | the same |

The Docker image has no `sqlite3` command; Python's own online backup does the same there:

```bash
docker compose exec markdown-vault-mcp python -c "import sqlite3; sqlite3.connect('/data/state/index-db-embeddings.sqlite3').backup(sqlite3.connect('/data/state/embeddings-copy.sqlite3'))"
```

A plain copy of a SQLite file while the server runs can miss changes that still sit in its `-wal` file; the `.backup` command does not. A server that stops cleanly folds each `-wal` file into its database, unless another process still read an older state at that moment. The log then says `fts_index_checkpoint_incomplete` for `index.db` or `sqlite_vector_store_truncate_busy` for the `sqlite` store; copy the `-wal` file with its database, or start and stop the server once more.

## Restoring

Stop the server and put the files back where they were. Before you start it, delete any `-wal` or `-shm` file beside a SQLite file you put back (`index.db` or `index-db-embeddings.sqlite3`) unless it came from the same backup: SQLite would replay a newer `-wal` file onto the older database and damage it. The backups need not be the same age as each other or as the notes:

- **Embeddings older than the notes:** only the notes changed since the backup are embedded again.
- **An index older than the embeddings, or no index at all:** the index catches up from the notes, and nothing is embedded.

To restore embeddings into another store, or from a backup kept somewhere else, leave the backup where it is and import from it:

```bash
markdown-vault-mcp embeddings import --from sqlite --from-path /backups/index-db-embeddings
```

`--from-path` is the backup's base path, without the `.sqlite3` or `.v2.json` ending. The import reads the backup and never changes it.

## Free space

Each of these needs room beyond the store's own size:

- **The 5.x upgrade:** a second copy of the vectors, until you delete the old files.
- **An import:** the new store beside the source, which the import never removes.
- **A `numpy` save:** the new matrix is written before the old one is removed.
- **A full rebuild of the `sqlite` store:** the new vectors first land in `index-db-embeddings.sqlite3-wal`, which grows to about the store's size again before the server empties it. The database itself never shrinks; freed space is reused.
- **A damaged store set aside:** the `.corrupt-<time>` copies stay until you delete them.

The `postgres` store's space is covered in [Space](postgresql.md#space).

## What embeds everything again

Each of these asks the provider for every section of every note, which a paid provider bills:

- no `MARKDOWN_VAULT_MCP_INDEX_PATH` or `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` with a `numpy` or `sqlite` store, so the vectors live in memory and are embedded again at each start;
- a change of embedding provider, model or dimensions;
- a switch of `MARKDOWN_VAULT_MCP_EMBEDDING_STORE` without `embeddings import`;
- `markdown-vault-mcp index --force`, or the `build_embeddings` tool with `force`;
- deleting the store with no backup, or recreating an empty `postgres` schema;
- a damaged store set aside when no usable 5.x files remain beside it;
- 5.x files from another model, or in the oldest layout, at upgrade.

These embed only part of the vault:

- notes changed since a restored backup was made;
- notes changed while you ran 5.1 again, when you upgrade a second time;
- every note long enough to split, after a change to `MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS`, `MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS` or `MARKDOWN_VAULT_MCP_CHUNK_OVERLAP_WORDS`;
- every note whose title changes, after a change to `MARKDOWN_VAULT_MCP_TITLE_FIELD`, since a section is embedded with its note's title.

`markdown-vault-mcp reindex --force` rebuilds the index only: the embeddings are kept.

## Log lines to look for

| Event | What it says |
|---|---|
| `embeddings_upgrade_started donor=… store=…` | The 5.x files are being copied into the configured store. |
| `embeddings_donor_used source=… reused=… embedded=… refused_rows=… published=…` | A build or import finished. The fields count the vectors copied, the chunks embedded and the stored rows skipped as unreadable; `published` says whether the result replaced the store. |
| `embeddings_donor_refused source=… reason=…` | The files or store offered cannot be reused: `identity_unknown` (no model recorded), `identity_differs` (another model), or a store that is empty, damaged, out of reach or unsupported. |
| `embeddings_legacy_sidecar_unused path=… bytes=…` | The 5.x files are still there and no longer read. `path` is their shared base, such as `/data/state/index-db-embeddings`, and `bytes` their total size. |
| `embeddings_import_completed …` | `embeddings import` finished; `embeddings_import_refused reason=…` says why it stopped instead. |
| `vector_store_moved_aside location=… reason=… paths=…` | A damaged `numpy` or `sqlite` store was renamed with a `.corrupt-<time>` suffix and is being built again. `location` is the store's base; `reason` is `corrupt`, or `unusable` for a file found in the store's place that is not a usable store. |
| `vault_root_empty_removals_refused indexed=…` | The vault folder looked empty, so nothing was removed. |
| `fts_index_checkpoint_incomplete path=… frames=… checkpointed=…` | At shutdown another process still needed part of `index.db-wal`; copy the `-wal` file with `index.db`. |
