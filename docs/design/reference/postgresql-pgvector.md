---
type: Reference
title: PostgreSQL and pgvector behaviour behind the vector store
description: What the PostgreSQL vector store relies on in PostgreSQL 17, pgvector 0.8 and psycopg 3 — extension privileges, zero-vector scoring, schemas and search_path, snapshots, DELETE against TRUNCATE, advisory locks, durability settings, error codes and connect-time failures, cursors, COPY, pooling, connection poolers, connection parameters, schema backups and vacuuming.
subject_version: "PostgreSQL 17 documentation; pgvector 0.8.0 README and changelog to 0.8.7; psycopg 3 docs (current as read 2026-10-18); observed on PostgreSQL 17.4 + pgvector 0.8.0 (tensorchord/vchord-suite:pg17-20250620) and PostgreSQL 16.10 + pgvector 0.8.1 (pgvector/pgvector:pg16), with psycopg 3.3.6 (libpq 18) and pgvector-python 0.5.1; the range workflow's eight images (2026-10-22); psycopg 3.3.0 to 3.3.3, psycopg-pool 3.3.0 and pgvector-python 0.5.0 on Python 3.11 (2026-10-27)"
valid_for: "PostgreSQL 15–18, pgvector 0.7–0.8.x (0.7.4 or later on 17, 0.8.7 or later on 18), psycopg 3.3.x (3.3.1 or later)"
generated:
  by: process:researching-references
  at: 2026-10-22T15:00:00+02:00
stale_after: 2027-04-22T15:00:00+02:00
verified:
  - by: process:researching-references
    at: 2026-10-22T15:00:00+02:00
  - by: process:researching-references
    at: 2026-10-27T15:30:00+01:00
