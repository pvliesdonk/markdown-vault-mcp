---
type: Reference
title: PostgreSQL and pgvector behaviour behind the vector store
description: What the PostgreSQL vector store relies on in PostgreSQL 17, pgvector 0.8 and psycopg 3 — extension privileges, zero-vector scoring, schemas and search_path, snapshots, DELETE against TRUNCATE, advisory locks, durability settings, error codes and connect-time failures, cursors, COPY, pooling, connection parameters and schema backups.
subject_version: "PostgreSQL 17 documentation; pgvector 0.8.0 README and changelog to 0.8.7; psycopg 3 docs (current as read 2026-10-18); observed on PostgreSQL 17.4 + pgvector 0.8.0 (tensorchord/vchord-suite:pg17-20250620) and PostgreSQL 16.10 + pgvector 0.8.1 (pgvector/pgvector:pg16), with psycopg 3.3.6 (libpq 18) and pgvector-python 0.5.1"
valid_for: "PostgreSQL 15–18, pgvector 0.8.x, psycopg 3.3.x"
generated:
  by: process:researching-references
  at: 2026-10-18T10:01:06+02:00
stale_after: 2027-04-18T10:01:06+02:00
verified:
  - by: process:researching-references
    at: 2026-10-18T10:01:06+02:00
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
  password sources; `pg_dump` of one schema; relation sizes.
- Does not cover: ANN indexes (`hnsw`, `ivfflat`) and their recall;
  `halfvec`/`sparsevec`/`bit`; streaming replication and replica
  read-after-write; crash and power-loss recovery; the async psycopg API.
