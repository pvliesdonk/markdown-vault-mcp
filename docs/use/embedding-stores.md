---
description: "Choose where the embeddings live (SQLite, numpy or PostgreSQL) from what each store was measured to cost in memory, disk, latency and restart time."
kind: how-to
---

# Choosing an embedding store

With an [embedding provider](embeddings.md) configured, the server keeps one vector per section of every note so it can search by meaning. `MARKDOWN_VAULT_MCP_EMBEDDING_STORE` names the store that keeps them, and `MARKDOWN_VAULT_MCP_EMBEDDING_SEARCH` how a search reads them. This page is for the operator deciding between them: which store suits a vault, and what each one was measured to cost at two vault sizes.

## Which store suits your vault

| Store | Set | Suits |
|---|---|---|
| `sqlite`, streamed | nothing: the default | most vaults. Search memory stays flat as the vault grows, and each edit writes only that note's vectors. |
| `sqlite`, resident | `MARKDOWN_VAULT_MCP_EMBEDDING_SEARCH=resident` | a vault whose server has memory to spare and wants faster searches, keeping per-note writes. |
| `numpy` | `MARKDOWN_VAULT_MCP_EMBEDDING_STORE=numpy` | a small vault that is rarely edited. It is the fastest to search, but it holds every vector in memory and rewrites all of them at each save. |
| `postgres` | `MARKDOWN_VAULT_MCP_EMBEDDING_STORE=postgres` and its settings | a team that already runs PostgreSQL with pgvector, or wants the vectors off the server's disk. [PostgreSQL embedding store](postgresql.md) covers running it. |

Streamed and resident search give the same results, apart from the order of notes with equal scores. Without `MARKDOWN_VAULT_MCP_INDEX_PATH` or `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH`, `sqlite` and `numpy` keep the vectors in memory, searched resident, and every note is embedded again at each start, which a paid provider bills each time. The Claude Desktop bundle and the Claude Code plugin set no index path yet ([#1728](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1728)).

## Where the vectors live

At `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` when set, otherwise beside `MARKDOWN_VAULT_MCP_INDEX_PATH`, as `index-db-embeddings` beside `index.db`:

- `sqlite` keeps one database, `index-db-embeddings.sqlite3`, and writes each change on its own, durable once written. It does not shrink: space freed by deleted notes or a model change is reused by later writes.
- `numpy` keeps `index-db-embeddings.v2.json` and the `index-db-embeddings.v2.m<N>.npy` matrix it names, and rewrites both at each save.
- `postgres` keeps them in the schema `MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_SCHEMA` names.

A streamed search reads the vectors from the database in batches of `MARKDOWN_VAULT_MCP_EMBEDDING_SEARCH_WORKSPACE_MB` (1 MiB by default) instead of holding them all. The workspace bounds one batch of a search, not the server's memory. `postgres` is always streamed and scores in the database; `numpy` is always resident. The `embeddings_status` tool reports the mode the store runs in, its workspace, the size of its files (`stored_bytes`) and the bytes of vectors it holds in memory (`resident_vector_bytes`). That last figure leaves out the server's other memory.

## What each store costs

The figures below come from running the whole server on one host at two vault sizes, twice each ([#1899](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1899)). Where the two runs differed, both are given as a range. The vectors had 1,024 dimensions; memory is in decimal MB.

| | `sqlite`, streamed | `numpy` | `postgres` |
|---|---|---|---|
| **3,252 notes, 22,764 sections** | | | |
| Memory searching adds | 20 MB | 165 to 205 MB | 15 MB, plus the database |
| Server memory after searching | 102 MB | 247 MB | 111 MB |
| Semantic search, 95 in 100 within | 140 to 159 ms | 39 to 45 ms | 118 ms |
| Similar notes, 95 in 100 within | 122 to 126 ms | 53 to 82 ms | 105 to 112 ms |
| Written per one-note edit | 0.27 MB | 106 MB | 0.14 MB of WAL |
| Disk | 110 MB | 106 MB | 144 MB in the schema |
| Restart until search works | 2.3 s | 1.8 to 1.9 s | 10 s |
| **32,520 notes, 227,640 sections** | | | |
| Memory searching adds | 25 MB | about 2,100 MB | 20 MB, plus the database |
| Server memory after searching | 106 MB | 1,330 MB | 117 MB |
| Semantic search, 95 in 100 within | 1.00 to 1.04 s | 81 to 107 ms | 0.51 to 0.53 s |
| Similar notes, 95 in 100 within | 1.06 s | 0.31 to 0.33 s | 0.49 s |
| Written per one-note edit | 0.27 MB | 1,058 MB | 0.13 MB of WAL |
| Disk | 1,102 MB | 1,058 MB | 1,433 MB in the schema |
| Restart until search works | 13.6 s | 10.7 to 11.0 s | 81 to 89 s |

How to read it:

- **Memory.** A streamed store's search memory barely moves with the vault; `numpy` adds about twice its vectors' size, which grows with sections × dimensions. The PostgreSQL service itself used 531 to 580 MB at rest on top of the server.
- **Speed.** Resident search stays fast at both sizes. A streamed search reads every vector, so its time grows with the vault: about 0.15 s at the smaller size, about a second at the larger. `postgres` streams too, but scores in the database.
- **Edits.** `sqlite` writes the edited note's pages and the keyword index; `numpy` rewrites the whole store on every save.
- **Restarts.** No store embeds an unchanged note again after a restart or a crash. `postgres` takes four to eight times as long as a local store to come back; [#1937](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1937) looks into why.
- **The first build.** Building the index and every vector from nothing peaked at 360 to 470 MB at the smaller size and 2.4 to 3.6 GB at the larger, on every store, which is more than any store needs once it serves ([#1938](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1938)). Size the server's memory for the first build.

`sqlite`, resident was not measured. It holds the vectors in memory as `numpy` does and writes each edit as streamed `sqlite` does, so its memory and search time are probably close to `numpy`'s, with `sqlite`'s writes.

## What these figures do not cover

The figures are a measured envelope, not a guarantee:

- one host (an 8-core Intel desktop with local ext4 disk), two runs; a few percent between runs is within what another run could move;
- a stand-in embedding provider that makes no network calls, so build times leave out the real provider, which dominates a real build;
- PostgreSQL in a container on the same host; a database across a network adds a round trip to every search and every restart check;
- a uniform synthetic vault (six sections a note); real vaults vary note size and folder shape;
- not measured: several clients at once, a vault larger than 227,640 sections, other vector widths and other filesystems.

## Changing store later

A switch starts the new store empty, so on its own it embeds every note again. Run `markdown-vault-mcp embeddings import` first to copy the vectors across. [Upgrading, switching and restoring embeddings](embedding-state.md) covers the switch, the upgrade from 5.x, backups and recovery; [Index storage and SQLite](storage.md) covers what the `sqlite` store needs from its disk.
