## Envelope: deployed

3,252 notes, 22,764 chunks, commit d77ec72d.

Sources: postgres (streamed): throwaway container tensorchord/vchord-suite:pg17-20260501.

### Build

| Configuration | Index and embed | Texts embedded | Peak (anonymous) | Peak (resident) |
|---|---|---|---|---|
| numpy (resident) | 7.2 s | 22,764 | 415.3 MiB | 448.9 MiB |
| sqlite (streamed) | 8.7 s | 22,764 | 308.7 MiB | 342.5 MiB |
| postgres (streamed) | 10.2 s | 22,764 | 402.3 MiB | 442.7 MiB |

### Memory

| Configuration | Adds (anonymous) | Adds (resident) | Process | Mapped files | Index and store in page cache | Store closed at baseline |
|---|---|---|---|---|---|---|
| numpy (resident) | 157.1 MiB | 157.3 MiB | 235.9 MiB | 14.8 MiB | 129.2 MiB | yes |
| sqlite (streamed) | 15.9 MiB | 18.7 MiB | 97.6 MiB | 14.9 MiB | 133.4 MiB | yes |
| postgres (streamed) | 8.9 MiB | 14.1 MiB | 106.2 MiB | 17.6 MiB | 28.3 MiB | yes |

### Status beside the measurements

| Configuration | Search mode | Stored (status) | Store (measured) | Resident vectors (status) | Adds, anonymous (measured; bounds the vectors or workspace) | Search workspace (status) |
|---|---|---|---|---|---|---|
| numpy (resident) | resident | 100.8 MiB | 100.8 MiB | 88.9 MiB | 157.1 MiB | — |
| sqlite (streamed) | streamed | 105.1 MiB | 105.1 MiB | 0.0 MiB | 15.9 MiB | 1.0 MiB |
| postgres (streamed) | streamed | 137.0 MiB | 137.0 MiB | 0.0 MiB | 8.9 MiB | — |

PostgreSQL's measured store is its schema's relations by the function its status uses, so a match shows only that the status counts every relation.

### Search latency

| Configuration | Kind | Warm p50 | Warm p95 | Cold | Cold eviction |
|---|---|---|---|---|---|
| numpy (resident) | filtered | 37.2 ms | 64.5 ms | 309.2 ms | confirmed |
| numpy (resident) | hybrid | 23.9 ms | 41.7 ms | 328.4 ms | confirmed |
| numpy (resident) | semantic | 21.3 ms | 45.0 ms | 296.5 ms | confirmed |
| numpy (resident) | similar | 51.5 ms | 81.7 ms | 375.7 ms | confirmed |
| sqlite (streamed) | filtered | 25.2 ms | 37.8 ms | 274.7 ms | confirmed |
| sqlite (streamed) | hybrid | 110.4 ms | 155.3 ms | 339.1 ms | confirmed |
| sqlite (streamed) | semantic | 116.7 ms | 158.5 ms | 312.8 ms | confirmed |
| sqlite (streamed) | similar | 93.8 ms | 126.3 ms | 318.1 ms | confirmed |
| postgres (streamed) | filtered | 28.1 ms | 40.6 ms | 112.5 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | hybrid | 104.5 ms | 132.0 ms | 230.2 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | semantic | 99.1 ms | 117.6 ms | 240.5 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | similar | 86.7 ms | 112.1 ms | 255.5 ms | index files confirmed; service restarted, host page cache kept |

### Single-note edits (median / max per edit)

| Configuration | Edits | wchar bytes | Store growth | Note vectors | Texts embedded |
|---|---|---|---|---|---|
| numpy (resident) | 200 | 105,871,419.0 / 109,264,192 | 0.0 / 2 | 28,672 / 28,672 | 1.0 / 1 |
| sqlite (streamed) | 200 | 270,200.0 / 3,658,872 | 0.0 / 78,264 | 28,672 / 28,672 | 1.0 / 1 |
| postgres (streamed) | 200 | 134,609.5 / 3,527,384 | — | 28,672 / 28,672 | 1.0 / 1 |

PostgreSQL's store writes no local files: its wchar counts the local index only (libpq sends with send(2), which wchar leaves out), and its store's writes are the WAL column of the PostgreSQL service table.

### Restart and recovery

| Configuration | Until search works | Texts embedded | Texts embedded after a kill | Beyond the edit in progress | Store moved aside (restart / after a kill) |
|---|---|---|---|---|---|
| numpy (resident) | 1.75 s | 0 | 1 | 0 | no / no |
| sqlite (streamed) | 2.33 s | 0 | 1 | 0 | no / no |
| postgres (streamed) | 10.03 s | 0 | 1 | 0 | no / no |

### PostgreSQL service

| Configuration | Source | Service memory at rest, after warm searches | Database (after the edits) | Vectors' schema (after the edits) | WAL per edit, cluster-wide (median / max) | PostgreSQL / pgvector |
|---|---|---|---|---|---|---|
| postgres (streamed) | throwaway container tensorchord/vchord-suite:pg17-20260501 | 509.1 MiB | 153.3 MiB | 145.6 MiB | 93,036.0 / 139,992 | 17.9 (Debian 17.9-1.pgdg12+1) / 0.8.2 |
