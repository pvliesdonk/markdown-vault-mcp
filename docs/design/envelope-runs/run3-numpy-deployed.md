## Envelope: deployed

3,252 notes, 22,764 chunks, commit ec328910.

### Build

| Configuration | Index and embed | Texts embedded | Peak (anonymous) | Peak (resident) |
|---|---|---|---|---|
| numpy (resident) | 5.3 s | 22,764 | 415.4 MiB | 450.3 MiB |

### Memory

| Configuration | Adds (anonymous) | Adds (resident) | Process | Mapped files | Index and store in page cache | Store closed at baseline |
|---|---|---|---|---|---|---|
| numpy (resident) | 152.1 MiB | 154.8 MiB | 236.1 MiB | 12.4 MiB | 129.2 MiB | yes |

### Status beside the measurements

| Configuration | Search mode | Stored (status) | Store (measured) | Resident vectors (status) | Adds, anonymous (measured; bounds the vectors or workspace) | Search workspace (status) |
|---|---|---|---|---|---|---|
| numpy (resident) | resident | 100.8 MiB | 100.8 MiB | 88.9 MiB | 152.1 MiB | — |

### Search latency

| Configuration | Kind | Warm p50 | Warm p95 | Cold | Cold eviction |
|---|---|---|---|---|---|
| numpy (resident) | filtered | 22.3 ms | 56.4 ms | 142.2 ms | resident 135421952 bytes |
| numpy (resident) | hybrid | 39.6 ms | 70.1 ms | 112.7 ms | resident 135421952 bytes |
| numpy (resident) | semantic | 30.8 ms | 50.9 ms | 94.6 ms | resident 135421952 bytes |
| numpy (resident) | similar | 32.9 ms | 76.7 ms | 248.6 ms | resident 135421952 bytes |

### Single-note edits (median / max per edit)

| Configuration | Edits | wchar bytes | Store growth | Note vectors | Texts embedded |
|---|---|---|---|---|---|
| numpy (resident) | 200 | 105,871,419.0 / 109,264,192 | 0.0 / 2 | 28,672 / 28,672 | 1.0 / 1 |

### Restart and recovery

| Configuration | Until search works | Texts embedded | Texts embedded after a kill | Beyond the edit in progress | Store moved aside (restart / after a kill) |
|---|---|---|---|---|---|
| numpy (resident) | 1.87 s | 0 | 1 | 0 | no / no |

warning: --out is on a memory-backed filesystem, so cold latency is not cold; choose a folder on disk
wrote /tmp/claude-1000/-mnt-code-mcp-servers-markdown-vault-mcp--claude-worktrees-bridge-cse-01M3q9Ko7NoqoAWM51JWDHSE/d85b9cfa-4e64-54e6-b0f5-1d08ef79c407/scratchpad/env1936/growth/1d51e7a1-growth-20261031T104836034600Z.json
