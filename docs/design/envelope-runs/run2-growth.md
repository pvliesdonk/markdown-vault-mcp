## Envelope: growth

32,520 notes, 227,640 chunks, commit d77ec72d.

Sources: postgres (streamed): throwaway container tensorchord/vchord-suite:pg17-20260501.

### Build

| Configuration | Index and embed | Texts embedded | Peak (anonymous) | Peak (resident) |
|---|---|---|---|---|
| numpy (resident) | 80.9 s | 227,640 | 3370.2 MiB | 3403.8 MiB |
| sqlite (streamed) | 88.9 s | 227,640 | 2292.2 MiB | 2328.0 MiB |
| postgres (streamed) | 105.8 s | 227,640 | 2862.4 MiB | 2902.7 MiB |

### Memory

| Configuration | Adds (anonymous) | Adds (resident) | Process | Mapped files | Index and store in page cache | Store closed at baseline |
|---|---|---|---|---|---|---|
| numpy (resident) | 1964.4 MiB | 1964.6 MiB | 1269.0 MiB | 14.8 MiB | 1289.5 MiB | yes |
| sqlite (streamed) | 21.1 MiB | 23.9 MiB | 101.5 MiB | 14.9 MiB | 1331.6 MiB | yes |
| postgres (streamed) | 14.2 MiB | 19.3 MiB | 111.9 MiB | 17.6 MiB | 280.9 MiB | yes |

### Status beside the measurements

| Configuration | Search mode | Stored (status) | Store (measured) | Resident vectors (status) | Adds, anonymous (measured; bounds the vectors or workspace) | Search workspace (status) |
|---|---|---|---|---|---|---|
| numpy (resident) | resident | 1008.6 MiB | 1008.6 MiB | 889.2 MiB | 1964.4 MiB | — |
| sqlite (streamed) | streamed | 1050.7 MiB | 1050.7 MiB | 0.0 MiB | 21.1 MiB | 1.0 MiB |
| postgres (streamed) | streamed | 1367.0 MiB | 1367.0 MiB | 0.0 MiB | 14.2 MiB | — |

PostgreSQL's measured store is its schema's relations by the function its status uses, so a match shows only that the status counts every relation.

### Search latency

| Configuration | Kind | Warm p50 | Warm p95 | Cold | Cold eviction |
|---|---|---|---|---|---|
| numpy (resident) | filtered | 69.2 ms | 95.7 ms | 2674.8 ms | confirmed |
| numpy (resident) | hybrid | 66.2 ms | 102.1 ms | 2856.4 ms | confirmed |
| numpy (resident) | semantic | 66.1 ms | 80.8 ms | 2722.1 ms | confirmed |
| numpy (resident) | similar | 266.1 ms | 312.4 ms | 3022.6 ms | confirmed |
| sqlite (streamed) | filtered | 154.3 ms | 198.5 ms | 2291.5 ms | confirmed |
| sqlite (streamed) | hybrid | 882.3 ms | 987.1 ms | 2906.3 ms | confirmed |
| sqlite (streamed) | semantic | 854.1 ms | 1000.0 ms | 2919.2 ms | confirmed |
| sqlite (streamed) | similar | 861.4 ms | 1062.5 ms | 2801.3 ms | confirmed |
| postgres (streamed) | filtered | 114.5 ms | 152.2 ms | 257.2 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | hybrid | 481.6 ms | 599.5 ms | 763.1 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | semantic | 451.1 ms | 513.7 ms | 944.3 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | similar | 435.4 ms | 485.6 ms | 565.2 ms | index files confirmed; service restarted, host page cache kept |

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
| numpy (resident) | 10.70 s | 0 | 1 | 0 | no / no |
| sqlite (streamed) | 13.60 s | 0 | 1 | 0 | no / no |
| postgres (streamed) | 81.10 s | 0 | 1 | 0 | no / no |

### PostgreSQL service

| Configuration | Source | Service memory at rest, after warm searches | Database (after the edits) | Vectors' schema (after the edits) | WAL per edit, cluster-wide (median / max) | PostgreSQL / pgvector |
|---|---|---|---|---|---|---|
| postgres (streamed) | throwaway container tensorchord/vchord-suite:pg17-20260501 | 552.8 MiB | 1386.8 MiB | 1379.2 MiB | 109,480.0 / 126,576 | 17.9 (Debian 17.9-1.pgdg12+1) / 0.8.2 |