status: stable
sources:
  - id: pgvector-readme
    title: pgvector README (v0.8.0)
    resource: https://github.com/pgvector/pgvector/blob/v0.8.0/README.md
    accessed: 2026-10-18
  - id: pgvector-changelog
    title: pgvector CHANGELOG
    resource: https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md
    accessed: 2026-10-18
  - id: pg-create-extension
    title: CREATE EXTENSION (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/sql-createextension.html
    accessed: 2026-10-18
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
  - id: pg-schemas
    title: Schemas (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/ddl-schemas.html
    accessed: 2026-10-18
  - id: pg-privileges
    title: Privileges (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/ddl-priv.html
    accessed: 2026-10-18
  - id: pg15-release
    title: PostgreSQL 15.0 release notes
    resource: https://www.postgresql.org/docs/release/15.0/
    accessed: 2026-10-18
  - id: pg-json-types
    title: JSON Types (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/datatype-json.html
    accessed: 2026-10-19
  - id: pg-locks-view
    title: pg_locks (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/view-pg-locks.html
    accessed: 2026-10-18
  - id: pg-monitoring-stats
    title: The Cumulative Statistics System (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/monitoring-stats.html
    accessed: 2026-10-18
  - id: pg-errcodes
    title: PostgreSQL Error Codes (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/errcodes-appendix.html
    accessed: 2026-10-18
  - id: pg-string-functions
    title: String Functions and Operators (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/functions-string.html
    accessed: 2026-10-18
  - id: pg-declare
    title: DECLARE (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/sql-declare.html
    accessed: 2026-10-18
  - id: pg-dump
    title: pg_dump (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/app-pgdump.html
    accessed: 2026-10-18
  - id: libpq-connect
    title: libpq Database Connection Control Functions (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/libpq-connect.html
    accessed: 2026-10-18
  - id: libpq-status
    title: libpq Connection Status Functions (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/libpq-status.html
    accessed: 2026-10-18
  - id: libpq-envars
    title: libpq Environment Variables (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/libpq-envars.html
    accessed: 2026-10-18
  - id: libpq-pgpass
    title: The Password File (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/libpq-pgpass.html
    accessed: 2026-10-18
  - id: psycopg-errors
    title: psycopg.errors – Package exceptions
    resource: https://www.psycopg.org/psycopg3/docs/api/errors.html
    accessed: 2026-10-18
  - id: psycopg-news
    title: psycopg 3 release notes
    resource: https://www.psycopg.org/psycopg3/docs/news.html
    accessed: 2026-10-27
  - id: psycopg-cursors-api
    title: Cursor classes (psycopg 3 API)
    resource: https://www.psycopg.org/psycopg3/docs/api/cursors.html
    accessed: 2026-10-18
  - id: psycopg-transactions
    title: Transactions management (psycopg 3)
    resource: https://www.psycopg.org/psycopg3/docs/basic/transactions.html
    accessed: 2026-10-18
  - id: psycopg-copy
    title: Using COPY TO and COPY FROM (psycopg 3)
    resource: https://www.psycopg.org/psycopg3/docs/basic/copy.html
    accessed: 2026-10-18
  - id: psycopg-pool
    title: Connection pools (psycopg 3)
    resource: https://www.psycopg.org/psycopg3/docs/advanced/pool.html
    accessed: 2026-10-18
  - id: psycopg-conninfo
    title: psycopg.conninfo – manipulate connection strings
    resource: https://www.psycopg.org/psycopg3/docs/api/conninfo.html
    accessed: 2026-10-18
  - id: pgvector-python
    title: pgvector-python README
    resource: https://github.com/pgvector/pgvector-python/blob/master/README.md
    accessed: 2026-10-18
  - id: pgbouncer-features
    title: PgBouncer features (pooling modes and SQL feature map)
    resource: https://www.pgbouncer.org/features.html
    accessed: 2026-10-21
  - id: pgbouncer-changelog
    title: PgBouncer changelog
    resource: https://www.pgbouncer.org/changelog.html
    accessed: 2026-10-21
  - id: pg-routine-vacuuming
    title: Routine Vacuuming (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/routine-vacuuming.html
    accessed: 2026-10-21
  - id: pg-autovacuum-config
    title: Automatic Vacuuming settings (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/runtime-config-autovacuum.html
    accessed: 2026-10-21
---

# PostgreSQL and pgvector behaviour behind the vector store

This page records how PostgreSQL, the pgvector extension, libpq and psycopg 3
behave where the PostgreSQL vector store (#1504) depends on them: who may
install the extension, how the distance operators treat zero vectors, how
`search_path` resolves names, what a snapshot and a lock guarantee, what a
commit promises, how failures are reported, and how the client streams and
bulk-loads rows. It leaves out ANN indexes, replication and crash or
power-loss behaviour, which the store does not rely on. Claims marked
`[observed: probe N]` were reproduced on throwaway containers of both images
named in the frontmatter with the probe scripts listed under "Probes".

## Scope

- Covers: extension installation and privileges; the `vector` type,
  operators, aggregates and storage; schemas and `search_path`; snapshots
  under `REPEATABLE READ`; `DELETE` against `TRUNCATE`; row and advisory
  locks; `synchronous_commit` and `fsync`; SQLSTATEs and how psycopg reports
  connect-time failures; server-side cursors, binary `COPY`, the connection
  pool and connection-string parsing; libpq connection parameters and
  password sources; PgBouncer's pooling modes; `pg_dump` of one schema;
  relation sizes; vacuuming after `DELETE`.
- Does not cover: ANN indexes (`hnsw`, `ivfflat`) and their recall;
  `halfvec`/`sparsevec`/`bit`; streaming replication and replica
  read-after-write; crash and power-loss recovery; the async psycopg API.
- Depended on by: `src/markdown_vault_mcp/stores/postgres_vectors.py`,
  `_postgres.py`, `_postgres_lease.py`, `_postgres_tables.py`,
  `_postgres_rows.py` and `_postgres_search.py` in the same package;
  `docs/design/design.md` § "The storage contract (#1766, #1503)" (the
  PostgreSQL store paragraphs and decision 34); and the operator guide,
  `docs/use/postgresql.md`.

## Claims

### Extension and privileges

- `CREATE EXTENSION` needs the privileges to create the extension's objects, usually a superuser; only an extension marked `trusted` in its control file can be installed by a role with `CREATE` on the database. [source: pg-create-extension]
- pgvector's control file carries no `trusted = true` (it reads `comment`, `default_version`, `module_pathname`, `relocatable = true`), so installing it needs a superuser. [observed: probe "control" on pgvector 0.8.0 and 0.8.1] [pins: tests/test_stores_postgres_connection.py::test_a_database_without_the_extension_is_not_configured]
- An extension is loaded into the current database, its name is unique database-wide, and its objects live in a schema chosen at creation (`SCHEMA`, or the current creation schema). [source: pg-create-extension]
- The store's whole PostgreSQL suite passes on PostgreSQL 15 to 18 with pgvector 0.7.0 to 0.8.7 (on 17 from 0.7.4, the first 0.7 built for it; on 18 only 0.8.7, since 0.7 is not built for it), and on `tensorchord/vchord-suite:pg17-20260501`: the images the range workflow pins. [observed: `.github/workflows/postgres-range.yml`, and a local run of each of its eight images on 2026-10-22, the store's 376 PostgreSQL tests passing on each] [pins: tests/test_postgres_range_workflow.py::test_the_lowest_pgvector_tested_is_the_minimum_the_store_accepts, tests/test_postgres_range_workflow.py::test_the_guide_states_the_postgresql_majors_the_workflow_runs]
- `CREATE EXTENSION IF NOT EXISTS` checks only the name: "there is no guarantee that the existing extension is anything like the one that would have been created". [source: pg-create-extension]
- The installed version and schema are in `pg_extension.extversion` and `pg_extension.extnamespace`. [observed: probe "versions", `select extversion from pg_extension where extname='vector'` → `0.8.0` / `0.8.1`]

### The `vector` type, operators and aggregates

- Each element is a single-precision float; a vector takes `4 * dimensions + 8` bytes and may have up to 16,000 dimensions. [source: pgvector-readme] (§ Vector Type)
- Every element must be finite: no `NaN`, `Infinity` or `-Infinity`. [source: pgvector-readme] (§ Vector Type)
- A column declared `vector` without a dimension stores vectors of different dimensions. [source: pgvector-readme] (§ FAQ) [observed: probe 3, `vector_dims` returns 2 and 3 from one column]
- An operator between vectors of different dimensions raises `different vector dimensions 3 and 2`. [observed: probe 3]
- `<#>` returns the negative inner product, `<=>` the cosine distance, `<->` the L2 distance. [source: pgvector-readme] (§ Querying)
- With a zero vector on either side, `<#>` returns `-0` and `<=>` returns `NaN`; negating `<#>` gives `0`. [observed: probe 1, `-0|-0|NaN|0` on both images] [pins: tests/test_postgres_harness.py::test_inner_product_with_a_zero_vector_is_minus_zero_not_nan]
- Without an index, pgvector performs exact nearest-neighbour search with perfect recall; the 2,000-dimension limit applies only to `hnsw` and `ivfflat` indexes on `vector`. [source: pgvector-readme] (§ Indexing)
- `avg(vector)` exists since 0.4.0. [source: pgvector-changelog] (0.4.0) The mean of `[1,0]` and `[-1,0]` is `[0,0]`. [observed: probe 2]
- `avg` over no rows returns `NULL` on 0.8.0 and 0.8.1. [observed: probe 13] [pins: tests/test_stores_postgres_search.py::test_similar_to_a_document_without_rows_is_empty] The 0.8.7 changelog entry "Fixed error with `avg` aggregate when no matching rows" names a case these probes did not hit. [source: pgvector-changelog] (0.8.7) [unverified] Which query shape raised that error is not established; the pgvector commit or issue behind the 0.8.7 entry would settle it. A store that returns `[]` for a path with no rows before calling `avg` does not depend on it.
- pgvector 0.8.0 dropped Postgres 12 and the README states support for Postgres 13+. [source: pgvector-changelog] (0.8.0) [source: pgvector-readme] (§ Installation)
- `l2_normalize` exists only from 0.7.0. [source: pgvector-changelog] (0.7.0)

### Schemas and `search_path`

- A schema in `search_path` that does not exist, or lacks `USAGE` for the role, is "silently ignored". [source: pg-client-config] [observed: probe 8, `set search_path = no_such_schema, public` leaves `{public}`, and `pg_tables` places the new table in `public`] [pins: tests/test_stores_postgres_connection.py::test_a_missing_schema_is_not_configured_with_the_sql]
- `pg_catalog` is always searched, before the path unless the path names it. [source: pg-client-config]
- The first existing schema in the path is the default creation schema; unqualified names resolve to the first match along the path. [source: pg-schemas]
- Putting a schema in `search_path` "effectively trusts all users having `CREATE` privilege on that schema". [source: pg-schemas] (§ Secure Schema Usage Patterns)
- From PostgreSQL 15, new databases no longer grant `CREATE` on `public` to `PUBLIC`; upgraded clusters and restored dumps keep the old grant. [source: pg15-release]
- `CREATE SCHEMA name AUTHORIZATION role` creates a schema owned by `role`; by default `PUBLIC` has no privileges on a schema, and `CONNECT` and `TEMPORARY` on a database. [source: pg-schemas] [source: pg-privileges]
- `SET LOCAL` lasts until the end of the current transaction, committed or not; outside a transaction block it warns and has no effect. [source: pg-set]

### JSON and text

- `jsonb` rejects `\u0000`, "because that cannot be represented in PostgreSQL's `text` type"; a JSON string maps to `text`, where `\u0000` is disallowed. [source: pg-json-types] [pins: tests/test_stores_postgres_writes.py::test_rows_that_cannot_be_stored_change_nothing]
- `jsonb` keeps neither key order, whitespace nor duplicate keys (the last value of a duplicated key wins); `json` stores the input text exactly. [source: pg-json-types]
- `jsonb` rejects `NaN` and `Infinity` (JSON has no such numbers) and a lone surrogate escape; Python's `json.dumps` writes `NaN` unless told not to. [observed: #1504 child 5a's final review, `Token "NaN" is invalid`, `Unicode low surrogate must follow a high surrogate`] [pins: tests/test_stores_postgres_writes.py::test_metadata_postgresql_cannot_store_is_refused_before_writing]
- Numbers and keys do not all round-trip through `jsonb` as Python wrote them: `1e20` comes back an integer, `-0.0` as `0.0`, non-string keys as strings, tuples as lists. [observed: #1504 child 5a's final review] [pins: tests/test_stores_postgres_writes.py::test_ordinary_nested_metadata_is_stored]
- A btree index entry holds at most about 2,700 bytes (`index row size 3648 exceeds btree version 4 maximum 2704`). [observed: #1504 child 5a's final review, a 3,000-byte path] [pins: tests/test_stores_postgres_writes.py::test_a_key_too_long_for_postgresql_indexes_is_refused]

### Transactions and snapshots

- A `REPEATABLE READ` transaction sees a snapshot taken at its first non-transaction-control statement, the same for every later statement; `READ COMMITTED` takes one per statement. [source: pg-isolation]
- Read-only transactions never have serialization conflicts. [source: pg-isolation]
- DDL is transactional: a `CREATE TABLE` inside a rolled-back transaction leaves no table. [observed: probe 9] [pins: tests/test_stores_postgres_tables.py::test_an_interrupted_create_leaves_nothing]
- While a `DELETE` of every row is uncommitted, a `REPEATABLE READ` reader counts the old rows. [observed: probe 5, count `3`]
- `DELETE` takes `ROW EXCLUSIVE`, which does not conflict with the `ACCESS SHARE` a `SELECT` takes. [source: pg-explicit-locking]
- `TRUNCATE` takes `ACCESS EXCLUSIVE`, which conflicts with every mode, so a reader waits until it commits. [source: pg-explicit-locking] [observed: probe 5, the reader fails with `canceling statement due to lock timeout`]
- psycopg starts a transaction implicitly on the first statement and needs `commit()`; `isolation_level` and `read_only` can be changed only while no transaction is open on the connection. [source: psycopg-transactions]

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

### Errors

- Class `08` is connection exception (`08000`, `08003`, `08006`, `08001`, `08004`, `08007 transaction_resolution_unknown`, `08P01`); class `28` invalid authorization (`28000`, `28P01`); class `53` insufficient resources; class `57` operator intervention (including `57014 query_canceled`, `57P01 admin_shutdown`); class `40` transaction rollback (including `40003 statement_completion_unknown`); `42501` insufficient privilege; `42P01` undefined table; `3F000` invalid schema name; `25006` read-only transaction; `55P03` lock not available. [source: pg-errcodes]
- The first two characters of a SQLSTATE are its class, and an application that does not know a code "might still be able to infer what to do from the error class". [source: pg-errcodes]
- `pg_terminate_backend` on a session idle in a transaction makes that session's next statement fail as `57P01 admin_shutdown` (or, when the line drops first, with no SQLSTATE); `pg_terminate_backend(pid, timeout)` waits for the backend to exit. [source: pg-admin-functions] [observed: #1504 child 6, a named cursor's next fetch] [pins: tests/test_stores_postgres_search.py::test_a_search_whose_connection_dies_midway_is_retried_once]
- A query on a schema without `USAGE` fails with `42501: permission denied for schema`. [observed: probe 10]
- psycopg exposes `sqlstate` only for errors received from PostgreSQL; client-side failures such as a failed connection usually have none. [source: psycopg-errors]
- Every connect-time failure probed raised `psycopg.OperationalError` with `sqlstate=None`: wrong password, unknown role, missing database, no `CONNECT` privilege, no password supplied, refused port. [observed: probe 10e, both images, `scram-sha-256` and `trust`] An unresolvable host raised `OperationalError` and a connect timeout its subclass `ConnectionTimeout`, both with `sqlstate=None` and `pgconn=None`. [observed: probe 10d, one run against the PostgreSQL 17.4 image; neither case reaches PostgreSQL]
- After a failed connection, `Error.pgconn` is a closed connection; its `needs_password`, `used_password`, `server_version`, `status` and `error_message` can be read, `parameter_status()` raises `the connection is closed`, and `server_version` reads `0` whether or not PostgreSQL answered. [source: psycopg-errors] [observed: probe 10e, both images]
- `PQconnectionNeedsPassword` is true when "the connection authentication method required a password, but none was available"; `PQconnectionUsedPassword` is true when "the connection authentication method used a password", which detects "whether the server demanded a password". [source: libpq-status]
- Under `scram-sha-256`: a wrong password, an unknown role, a missing database and a missing `CONNECT` privilege give `needs_password=False`, `used_password=True`; no password supplied gives `needs_password=True` and also `used_password=True`; a refused port gives both `False`. [observed: probe 10e, both images] So `used_password` alone does not mean a password was sent: `needs_password` must be tested first. [pins: tests/test_stores_postgres_connection.py::test_connect_reports_a_wrong_password_as_refused, tests/test_stores_postgres_connection.py::test_connect_reports_a_missing_database_as_refused, tests/test_stores_postgres_connection.py::test_connect_reports_no_password_as_no_password, tests/test_stores_postgres_connection.py::test_needs_password_is_tested_before_used_password]
- With every slot under `max_connections` taken, a new connection fails with `sorry, too many clients already`, before authentication: `needs_password` and `used_password` are both false, so the store tags it `connect`. With only reserved slots left (`reserved_connections`, `superuser_reserved_connections`), a role without them fails after authentication with `remaining connection slots are reserved for roles with privileges of …`: `used_password` is true, so the store tags it `refused`. [observed: a throwaway `pgvector/pgvector:0.8.7-pg17` with `max_connections=4` and no reserved slots, then `max_connections=5` with one of each reserved, psycopg 3.3.6 through `classify_connect_failure`, 2026-10-22]
- Under `trust`: a wrong password and no password both connect; an unknown role, a missing database, a missing `CONNECT` privilege and a refused port all give both flags `False`. [observed: probe 10e, both images]
- PostgreSQL displays its messages in the language `lc_messages` selects, falling back to English where no translation exists. [source: pg-client-config] A message's wording is therefore not a stable value to match on.
- What a client should conclude when the connection drops after `COMMIT` was sent is not documented by the transaction-isolation page or by psycopg. [source: pg-isolation] [source: psycopg-transactions] [unverified] That the commit may or may not have landed follows from PostgreSQL naming the condition (`08007 transaction_resolution_unknown`); the store therefore reports such a write as `commit_unknown` and lets its next read show whether it landed. [pins: tests/test_stores_postgres_writes.py::test_a_commit_whose_reply_is_lost_is_commit_unknown_and_landed]

### Client: cursors, COPY, pool, connection strings

- `DECLARE` without `WITH HOLD` is usable only inside a transaction block, and every PostgreSQL cursor is insensitive. [source: pg-declare]
- A psycopg named (server-side) cursor iterates in batches of `itersize`, default 100. [source: psycopg-cursors-api] [pins: tests/test_stores_postgres_search.py::test_a_selective_predicate_reads_past_the_first_batch]
- psycopg 3.3.0 iterates a named cursor wrongly; 3.3.1's notes read "Fix iteration on server-side cursors (ticket #1226)". [source: psycopg-news] With 3.3.0 a store search with a `predicate` (offered by the store's contract; none of the server's searches pass one) returned none of the rows past the first batch, and the two search tests that read past it failed; with 3.3.1 every PostgreSQL test passes, and 3.3.2 and 3.3.3 pass the selective-predicate test. [observed: the PostgreSQL tests with psycopg pinned to 3.3.0 and 3.3.1, the selective-predicate test with 3.3.2 and 3.3.3, psycopg-pool 3.3.0 and pgvector-python 0.5.0, Python 3.11, `pgvector/pgvector:0.8.7-pg18`, and for 3.3.0 and 3.3.1 also `pgvector/pgvector:0.7.0-pg15`, 2026-10-27] The `postgres` extra therefore requires `psycopg[binary]>=3.3.1`. The range workflow's `floors` job runs the search test on the floor driver. [pins: tests/test_stores_postgres_search.py::test_a_selective_predicate_reads_past_the_first_batch, tests/test_postgres_range_workflow.py::test_the_floors_job_installs_exactly_the_postgres_extras_floors]
- On a non-autocommit connection a named cursor runs inside the implicit transaction; on an autocommit connection it fails with `NoActiveSqlTransaction: DECLARE CURSOR can only be used in transaction blocks`. [observed: probe 13]
- Ordered rows (`ORDER BY e <#> q, path`) stream through a named cursor inside a `REPEATABLE READ`, read-only transaction. [observed: probe 13]
- Binary `COPY … FROM STDIN` with `write_row()` needs a binary dumper for every type and applies no casts; `Copy.set_types()` names the types. [source: psycopg-copy]
- After `register_vector(conn)`, binary `COPY` with `set_types(["text", "int4", "vector", "jsonb"])` loads numpy float32 vectors, and a selected `vector` comes back as `pgvector.Vector`, whose `to_numpy()` gives float32. [observed: probe 13, pgvector-python 0.5.1]
- Binary `COPY` with `set_types(["text", "int4", "text", "vector", "jsonb"])` takes numpy float32 rows and `Jsonb` values with a custom `dumps`; a `vector` column without a typmod takes rows of different dimensions; a foreign-key violation inside `COPY` raises `23503`. [observed: #1504 child 5b probe, 2026-10-19, pgvector 0.8.1-pg17, psycopg 3.3.6] [pins: tests/test_stores_postgres_writes.py::test_a_generation_replaces_everything]
- pgvector-python registers its types per connection; with a pool it does so in the `configure` callback. [source: pgvector-python]
- `ConnectionPool` keeps between `min_size` and `max_size` connections; a `check` callback runs before a connection is handed out and a failing check gets a new connection; `ConnectionPool.check_connection` is a ready-made check; `configure` runs on each new connection. [source: psycopg-pool]
- A pool commits a returned connection's open transaction on a normal exit, rolls it back on an exception, and discards a broken one; a client blocks until `PoolTimeout` when PostgreSQL is unreachable. [source: psycopg-pool]
- The pool's default `open=True` may change, so it should be opened explicitly. [source: psycopg-pool]
- `conninfo_to_dict` parses a URI or key/value string into parameters and raises `ProgrammingError` for an invalid one. [source: psycopg-conninfo] [unverified] That it never connects is not stated on the page; the function's name and examples suggest it, and a test that parses a DSN for an unreachable host would settle it.

### Connection poolers

- PgBouncer's session pooling assigns a server connection to a client for as long as the client stays connected; transaction pooling assigns one only for the duration of a transaction; statement pooling is transaction pooling that disallows multi-statement transactions. [source: pgbouncer-features] (§ Pooling modes)
- Under transaction pooling, `SET`/`RESET`, session-level advisory locks, `LISTEN`, `WITH HOLD` cursors and SQL `PREPARE`/`DEALLOCATE` are "Never" supported; session pooling supports all of them. Protocol-level prepared plans work under transaction pooling only once `max_prepared_statements` is set above zero. [source: pgbouncer-features] (§ SQL feature map for pooling modes)
- Support for protocol-level named prepared statements arrived in PgBouncer 1.21.0, released 2023-10-16. [source: pgbouncer-changelog] (1.21.x)
- So only session pooling serves this store: the writer lease is a session-level advisory lock held across transactions, each pool connection sets `search_path` and `statement_timeout` with a session `SET` once, when it opens, and the lease connection sets `idle_session_timeout`, `statement_timeout` and `lock_timeout` the same way. Under transaction pooling a later transaction could run on a server connection that has neither the lock nor the settings. Decided in `docs/design/design.md` § "The storage contract (#1766, #1503)". [pins: tests/test_stores_postgres_lease.py::test_acquire_holds_the_key_until_release]

### Connections and passwords

- `connect_timeout` absent, zero or negative waits indefinitely, and applies per host. [source: libpq-connect]
- TCP keepalives are on by default; `keepalives_idle`, `keepalives_interval`, `keepalives_count` tune them where the platform supports it; `tcp_user_timeout` bounds unacknowledged data. [source: libpq-connect]
- `sslmode` defaults to `prefer`; `verify-full` requires TLS, a trusted CA and a host-name match. [source: libpq-connect]
- `options` passes `-c name=value` run-time settings at connection start. [source: libpq-connect]
- Environment variables fill parameters the caller did not give; `PGPASSWORD` is discouraged because other users may see a process's environment. [source: libpq-envars]
- The password file is used only when no password was given otherwise, and is ignored unless its permissions deny group and world access (`0600`). [source: libpq-pgpass]

### Backup and sizes

- `pg_dump` makes a consistent backup without blocking readers or writers. [source: pg-dump]
- `pg_dump -n` dumps the selected schemas and their objects but "makes no attempt to dump any other database objects that the selected schema(s) might depend upon", so such a dump may not restore into a clean database on its own; extensions are selected with `-e`. [source: pg-dump]
- An `UPDATE` or `DELETE` does not remove the old row version at once; `VACUUM` reclaims the space of row versions no transaction still needs. [source: pg-routine-vacuuming] (§ 24.1.2 Recovering Disk Space)
- Plain `VACUUM` marks that space reusable by new rows but does not return it to the operating system, "except in the special case where one or more pages at the end of a table become entirely free and an exclusive table lock can be easily obtained". [source: pg-routine-vacuuming] (§ 24.1.2)
- `VACUUM FULL` writes a new copy of the table without dead space, needs extra disk for that copy until it finishes, and takes `ACCESS EXCLUSIVE` on the table, so it cannot run alongside other use of it. [source: pg-routine-vacuuming] (§ 24.1.1, § 24.1.2)
- The autovacuum daemon vacuums a table once its updated or deleted tuples pass `autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor * reltuples`. [source: pg-routine-vacuuming] (§ 24.1.6 The Autovacuum Daemon) `autovacuum` is on by default; the threshold defaults to 50 tuples and the scale factor to 0.2, both overridable per table with storage parameters; `autovacuum_naptime` defaults to one minute. [source: pg-autovacuum-config]
- `pg_total_relation_size` is a table's disk space including its indexes and TOAST data. [source: pg-admin-functions] [observed: probe 12, 16384 bytes for a two-row table]
- `starts_with(string, prefix)` and the `^@` operator test a prefix. [source: pg-string-functions] [observed: probe 7, `starts_with('a/b','a/')` true, `starts_with('ab/c','a/')` false, `starts_with('A/b','a/')` false] It is a plain prefix test: `_` and `%` match only themselves. [observed: #1504 child 6] [pins: tests/test_stores_postgres_search.py::test_folder_scopes_are_exact_prefixes]

## Probes

Every probe started its own throwaway container and removed it afterwards,
on the images `tensorchord/vchord-suite:pg17-20250620` (PostgreSQL 17.4,
pgvector 0.8.0) and `pgvector/pgvector:pg16` (PostgreSQL 16.10, pgvector
0.8.1). Probes 1–12 shared one container per image; probes 10e, 13 and 14
each started their own. The two images agreed on every probe run on both;
probe 10d ran once, on the PostgreSQL 17.4 image.

- Probes 1–12: SQL through `psql` inside the container: zero vectors, `avg`,
  mixed dimensions, the advisory lock and its holder, `DELETE` and
  `TRUNCATE` against a `REPEATABLE READ` reader with `lock_timeout = 1s`,
  `SET LOCAL synchronous_commit` under `ALTER SYSTEM … remote_apply`,
  `starts_with`, `search_path` naming a missing schema, DDL in a rolled-back
  transaction, a refused schema privilege, `SHOW` as a plain role, and
  `pg_total_relation_size`.
- Probe 10d: psycopg 3.3.6 connecting from the host to an unresolvable host
  and to a non-routable address with `connect_timeout=2`, printing the
  exception class, `sqlstate` and `pgconn`.
- Probe 10e: psycopg 3.3.6 (libpq 18) connecting from the host through a
  published port, under the image's default `scram-sha-256` and under
  `POSTGRES_HOST_AUTH_METHOD=trust`, printing the exception class,
  `sqlstate` and the readable `Error.pgconn` attributes per failure. Its
  first run also showed `parameter_status()` raising. (Inside the container,
  `127.0.0.1` is `trust`, so connect failures must be probed from outside
  it.)
- Probe 13: psycopg 3.3.6 with pgvector-python 0.5.1 (both pinned): `avg` over no rows,
  binary `COPY` of 1,000 rows, a vector round trip, a named cursor in a
  `REPEATABLE READ` read-only transaction, and named cursors outside a
  transaction and on an autocommit connection.
- Probe 14: two `psql` sessions taking advisory key 42, in the same database
  and in another one.
- The range workflow, `.github/workflows/postgres-range.yml`: the store's
  PostgreSQL tests on each pinned image, through the test harness's
  `MVM_TEST_POSTGRES_IMAGE`; first run locally on 2026-10-22.

## Where this project departs from the subject

Each departure is decided in `docs/design/design.md` § "The storage contract
(#1766, #1503)", decision 34.

- Search is exact; pgvector's ANN indexes are not used.
- Scores come from `<#>` over normalised rows, never `<=>`, because `<=>`
  returns `NaN` for a zero vector.
- The store does not rely on a role's `search_path`: it sets the path itself
  and checks that the vault schema exists in `pg_namespace`, because a missing
  schema in the path is silently ignored.
- A new generation deletes the old rows rather than truncating them, so
  readers keep the previous generation until it commits.
- A fresh store's first write empties an intact store with `DELETE` and
  drops our tables only when they are damaged or absent, never with
  `CASCADE`, so a rebuild over an intact store does not make readers wait
  behind `ACCESS EXCLUSIVE`.
- Before any write, a row is refused with `ValueError` where the SQLite store would keep it: a path, folder or metadata string holding a `NUL` character or a lone surrogate, metadata holding `NaN` or infinity, or a path or folder over 2,000 bytes. PostgreSQL's `text`, `jsonb` and btree indexes cannot hold them, and refusing them up front keeps the document's old rows instead of a write the reconciler would retry forever.
- `synchronous_commit` is raised to `on` from any weaker value (`off`,
  `local`, `remote_write`) and left alone at `on` or `remote_apply`, because
  `SET LOCAL … = on` would lower a configured `remote_apply`.
- Connect-time failures are told apart only by psycopg's documented
  attributes (`ConnectionTimeout`, then `Error.pgconn.needs_password`, then
  `used_password`), never by message text, whose language follows
  `lc_messages`.
- libpq's messages are carried redacted, not verbatim: they quote the token
  that failed to parse or resolve, which for a mistyped DSN can be part of
  the password (observed in the final review of #1504 child 3a:
  `missing "=" after "SECRET"`, `failed to resolve host 'CRET@127.0.0.1'`
  for an unencoded `@`).

## Not covered

- Pins: the claims the store depends on are pinned by its tests; the
  documentation-only claims (TLS, password sources, `pg_dump`, vacuuming)
  carry none.
- Which query shape raised the `avg` error fixed in pgvector 0.8.7.
- A lost reply to `COMMIT` on a real connection: the store's test simulates
  it with a pool that drops the reply after committing, and does not provoke
  it on PostgreSQL.
- Crash and power-loss behaviour, replication and replica read-after-write:
  not probed; the store assumes one primary.
- pgvector before 0.7.0: refused by the store (`MIN_PGVECTOR`), not run.
- The probes above ran on PostgreSQL 16 and 17 only; on 15 and 18 the store's
  tests, run by the range workflow, are the evidence.
- Whether `conninfo_to_dict` ever connects (see its claim).
