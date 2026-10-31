# The measured operating envelope (#1899)

This page records what the assembled server costs on each embedding store,
measured on 28 October 2026 by the envelope harness (`scripts/envelope/`,
#1895; method in [design.md](design.md#measuring-the-operating-envelope-1506)).
It is evidence for #1900 (the default store) and #1901 (the operator docs).
It decides neither: #1900 chose the SQLite store, searched streamed, and
adopted none of the proposed numbers as a budget.

The numbers proposed on #1506 for each figure were derived from #1502's
search-only prototype with headroom. #1506's refinement comment calls them
agreed budgets; the account holder has since said they are proposals to be
validated, not limits ([correction on #1506](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1506#issuecomment-6469508664)).
So this page reports the measurements first, then asks of each proposed
number whether the measurements support it.

Units are decimal MB (10^6 bytes); the harness's own tables print MiB. Each figure gives both runs, run 1 / run 2, where they
differ.

## Runs

- **Command**, at commit `d77ec72d` with a clean tree, once per size:

  ```bash
  MVM_TEST_POSTGRES_IMAGE=tensorchord/vchord-suite:pg17-20260501 \
    uv run --extra postgres python -m scripts.envelope --size SIZE \
    --backends numpy,sqlite,postgres --out DIR
  ```

  The defaults applied: seed 1506, 1,024 dimensions, 50 timed queries per
  kind after 5 discarded, 200 edits, 64 texts per provider call.
- **Sizes**: deployed, 3,252 notes and 22,764 chunks; growth, 32,520 notes
  and 227,640 chunks.
- **Stores**: numpy (resident), sqlite (streamed), postgres (streamed).
  PostgreSQL 17.9 with pgvector 0.8.2 ran in a throwaway container the
  harness started on the same host, reached over loopback.
- **Host**: Intel i7-11700K (16 threads), 131.5 GB of memory as the kernel
  reports it, ext4 (which `stat -f` reports as `ext2/ext3`), Linux 7.2.9,
  Python 3.14.8, SQLite 3.53.1, numpy 2.4.6, psycopg 3.3.6.
- **Two runs** of the whole matrix, back to back. As the operator observed
  them (the JSON carries no timestamps): run 1 from 11:27 to 12:05 UTC, run 2
  from 12:08 to 12:45 UTC, and run 1 had three idle PostgreSQL containers,
  left behind by earlier test runs, beside it for everything except its
  growth PostgreSQL run. They were removed before run 2. Each run's JSON and tables
  are in [`envelope-runs/`](envelope-runs/).

## Measurements

### Memory

| | Deployed | Growth |
|---|---|---|
| **Memory warm searches add** (peak RSS during the warm searches of every kind, above the keyword-only baseline) | | |
| numpy | 165 / 205 | 2,093 / 2,060 |
| sqlite | 20 | 25 |
| postgres (the vault process) | 15 | 20 |
| **Process anonymous memory after the searches** | | |
| numpy | 247 | 1,330 / 1,331 |
| sqlite | 102 | 106 |
| postgres | 111 | 117 |
| **PostgreSQL service memory at rest** (container) | 534 / 531 | 580 |
| **Cold-build peak RSS** (vault open to close) | | |
| numpy | 471 | 3,569 |
| sqlite | 359 | 2,441 |
| postgres | 464 / 471 | 2,972 / 3,044 |

All figures are MB.

### Search latency, p95 (p50 in brackets)

| | Deployed | Growth |
|---|---|---|
| **Semantic** | | |
| numpy | 45 / 39 (21 / 17) | 107 / 81 (71 / 66) |
| sqlite | 159 / 140 (117 / 104) | 1,035 / 1,000 (866 / 854) |
| postgres | 118 / 118 (99 / 98) | 526 / 514 (465 / 451) |
| **Filtered** (semantic in the filter folder) | | |
| numpy | 65 / 32 | 102 / 96 |
| sqlite | 38 | 201 / 199 |
| postgres | 41 / 42 | 137 / 152 |
| **Hybrid** | | |
| numpy | 42 / 37 | 115 / 102 |
| sqlite | 155 / 144 | 1,072 / 987 |
| postgres | 132 / 123 | 546 / 600 |
| **Similar notes** | | |
| numpy | 82 / 53 | 330 / 312 |
| sqlite | 126 / 122 | 1,055 / 1,063 |
| postgres | 112 / 105 | 488 / 486 |
| **Cold first semantic search** | | |
| numpy | 297 / 318 | 2,826 / 2,722 |
| sqlite | 313 / 331 | 2,840 / 2,919 |
| postgres | 241 / 263 | 669 / 944 |

All figures are ms. Every cold search's files were confirmed out of page
cache. PostgreSQL's service was restarted for it, but the host's page cache
was kept, so its cold figure is warmer than the local stores'.

### One-note edits (200 per run; the note's vectors are 28,672 bytes)

| | Deployed | Growth |
|---|---|---|
| numpy, bytes written per edit (`wchar`), median | 105.9 MB | 1,057.8 MB |
| sqlite, store growth per edit, max (median 0) | 78,264 B | 78,264 B |
| sqlite, bytes written per edit (`wchar`, with the keyword index), median | 0.27 MB | 0.27 MB |
| postgres, WAL per edit, max (cluster-wide) | 139,992 / 145,280 B | 126,632 / 126,576 B |
| texts embedded per edit, max (every store) | 1 | 1 |

### Restart and recovery

| | Deployed | Growth |
|---|---|---|
| **Until the first semantic search returns** | | |
| numpy | 1.8 / 1.9 s | 11.0 / 10.7 s |
| sqlite | 2.3 s | 13.6 s |
| postgres | 10.0 / 10.1 s | 89.2 / 81.1 s |
| **Texts embedded on a clean restart** (every store) | 0 | 0 |
| **Texts embedded after a kill, beyond the edit in progress** (every store) | 0 | 0 |
| **Store moved aside** (every store) | no | no |

### Storage (before the edits)

| | Deployed | Growth |
|---|---|---|
| numpy | 105.7 MB | 1,057.6 MB |
| sqlite | 110.2 MB | 1,101.7 MB |
| postgres (vectors' schema) | 143.7 MB | 1,433.4 MB |

These are the status's `stored_bytes` after the warm searches, before the
edits. After the 200 edits, PostgreSQL's schema was 152.7 MB and 1,446.2 MB
(the service table in `envelope-runs/`).

## The proposed numbers against the measurements

| Proposed on #1506 | What the measurements show |
|---|---|
| Memory semantic search adds: ≤ 150 MB at both sizes | A flat figure fits only the streamed stores, and they use far less (15–25 MB at both sizes). numpy's grows with the vault: 165–205 MB deployed, about 2.1 GB at growth, so it scales with chunks × dimensions. About 0.85 GB of the growth figure was a copy of the matrix while the store opened, removed in #1936; growth now adds 1.25 GB ([after #1936](#after-1936-the-numpy-stores-memory)). |
| Unfiltered search p95: ≤ 100 ms deployed, ≤ 1 s growth | Semantic, hybrid and similar-note search are all unfiltered. Deployed, only numpy is under 100 ms for all three (at most 82 ms). The streamed stores' p95 is 105–159 ms across the three, and their semantic median 98–117 ms. #1502's prototype took about 39 ms per search; it scanned vectors without the server's metadata and result work. At growth, sqlite's semantic and hybrid p95 sit at the 1 s line (1,035 / 1,000 and 1,072 / 987 ms) and its similar-note p95 is above it in both runs (1,055 / 1,063 ms). postgres is at 0.49–0.60 s for all three, and numpy's similar-note search at 0.31–0.33 s. |
| Filtered p95 below the unfiltered figure | It holds for sqlite and postgres at both sizes, by a wide margin. For numpy it held in one run of each size and not the other (deployed 65 vs 45 ms, then 32 vs 39 ms; growth 102 vs 107 ms, then 96 vs 81 ms). Resident filtered search is about as fast as unfiltered, not reliably faster. |
| A one-note edit writes on the order of that note's vectors, never the whole store | postgres writes up to 145 KB of WAL, about five times the note's 29 KB of vectors. sqlite's store file grows by at most 78 KB (median 0), since pages are rewritten in place. The bytes sqlite's process writes, keyword index included, are 0.27 MB median (about nine times the note's vectors) and at most 3.7 MB deployed and 2.5 MB at growth. Neither rewrites its store. numpy rewrites its whole store on every edit: 106 MB deployed, 1.06 GB at growth. The sidecar is written as one file, so this follows from its design. |
| Restart and recovery: no unchanged note is embedded again | Holds on every store at both sizes, in both runs. |
| Whole-process memory and time until search works: measured, no number yet | Measured above. Two findings. PostgreSQL's restart takes 4–6 times as long as a local store's at deployed size (10 s) and 6–8 times at growth (81–89 s; #1937). Every store's cold build peaks at 2.4–3.6 GB of resident memory at growth, 21 to 34 times the 106–117 MB of anonymous memory a streamed store's process holds once it serves (#1938). |

## What the status reports, against the measurements

`embeddings_status` was read after the warm searches on every run (#1935).

- `stored_bytes` equals the measured store size on every store, size and run.
  For numpy and sqlite the measurement is an independent sum of the store's
  files. For postgres it is the same `pg_total_relation_size` the status
  uses, so the match shows only that every relation is counted.
- `resident_vector_bytes` is the vector matrix: 93.2 MB and 932.4 MB for
  numpy, 0 for the streamed stores. That is what it is documented to be. The
  memory numpy's search added was about twice it, and the operator docs
  already say the figure leaves out the server's other memory;
  [after #1936](#after-1936-the-numpy-stores-memory) says what that memory is.
- `search_mode` matches the store in every run. `search_workspace_bytes` is
  1 MiB for sqlite and empty for the others, as configured.

No status figure disagrees with its measurement, so #1899 needs no status
correction.

## After #1936: the numpy store's memory

#1936 asked what the memory numpy's search adds holds beyond its matrix. The
harness's "adds" figure covers opening the store, since the first semantic
search is what opens it, and that is where the peak was.

- **The peak was a copy.** Opening the store copied the matrix `np.load` had
  just returned, so the open briefly held two matrices. #1936 freezes the
  loaded matrix in place instead. Measured directly on a synthetic store of
  22,764 rows at 1,024 dimensions, the open's peak fell from 203 MB to 115 MB
  above the process before it; at 100,000 rows, from 895 MB to 505 MB.
- **Searching adds little.** At 100,000 rows, five semantic and five
  similar-note searches after the open added about 35 MB.
- **What stays is the matrix and the rows.** Each row's metadata, which
  includes the chunk's text, is held beside the matrix. At growth the
  process holds 1,331 MB once searches settle: the 932 MB matrix, about
  106 MB a streamed store's process also holds, and about 290 MB of rows.

A third run of the harness, numpy only, after the fix (31 October 2026, the
growth run at commit `1d51e7a1`; the deployed run at `ec328910` with the
fix uncommitted in the tree), in decimal MB:

| | Deployed | Growth |
|---|---|---|
| Memory warm searches add | 162 (runs 1 and 2: 165 / 205) | 1,247 (runs 1 and 2: 2,093 / 2,060) |
| Process anonymous memory after the searches | 248 (247) | 1,331 (1,330 / 1,331) |

At deployed size the harness's figure barely moves, though the direct
measurement above shows the copy there too. The harness samples memory at an
interval, and a copy of a 93 MB matrix lasts too short a time for it to be
seen reliably, which is also why runs 1 and 2 differ there. The run wrote to a
memory-backed folder, so its cold latencies are not cold and are not used
here. Its JSON and tables are `envelope-runs/run3-numpy-*`.

`embeddings_status` is unchanged: `resident_vector_bytes` is still the
matrix, and the operator docs still say it leaves the rows out.

## Limits

- One host, two runs. A p95 is one figure from 50 queries, so a difference of
  a few percent between runs (sqlite growth, 1,035 and 1,000 ms) is within
  what a third run could move.
- The vectors come from a stand-in provider with no network calls. Build times
  (7–10 s deployed, 80–107 s growth) leave out the real provider's cost, which
  dominates a real build.
- PostgreSQL ran on loopback in a container on the same host. A database
  across a network adds a round trip to every query and every restart check.
- Restart memory is not measured. The cold-build peak stands in for boot
  memory, since the boot `reindex` reads every note through the same scanner.
- Cold latency is cold for the vault's files. PostgreSQL's was measured with
  the host page cache kept.
- The synthetic vault is uniform: six sections a note, a 1,000-word
  vocabulary. Real vaults vary note size and folder shape.
- Not covered: concurrent clients, a vault larger than the growth size, other
  vector widths, other filesystems, and other PostgreSQL images in the
  supported range (CI runs the tests on them, not the measurements).

## What follows

- **#1900** chose the SQLite store, searched streamed, as the default, and
  adopted none of the proposed numbers; the measured envelope is the
  documented capacity. The release kind is not #1900's: knope cuts it from
  the commits.
- **#1901** wrote the operator page from this record,
  [`docs/use/embedding-stores.md`](../use/embedding-stores.md); the prototype
  figures `docs/use/embeddings.md` gave (#1501, #1502) were replaced in #1940.
- **#1936** explained the numpy memory and removed the copy at open
  ([above](#after-1936-the-numpy-stores-memory)). **#1937** and **#1938**
  follow up the PostgreSQL restart and the cold-build peak.
