## Envelope: deployed

3,252 notes, 22,764 chunks, commit d77ec72d.

Sources: postgres (streamed): throwaway container tensorchord/vchord-suite:pg17-20260501.

### Build

| Configuration | Index and embed | Texts embedded | Peak (anonymous) | Peak (resident) |
|---|---|---|---|---|
| numpy (resident) | 7.9 s | 22,764 | 415.3 MiB | 448.9 MiB |
| sqlite (streamed) | 8.9 s | 22,764 | 308.8 MiB | 342.7 MiB |
| postgres (streamed) | 10.4 s | 22,764 | 408.3 MiB | 448.7 MiB |

### Memory

| Configuration | Adds (anonymous) | Adds (resident) | Process | Mapped files | Index and store in page cache | Store closed at baseline |
|---|---|---|---|---|---|---|
| numpy (resident) | 195.1 MiB | 195.3 MiB | 235.9 MiB | 14.8 MiB | 129.2 MiB | yes |
| sqlite (streamed) | 15.9 MiB | 18.7 MiB | 97.6 MiB | 14.9 MiB | 133.4 MiB | yes |
| postgres (streamed) | 8.9 MiB | 14.2 MiB | 106.2 MiB | 17.7 MiB | 28.3 MiB | yes |

### Status beside the measurements

| Configuration | Search mode | Stored (status) | Store (measured) | Resident vectors (status) | Adds, anonymous (measured; bounds the vectors or workspace) | Search workspace (status) |
|---|---|---|---|---|---|---|
| numpy (resident) | resident | 100.8 MiB | 100.8 MiB | 88.9 MiB | 195.1 MiB | — |
| sqlite (streamed) | streamed | 105.1 MiB | 105.1 MiB | 0.0 MiB | 15.9 MiB | 1.0 MiB |
| postgres (streamed) | streamed | 137.0 MiB | 137.0 MiB | 0.0 MiB | 8.9 MiB | — |

PostgreSQL's measured store is its schema's relations by the function its status uses, so a match shows only that the status counts every relation.

### Search latency

| Configuration | Kind | Warm p50 | Warm p95 | Cold | Cold eviction |
|---|---|---|---|---|---|
| numpy (resident) | filtered | 23.9 ms | 32.4 ms | 292.5 ms | confirmed |
| numpy (resident) | hybrid | 22.5 ms | 37.1 ms | 306.9 ms | confirmed |
| numpy (resident) | semantic | 17.1 ms | 39.2 ms | 317.9 ms | confirmed |
| numpy (resident) | similar | 31.6 ms | 52.9 ms | 383.1 ms | confirmed |
| sqlite (streamed) | filtered | 25.0 ms | 37.7 ms | 265.7 ms | confirmed |
| sqlite (streamed) | hybrid | 93.8 ms | 143.6 ms | 344.6 ms | confirmed |
| sqlite (streamed) | semantic | 103.8 ms | 139.8 ms | 331.1 ms | confirmed |
| sqlite (streamed) | similar | 97.7 ms | 121.5 ms | 328.6 ms | confirmed |
| postgres (streamed) | filtered | 25.4 ms | 41.9 ms | 85.4 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | hybrid | 99.5 ms | 123.4 ms | 244.1 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | semantic | 97.5 ms | 118.4 ms | 263.4 ms | index files confirmed; service restarted, host page cache kept |
| postgres (streamed) | similar | 86.3 ms | 104.7 ms | 189.7 ms | index files confirmed; service restarted, host page cache kept |

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
| numpy (resident) | 1.90 s | 0 | 1 | 0 | no / no |
| sqlite (streamed) | 2.27 s | 0 | 1 | 0 | no / no |
| postgres (streamed) | 10.10 s | 0 | 1 | 0 | no / no |

### PostgreSQL service

| Configuration | Source | Service memory at rest, after warm searches | Database (after the edits) | Vectors' schema (after the edits) | WAL per edit, cluster-wide (median / max) | PostgreSQL / pgvector |
|---|---|---|---|---|---|---|
| postgres (streamed) | throwaway container tensorchord/vchord-suite:pg17-20260501 | 506.2 MiB | 153.3 MiB | 145.6 MiB | 92,664.0 / 145,280 | 17.9 (Debian 17.9-1.pgdg12+1) / 0.8.2 |
