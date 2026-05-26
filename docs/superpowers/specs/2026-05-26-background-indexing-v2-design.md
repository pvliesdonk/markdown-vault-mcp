# Non-blocking startup via Collection-owned background reindex (#513, second attempt)

**Status:** Approved — ready for implementation planning.
**Supersedes:** PR #515 (own attempt, abandoned 2026-05-26) and PR #510 (external attempt, abandoned earlier).
**Issue:** [#513](https://github.com/pvliesdonk/markdown-vault-mcp/issues/513).

## Background

PR #515 attempted non-blocking startup by extracting an event-driven coupling between `Collection._ensure_initialized` and a background thread's `_fts_done_event`. After six rounds of whack-a-mole bug-fixing, the branch was abandoned per the project's "abandon the branch" rule. The structural root cause: foreground methods waiting on background-thread state cascaded into every downstream issue (two-event split, 60s timeout race against `_write_lock`, broad `except` masks in `_count_documents`/`_fts_has_documents`, dual-invariant `stats()` bypass, dead `skip_if_missing` parameter).

The first attempt was a productive spike — its findings carry forward (warm-start fast path with `exclude_patterns` purge, close() shutdown ordering, the value of distinguishing FTS-done from full-done). Only the messy diff is discarded.

PR #510 (external) used a "skip eager build" approach that regressed PR #256's `exclude_patterns` purge logic. That regression mode is also avoided by this design.

## Goal

Server startup completes within seconds even on a fresh deploy with thousands of documents and no pre-built index. The MCP `initialize` handshake never blocks on FTS or embedding work. Tools become usable immediately; clients can see what's currently indexed and learn that indexing is still in progress.

## Architecture

`Collection` owns indexing in both invocation modes:

- **Synchronous** — `build_index()` / `reindex()`. Used by the CLI and the existing `reindex` MCP tool. Caller blocks until done. Unchanged behaviour.
- **Background** — `start_background_reindex()` (new). Spawns a daemon thread that runs `reindex()` followed by `build_embeddings()` (when a provider is configured). Returns immediately. Idempotent (no-op if a background thread is already alive).

### The load-bearing rule

> **No public method on `Collection` may block on background indexing state.** Foreground reads return whatever is currently in the FTS / vector index. Empty-during-cold-start is a valid result, not an error to wait out.

The rule is enforced two ways:

1. **`_ensure_initialized()` is stripped entirely** — method deleted, all call sites cleaned. It was the structural vehicle of the #515 failure; deleting it removes the temptation to re-add a waiter to it. Methods that used to call it instead trust the underlying FTS / vector indexes to return what they have.
2. **A regression test** asserts that `search()`, `list_documents()`, and `stats()` return within 500ms while a deliberately slow background reindex is in flight (see Tests below). Future code that re-introduces a foreground waiter will fail this test.

Why a wrapper class (`BackgroundIndexer`) was rejected: indexing IS Collection's responsibility (its FTS table, vector index, and tracker are Collection internals; "what's currently indexed" is intrinsic state). A wrapper that exists only to ask Collection for state and re-expose it through a thread-aware facade adds indirection, not architecture. The right fix is to make Collection's foreground methods non-blocking, not to extract the threading concern into a separate type.

## New Collection state

```python
_background_thread: threading.Thread | None
_background_shutdown: threading.Event       # cooperative shutdown signal
_background_state_lock: threading.Lock      # protects status snapshot fields
_background_phase: Literal["indexing", "embedding"] | None
_background_last_started_at: str | None     # ISO-8601 UTC
_background_last_completed_at: str | None
_background_last_error: str | None
```

There is no event the foreground waits on. `_background_state_lock` exists only to serialise the status-snapshot read against the background thread's status updates.

## New / changed methods

```python
def start_background_reindex(self) -> None:
    """Spawn a daemon thread running reindex() then build_embeddings().

    No-op if a background thread is already alive (logs at DEBUG).
    Returns immediately.
    """

def index_status(self) -> dict[str, Any]:
    """Read-only snapshot of background indexing state. Never blocks.

    Returns a dict with:
        background_running: bool
        background_phase:   "indexing" | "embedding" | None
        last_run_started_at:   ISO-8601 UTC string or None
        last_run_completed_at: ISO-8601 UTC string or None
        last_error: str or None    # from most recent failed run; cleared on success
    """

def stats(self) -> CollectionStats:
    # Existing method. Two changes:
    #   - Stops calling self._ensure_initialized().
    #   - Returned CollectionStats gains a nested index_status field
    #     (same dict shape as index_status()).

def close(self) -> None:
    # Existing method. Gains a new first step:
    #   1. Set _background_shutdown.
    #   2. Join _background_thread with 30s timeout.
    #   3. (If still alive) log a warning; daemon=True ensures process exit.
    # Then the existing FTS / git teardown runs unchanged.
```

`build_index()` retains its warm-start fast path (no-op when the FTS DB already has rows) and the PR #256 `exclude_patterns` purge — both preserved from the #515 spike and sound regardless of foreground/background split.

`_ensure_initialized()` is removed. Any helper introduced during the #515 spike (`_count_documents`, `_fts_has_documents`, `skip_if_missing` parameter on `build_embeddings`) is also removed — they existed only to paper over the foreground waiter.

## Background worker logic

```python
def _background_reindex_worker(self) -> None:
    try:
        self._set_phase("indexing")
        if self._background_shutdown.is_set():
            return
        self.reindex()  # acquires _write_lock during mutation phase

        if self._embedding_provider is not None and self._embeddings_path is not None:
            if self._background_shutdown.is_set():
                return
            self._set_phase("embedding")
            self.build_embeddings()

        self._set_completed(error=None)
    except Exception as exc:
        logger.error("background_reindex_failed", exc_info=True)
        self._set_completed(error=str(exc))
    finally:
        self._set_phase(None)
```

The thread checks `_background_shutdown` between phases. Mid-`reindex()` interruption is not supported (would require invasive changes to the scanner); `daemon=True` ensures the Python process exits regardless on hard shutdown.

The embedding phase is guarded by an explicit `if self._embedding_provider is not None and self._embeddings_path is not None:` check — **not** by a `skip_if_missing` parameter on `build_embeddings()`. The dead parameter from #515 is not reintroduced.

## Lifespan composition (`_server_deps.py`)

```python
collection = Collection(**kwargs)
set_collection_singleton(collection)

await asyncio.to_thread(collection.sync_from_remote_before_index)
collection.start()                       # FTS open, git start — fast
collection.start_background_reindex()    # fire-and-forget
yield {"collection": collection, "config": config}
# on shutdown: collection.close() handles bg join + existing teardown
```

The lifespan no longer calls `build_index()`. The CLI path (`markdown-vault-mcp index`) still calls it synchronously — unchanged.

## Exposure

- **`stats` MCP tool** (and the `stats://vault` resource): `CollectionStats` gains a nested `index_status` field. An LLM client that wants "is the index ready?" calls `stats` and reads it.
- **`get_server_info` tool**: gains an `index_status` field for operator health checks and "is my latest fix actually running?" workflows.
- **No separate `index_status` MCP tool** — keeps the surface tight. The information is composable from `stats` + `embeddings_status` for clients that want counts.

## Failure recovery

On background failure: `last_error` is set, `background_phase` returns to `None`, `background_running` becomes `False`, the server stays running. Operator recovers via either:

- CLI: `markdown-vault-mcp index` (rebuilds from scratch).
- MCP `reindex` tool: incremental from current FTS state. Contends naturally on `_write_lock` if a background run is somehow still in flight.

`last_error` clears on the next successful background run, or is overwritten by the next failure. No automatic retry — sustained transient errors would otherwise burn provider quota and mask config problems.

## Concurrency contract

| Caller | Behaviour |
|--------|-----------|
| Foreground reads (`search`, `list_documents`, `stats`, `index_status`) | Never block on background. Return current FTS / vector contents. |
| Foreground writes (`write`, `edit`, `delete`, `rename`) | Acquire `_write_lock`. If background is in its mutation phase, write waits briefly (existing behaviour). Tracker hash ensures the next background pass skips the file the foreground just wrote. |
| Periodic git pull | Unchanged. Calls `collection.reindex()` synchronously in its own thread; contends on `_write_lock`. |
| `reindex` MCP tool | Unchanged. Sync, contends on `_write_lock`. |
| `start_background_reindex()` called twice | No-op on second call (thread alive check); DEBUG log. |

## Tests

### The load-bearing test

**`test_foreground_reads_never_block_on_background`** — instantiate a Collection with a `MockEmbeddingProvider` that sleeps per chunk to deliberately slow the background run. Call `start_background_reindex()`. While the background thread is in its `indexing` phase, assert each of `search(...)`, `list_documents(...)`, `stats()`, `index_status()` returns within 500ms. This is the regression test that future code re-adding a foreground waiter will fail.

### Other tests

- Phase transitions: `idle → indexing → embedding → idle` (with provider); `idle → indexing → idle` (without provider).
- Failure path: provider raises mid-embedding → phase returns to `None`, `last_error` set, foreground reads still work.
- Shutdown during background: `close()` returns within 30s; daemon-thread fallback observable when leaving a deliberately stuck thread.
- Idempotent `start_background_reindex()`: two calls → one thread; second call returns immediately and logs at DEBUG.
- Warm-start fast path: existing populated DB → `build_index()` returns quickly, `exclude_patterns` purge still applies (preserve PR #256 regression test).
- Concurrent write during background: write succeeds, tracker hash causes background reindex to skip the just-written file.
- `index_status` snapshot during each phase reports correct values; ISO timestamps parse round-trip.
- `get_server_info` includes the `index_status` field with the expected shape.
- `stats` MCP tool returns nested `index_status` with the expected shape.

## Documentation updates (must land with implementation)

- **`docs/design.md`** — add a "Background indexing" section that names the no-foreground-waiter rule explicitly and references the regression test.
- **`docs/configuration.md`** — `EXCLUDE_PATTERNS` row note: "Changes take effect on the next background reindex (server start or git pull)."
- **`docs/guides/claude-desktop.md`** — pre-build is no longer required for the server to start; it remains the fastest path to immediate full readiness.
- **`docs/tools/index.md`** — `stats` tool gains `index_status` nested field; `get_server_info` tool gains `index_status` field. Update return descriptions.
- **`README.md`** — brief note on non-blocking startup in the "How it works" section.

## Explicitly out of scope

- A `BackgroundIndexer` wrapper class — rejected as bolt-on; indexing belongs in Collection.
- `skip_if_missing` parameter on `build_embeddings()` — dead code from #515; use an `if provider:` guard in the worker.
- Tracker `exclude_patterns` checksum for finer-grained warm-start invalidation — covered by follow-up issue #257.
- Symlink cycle detection — covered by #508's docs warning; separate hardening if ever needed.
- Two-thread parallel FTS + embeddings — complexity not justified without measured cold-start latency need.
- Mid-`reindex()` cooperative interruption — would require scanner-level changes; daemon-thread shutdown is sufficient.

## Acceptance criteria

- [ ] MCP `initialize` handshake completes within seconds on a fresh deploy with thousands of documents and no pre-built indexes.
- [ ] Background thread transitions through `indexing → embedding → idle` (or `indexing → idle` without provider); `stats` and `get_server_info` reflect this.
- [ ] Background failure sets `last_error`, returns `background_phase` to `None`, logs at ERROR, leaves the server running.
- [ ] Foreground reads during background indexing return promptly (regression test passes).
- [ ] Foreground writes during background indexing serialise correctly via `_write_lock` (no corruption; tracker prevents double-processing).
- [ ] `close()` during background indexing aborts cleanly within ~30s.
- [ ] Warm-start path: pre-built DB + `.npy` → background reindex finds no changes and completes quickly.
- [ ] PR #256 regression test (`test_build_index_purges_stale_excluded_docs`) still passes — `Collection.build_index()` retains purge semantics for the direct-call path.
- [ ] `_ensure_initialized` and all related helpers from the #515 spike (`_count_documents`, `_fts_has_documents`, `skip_if_missing`) are absent from the final diff.
- [ ] CLI `markdown-vault-mcp index` unchanged in behaviour.
- [ ] MCP `reindex` tool unchanged in behaviour.
- [ ] Documentation updates listed above land in the same PR.

## References

- [#513](https://github.com/pvliesdonk/markdown-vault-mcp/issues/513) — original issue.
- [#509](https://github.com/pvliesdonk/markdown-vault-mcp/issues/509) — original "handshake timeout" report (superseded by #513).
- [#510](https://github.com/pvliesdonk/markdown-vault-mcp/pull/510) — abandoned external PR (skip approach; regressed #256).
- [#515](https://github.com/pvliesdonk/markdown-vault-mcp/pull/515) — abandoned own PR (foreground waiter coupled to background event).
- [#256](https://github.com/pvliesdonk/markdown-vault-mcp/pull/256) — `exclude_patterns` purge invariant that must be preserved.
- `memory/feedback_background_indexing_abandon.md` — post-abandon analysis that this design responds to.
- `docs/guides/claude-desktop.md#pre-build-embeddings-before-first-launch` — operator guidance retained as an optional acceleration path.
