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
status: deprecated
superseded_by: index.md
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

This page was split by the module that reads it (#1927). Its claims moved,
unchanged and with their dates, to four pages. Nothing was dropped: the
four pages together carry this page's 78 source, 3 unverified and 27 pin
markers. [observed: `scripts/check_references.py` before and after the
split, 2026-11-01]

| Section of this page | Now in |
| --- | --- |
| Extension and privileges | [PostgreSQL extension, schema and operations](postgresql-schema-and-operations.md) |
| The `vector` type, operators and aggregates | [pgvector search and stored rows](pgvector-search.md) |
| Schemas and `search_path` | [PostgreSQL extension, schema and operations](postgresql-schema-and-operations.md) |
| JSON and text | [pgvector search and stored rows](pgvector-search.md) |
| Transactions and snapshots | [PostgreSQL writes](postgresql-writes.md) |
| Locks | [PostgreSQL writes](postgresql-writes.md) |
| Durability | [PostgreSQL writes](postgresql-writes.md) |
| Errors | [PostgreSQL connections](postgresql-connections.md) |
| Client: cursors | [pgvector search and stored rows](pgvector-search.md) |
| Client: binary `COPY` | [PostgreSQL writes](postgresql-writes.md) |
| Client: pool and connection strings | [PostgreSQL connections](postgresql-connections.md) |
| Connection poolers | [PostgreSQL connections](postgresql-connections.md) |
| Connections and passwords | [PostgreSQL connections](postgresql-connections.md) |
| Backup and sizes | [PostgreSQL extension, schema and operations](postgresql-schema-and-operations.md) |
| Backup and sizes: `starts_with` | [pgvector search and stored rows](pgvector-search.md) |

The reference log's entries before 2026-11-01 link here; this page stays
until nothing does.
