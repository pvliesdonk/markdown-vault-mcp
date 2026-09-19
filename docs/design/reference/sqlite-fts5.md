---
type: Reference
title: SQLite FTS5 delete cost and shadow-table architecture
description: Why a content-carrying FTS5 table full-scans on non-rowid deletes, and what rowid-based access avoids.
subject_version: "SQLite FTS5 documentation, sqlite.org (unversioned page; current as read 2026-09-19); observed on SQLite 3.53.4"
valid_for: "SQLite FTS5 as shipped in Python's sqlite3 stdlib module"
generated:
  by: process:researching-references
  at: 2026-09-19
stale_after: 2027-03-19
status: stable
sources:
  - id: fts5-docs
    title: SQLite FTS5 Extension
    resource: https://sqlite.org/fts5.html
    accessed: 2026-09-19
  - id: vtab-docs
    title: The Virtual Table Mechanism Of SQLite
    resource: https://sqlite.org/vtab.html
    accessed: 2026-09-19
---

# SQLite FTS5 delete cost and shadow-table architecture

## Scope

`fts_index.py`'s `notes_fts` table depends on FTS5's content-carrying
default, its rowid handling, and its virtual-table query-plan behaviour for
ordinary (non-`MATCH`) column filters. Full-text ranking, tokenizers, and
the `automerge`/`optimize` maintenance commands are out of scope except
where they bear on delete cost.

## Claims

- By default, an FTS5 table is **content-carrying**: "when a row is
  inserted into an FTS5 table, in addition to building the index, FTS5
  makes a copy of the original row content" — every ordinary column
  (`path`, `title`, `folder`, `heading`, `content`, `summary` in
  `notes_fts`) is stored verbatim in a shadow table, not just tokenized
  (§4.4 External Content and Contentless Tables). [source: fts5-docs]

- The `'delete'` special INSERT command — the documented per-row way to
  remove a single row's index entries without touching every other row —
  "is only available with external content and contentless tables." A
  content-carrying table (`notes_fts`'s configuration, and the only one
  this project uses) has no access to it (§6.3 The 'delete' Command). [source: fts5-docs]

- SQLite picks a virtual table's access path via the module's `xBestIndex`
  callback: "SQLite uses the xBestIndex method of a virtual table module to
  determine the best way to access the virtual table," trying multiple
  constraint combinations and selecting the one that "appears to give the
  best performance" (§2.3 The xBestIndex Method). [source: vtab-docs]

- FTS5's `xBestIndex` implementation recognizes `rowid` equality and
  `MATCH` constraints as efficient access paths, but not equality on an
  ordinary column: querying `notes_fts` by `path` with no `MATCH` plans as
  a full virtual-table scan, confirmed live against this project's schema —
  `EXPLAIN QUERY PLAN SELECT rowid FROM notes_fts WHERE path = ?` returns
  `SCAN notes_fts VIRTUAL TABLE INDEX 0:` (bare — no idxStr, no constraint
  accepted), while `... WHERE rowid IN (...)` returns
  `SCAN notes_fts VIRTUAL TABLE INDEX 0:=` (the `=` idxStr signals FTS5
  accepted a rowid-equality constraint). The `:=` suffix — not the absence
  of the `SCAN ... INDEX 0:` prefix, which is always present for a virtual
  table — is what the regression test actually keys on. FTS5's own
  documentation does not state this outright — §4.4.3's `content_rowid`
  join implies rowid access is the fast path, but the page never contrasts
  it with ordinary-column filters. [observed: EXPLAIN QUERY PLAN against
  notes_fts on SQLite 3.53.4, reproduced in
  tests/test_fts_index.py::TestDelete::test_delete_does_not_full_scan_notes_fts]
  [pins: tests/test_fts_index.py::TestDelete::test_delete_does_not_full_scan_notes_fts]

- Consequently, deleting a document from a content-carrying `notes_fts` by
  an ordinary-column filter costs one full read of the shadow content table
  per call — O(N × table_size) for a batch of N changed documents, since
  every upsert and every delete calls the same delete path (#1535). The fix
  keys the delete off `rowid` through a `document_id → fts_rowid` bridge
  table (`notes_fts_rowid_map`, an ordinary `WITHOUT ROWID` SQLite table,
  not FTS5) rather than the `rowid = documents.id` approach a naive reading
  of the 'delete' command's restriction might suggest, because `notes_fts`
  holds one row per **chunk**, not one row per document, so `documents.id`
  cannot serve as the rowid directly.
  [pins: tests/test_fts_index.py::TestDelete::test_delete_removes_all_chunks_leaves_other_documents]

- Not covered: the algorithmic complexity of an FTS5 rowid lookup itself
  (e.g. whether it is O(log n) against the internal shadow tables) is not
  stated by the documentation and was not independently benchmarked here;
  the claim this reference and the fix rely on is only "not a full scan,"
  which the query-plan test pins directly. [unverified]
