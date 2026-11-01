---
type: Reference
title: PostgreSQL connections, errors and pooling behind the vector store
description: How PostgreSQL, libpq, psycopg 3 and PgBouncer report and shape a connection where the PostgreSQL vector store depends on them — SQLSTATE classes, connect-time failures and psycopg's password flags, message language, the connection pool and connection strings, pooling modes, connection parameters and password sources.
subject_version: "PostgreSQL 17 documentation (error codes, client defaults, libpq); psycopg 3 docs (current as read 2026-10-18); PgBouncer documentation (2026-10-21); pgvector-python README; observed on PostgreSQL 17.4 + pgvector 0.8.0 (tensorchord/vchord-suite:pg17-20250620) and PostgreSQL 16.10 + pgvector 0.8.1 (pgvector/pgvector:pg16) with psycopg 3.3.6 (libpq 18), and pgvector/pgvector:0.8.7-pg17 (2026-10-22)"
valid_for: "PostgreSQL 15–18, psycopg 3.3.x (3.3.1 or later)"
generated:
  by: process:researching-references
  at: 2026-10-22T15:00:00+02:00
stale_after: 2027-04-22T15:00:00+02:00
verified:
  - by: process:researching-references
    at: 2026-10-22T15:00:00+02:00
status: stable
sources:
  - id: pg-admin-functions
    title: System Administration Functions (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/functions-admin.html
    accessed: 2026-10-18
  - id: pg-isolation
    title: Transaction Isolation (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/transaction-iso.html
    accessed: 2026-10-18
  - id: pg-client-config
    title: Client Connection Defaults (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/runtime-config-client.html
    accessed: 2026-10-18
  - id: pg-errcodes
    title: PostgreSQL Error Codes (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/errcodes-appendix.html
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
  - id: psycopg-transactions
    title: Transactions management (psycopg 3)
    resource: https://www.psycopg.org/psycopg3/docs/basic/transactions.html
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
---

# PostgreSQL connections, errors and pooling behind the vector store

This page records how PostgreSQL, libpq, psycopg 3 and PgBouncer behave
where the PostgreSQL vector store (#1504) connects: how failures are
reported, what psycopg can tell about a failed connection, how the pool
hands out connections, which pooling modes keep the store's session
state, and which connection parameters and password sources libpq
reads. Claims marked `[observed: probe N]` were reproduced on throwaway
containers with the probe scripts listed under "Probes".

Split from the former `postgresql-pgvector.md` (#1927); the claims are
unchanged, and so are their dates.

## Scope

- Covers: SQLSTATEs and how psycopg reports connect-time failures; message
  language; the connection pool and connection-string parsing; libpq
  connection parameters and password sources; PgBouncer's pooling modes.
- Does not cover: what a transaction, lock or commit guarantees
  ([PostgreSQL writes](postgresql-writes.md)); the async psycopg API.
- Depended on by: `src/markdown_vault_mcp/stores/_postgres.py` (connecting,
  the pool, failure classification) and `postgres_vectors.py` (error
  handling); `docs/design/design.md` § "The storage contract (#1766,
  #1503)" (the PostgreSQL store paragraphs and decision 34); and the
  operator guide, `docs/use/postgresql.md`.

## Claims

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

### Client: pool and connection strings

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

## Probes

Every probe started its own throwaway container and removed it afterwards,
on the images `tensorchord/vchord-suite:pg17-20250620` (PostgreSQL 17.4,
pgvector 0.8.0) and `pgvector/pgvector:pg16` (PostgreSQL 16.10, pgvector
0.8.1). Probe 10 ran in the container probes 1–12 shared on each
image; probe 10e started its own. The two images agreed on every probe run on
both; probe 10d ran once, on the PostgreSQL 17.4 image.

- Probe 10: SQL through `psql` inside the container: a query on a schema
  the role has no `USAGE` on.
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

## Where this project departs from the subject

Each departure is decided in `docs/design/design.md` § "The storage contract
(#1766, #1503)", decision 34.

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
  documentation-only claims (TLS, password sources) carry none.
- A lost reply to `COMMIT` on a real connection: the store's test simulates
  it with a pool that drops the reply after committing, and does not provoke
  it on PostgreSQL.
- The probes above ran on PostgreSQL 16 and 17 only; on 15 and 18 the store's
  tests, run by the range workflow, are the evidence (its claim is in
  [PostgreSQL extension, schema and operations](postgresql-schema-and-operations.md)).
- The psycopg 3.3.1 floor in `valid_for`: its evidence is the cursor claim
  in [pgvector search](pgvector-search.md).
- Whether `conninfo_to_dict` ever connects (see its claim).
