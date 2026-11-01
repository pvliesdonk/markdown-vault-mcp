---
type: Reference
title: PostgreSQL transactions, locks, durability and COPY behind the vector store
description: What the PostgreSQL vector store's writes and writer lease rely on in PostgreSQL 17 and psycopg 3 — snapshots under REPEATABLE READ, DELETE against TRUNCATE and DROP, row and advisory locks, lock and statement timeouts, synchronous_commit and fsync, and binary COPY.
subject_version: "PostgreSQL 17 documentation (isolation, explicit locking, SET, WAL settings, administration functions); psycopg 3 docs (current as read 2026-10-18); observed on PostgreSQL 17.4 + pgvector 0.8.0 (tensorchord/vchord-suite:pg17-20250620) and PostgreSQL 16.10 + pgvector 0.8.1 (pgvector/pgvector:pg16), with psycopg 3.3.6 (libpq 18) and pgvector-python 0.5.1; binary results observed on tensorchord/vchord-suite:pg17-20260501 (2026-11-01)"
valid_for: "PostgreSQL 15–18, pgvector 0.7–0.8.x, psycopg 3.3.x (3.3.1 or later)"
generated:
  by: process:researching-references
  at: 2026-10-22T15:00:00+02:00
stale_after: 2027-04-22T15:00:00+02:00
verified:
  - by: process:researching-references
    at: 2026-10-22T15:00:00+02:00
  - by: process:researching-references
    at: 2026-11-01T18:00:00+01:00
