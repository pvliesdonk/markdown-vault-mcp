## Envelope: growth

32,520 notes, 227,640 chunks, commit d77ec72d.

Sources: postgres (streamed): throwaway container tensorchord/vchord-suite:pg17-20260501.

### Build

| Configuration | Index and embed | Texts embedded | Peak (anonymous) | Peak (resident) |
|---|---|---|---|---|
| numpy (resident) | 80.3 s | 227,640 | 3370.2 MiB | 3403.8 MiB |
| sqlite (streamed) | 103.3 s | 227,640 | 2292.1 MiB | 2327.8 MiB |
| postgres (streamed) | 106.7 s | 227,640 | 2794.3 MiB | 2834.7 MiB |

### Memory

| Configuration | Adds (anonymous) | Adds (resident) | Process | Mapped files | Index and store in page cache | Store closed at baseline |
|---|---|---|---|---|---|---|
| numpy (resident) | 1996.1 MiB | 1996.3 MiB | 1268.4 MiB | 14.8 MiB | 1289.5 MiB | yes |
| sqlite (streamed) | 21.1 MiB | 23.7 MiB | 101.5 MiB | 14.9 MiB | 1331.6 MiB | yes |
| postgres (streamed) | 14.2 MiB | 19.3 MiB | 111.9 MiB | 17.6 MiB | 280.9 MiB | yes |

### Status beside the measurements

| Configuration | Search mode | Stored (status) | Store (measured) | Resident vectors (status) | Adds, anonymous (measured; bounds the vectors or workspace) | Search workspace (status) |
|---|---|---|---|---|---|---|
| numpy (resident) | resident | 1008.6 MiB | 1008.6 MiB | 889.2 MiB | 1996.1 MiB | — |
| sqlite (streamed) | streamed | 1050.7 MiB | 1050.7 MiB | 0.0 MiB | 21.1 MiB | 1.0 MiB |
| postgres (streamed) | streamed | 1367.0 MiB | 1367.0 MiB | 0.0 MiB | 14.2 MiB | — |

PostgreSQL's measured store is its schema's relations by the function its status uses, so a match shows only that the status counts every relation.

### Search latency

| Configuration | Kind | Warm p50 | Warm p95 | Cold | Cold eviction |
|---|---|---|---|---|---|
| numpy (resident) | filtered | 66.9 ms | 102.0 ms | 2691.8 ms | confirmed |
| numpy (resident) | hybrid | 87.2 ms | 115.4 ms | 2932.4 ms | confirmed |
| numpy (resident) | semantic | 70.8 ms | 107.2 ms | 2826.4 ms | confirmed |
| numpy (resident) | similar | 266.0 ms | 330.3 ms | 2984.4 ms | confirmed |
| sqlite (streamed) | filtered | 169.8 ms | 201.4 ms | 2292.1 ms | confirmed |
| sqlite (streamed) | hybrid | 864.8 ms | 1071.7 ms | 2930.7 ms | confirmed |
| sqlite (streamed) | semantic | 865.6 ms | 1034.8 ms | 2840.1 ms | confirmed |
| sqlite (streamed) | similar | 848.4 ms | 1054.9 ms | 3058.3 ms | confirmed |
| postgres (streamed) | filtered | 110.9 ms | 137.1 ms | 188.5 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | hybrid | 474.9 ms | 545.9 ms | 748.4 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | semantic | 464.9 ms | 526.1 ms | 669.2 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | similar | 436.9 ms | 487.5 ms | 575.6 ms | index files confirmed; service restarted, host page cache kept |

### Single-note edits (median / max per edit)

| Configuration | Edits | wchar bytes | Store growth | Note vectors | Texts embedded |
|---|---|---|---|---|---|
| numpy (resident) | 200 | 1,057,759,119.0 / 1,060,011,990 | 0.0 / 2 | 28,672 / 28,672 | 1.0 / 1 |
| sqlite (streamed) | 200 | 270,189.0 / 2,518,954 | 0.0 / 78,264 | 28,672 / 28,672 | 1.0 / 1 |
| postgres (streamed) | 200 | 134,594.0 / 2,387,466 | — | 28,672 / 28,672 | 1.0 / 1 |

PostgreSQL's store writes no local files: its wchar counts the local index only (libpq sends with send(2), which wchar leaves out), and its store's writes are the WAL column of the PostgreSQL service table.

### Restart and recovery

| Configuration | Until search works | Texts embedded | Texts embedded after a kill | Beyond the edit in progress | Store moved aside (restart / after a kill) |
|---|---|---|---|---|---|
| numpy (resident) | 11.04 s | 0 | 1 | 0 | no / no |
| sqlite (streamed) | 13.63 s | 0 | 1 | 0 | no / no |
| postgres (streamed) | 89.22 s | 0 | 1 | 0 | no / no |

### PostgreSQL service

| Configuration | Source | Service memory at rest, after warm searches | Database (after the edits) | Vectors' schema (after the edits) | WAL per edit, cluster-wide (median / max) | PostgreSQL / pgvector |
|---|---|---|---|---|---|---|
| postgres (streamed) | throwaway container tensorchord/vchord-suite:pg17-20260501 | 553.3 MiB | 1386.8 MiB | 1379.2 MiB | 109,480.0 / 126,632 | 17.9 (Debian 17.9-1.pgdg12+1) / 0.8.2 |
