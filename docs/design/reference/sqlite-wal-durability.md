---
type: Reference
title: SQLite WAL durability, checkpoints, the WAL-reset bug and backup
description: What a WAL commit promises under each synchronous level, how checkpoints and the -wal/-shm files behave, how BEGIN IMMEDIATE and Python's explicit transactions work, which usage the WAL-reset bug threatens, and how to back up a live database.
subject_version: "SQLite documentation, sqlite.org (unversioned pages; current as read 2026-10-15); Python 3 sqlite3 docs; observed on SQLite 3.50.4 (the project venv's stdlib sqlite3) and 3.53.4"
valid_for: "SQLite 3.x from 3.22.0, as shipped in Python's sqlite3 stdlib module (Python ≥ 3.11)"
generated:
  by: process:researching-references
  at: 2026-10-15T17:15:41+02:00
stale_after: 2027-04-15T17:15:41+02:00
verified:
  - by: process:researching-references
    at: 2026-10-15T17:15:41+02:00
status: stable
sources:
  - id: wal-docs
    title: Write-Ahead Logging
    resource: https://sqlite.org/wal.html
    accessed: 2026-10-15
  - id: pragma-docs
    title: Pragma statements supported by SQLite
    resource: https://sqlite.org/pragma.html
    accessed: 2026-10-15
  - id: transaction-docs
    title: Transaction
    resource: https://sqlite.org/lang_transaction.html
    accessed: 2026-10-15
  - id: backup-docs
    title: SQLite Backup API
    resource: https://sqlite.org/backup.html
    accessed: 2026-10-15
  - id: vacuum-docs
    title: VACUUM
    resource: https://sqlite.org/lang_vacuum.html
    accessed: 2026-10-15
  - id: python-sqlite3
    title: sqlite3 — DB-API 2.0 interface for SQLite databases
    resource: https://docs.python.org/3/library/sqlite3.html
    accessed: 2026-10-15
---

# SQLite WAL durability, checkpoints, the WAL-reset bug and backup