- Depended on by: `src/markdown_vault_mcp/stores/postgres_vectors.py`
  (arrives in #1504's third child), `docs/design/design.md` § "The storage
  contract (#1766, #1503)" (the PostgreSQL store paragraphs and decision 34).

## Claims

### Extension and privileges

- `CREATE EXTENSION` needs the privileges to create the extension's objects, usually a superuser; only an extension marked `trusted` in its control file can be installed by a role with `CREATE` on the database. [source: pg-create-extension]
- pgvector's control file carries no `trusted = true` (it reads `comment`, `default_version`, `module_pathname`, `relocatable = true`), so installing it needs a superuser. [observed: probe "control" on pgvector 0.8.0 and 0.8.1] [pins: tests/test_stores_postgres_connection.py::test_a_database_without_the_extension_is_not_configured]
- An extension is loaded into the current database, its name is unique database-wide, and its objects live in a schema chosen at creation (`SCHEMA`, or the current creation schema). [source: pg-create-extension]
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
- `avg` over no rows returns `NULL` on 0.8.0 and 0.8.1. [observed: probe 13] The 0.8.7 changelog entry "Fixed error with `avg` aggregate when no matching rows" names a case these probes did not hit. [source: pgvector-changelog] (0.8.7) [unverified] Which query shape raised that error is not established; the pgvector commit or issue behind the 0.8.7 entry would settle it. A store that returns `[]` for a path with no rows before calling `avg` does not depend on it.
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

### Transactions and snapshots

- A `REPEATABLE READ` transaction sees a snapshot taken at its first non-transaction-control statement, the same for every later statement; `READ COMMITTED` takes one per statement. [source: pg-isolation]
- Read-only transactions never have serialization conflicts. [source: pg-isolation]
- DDL is transactional: a `CREATE TABLE` inside a rolled-back transaction leaves no table. [observed: probe 9]
- While a `DELETE` of every row is uncommitted, a `REPEATABLE READ` reader counts the old rows. [observed: probe 5, count `3`]
- `DELETE` takes `ROW EXCLUSIVE`, which does not conflict with the `ACCESS SHARE` a `SELECT` takes. [source: pg-explicit-locking]
- `TRUNCATE` takes `ACCESS EXCLUSIVE`, which conflicts with every mode, so a reader waits until it commits. [source: pg-explicit-locking] [observed: probe 5, the reader fails with `canceling statement due to lock timeout`]
- psycopg starts a transaction implicitly on the first statement and needs `commit()`; `isolation_level` and `read_only` can be changed only while no transaction is open on the connection. [source: psycopg-transactions]

### Locks

- `SELECT … FOR UPDATE` blocks other transactions' `UPDATE`, `DELETE` and `FOR UPDATE`/`FOR SHARE` of those rows until it ends, and does not block plain reads. [source: pg-explicit-locking]
- In `REPEATABLE READ`, `FOR UPDATE` on a row changed since the transaction began raises an error. [source: pg-explicit-locking]
- A session-level advisory lock is held until released or the session ends, survives a rollback, and is counted per acquisition. [source: pg-explicit-locking]
- Advisory locks are identified by one 64-bit key or two 32-bit keys, and "these two key spaces do not overlap". [source: pg-admin-functions]
- Advisory locks are application-defined: "the system does not enforce their use". [source: pg-explicit-locking] Any session in the same database that takes the same key contends with the holder, whatever application it belongs to; a key chosen by one application cannot rule out a collision with another's. [observed: probe 14, a second session in the same database gets `f`]
- An advisory-lock key is scoped to one database: while one session holds key 42 in database `postgres`, a session in database `other` takes key 42 at once. [observed: probe 14, `t` in the other database]
- `pg_try_advisory_lock` takes the lock at once and returns `true`, or returns `false` without waiting. [source: pg-admin-functions]
- A `bigint` key of the form `(0x4D564D31 << 32) | oid` with an OID above 2^31 is accepted; a second session's `pg_try_advisory_lock` returns `f`; the holder's `application_name` is found by joining `pg_locks` (`locktype = 'advisory'`, `granted`) to `pg_stat_activity` on `pid`; the lock is free again once the holding session ends. [observed: probe 4]
- `lock_timeout` aborts a statement whose wait for a lock exceeds it, the limit applying "separately to each lock acquisition attempt"; `statement_timeout` aborts any statement that runs longer, measured per statement; zero disables either. [source: pg-client-config]

### Durability

- Every non-`off` value of `synchronous_commit` waits for the local WAL flush; `off` may lose recent commits after a crash but leaves the database consistent. [source: pg-wal-config]
- `remote_apply`, `on` and `remote_write` differ only in what they wait for from synchronous standbys; without `synchronous_standby_names` they equal `on`. [source: pg-wal-config]
- The setting in effect at commit decides a transaction's behaviour, so `SET LOCAL synchronous_commit` changes it for one transaction. [source: pg-wal-config]
- `SET LOCAL synchronous_commit = on` under a configured `remote_apply` shows `on` inside the transaction and `remote_apply` after it: it lowers the level for that transaction. [observed: probe 6]
- `fsync = off` can cause unrecoverable corruption after a crash or power failure, and is set only in the configuration file or on the command line. [source: pg-wal-config]
- A role without special privileges can read `fsync` and `synchronous_commit` with `SHOW`. [observed: probe 11]

### Errors

- Class `08` is connection exception (`08000`, `08003`, `08006`, `08001`, `08004`, `08007 transaction_resolution_unknown`, `08P01`); class `28` invalid authorization (`28000`, `28P01`); class `53` insufficient resources; class `57` operator intervention (including `57014 query_canceled`, `57P01 admin_shutdown`); class `40` transaction rollback (including `40003 statement_completion_unknown`); `42501` insufficient privilege; `42P01` undefined table; `3F000` invalid schema name; `25006` read-only transaction; `55P03` lock not available. [source: pg-errcodes]
- The first two characters of a SQLSTATE are its class, and an application that does not know a code "might still be able to infer what to do from the error class". [source: pg-errcodes]
- A query on a schema without `USAGE` fails with `42501: permission denied for schema`. [observed: probe 10]
- psycopg exposes `sqlstate` only for errors received from PostgreSQL; client-side failures such as a failed connection usually have none. [source: psycopg-errors]
- Every connect-time failure probed raised `psycopg.OperationalError` with `sqlstate=None`: wrong password, unknown role, missing database, no `CONNECT` privilege, no password supplied, refused port. [observed: probe 10e, both images, `scram-sha-256` and `trust`] An unresolvable host raised `OperationalError` and a connect timeout its subclass `ConnectionTimeout`, both with `sqlstate=None` and `pgconn=None`. [observed: probe 10d, one run against the PostgreSQL 17.4 image; neither case reaches PostgreSQL]
- After a failed connection, `Error.pgconn` is a closed connection; its `needs_password`, `used_password`, `server_version`, `status` and `error_message` can be read, `parameter_status()` raises `the connection is closed`, and `server_version` reads `0` whether or not PostgreSQL answered. [source: psycopg-errors] [observed: probe 10e, both images]
- `PQconnectionNeedsPassword` is true when "the connection authentication method required a password, but none was available"; `PQconnectionUsedPassword` is true when "the connection authentication method used a password", which detects "whether the server demanded a password". [source: libpq-status]
- Under `scram-sha-256`: a wrong password, an unknown role, a missing database and a missing `CONNECT` privilege give `needs_password=False`, `used_password=True`; no password supplied gives `needs_password=True` and also `used_password=True`; a refused port gives both `False`. [observed: probe 10e, both images] So `used_password` alone does not mean a password was sent: `needs_password` must be tested first. [pins: tests/test_stores_postgres_connection.py::test_connect_reports_a_wrong_password_as_refused, tests/test_stores_postgres_connection.py::test_connect_reports_a_missing_database_as_refused, tests/test_stores_postgres_connection.py::test_connect_reports_no_password_as_no_password, tests/test_stores_postgres_connection.py::test_needs_password_is_tested_before_used_password]
- Under `trust`: a wrong password and no password both connect; an unknown role, a missing database, a missing `CONNECT` privilege and a refused port all give both flags `False`. [observed: probe 10e, both images]
- PostgreSQL displays its messages in the language `lc_messages` selects, falling back to English where no translation exists. [source: pg-client-config] A message's wording is therefore not a stable value to match on.
- What a client should conclude when the connection drops after `COMMIT` was sent is not documented by the transaction-isolation page or by psycopg. [source: pg-isolation] [source: psycopg-transactions] [unverified] That the commit may or may not have landed follows from PostgreSQL naming the condition (`08007 transaction_resolution_unknown`); the store's test of a lost commit reply (#1504 child 5) would pin the behaviour the store relies on.

### Client: cursors, COPY, pool, connection strings

- `DECLARE` without `WITH HOLD` is usable only inside a transaction block, and every PostgreSQL cursor is insensitive. [source: pg-declare]
- A psycopg named (server-side) cursor iterates in batches of `itersize`, default 100. [source: psycopg-cursors-api]
- On a non-autocommit connection a named cursor runs inside the implicit transaction; on an autocommit connection it fails with `NoActiveSqlTransaction: DECLARE CURSOR can only be used in transaction blocks`. [observed: probe 13]
- Ordered rows (`ORDER BY e <#> q, path`) stream through a named cursor inside a `REPEATABLE READ`, read-only transaction. [observed: probe 13]
- Binary `COPY … FROM STDIN` with `write_row()` needs a binary dumper for every type and applies no casts; `Copy.set_types()` names the types. [source: psycopg-copy]
- After `register_vector(conn)`, binary `COPY` with `set_types(["text", "int4", "vector", "jsonb"])` loads numpy float32 vectors, and a selected `vector` comes back as `pgvector.Vector`, whose `to_numpy()` gives float32. [observed: probe 13, pgvector-python 0.5.1]
- pgvector-python registers its types per connection; with a pool it does so in the `configure` callback. [source: pgvector-python]
- `ConnectionPool` keeps between `min_size` and `max_size` connections; a `check` callback runs before a connection is handed out and a failing check gets a new connection; `ConnectionPool.check_connection` is a ready-made check; `configure` runs on each new connection. [source: psycopg-pool]
- A pool commits a returned connection's open transaction on a normal exit, rolls it back on an exception, and discards a broken one; a client blocks until `PoolTimeout` when PostgreSQL is unreachable. [source: psycopg-pool]
- The pool's default `open=True` may change, so it should be opened explicitly. [source: psycopg-pool]
- `conninfo_to_dict` parses a URI or key/value string into parameters and raises `ProgrammingError` for an invalid one. [source: psycopg-conninfo] [unverified] That it never connects is not stated on the page; the function's name and examples suggest it, and a test that parses a DSN for an unreachable host would settle it.

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
- `pg_total_relation_size` is a table's disk space including its indexes and TOAST data. [source: pg-admin-functions] [observed: probe 12, 16384 bytes for a two-row table]
- `starts_with(string, prefix)` and the `^@` operator test a prefix. [source: pg-string-functions] [observed: probe 7, `starts_with('a/b','a/')` true, `starts_with('ab/c','a/')` false, `starts_with('A/b','a/')` false]

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
- `synchronous_commit` is raised to `on` from any weaker value (`off`,
  `local`, `remote_write`) and left alone at `on` or `remote_apply`, because
  `SET LOCAL … = on` would lower a configured `remote_apply`.
- Connect-time failures are told apart only by psycopg's documented
  attributes (`ConnectionTimeout`, then `Error.pgconn.needs_password`, then
  `used_password`), never by message text, whose language follows
  `lc_messages`.

## Not covered

- Pins: one claim is pinned by the test harness (#1504 child 2); the
  store's children of #1504 (open, lease, publishing, searching) add the
  rest as their tests land.
- Which query shape raised the `avg` error fixed in pgvector 0.8.7.
- A lost reply to `COMMIT`: not probed here; #1504 child 5's test settles
  what the store sees.
- Crash and power-loss behaviour, replication and replica read-after-write:
  not probed; the store assumes one primary.
- PostgreSQL 15 and 18, and pgvector 0.7: not probed; the range workflow of
  #1504 child 10 decides the supported range.
- Whether `conninfo_to_dict` ever connects (see its claim).
