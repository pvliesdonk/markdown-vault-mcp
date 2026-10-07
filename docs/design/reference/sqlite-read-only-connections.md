---
type: Reference
title: SQLite read-only connections, WAL and FTS5's persisted rank
description: How a mode=ro connection reads a WAL database, what it refuses, and why FTS5's rank weights are shared by every connection to the file.
subject_version: "SQLite documentation, sqlite.org (unversioned pages; current as read 2026-10-07); observed on SQLite 3.50.4 (Python stdlib sqlite3)"
valid_for: "SQLite 3.x from 3.22.0, as shipped in Python's sqlite3 stdlib module"
generated:
  by: process:researching-references
  at: 2026-10-07T14:18:00+02:00
stale_after: 2027-04-07T14:18:00+02:00
verified:
  - by: process:researching-references
    at: 2026-10-07T14:18:00+02:00
status: stable
sources:
  - id: wal-docs
    title: Write-Ahead Logging
    resource: https://sqlite.org/wal.html
    accessed: 2026-10-07
  - id: uri-docs
    title: Uniform Resource Identifiers
    resource: https://sqlite.org/uri.html
    accessed: 2026-10-07
  - id: fts5-docs
    title: SQLite FTS5 Extension
    resource: https://sqlite.org/fts5.html
    accessed: 2026-10-07
---

# SQLite read-only connections, WAL and FTS5's persisted rank

## Scope

- Covers: opening a WAL-mode database file read-only through a `mode=ro`
  URI, what such a connection refuses, how a file path becomes a URI, and
  where FTS5 keeps its `rank` option, as far as one process reading an index
  another process owns depends on them.
- Does not cover: `immutable=1`, locking and busy handling between writers,
  checkpointing, or FTS5 ranking functions beyond the persisted `rank`
  option. [`sqlite-fts5.md`](sqlite-fts5.md) covers FTS5 delete cost.
- Depended on by: `src/markdown_vault_mcp/_fts_connection.py`
  (`resolve_connect_uri`), `src/markdown_vault_mcp/fts_index.py`
  (`FTSIndex(read_only=True)`, `_persist_rank_config`), `src/markdown_vault_mcp/cli.py`
  (`search`), and `docs/design/design.md` (the CLI paragraph of the index
  lifecycle, and "Weighted BM25").

## Claims

### Read-only opens

- The `mode` URI parameter "determines if the new database is opened
  read-only, read-write, read-write and created if it does not exist, or
  that the database is a pure in-memory database", for `mode=ro`, `rw`,
  `rwc` and `memory` respectively (§3.3 Recognized Query Parameters).
  [source: uri-docs]
- A `mode=ro` connection refuses writes: an `INSERT` of FTS5's `'rank'`
  command fails with `sqlite3.OperationalError: attempt to write a readonly
  database`, with or without another connection holding the database open.
  [observed: a two-row FTS5 table in a WAL database under the scratchpad,
  Python 3.14, SQLite 3.50.4] [pins: tests/test_fts_index.py::TestReadOnly::test_reads_with_the_stored_weights_and_refuses_writes]
- A `mode=ro` open of a file that does not exist fails rather than creating
  it, as the `rwc` wording above implies: `FTSIndex(read_only=True)` on a
  missing path raises `sqlite3.OperationalError` and leaves no file.
  [observed: same environment] [pins: tests/test_fts_index.py::TestReadOnly::test_missing_file_is_not_created]
- Since 3.22.0, "a WAL-mode database on read-only media, or a WAL-mode
  database that lacks write permission, can still be read" when one of
  three conditions holds: "The `-shm` and `-wal` files already exist and are
  readable", "There is write permission on the directory containing the
  database so that the `-shm` and `-wal` files can be created", or the
  connection uses the `immutable` parameter (§5 Read-Only Databases).
  Before 3.22.0, write access was required to read a WAL database.
  [source: wal-docs]
- So a `mode=ro` connection reads a WAL index after its last writer closed
  and removed the `-wal` and `-shm` files, when the directory is writable.
  When the directory is not writable and the files are absent, the open
  fails, and even a read raises `attempt to write a readonly database`.
  [observed: same environment, with the directory set to mode 0500]

### Turning a path into a URI

- To convert a filename into a URI: convert "`?`" into "`%3f`" and "`#`"
  into "`%23`", collapse runs of "`/`", and prepend "`file:`"; on Windows
  also turn "`\`" into "`/`" and prefix a drive letter with "`/`" (§3.1 The
  URI Path). [source: uri-docs]
- "Zero or more escape sequences of the form `%HH` ... can occur in the
  path, query string, or fragment" (§3 URI Format), so the percent-encoding
  of `pathlib.Path.as_uri()`, which also encodes spaces, names the same
  file. [source: uri-docs] [pins: tests/test_fts_index.py::TestReadOnly::test_reads_with_the_stored_weights_and_refuses_writes]

### FTS5's persisted rank

- `INSERT INTO ft(ft, rank) VALUES('rank', 'bm25(10.0, 5.0)')` "is used to
  set the persistent "rank" option", which changes "the default auxiliary
  function mapping for the rank column" (§6.11 The 'rank' Configuration
  Option). [source: fts5-docs]
- Persistent options live in the `%_config` shadow table, `CREATE TABLE
  %_config(k PRIMARY KEY, v) WITHOUT ROWID`, which "stores the values of any
  persistent configuration options" (§9.5). The rank is therefore a property
  of the database file, shared by every connection to it, not of the
  connection that set it. [source: fts5-docs]
- The structure record at `%_data` id 10 "begins with a single 32-bit
  unsigned value - the cookie value. This value is incremented each time the
  structure is modified" (§9.2.2 Structure Record Format). [source: fts5-docs]
- Writing the `'rank'` option bumps that cookie even when the value is
  unchanged: re-writing `bm25()` over `bm25()` changed the record from
  `X'00000001…'` to `X'00000002…'`, and another open connection saw
  `PRAGMA data_version` change. [observed: `markdown-vault-mcp search`
  against an index built by `index`, before #1758, comparing `iterdump()`
  output] [pins: tests/test_cli.py::test_search_leaves_an_on_disk_index_unchanged]
- A connection that is already open ranks with a `rank` value another
  connection commits, from its next query on: ordering flipped from
  `alpha, zzz` to `zzz, alpha` after a second connection set
  `bm25(1, 10)` over `bm25(10, 1)`. The FTS5 page does not say when a
  connection re-reads `%_config`. [observed: same environment as above]
- A query can pick a ranking function for itself with
  `rank MATCH 'bm25(10.0, 5.0)'` instead of the persisted default (§5.2 Sorting by Auxiliary Function Results).
  [source: fts5-docs]

## Where this project departs from the subject

- The project persists the rank (`FTS_WEIGHTS`) rather than passing it per
  query, so a read-only reader ranks with the owner's weights, not its own
  (`docs/design/design.md`, "Weighted BM25").

## Not covered

- How long a `mode=ro` reader waits, or whether it fails, while a writer
  holds a long transaction or checkpoints; not probed.
- Windows paths through `Path.as_uri()`; not probed.
