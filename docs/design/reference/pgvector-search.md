---
type: Reference
title: pgvector and PostgreSQL behaviour behind vector search and stored rows
description: What the PostgreSQL vector store's search and row validation rely on in pgvector 0.8, PostgreSQL 17 and psycopg 3 — the vector type and its limits, distance operators and zero vectors, avg, jsonb and text limits, btree key size, server-side cursors and prefix matching.
subject_version: "pgvector 0.8.0 README and changelog to 0.8.7; PostgreSQL 17 documentation (JSON types, DECLARE, string functions); psycopg 3 docs (current as read 2026-10-18); observed on PostgreSQL 17.4 + pgvector 0.8.0 (tensorchord/vchord-suite:pg17-20250620) and PostgreSQL 16.10 + pgvector 0.8.1 (pgvector/pgvector:pg16), with psycopg 3.3.6 (libpq 18) and pgvector-python 0.5.1; psycopg 3.3.0 to 3.3.3, psycopg-pool 3.3.0 and pgvector-python 0.5.0 on Python 3.11 (2026-10-27)"
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
  - id: pg-json-types
    title: JSON Types (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/datatype-json.html
    accessed: 2026-10-19
  - id: pg-string-functions
    title: String Functions and Operators (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/functions-string.html
    accessed: 2026-10-18
  - id: pg-declare
    title: DECLARE (PostgreSQL 17)
    resource: https://www.postgresql.org/docs/17/sql-declare.html
    accessed: 2026-10-18
  - id: psycopg-news
    title: psycopg 3 release notes
    resource: https://www.psycopg.org/psycopg3/docs/news.html
    accessed: 2026-10-27
  - id: psycopg-cursors-api
    title: Cursor classes (psycopg 3 API)
    resource: https://www.psycopg.org/psycopg3/docs/api/cursors.html
    accessed: 2026-10-18
---

# pgvector and PostgreSQL behaviour behind vector search and stored rows

This page records how pgvector, PostgreSQL and psycopg 3 behave where
the PostgreSQL vector store (#1504) searches and validates rows: what
the `vector` type holds, how the distance operators treat zero vectors,
what `jsonb`, `text` and a btree index refuse, how the client streams
rows, and how a folder prefix matches. It leaves out ANN indexes, which
the store does not use. Claims marked `[observed: probe N]` were
reproduced on throwaway containers of both images named in the
frontmatter with the probe scripts listed under "Probes".

Split from the former `postgresql-pgvector.md` (#1927); the claims are
unchanged, and so are their dates.

## Scope

- Covers: the `vector` type, operators, aggregates and storage; `jsonb` and
  `text` limits and btree key size; server-side cursors; prefix matching.
- Does not cover: ANN indexes (`hnsw`, `ivfflat`) and their recall;
  `halfvec`/`sparsevec`/`bit`; binary `COPY`
  ([PostgreSQL writes](postgresql-writes.md)).
- Depended on by: `src/markdown_vault_mcp/stores/_postgres_search.py` (search,
  folder scopes, cursors) and `_postgres_rows.py` (which rows PostgreSQL
  can store); `docs/design/design.md` § "The storage contract (#1766,
  #1503)" (decision 34).

## Claims

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

### JSON and text

- `jsonb` rejects `\u0000`, "because that cannot be represented in PostgreSQL's `text` type"; a JSON string maps to `text`, where `\u0000` is disallowed. [source: pg-json-types] [pins: tests/test_stores_postgres_writes.py::test_rows_that_cannot_be_stored_change_nothing]
- `jsonb` keeps neither key order, whitespace nor duplicate keys (the last value of a duplicated key wins); `json` stores the input text exactly. [source: pg-json-types]
- `jsonb` rejects `NaN` and `Infinity` (JSON has no such numbers) and a lone surrogate escape; Python's `json.dumps` writes `NaN` unless told not to. [observed: #1504 child 5a's final review, `Token "NaN" is invalid`, `Unicode low surrogate must follow a high surrogate`] [pins: tests/test_stores_postgres_writes.py::test_metadata_postgresql_cannot_store_is_refused_before_writing]
- Numbers and keys do not all round-trip through `jsonb` as Python wrote them: `1e20` comes back an integer, `-0.0` as `0.0`, non-string keys as strings, tuples as lists. [observed: #1504 child 5a's final review] [pins: tests/test_stores_postgres_writes.py::test_ordinary_nested_metadata_is_stored]
- A btree index entry holds at most about 2,700 bytes (`index row size 3648 exceeds btree version 4 maximum 2704`). [observed: #1504 child 5a's final review, a 3,000-byte path] [pins: tests/test_stores_postgres_writes.py::test_a_key_too_long_for_postgresql_indexes_is_refused]

### Cursors

- `DECLARE` without `WITH HOLD` is usable only inside a transaction block, and every PostgreSQL cursor is insensitive. [source: pg-declare]
- A psycopg named (server-side) cursor iterates in batches of `itersize`, default 100. [source: psycopg-cursors-api] [pins: tests/test_stores_postgres_search.py::test_a_selective_predicate_reads_past_the_first_batch]
- psycopg 3.3.0 iterates a named cursor wrongly; 3.3.1's notes read "Fix iteration on server-side cursors (ticket #1226)". [source: psycopg-news] With 3.3.0 a store search with a `predicate` (offered by the store's contract; none of the server's searches pass one) returned none of the rows past the first batch, and the two search tests that read past it failed; with 3.3.1 every PostgreSQL test passes, and 3.3.2 and 3.3.3 pass the selective-predicate test. [observed: the PostgreSQL tests with psycopg pinned to 3.3.0 and 3.3.1, the selective-predicate test with 3.3.2 and 3.3.3, psycopg-pool 3.3.0 and pgvector-python 0.5.0, Python 3.11, `pgvector/pgvector:0.8.7-pg18`, and for 3.3.0 and 3.3.1 also `pgvector/pgvector:0.7.0-pg15`, 2026-10-27] The `postgres` extra therefore requires `psycopg[binary]>=3.3.1`. The range workflow's `floors` job runs the search test on the floor driver. [pins: tests/test_stores_postgres_search.py::test_a_selective_predicate_reads_past_the_first_batch, tests/test_postgres_range_workflow.py::test_the_floors_job_installs_exactly_the_postgres_extras_floors]
- On a non-autocommit connection a named cursor runs inside the implicit transaction; on an autocommit connection it fails with `NoActiveSqlTransaction: DECLARE CURSOR can only be used in transaction blocks`. [observed: probe 13]
- Ordered rows (`ORDER BY e <#> q, path`) stream through a named cursor inside a `REPEATABLE READ`, read-only transaction. [observed: probe 13]

### Prefix matching

- `starts_with(string, prefix)` and the `^@` operator test a prefix. [source: pg-string-functions] [observed: probe 7, `starts_with('a/b','a/')` true, `starts_with('ab/c','a/')` false, `starts_with('A/b','a/')` false] It is a plain prefix test: `_` and `%` match only themselves. [observed: #1504 child 6] [pins: tests/test_stores_postgres_search.py::test_folder_scopes_are_exact_prefixes]

## Probes

Every probe started its own throwaway container and removed it afterwards,
on the images `tensorchord/vchord-suite:pg17-20250620` (PostgreSQL 17.4,
pgvector 0.8.0) and `pgvector/pgvector:pg16` (PostgreSQL 16.10, pgvector
0.8.1). Probes 1–3 and 7 ran in the container probes 1–12 shared on each
image; probe 13 started its own. The two images agreed on every probe.

- Probes 1–3 and 7: SQL through `psql` inside the container: zero
  vectors, `avg`, mixed dimensions and `starts_with`.
- Probe 13: psycopg 3.3.6 with pgvector-python 0.5.1 (both pinned): `avg`
  over no rows, a named cursor in a `REPEATABLE READ` read-only
  transaction, and named cursors outside a transaction and on an
  autocommit connection.

## Where this project departs from the subject

Each departure is decided in `docs/design/design.md` § "The storage contract
(#1766, #1503)", decision 34.

- Search is exact; pgvector's ANN indexes are not used.
- Scores come from `<#>` over normalised rows, never `<=>`, because `<=>`
  returns `NaN` for a zero vector.
- Before any write, a row is refused with `ValueError` where the SQLite store would keep it: a path, folder or metadata string holding a `NUL` character or a lone surrogate, metadata holding `NaN` or infinity, or a path or folder over 2,000 bytes. PostgreSQL's `text`, `jsonb` and btree indexes cannot hold them, and refusing them up front keeps the document's old rows instead of a write the reconciler would retry forever.

## Not covered

- Which query shape raised the `avg` error fixed in pgvector 0.8.7.
- The probes above ran on PostgreSQL 16 and 17 only; on 15 and 18 the store's
  tests, run by the range workflow, are the evidence.
