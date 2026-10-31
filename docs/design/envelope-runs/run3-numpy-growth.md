## Envelope: growth

32,520 notes, 227,640 chunks, commit 1d51e7a1.

### Build

| Configuration | Index and embed | Texts embedded | Peak (anonymous) | Peak (resident) |
|---|---|---|---|---|
| numpy (resident) | 58.5 s | 227,640 | 3371.4 MiB | 3406.5 MiB |

### Memory

| Configuration | Adds (anonymous) | Adds (resident) | Process | Mapped files | Index and store in page cache | Store closed at baseline |
|---|---|---|---|---|---|---|
| numpy (resident) | 1186.4 MiB | 1189.0 MiB | 1269.1 MiB | 12.4 MiB | 1289.5 MiB | yes |

### Status beside the measurements

| Configuration | Search mode | Stored (status) | Store (measured) | Resident vectors (status) | Adds, anonymous (measured; bounds the vectors or workspace) | Search workspace (status) |
|---|---|---|---|---|---|---|
| numpy (resident) | resident | 1008.6 MiB | 1008.6 MiB | 889.2 MiB | 1186.4 MiB | — |

### Search latency

| Configuration | Kind | Warm p50 | Warm p95 | Cold | Cold eviction |
|---|---|---|---|---|---|
| numpy (resident) | filtered | 61.4 ms | 93.2 ms | 805.9 ms | resident 1352142848 bytes |
| numpy (resident) | hybrid | 82.8 ms | 110.2 ms | 923.4 ms | resident 1352142848 bytes |
| numpy (resident) | semantic | 59.8 ms | 116.5 ms | 839.6 ms | resident 1352142848 bytes |
| numpy (resident) | similar | 258.3 ms | 308.5 ms | 1215.9 ms | resident 1352142848 bytes |

### Single-note edits (median / max per edit)

| Configuration | Edits | wchar bytes | Store growth | Note vectors | Texts embedded |
|---|---|---|---|---|---|
| numpy (resident) | 200 | 1,057,759,119.0 / 1,060,011,990 | 0.0 / 2 | 28,672 / 28,672 | 1.0 / 1 |

### Restart and recovery

| Configuration | Until search works | Texts embedded | Texts embedded after a kill | Beyond the edit in progress | Store moved aside (restart / after a kill) |
|---|---|---|---|---|---|
| numpy (resident) | 8.78 s | 0 | 1 | 0 | no / no |