This page records the SQLite behaviour that the SQLite vector store (#1497)
relies on:
- what a commit in WAL mode promises under `synchronous=FULL` and `NORMAL`;
- what checkpoints do, and when the `-wal` and `-shm` files go;
- how `BEGIN IMMEDIATE` and Python's explicit transaction control behave;
- the conditions of the WAL-reset bug, and whether this project's usage
  meets them;
- how to back up a live WAL database, and why the file doesn't shrink.

Read-only (`mode=ro`) opens are covered by
[`sqlite-read-only-connections.md`](sqlite-read-only-connections.md); this
page adds only what a read-only connection does to checkpoints.

## Scope

- **Covers:**
  - durability under `synchronous`;
  - `wal_checkpoint` modes and `wal_autocheckpoint`;
  - the lifetime of the `-wal` and `-shm` files;
  - `BEGIN IMMEDIATE` and `SQLITE_BUSY`;
  - Python's `isolation_level=None`;
  - the WAL-reset bug;
  - the online backup API and `VACUUM INTO`;
  - the freelist.
- **Does not cover:**
  - `immutable=1`;
  - checkpoint tuning beyond the defaults;
  - `mmap`;
  - shared-cache locking;
  - multi-writer workloads, which this project does not support on one
    file.
- **Depended on by:**
  - `src/markdown_vault_mcp/stores/sqlite_vectors.py`;
  - `src/markdown_vault_mcp/_fts_connection.py` (`synchronous` and
    `autocommit` on `SqliteConnectionRegistry`);
  - `docs/design/design.md` § the storage contract (the SQLite backend
    paragraph);
  - `docs/use/embeddings.md` (backup and size).

## Claims

### Durability of a commit

- "Transactions are durable across application crashes regardless of the
  synchronous setting or journal mode." So a killed process never loses a
  committed transaction under either `FULL` or `NORMAL`. [source: pragma-docs]
- In WAL mode, `FULL` is fully ACID: "FULL is atomic, consistent, isolated,
  and durable (ACID) in WAL mode". The cost is that "an additional sync
  operation of the WAL file happens after each transaction commit".
  [source: pragma-docs]
- `NORMAL` in WAL mode keeps consistency but not durability. "WAL mode is
  always consistent with synchronous=NORMAL, but WAL mode does lose
  durability." A transaction committed under `NORMAL` "might roll back
  following a power loss or system crash". [source: pragma-docs]
- Writers sync the WAL at every commit under `FULL` and omit that sync under
  `NORMAL`. With `NORMAL`, the checkpoint is the only operation that issues a
  sync. (§2.3) [source: wal-docs]
- `journal_mode=WAL` is persistent: it "stays in effect across multiple
  database connections and after closing and reopening the database". The
  `synchronous` level, by contrast, is set per connection; the registry
  sets it on every connection it opens. [source: pragma-docs]

### Checkpoints

- **Automatic checkpoints.** By default SQLite checkpoints automatically
  "whenever a COMMIT occurs that causes the WAL file to be 1000 pages or
  more in size". The checkpoint "will be run automatically by the same
  thread that does the COMMIT". (§2.1, §2.3, §3.1) [source: wal-docs]
- `wal_autocheckpoint` defaults to 1000 pages, and zero or a negative value
  turns it off. "All automatic checkpoints are PASSIVE." [source: pragma-docs]
- **Modes.**
  - `PASSIVE` checkpoints "as many frames as possible without waiting for
    any database readers or writers to finish".
  - `FULL` and `RESTART` block concurrent writers, but readers proceed.
  - `TRUNCATE` works like `RESTART`, and also truncates the WAL to zero
    bytes on success.

  [source: pragma-docs]
- **Checkpoint starvation.** "If another connection has a read transaction
  open, then the checkpoint cannot reset the WAL file". A long-running reader
  can therefore stop the WAL from shrinking. (§6) [source: wal-docs]
- **A read-only connection cannot checkpoint.**
  - `PRAGMA wal_checkpoint(PASSIVE)` on a `mode=ro` connection fails with
    `sqlite3.OperationalError: disk I/O error`, and the WAL keeps its size.
  - When such a connection is the last to close, it leaves the `-wal` and
    `-shm` files in place. The next writable connection to open and close
    folds and removes them.

  [observed: a 50-row WAL database with autocheckpoint off, a writer and a
  `mode=ro` reader, under `uv run python -I`, on SQLite 3.50.4; the same
  result on 3.53.4]
- **The `-wal` and `-shm` files on close.** "Usually, the WAL file is
  deleted automatically when the last connection to the database closes".
  That connection "does one last checkpoint and then deletes the WAL and its
  associated shared-memory file". (§4, §6) [source: wal-docs]

  [observed: same probe] Two things qualify that:
  - closing a writer while a reader is still open removes neither file;
  - the reader closing last removes nothing either.

### Write transactions

- `BEGIN IMMEDIATE` "causes the database connection to start a new write
  immediately, without waiting for a write statement". It "might fail with
  SQLITE_BUSY if another write transaction is already active on another
  database connection". [source: transaction-docs]
- `EXCLUSIVE` and `IMMEDIATE` are the same in WAL mode. SQLite allows "only
  one simultaneous write transaction". [source: transaction-docs]
- `busy_timeout` sets the busy handler that retries a `SQLITE_BUSY`, and
  each connection has one. [source: pragma-docs] The transaction page does
  not say how it interacts with `BEGIN IMMEDIATE`. [unverified] That
  `BEGIN IMMEDIATE` waits out `busy_timeout` before failing; a two-process
  probe holding the write lock would settle it.
- **Errors inside a transaction.** `SQLITE_FULL`, `SQLITE_IOERR`,
  `SQLITE_INTERRUPT` and `SQLITE_NOMEM` may undo only the failing statement
  and leave the transaction open, or may roll back the whole transaction.
  [source: transaction-docs] So a writer that sees an error rolls back
  explicitly, rather than assuming either outcome.

### Python's transaction control

- `isolation_level=None` means "transactions are never implicitly opened".
  It applies under `autocommit=LEGACY_TRANSACTION_CONTROL`, which is still
  the default. The application then issues `BEGIN`, `COMMIT` and `ROLLBACK`
  itself. [source: python-sqlite3]
- The `autocommit` attribute was added in Python 3.12 and is the
  recommended control. This project's floor is 3.11, so it uses
  `isolation_level`. [source: python-sqlite3]
- Under legacy control, `executescript()` first issues an implicit `COMMIT`
  if a transaction is pending. A script therefore cannot run inside a
  transaction the application opened. [source: python-sqlite3]
- `sqlite3.Error.sqlite_errorname` gives the symbolic error (for example
  `SQLITE_NOTADB`). It was added in Python 3.11. [source: python-sqlite3]

### The WAL-reset bug

- The bug "only affects databases in WAL mode when there are two or more
  database connections open on the same file". Those connections must
  "attempt to write or checkpoint at the same instant". The connections may
  be in separate threads or in separate processes. (§11) [source: wal-docs]
- Its consequence is corruption: a later checkpoint skips part of a write
  transaction, so those changes never reach the database file. The SQLite
  developers rate the probability as very low, and could not reproduce it
  without a test hook. (§11, §11.2) [source: wal-docs]
- It is "likely present in all version of SQLite from 3.7.0 (2010-07-21)
  through 3.51.2 (2026-01-09)" and fixed in 3.51.3 (2026-03-13) and later.
  Backports exist as 3.44.6 and 3.50.7. (§11) [source: wal-docs]
- The page lists no workaround other than upgrading. It does not say that
  serialising writes and checkpoints avoids the bug. (§11) [source: wal-docs]
  That avoidance follows from the stated condition (two connections acting
  "at the same instant"), not from a separate statement.
- The project venv's stdlib `sqlite3` is SQLite 3.50.4, which falls in the
  affected range. [observed: `uv run python -c "import sqlite3;
  print(sqlite3.sqlite_version)"`]

### Backup and file size

- The online backup API copies one database into another, incrementally if
  asked. The documented weaknesses of copying the file under a lock are:
  - writers wait while it runs;
  - a power or OS failure during the copy can corrupt it;
  - it cannot copy an in-memory database.

  [source: backup-docs]
- In WAL mode, committed transactions may still sit in the `-wal` file. A
  copy of the main file alone therefore misses them until a checkpoint has
  folded them in. [unverified] The backup and WAL pages imply this but do
  not state it; #1502's probe observed it ("copying only the main file of a
  live WAL database lost committed rows").
- `VACUUM INTO` writes "a vacuumed copy of a live SQLite database into a
  separate file", as a consistent snapshot. It is "an alternative to the
  backup API". (§1.1) [source: backup-docs] [source: vacuum-docs]
- Deleted content leaves free pages that later writes reuse. The file does
  not shrink until `VACUUM` rebuilds it, which can need up to twice the
  file's size in free space. [source: vacuum-docs]

## Verdict for markdown-vault-mcp

- **WAL-reset bug affects this usage: no.**
  - The SQLite vector store has one writing process per file.
  - Inside that process, every write transaction, `checkpoint()` and
    `close()` (including the automatic checkpoint a commit runs, and the
    final checkpoint of the last connection's close) happens under the
    store's single lock. So no two connections write or checkpoint at the
    same instant.
  - Another process opens the file `mode=ro`, and such a connection cannot
    checkpoint [observed above].
  - **The residual case** is two processes both opening the same file as
    owner. That configuration is unsupported: the numpy backend already
    forbids it, because two writers would pick the same matrix name. On
    affected SQLite it would also expose this bug.
- **Affected:** SQLite 3.7.0 through 3.51.2, except the backport releases.
- **Fixed:** 3.51.3, 3.50.7, 3.44.6.
- **Python's bundled SQLite here:** 3.50.4 (affected; immaterial under the
  usage above).

## Where this project departs from the subject

The vector store commits each write with `synchronous=FULL`, where the
connection registry's default, and the keyword index's setting, is
`NORMAL`. That is the owner's decision (#1497): a published embedding must
survive power loss, so recovery never costs a paid provider call. The
keyword index stays on `NORMAL`.

## Not covered

- **How `busy_timeout` applies to `BEGIN IMMEDIATE`.** Only one process
  writes, so this doesn't arise in supported use. A two-process probe would
  settle it.
- **The I/O cost of `synchronous=FULL` on network or container storage.**
  #1506 owns measuring the operating envelope.