status: stable
sources:
  - id: pg-explicit-locking
    title: Explicit Locking (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/explicit-locking.html
    accessed: 2026-10-18
  - id: pg-admin-functions
    title: System Administration Functions (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/functions-admin.html
    accessed: 2026-10-18
  - id: pg-isolation
    title: Transaction Isolation (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/transaction-iso.html
    accessed: 2026-10-18
  - id: pg-wal-config
    title: Write Ahead Log settings (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/runtime-config-wal.html
    accessed: 2026-10-18
  - id: pg-client-config
    title: Client Connection Defaults (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/runtime-config-client.html
    accessed: 2026-10-18
  - id: pg-set
    title: SET (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/sql-set.html
    accessed: 2026-10-18
  - id: pg-locks-view
    title: pg_locks (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/view-pg-locks.html
    accessed: 2026-10-18
  - id: pg-monitoring-stats
    title: The Cumulative Statistics System (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/monitoring-stats.html
    accessed: 2026-10-18
  - id: psycopg-transactions
    title: Transactions management (psycopg 3)
    resource: https://www.psycopg.org/psycopg3/docs/basic/transactions.html
    accessed: 2026-10-18
  - id: psycopg-copy
    title: Using COPY TO and COPY FROM (psycopg 3)
    resource: https://www.psycopg.org/psycopg3/docs/basic/copy.html
    accessed: 2026-10-18
  - id: psycopg-binary-results
    title: Binary parameters and results (psycopg 3)
    resource: https://www.psycopg.org/psycopg3/docs/basic/params.html#binary-parameters-and-results
    accessed: 2026-11-01
  - id: pgvector-python-psycopg
    title: pgvector-python psycopg adapter (v0.5.1)
    resource: https://github.com/pgvector/pgvector-python/blob/v0.5.1/pgvector/psycopg/vector.py
    accessed: 2026-11-01
---

# PostgreSQL transactions, locks, durability and COPY behind the vector store

This page records how PostgreSQL and psycopg 3 behave where the
PostgreSQL vector store (#1504) writes: what a snapshot and a lock
guarantee, how the writer lease's advisory lock behaves, what a commit
promises, and how the client bulk-loads rows. It leaves out replication
and crash or power-loss behaviour, which the store does not rely on.
Claims marked `[observed: probe N]` were reproduced on throwaway
containers of both images named in the frontmatter with the probe
scripts listed under "Probes".

Split from the former `postgresql-pgvector.md` (#1927); the claims are
unchanged, and so are their dates.

## Scope

- Covers: snapshots under `REPEATABLE READ`; `SET LOCAL`; `DELETE` against
  `TRUNCATE` and `DROP TABLE`; row and advisory locks; `lock_timeout`
  and `statement_timeout`; `synchronous_commit` and `fsync`; binary
  `COPY`, binary results and the `vector` round trip through pgvector-python.
- Does not cover: streaming replication and replica read-after-write; crash and
  power-loss recovery; how a failed write is reported
  ([PostgreSQL connections](postgresql-connections.md)).
- Depended on by: `src/markdown_vault_mcp/stores/_postgres_lease.py` (the writer
  lease), `_postgres_tables.py` (generations, resets, durability, the
  batched row read),
  `postgres_vectors.py` (read snapshots, the `fsync` warning) and
  `_postgres.py` (each pool connection's `statement_timeout`); the
  operator guide, `docs/use/postgresql.md` (durability, PgBouncer);
  `docs/design/design.md` § "The storage contract (#1766, #1503)"
  (the PostgreSQL store paragraphs and decision 34).

## Claims

### Transactions and snapshots

- A `REPEATABLE READ` transaction sees a snapshot taken at its first non-transaction-control statement, the same for every later statement; `READ COMMITTED` takes one per statement. [source: pg-isolation]
- Read-only transactions never have serialization conflicts. [source: pg-isolation]
- DDL is transactional: a `CREATE TABLE` inside a rolled-back transaction leaves no table. [observed: probe 9] [pins: tests/test_stores_postgres_tables.py::test_an_interrupted_create_leaves_nothing]
- While a `DELETE` of every row is uncommitted, a `REPEATABLE READ` reader counts the old rows. [observed: probe 5, count `3`]
- `DELETE` takes `ROW EXCLUSIVE`, which does not conflict with the `ACCESS SHARE` a `SELECT` takes. [source: pg-explicit-locking]
- `TRUNCATE` takes `ACCESS EXCLUSIVE`, which conflicts with every mode, so a reader waits until it commits. [source: pg-explicit-locking] [observed: probe 5, the reader fails with `canceling statement due to lock timeout`]
- psycopg starts a transaction implicitly on the first statement and needs `commit()`; `isolation_level` and `read_only` can be changed only while no transaction is open on the connection. [source: psycopg-transactions]
- `SET LOCAL` lasts until the end of the current transaction, committed or not; outside a transaction block it warns and has no effect. [source: pg-set]

### Locks

- `SELECT … FOR UPDATE` blocks other transactions' `UPDATE`, `DELETE` and `FOR UPDATE`/`FOR SHARE` of those rows until it ends, and does not block plain reads. [source: pg-explicit-locking] [pins: tests/test_stores_postgres_writes.py::test_lock_meta_returns_the_row_and_holds_it, tests/test_stores_postgres_writes.py::test_a_write_behind_a_held_meta_row_times_out]
- In `REPEATABLE READ`, `FOR UPDATE` on a row changed since the transaction began raises an error. [source: pg-explicit-locking]
- A session-level advisory lock is held until released or the session ends, survives a rollback, and is counted per acquisition. [source: pg-explicit-locking]
- Advisory locks are identified by one 64-bit key or two 32-bit keys, and "these two key spaces do not overlap". [source: pg-admin-functions]
- In `pg_locks`, a single-`bigint` advisory key shows its high-order half in `classid`, its low-order half in `objid`, and `objsubid = 1` (a two-`int4` key has `objsubid = 2`); advisory locks are local to each database. [source: pg-locks-view] [pins: tests/test_stores_postgres_lease.py::test_a_second_acquire_is_refused_naming_the_holder]
- Ordinary roles see only their own sessions in full in `pg_stat_activity`; "in rows about other sessions, many columns will be null", while a session's existence and general properties stay visible to all. [source: pg-monitoring-stats] A non-superuser does see another role's session's `application_name` (its `state` is null). [observed: #1504 child 4, a scratch test as a role with only `USAGE` on the schema, PostgreSQL 17] [pins: tests/test_stores_postgres_lease.py::test_another_roles_holder_is_still_named]
- Advisory locks are application-defined: "the system does not enforce their use". [source: pg-explicit-locking] Any session in the same database that takes the same key contends with the holder, whatever application it belongs to; a key chosen by one application cannot rule out a collision with another's. [observed: probe 14, a second session in the same database gets `f`]
- An advisory-lock key is scoped to one database: while one session holds key 42 in database `postgres`, a session in database `other` takes key 42 at once. [observed: probe 14, `t` in the other database]
- `pg_try_advisory_lock` takes the lock at once and returns `true`, or returns `false` without waiting. [source: pg-admin-functions]
- A `bigint` key of the form `(0x4D564D31 << 32) | oid` with an OID above 2^31 is accepted; a second session's `pg_try_advisory_lock` returns `f`; the holder's `application_name` is found by joining `pg_locks` (`locktype = 'advisory'`, `granted`) to `pg_stat_activity` on `pid`; the lock is free again once the holding session ends. [observed: probe 4] [pins: tests/test_stores_postgres_lease.py::test_acquire_holds_the_key_until_release, tests/test_stores_postgres_lease.py::test_ensure_takes_a_lost_lease_back]
- `idle_session_timeout` ends a session idle (outside a transaction) longer than it, and a session can set it to 0 for itself; a session-level advisory lock then lasts as long as its session. [observed: a probe in #1504 child 4's final review: a lease connection opened with `-c idle_session_timeout=300` lost its lock within a second of idling; with the store setting it to 0, the pinned test shows the lock held after a second idle] [pins: tests/test_stores_postgres_lease.py::test_an_idle_session_timeout_does_not_drop_the_lease]
- `DROP TABLE` takes `ACCESS EXCLUSIVE`, which conflicts with every mode including the `ACCESS SHARE` a `SELECT` takes; `DELETE` takes `ROW EXCLUSIVE`, which does not conflict with `ACCESS SHARE`. [source: pg-explicit-locking] So emptying a store with `DELETE` leaves readers on the old rows until commit, where a drop would make them wait. [observed: #1504 child 5b, a reader times out behind a drop and reads at once beside a `DELETE`] [pins: tests/test_stores_postgres_writes.py::test_a_reader_is_not_blocked_by_a_fresh_store_reset]
- `DROP TABLE` naming a view fails with `42809` ("is not a table", hint `DROP VIEW`); dropping a table another object depends on, without `CASCADE`, fails with `2BP01`; `DROP TABLE IF EXISTS a, b, c` drops those that exist. [observed: #1504 child 5b probe, 2026-10-19, PostgreSQL 17] [pins: tests/test_stores_postgres_writes.py::test_a_reset_that_cannot_drop_changes_nothing]
- `lock_timeout` aborts a statement whose wait for a lock exceeds it, the limit applying "separately to each lock acquisition attempt"; `statement_timeout` aborts any statement that runs longer, measured per statement; zero disables either. [source: pg-client-config]

### Durability

- Every non-`off` value of `synchronous_commit` waits for the local WAL flush; `off` may lose recent commits after a crash but leaves the database consistent. [source: pg-wal-config]
- `remote_apply`, `on` and `remote_write` differ only in what they wait for from synchronous standbys; without `synchronous_standby_names` they equal `on`. [source: pg-wal-config]
- The setting in effect at commit decides a transaction's behaviour, so `SET LOCAL synchronous_commit` changes it for one transaction. [source: pg-wal-config]
- `SET LOCAL synchronous_commit = on` under a configured `remote_apply` shows `on` inside the transaction and `remote_apply` after it: it lowers the level for that transaction. [observed: probe 6] [pins: tests/test_stores_postgres_writes.py::test_make_durable_raises_only_weaker_settings]
- `fsync = off` can cause unrecoverable corruption after a crash or power failure, and is set only in the configuration file or on the command line. [source: pg-wal-config] `ALTER SYSTEM SET fsync = off` followed by `pg_reload_conf()` takes effect for new sessions without a restart. [observed: #1504 child 5b] [pins: tests/test_stores_postgres_vectors.py::test_a_writer_warns_when_postgresql_runs_without_fsync]
- A role without special privileges can read `fsync` and `synchronous_commit` with `SHOW`. [observed: probe 11]

### Binary COPY and binary results

- Binary `COPY … FROM STDIN` with `write_row()` needs a binary dumper for every type and applies no casts; `Copy.set_types()` names the types. [source: psycopg-copy]
- After `register_vector(conn)`, binary `COPY` with `set_types(["text", "int4", "vector", "jsonb"])` loads numpy float32 vectors, and a selected `vector` comes back as `pgvector.Vector`, whose `to_numpy()` gives float32. [observed: probe 13, pgvector-python 0.5.1]
- Binary `COPY` with `set_types(["text", "int4", "text", "vector", "jsonb"])` takes numpy float32 rows and `Jsonb` values with a custom `dumps`; a `vector` column without a typmod takes rows of different dimensions; a foreign-key violation inside `COPY` raises `23503`. [observed: #1504 child 5b probe, 2026-10-19, pgvector 0.8.1-pg17, psycopg 3.3.6] [pins: tests/test_stores_postgres_writes.py::test_a_generation_replaces_everything]
- A cursor opened with `Connection.cursor(binary=True)` asks for every result column in binary; a type with no loader comes back as an unparsed buffer. [source: psycopg-binary-results] `register_vector` registers a binary loader for `vector` that builds a `pgvector.Vector` from the float32 bytes without parsing text. [source: pgvector-python-psycopg] On such a cursor, `text`, `jsonb` and `vector` columns come back as `str`, `dict` and `Vector`, and reading 22,764 rows of 1,024 dimensions this way, 256 documents per query, took 0.63–0.68 s against 9.2–9.6 s for one text-format query per document. [observed: #1970, `tensorchord/vchord-suite:pg17-20260501`, psycopg 3.3.6, pgvector-python 0.5.1, 2026-11-01] [pins: tests/test_stores_postgres_writes.py::test_rows_by_path_reads_many_documents_as_float32]

## Probes

Every probe started its own throwaway container and removed it afterwards,
on the images `tensorchord/vchord-suite:pg17-20250620` (PostgreSQL 17.4,
pgvector 0.8.0) and `pgvector/pgvector:pg16` (PostgreSQL 16.10, pgvector
0.8.1). Probes 4–6, 9 and 11 ran in the container probes 1–12
shared on each image; probes 13 and 14 each started their own. The
two images agreed on every probe.

- Probes 4–6, 9 and 11: SQL through `psql` inside the container: the
  advisory lock and its holder, `DELETE` and `TRUNCATE` against a
  `REPEATABLE READ` reader with `lock_timeout = 1s`, `SET LOCAL
  synchronous_commit` under `ALTER SYSTEM … remote_apply`, DDL in a
  rolled-back transaction, and `SHOW` as a plain role.
- Probe 13: psycopg 3.3.6 with pgvector-python 0.5.1 (both pinned):
  binary `COPY` of 1,000 rows and a vector round trip.
- Probe 14: two `psql` sessions taking advisory key 42, in the same database
  and in another one.

## Where this project departs from the subject

Each departure is decided in `docs/design/design.md` § "The storage contract
(#1766, #1503)", decision 34.

- A new generation deletes the old rows rather than truncating them, so
  readers keep the previous generation until it commits.
- A fresh store's first write empties an intact store with `DELETE` and
  drops our tables only when they are damaged or absent, never with
  `CASCADE`, so a rebuild over an intact store does not make readers wait
  behind `ACCESS EXCLUSIVE`.
- `synchronous_commit` is raised to `on` from any weaker value (`off`,
  `local`, `remote_write`) and left alone at `on` or `remote_apply`, because
  `SET LOCAL … = on` would lower a configured `remote_apply`.

## Not covered

- Crash and power-loss behaviour, replication and replica read-after-write:
  not probed; the store assumes one primary.
- The probes above ran on PostgreSQL 16 and 17 only; on 15 and 18 the store's
  tests, run by the range workflow, are the evidence (its claim is in
  [PostgreSQL extension, schema and operations](postgresql-schema-and-operations.md)).
- The psycopg 3.3.1 floor in `valid_for`: its evidence is the cursor claim
  in [pgvector search](pgvector-search.md).
