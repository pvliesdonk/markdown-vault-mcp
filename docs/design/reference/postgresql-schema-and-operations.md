---
type: Reference
title: PostgreSQL extension, schema and operations behind the vector store
description: What the PostgreSQL vector store's setup checks and its operator guide rely on in PostgreSQL 17 and pgvector — who may install the extension, the supported range, schemas and search_path, schema privileges, pg_dump of one schema, vacuuming after DELETE and relation sizes.
subject_version: "PostgreSQL 17 documentation (CREATE EXTENSION, schemas, privileges, SET, pg_dump, vacuuming); PostgreSQL 15.0 release notes; observed on PostgreSQL 17.4 + pgvector 0.8.0 (tensorchord/vchord-suite:pg17-20250620) and PostgreSQL 16.10 + pgvector 0.8.1 (pgvector/pgvector:pg16); the range workflow's eight images (2026-10-22)"
valid_for: "PostgreSQL 15–18, pgvector 0.7–0.8.x (0.7.4 or later on 17, 0.8.7 or later on 18)"
generated:
  by: process:researching-references
  at: 2026-10-22T15:00:00+02:00
stale_after: 2027-04-22T15:00:00+02:00
verified:
  - by: process:researching-references
    at: 2026-10-22T15:00:00+02:00
status: stable
sources:
  - id: pg-create-extension
    title: CREATE EXTENSION (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/sql-createextension.html
    accessed: 2026-10-18
  - id: pg-admin-functions
    title: System Administration Functions (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/functions-admin.html
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
  - id: pg-dump
    title: pg_dump (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/app-pgdump.html
    accessed: 2026-10-18
  - id: pg-routine-vacuuming
    title: Routine Vacuuming (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/routine-vacuuming.html
    accessed: 2026-10-21
  - id: pg-autovacuum-config
    title: Automatic Vacuuming settings (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/runtime-config-autovacuum.html
    accessed: 2026-10-21
---

# PostgreSQL extension, schema and operations behind the vector store

This page records how PostgreSQL and the pgvector extension behave
where the PostgreSQL vector store (#1504) checks its database before
use, and where its operator guide tells an operator what to set up and
maintain: who may install the extension, which versions the store runs
on, how `search_path` resolves names, what a schema grants, and how a
schema is backed up and its space reclaimed. Claims marked
`[observed: probe N]` were reproduced on throwaway containers of both
images named in the frontmatter with the probe scripts listed under
"Probes".

Split from the former `postgresql-pgvector.md` (#1927); the claims are
unchanged, and so are their dates.

## Scope

- Covers: extension installation and privileges; the supported PostgreSQL
  and pgvector range; schemas, `search_path` and `SET LOCAL`; `pg_dump`
  of one schema; vacuuming after `DELETE`; relation sizes.
- Does not cover: pgvector's types and operators
  ([pgvector search](pgvector-search.md)); connection parameters
  ([PostgreSQL connections](postgresql-connections.md)).
- Depended on by: `src/markdown_vault_mcp/stores/_postgres.py` (the extension,
  version and schema checks, `search_path`) and `_postgres_tables.py`
  (relation sizes); `.github/workflows/postgres-range.yml`;
  `docs/design/design.md` § "The storage contract (#1766, #1503)"
  (decision 34); and the operator guide, `docs/use/postgresql.md`.

## Claims

### Extension and privileges

- `CREATE EXTENSION` needs the privileges to create the extension's objects, usually a superuser; only an extension marked `trusted` in its control file can be installed by a role with `CREATE` on the database. [source: pg-create-extension]
- pgvector's control file carries no `trusted = true` (it reads `comment`, `default_version`, `module_pathname`, `relocatable = true`), so installing it needs a superuser. [observed: probe "control" on pgvector 0.8.0 and 0.8.1] [pins: tests/test_stores_postgres_connection.py::test_a_database_without_the_extension_is_not_configured]
- An extension is loaded into the current database, its name is unique database-wide, and its objects live in a schema chosen at creation (`SCHEMA`, or the current creation schema). [source: pg-create-extension]
- The store's whole PostgreSQL suite passes on PostgreSQL 15 to 18 with pgvector 0.7.0 to 0.8.7 (on 17 from 0.7.4, the first 0.7 built for it; on 18 only 0.8.7, since 0.7 is not built for it), and on `tensorchord/vchord-suite:pg17-20260501`: the images the range workflow pins. [observed: `.github/workflows/postgres-range.yml`, and a local run of each of its eight images on 2026-10-22, the store's 376 PostgreSQL tests passing on each] [pins: tests/test_postgres_range_workflow.py::test_the_lowest_pgvector_tested_is_the_minimum_the_store_accepts, tests/test_postgres_range_workflow.py::test_the_guide_states_the_postgresql_majors_the_workflow_runs]
- `CREATE EXTENSION IF NOT EXISTS` checks only the name: "there is no guarantee that the existing extension is anything like the one that would have been created". [source: pg-create-extension]
- The installed version and schema are in `pg_extension.extversion` and `pg_extension.extnamespace`. [observed: probe "versions", `select extversion from pg_extension where extname='vector'` → `0.8.0` / `0.8.1`]

### Schemas and `search_path`

- A schema in `search_path` that does not exist, or lacks `USAGE` for the role, is "silently ignored". [source: pg-client-config] [observed: probe 8, `set search_path = no_such_schema, public` leaves `{public}`, and `pg_tables` places the new table in `public`] [pins: tests/test_stores_postgres_connection.py::test_a_missing_schema_is_not_configured_with_the_sql]
- `pg_catalog` is always searched, before the path unless the path names it. [source: pg-client-config]
- The first existing schema in the path is the default creation schema; unqualified names resolve to the first match along the path. [source: pg-schemas]
- Putting a schema in `search_path` "effectively trusts all users having `CREATE` privilege on that schema". [source: pg-schemas] (§ Secure Schema Usage Patterns)
- From PostgreSQL 15, new databases no longer grant `CREATE` on `public` to `PUBLIC`; upgraded clusters and restored dumps keep the old grant. [source: pg15-release]
- `CREATE SCHEMA name AUTHORIZATION role` creates a schema owned by `role`; by default `PUBLIC` has no privileges on a schema, and `CONNECT` and `TEMPORARY` on a database. [source: pg-schemas] [source: pg-privileges]
- `SET LOCAL` lasts until the end of the current transaction, committed or not; outside a transaction block it warns and has no effect. [source: pg-set]

### Backup and sizes

- `pg_dump` makes a consistent backup without blocking readers or writers. [source: pg-dump]
- `pg_dump -n` dumps the selected schemas and their objects but "makes no attempt to dump any other database objects that the selected schema(s) might depend upon", so such a dump may not restore into a clean database on its own; extensions are selected with `-e`. [source: pg-dump]
- An `UPDATE` or `DELETE` does not remove the old row version at once; `VACUUM` reclaims the space of row versions no transaction still needs. [source: pg-routine-vacuuming] (§ 24.1.2 Recovering Disk Space)
- Plain `VACUUM` marks that space reusable by new rows but does not return it to the operating system, "except in the special case where one or more pages at the end of a table become entirely free and an exclusive table lock can be easily obtained". [source: pg-routine-vacuuming] (§ 24.1.2)
- `VACUUM FULL` writes a new copy of the table without dead space, needs extra disk for that copy until it finishes, and takes `ACCESS EXCLUSIVE` on the table, so it cannot run alongside other use of it. [source: pg-routine-vacuuming] (§ 24.1.1, § 24.1.2)
- The autovacuum daemon vacuums a table once its updated or deleted tuples pass `autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor * reltuples`. [source: pg-routine-vacuuming] (§ 24.1.6 The Autovacuum Daemon) `autovacuum` is on by default; the threshold defaults to 50 tuples and the scale factor to 0.2, both overridable per table with storage parameters; `autovacuum_naptime` defaults to one minute. [source: pg-autovacuum-config]
- `pg_total_relation_size` is a table's disk space including its indexes and TOAST data. [source: pg-admin-functions] [observed: probe 12, 16384 bytes for a two-row table]

## Probes

Every probe started its own throwaway container and removed it afterwards,
on the images `tensorchord/vchord-suite:pg17-20250620` (PostgreSQL 17.4,
pgvector 0.8.0) and `pgvector/pgvector:pg16` (PostgreSQL 16.10, pgvector
0.8.1). Probes 8 and 12 ran in the container probes 1–12 shared on each
image. The two
images agreed on every probe.

- Probes 8 and 12: SQL through `psql` inside the container:
  `search_path` naming a missing schema, and `pg_total_relation_size`.
- Probes "control" and "versions": pgvector's control file and
  `pg_extension.extversion`, read inside each container.
- The range workflow, `.github/workflows/postgres-range.yml`: the store's
  PostgreSQL tests on each pinned image, through the test harness's
  `MVM_TEST_POSTGRES_IMAGE`; first run locally on 2026-10-22.

## Where this project departs from the subject

Each departure is decided in `docs/design/design.md` § "The storage contract
(#1766, #1503)", decision 34.

- The store does not rely on a role's `search_path`: it sets the path itself
  and checks that the vault schema exists in `pg_namespace`, because a missing
  schema in the path is silently ignored.

## Not covered

- Pins: the claims the store depends on are pinned by its tests; the
  documentation-only claims (`pg_dump`, vacuuming) carry none.
- pgvector before 0.7.0: refused by the store (`MIN_PGVECTOR`), not run.
- The probes above ran on PostgreSQL 16 and 17 only; on 15 and 18 the store's
  tests, run by the range workflow, are the evidence.
