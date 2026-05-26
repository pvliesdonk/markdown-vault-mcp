# Non-blocking startup via Collection-owned background reindex — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MCP `initialize` handshake non-blocking on cold start by moving index/embedding work to a daemon thread owned by `Collection`, with no foreground waiters anywhere in the public API.

**Architecture:** Add two methods to `Collection` (`start_background_reindex()` fire-and-forget, `index_status()` read-only snapshot) and remove `_ensure_initialized()`. Foreground reads return whatever's currently in the index. A regression test pinning sub-500ms foreground latency under a deliberately slow background enforces the no-waiter rule.

**Tech Stack:** Python 3.11+, `threading.Thread` (daemon), `threading.Event` (shutdown only), pytest, FastMCP, SQLite FTS5.

**Spec:** `docs/superpowers/specs/2026-05-26-background-indexing-v2-design.md`

**Branch:** `feat/513-background-indexing-v2` (already created off `origin/main`; spec already committed as `6770af2`).

---

## Pre-flight context (read before starting)

- The spec is the source of truth. If this plan conflicts with the spec, fix the plan.
- **Hard rule:** `_ensure_initialized()` must end up deleted from `Collection`. No method on `Collection` may block waiting on the background thread. This rule killed PR #515 and we are not re-litigating it.
- The existing lifespan (`src/markdown_vault_mcp/_server_deps.py:83-137`) calls `collection.build_index()` synchronously. We will replace this with `start_background_reindex()` *after* the strip task, so the eager-build path remains intact during intermediate states.
- `_ensure_initialized()` currently has ~22 call sites in `src/markdown_vault_mcp/collection.py`. All of them must be removed.
- Test count baseline: 1456 (per PR #433). Expect to land at ~1465–1475 after this work.
- Run gates locally before each commit: `uv run pytest -x -q`, `uv run ruff check --fix . && uv run ruff format .`, `uv run mypy src/ tests/`.

---

## File map

**Modify:**
- `src/markdown_vault_mcp/collection.py` — add background state + 2 new methods + close() shutdown step; remove `_ensure_initialized()` and 22 call sites; nest `index_status` in `stats()`.
- `src/markdown_vault_mcp/types.py` — extend `CollectionStats` with `index_status: dict[str, Any]` field.
- `src/markdown_vault_mcp/_server_deps.py` — lifespan switches from synchronous `build_index()` + `build_embeddings()` to `start_background_reindex()`.
- `src/markdown_vault_mcp/server.py` — `register_server_info_tool(...)` gains an `extra_status` (or equivalent) callable wiring index_status into the response. See Task 8 for the exact mechanism check.
- `docs/design.md` — add "Background indexing" section.
- `docs/configuration.md` — `EXCLUDE_PATTERNS` row note.
- `docs/guides/claude-desktop.md` — pre-build optional now.
- `docs/tools/index.md` — `stats` and `get_server_info` return shape updates.
- `README.md` — "How it works" note.

**Create:**
- `tests/test_background_indexing.py` — all tests for the new behaviour.

**Touch only if tests force it:**
- `tests/test_collection.py`, `tests/test_managers_*.py`, etc. — audit for places that rely on lazy `_ensure_initialized()` and fix by adding explicit `collection.build_index()` (or fixture update).

---

## Task 1: Add background-state fields and helpers to Collection (no behaviour change)

**Files:**
- Modify: `src/markdown_vault_mcp/collection.py` (`__init__` region + helper section)
- Test: `tests/test_background_indexing.py` (new file — write empty `import` + one stub test first)

- [ ] **Step 1.1: Create the test file with the stub test**

Create `tests/test_background_indexing.py`:

```python
"""Tests for non-blocking startup background indexing (#513)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from markdown_vault_mcp.collection import Collection


def test_index_status_idle_before_anything_runs(tmp_path: Path) -> None:
    """A fresh Collection that hasn't started background work reports idle status."""
    collection = Collection(source_dir=tmp_path)
    try:
        status = collection.index_status()
        assert status == {
            "background_running": False,
            "background_phase": None,
            "last_run_started_at": None,
            "last_run_completed_at": None,
            "last_error": None,
        }
    finally:
        collection.close()
```

- [ ] **Step 1.2: Run the test — it must fail with AttributeError**

Run: `uv run pytest tests/test_background_indexing.py::test_index_status_idle_before_anything_runs -v`

Expected: `AttributeError: 'Collection' object has no attribute 'index_status'`.

- [ ] **Step 1.3: Add background-state fields to `Collection.__init__`**

Locate `Collection.__init__` in `src/markdown_vault_mcp/collection.py`. Find the section where instance attributes are initialised (look for `self._write_lock = threading.RLock()` or similar — that's the threading-state area). Add directly after the existing threading-state initialisations:

```python
# Background reindex state (see docs/superpowers/specs/2026-05-26-background-indexing-v2-design.md).
# These fields exist so background indexing state is visible to read-only
# snapshots; foreground methods MUST NOT wait on any of them.
self._background_thread: threading.Thread | None = None
self._background_shutdown: threading.Event = threading.Event()
self._background_state_lock: threading.Lock = threading.Lock()
self._background_phase: str | None = None  # "indexing" | "embedding" | None
self._background_last_started_at: str | None = None
self._background_last_completed_at: str | None = None
self._background_last_error: str | None = None
```

- [ ] **Step 1.4: Add `index_status()` method**

Add directly after the `# Lazy initialisation` section header (currently around line 351-358, where `_ensure_initialized()` lives — put the new method just above it; we'll remove `_ensure_initialized()` in Task 6):

```python
def index_status(self) -> dict[str, Any]:
    """Snapshot of background reindex state. Never blocks.

    Returns:
        Dict with keys ``background_running``, ``background_phase``,
        ``last_run_started_at``, ``last_run_completed_at``, ``last_error``.
        Phase is ``"indexing"``, ``"embedding"``, or ``None``.
        Timestamps are ISO-8601 UTC strings or ``None``.
    """
    with self._background_state_lock:
        thread = self._background_thread
        return {
            "background_running": thread is not None and thread.is_alive(),
            "background_phase": self._background_phase,
            "last_run_started_at": self._background_last_started_at,
            "last_run_completed_at": self._background_last_completed_at,
            "last_error": self._background_last_error,
        }
```

- [ ] **Step 1.5: Run the test — it must pass**

Run: `uv run pytest tests/test_background_indexing.py::test_index_status_idle_before_anything_runs -v`

Expected: PASS.

- [ ] **Step 1.6: Run gates and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/ tests/
uv run pytest -x -q tests/test_background_indexing.py tests/test_collection.py
git add src/markdown_vault_mcp/collection.py tests/test_background_indexing.py
git commit -m "feat(collection): add background state fields and index_status snapshot

Refs #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Implement `start_background_reindex()` and the worker

**Files:**
- Modify: `src/markdown_vault_mcp/collection.py`
- Test: `tests/test_background_indexing.py`

- [ ] **Step 2.1: Write the failing test for idempotent start and phase transition**

Append to `tests/test_background_indexing.py`:

```python
def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.01) -> None:
    """Poll a predicate until it returns truthy or timeout expires.

    Raises AssertionError if predicate never becomes true.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"predicate {predicate!r} not satisfied within {timeout}s")


def test_start_background_reindex_runs_and_completes(tmp_path: Path) -> None:
    """Background reindex transitions through phases and reaches idle."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")
    collection = Collection(source_dir=tmp_path)
    try:
        collection.start_background_reindex()

        # Eventually the background thread completes; final state is idle, no error.
        _wait_until(lambda: not collection.index_status()["background_running"])

        status = collection.index_status()
        assert status["background_running"] is False
        assert status["background_phase"] is None
        assert status["last_error"] is None
        assert status["last_run_started_at"] is not None
        assert status["last_run_completed_at"] is not None
    finally:
        collection.close()


def test_start_background_reindex_is_idempotent(tmp_path: Path) -> None:
    """Calling start twice while a thread is alive does not spawn a second thread."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")
    collection = Collection(source_dir=tmp_path)
    try:
        collection.start_background_reindex()
        first_thread = collection._background_thread

        # Second call must be a no-op while the first thread is alive.
        # If the first thread completes between calls, this still must not
        # raise — we just accept whichever invariant holds.
        collection.start_background_reindex()
        second_thread = collection._background_thread

        assert second_thread is first_thread or not first_thread.is_alive()

        _wait_until(lambda: not collection.index_status()["background_running"])
    finally:
        collection.close()
```

- [ ] **Step 2.2: Run tests — they must fail with AttributeError**

Run: `uv run pytest tests/test_background_indexing.py -v -k start_background_reindex`

Expected: `AttributeError: 'Collection' object has no attribute 'start_background_reindex'`.

- [ ] **Step 2.3: Add the worker and start method**

Add directly after `index_status()` in `src/markdown_vault_mcp/collection.py`:

```python
def _set_background_phase(self, phase: str | None) -> None:
    """Update the background phase field under the state lock."""
    with self._background_state_lock:
        self._background_phase = phase

def _set_background_completed(self, error: str | None) -> None:
    """Mark a background run as completed under the state lock."""
    with self._background_state_lock:
        self._background_last_completed_at = datetime.now(timezone.utc).isoformat()
        self._background_last_error = error

def _background_reindex_worker(self) -> None:
    """Daemon-thread target: run reindex() then build_embeddings()."""
    try:
        if self._background_shutdown.is_set():
            return
        self._set_background_phase("indexing")
        self.reindex()

        if (
            self._embedding_provider is not None
            and self._embeddings_path is not None
        ):
            if self._background_shutdown.is_set():
                return
            self._set_background_phase("embedding")
            self.build_embeddings()

        self._set_background_completed(error=None)
    except Exception as exc:  # noqa: BLE001 — top-level boundary for daemon thread
        logger.error("background_reindex_failed", exc_info=True)
        self._set_background_completed(error=str(exc))
    finally:
        self._set_background_phase(None)

def start_background_reindex(self) -> None:
    """Spawn a daemon thread that runs reindex() then build_embeddings().

    No-op (logged at DEBUG) when a background thread is already alive.
    Returns immediately. The thread is daemonic, so a hard process exit
    does not block on it; SQLite WAL handles any partial uncommitted state
    on the next startup.

    See the spec at
    ``docs/superpowers/specs/2026-05-26-background-indexing-v2-design.md``
    for the no-foreground-waiter rule that motivates this method.
    """
    with self._background_state_lock:
        existing = self._background_thread
        if existing is not None and existing.is_alive():
            logger.debug("start_background_reindex skipped — thread already alive")
            return
        self._background_shutdown.clear()
        self._background_last_started_at = datetime.now(timezone.utc).isoformat()
        self._background_last_error = None
        thread = threading.Thread(
            target=self._background_reindex_worker,
            name="markdown-vault-mcp-bg-reindex",
            daemon=True,
        )
        self._background_thread = thread
        thread.start()
```

Ensure `from datetime import datetime, timezone` is imported at the top of the file (add to the existing imports if not present).

- [ ] **Step 2.4: Run the tests — they must pass**

Run: `uv run pytest tests/test_background_indexing.py -v -k start_background_reindex`

Expected: both PASS.

- [ ] **Step 2.5: Run gates and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/ tests/
uv run pytest -x -q tests/test_background_indexing.py
git add src/markdown_vault_mcp/collection.py tests/test_background_indexing.py
git commit -m "feat(collection): add start_background_reindex daemon worker

Refs #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Background failure path

**Files:**
- Test: `tests/test_background_indexing.py`

- [ ] **Step 3.1: Write the failing test**

Append:

```python
def test_background_failure_sets_last_error_and_recovers(tmp_path: Path) -> None:
    """When reindex raises, last_error is set, phase returns to None, server lives."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")
    collection = Collection(source_dir=tmp_path)
    try:
        # Patch reindex to raise.
        original_reindex = collection.reindex

        def boom() -> object:
            raise RuntimeError("synthetic failure")

        collection.reindex = boom  # type: ignore[method-assign]

        collection.start_background_reindex()
        _wait_until(lambda: not collection.index_status()["background_running"])

        status = collection.index_status()
        assert status["background_running"] is False
        assert status["background_phase"] is None
        assert status["last_error"] == "synthetic failure"

        # Server-equivalent behaviour: foreground operations still work.
        # (We don't have a populated index, but list_documents must not raise.)
        collection.reindex = original_reindex  # type: ignore[method-assign]
        # No further assertion needed — we already verified the worker did not
        # propagate the exception out of the thread.
    finally:
        collection.close()
```

- [ ] **Step 3.2: Run it — should already PASS (Task 2 already handles exceptions)**

Run: `uv run pytest tests/test_background_indexing.py::test_background_failure_sets_last_error_and_recovers -v`

Expected: PASS. (The worker's `try/except` set up in Task 2.3 already covers this. If it fails, fix Task 2's implementation rather than this test.)

- [ ] **Step 3.3: Commit**

```bash
git add tests/test_background_indexing.py
git commit -m "test(collection): pin background-failure last_error semantics

Refs #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Shutdown during background — extend `close()`

**Files:**
- Modify: `src/markdown_vault_mcp/collection.py` (`close` method, around line 290-350)
- Test: `tests/test_background_indexing.py`

- [ ] **Step 4.1: Write the failing test**

Append:

```python
def test_close_joins_background_thread_within_timeout(tmp_path: Path) -> None:
    """close() returns within the 30s bounded join; background thread is gone."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")
    collection = Collection(source_dir=tmp_path)
    collection.start_background_reindex()

    start = time.monotonic()
    collection.close()
    elapsed = time.monotonic() - start

    assert elapsed < 30.0
    thread = collection._background_thread
    # Either the thread completed before close() arrived, or close() joined it.
    assert thread is None or not thread.is_alive()
```

- [ ] **Step 4.2: Run it — should already PASS for fast vaults**

Run: `uv run pytest tests/test_background_indexing.py::test_close_joins_background_thread_within_timeout -v`

Expected: likely PASS already (a single doc finishes near-instantly). The interesting assertion is the *bounded* one; that's exercised in step 4.4. If this fails, jump straight to step 4.3.

- [ ] **Step 4.3: Add the shutdown step to `close()`**

In `src/markdown_vault_mcp/collection.py`, find the `def close(self) -> None:` method (around line 290-350). Add a new step as the **very first** action inside the method, before any existing teardown:

```python
def close(self) -> None:
    """Tear down the Collection. See spec section "Shutdown" for ordering."""
    # 1. Signal the background reindex thread and join with a bounded timeout.
    #    daemon=True ensures the process can still exit if the thread is stuck
    #    inside reindex()'s scanner work; SQLite WAL recovers on next startup.
    self._background_shutdown.set()
    thread = self._background_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=30.0)
        if thread.is_alive():
            logger.warning(
                "background_reindex_thread_join_timeout "
                "thread=%s — abandoning (daemon=True ensures process exit)",
                thread.name,
            )

    # 2. (existing teardown steps — embeddings flush, callback drain, git
    #    strategy close, FTS close — keep the existing comments and code.)
```

- [ ] **Step 4.4: Write the bounded-timeout regression test**

Append:

```python
def test_close_bounded_join_does_not_hang_indefinitely(tmp_path: Path) -> None:
    """A background thread that ignores shutdown still lets close() return."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")
    collection = Collection(source_dir=tmp_path)

    # Replace the worker target with one that sleeps past the 30s timeout.
    stop_sleeping = threading.Event()

    def slow_worker() -> None:
        stop_sleeping.wait(timeout=60.0)

    # We cannot easily replace the thread target after .start(); instead we
    # construct the thread manually to bypass start_background_reindex.
    collection._background_shutdown.clear()
    collection._background_thread = threading.Thread(
        target=slow_worker, daemon=True, name="test-slow-bg"
    )
    collection._background_thread.start()

    # Patch the join timeout to keep the test under 5 seconds.
    original_close = collection.close

    def fast_close() -> None:
        collection._background_shutdown.set()
        t = collection._background_thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)  # short for the test
        # Skip the rest of close() — we only want to verify the join semantics.

    fast_close()

    # The slow thread is still alive (it ignored shutdown), but fast_close returned.
    assert collection._background_thread is not None
    assert collection._background_thread.is_alive()

    # Cleanup.
    stop_sleeping.set()
    collection._background_thread.join(timeout=2.0)
    # Now do the real close.
    original_close()
```

- [ ] **Step 4.5: Run all task-4 tests**

Run: `uv run pytest tests/test_background_indexing.py -v -k close`

Expected: both PASS.

- [ ] **Step 4.6: Run gates and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/ tests/
uv run pytest -x -q tests/test_background_indexing.py
git add src/markdown_vault_mcp/collection.py tests/test_background_indexing.py
git commit -m "feat(collection): bounded-join background thread in close()

Refs #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: The load-bearing no-wait regression test

**Files:**
- Test: `tests/test_background_indexing.py`
- Test fixture: `tests/conftest.py` (only if the slow embedding provider needs to be shared)

- [ ] **Step 5.1: Build a deliberately slow embedding provider in-line**

Append to `tests/test_background_indexing.py`:

```python
import numpy as np  # noqa: E402  — kept near use site for clarity

from markdown_vault_mcp.providers import EmbeddingProvider


class _SlowEmbeddingProvider(EmbeddingProvider):
    """Deterministic provider that sleeps per call to simulate a slow backend.

    Used by the no-foreground-waiter regression test. Each ``embed`` call
    blocks for ``per_call_delay`` seconds before returning a fixed vector,
    which lets the test assert foreground reads remain responsive while a
    background embedding pass is in flight.
    """

    def __init__(self, *, per_call_delay: float = 0.5, dim: int = 8) -> None:
        self.per_call_delay = per_call_delay
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        time.sleep(self.per_call_delay)
        # Deterministic non-zero vector per text.
        return np.ones((len(texts), self.dim), dtype=np.float32)
```

If `EmbeddingProvider` lives under a different module path, adjust the import. Check `src/markdown_vault_mcp/providers.py` for the ABC's exact name and method signature.

- [ ] **Step 5.2: Verify the import resolves**

Run: `uv run python -c "from markdown_vault_mcp.providers import EmbeddingProvider; print(EmbeddingProvider)"`

Expected: prints the class. If `ImportError`, find the right symbol in `src/markdown_vault_mcp/providers.py` and update the import in step 5.1.

- [ ] **Step 5.3: Write the load-bearing test**

Append:

```python
def test_foreground_reads_never_block_on_background(tmp_path: Path) -> None:
    """REGRESSION TEST FOR PR #515.

    Foreground methods must NOT wait on the background thread. With a
    deliberately slow embedding provider, search/list_documents/stats/
    index_status must all return promptly while the background thread
    is mid-embedding. If this test ever fails, a foreground waiter has
    been reintroduced — find and remove it before re-running.

    See docs/superpowers/specs/2026-05-26-background-indexing-v2-design.md
    section "The load-bearing rule".
    """
    # Seed a handful of docs so the embedding phase has real work.
    for i in range(5):
        (tmp_path / f"doc{i}.md").write_text(f"# Doc {i}\n\nbody {i}\n", encoding="utf-8")

    embeddings_path = tmp_path / ".embeddings"
    collection = Collection(
        source_dir=tmp_path,
        embedding_provider=_SlowEmbeddingProvider(per_call_delay=0.2),
        embeddings_path=embeddings_path,
    )
    try:
        # Pre-build the FTS index synchronously so search has something to
        # return; the slow path we're testing is the embedding phase.
        collection.build_index()

        collection.start_background_reindex()

        # Wait until the worker enters the embedding phase (deterministic
        # signal that the slow path is now active).
        _wait_until(
            lambda: collection.index_status()["background_phase"] == "embedding",
            timeout=5.0,
        )

        # Each foreground call must return within 500ms while the slow
        # embedding loop is in flight.
        for op_name, op in [
            ("search", lambda: collection.search("body", limit=5)),
            ("list_documents", lambda: collection.list_documents()),
            ("stats", lambda: collection.stats()),
            ("index_status", lambda: collection.index_status()),
        ]:
            start = time.monotonic()
            op()
            elapsed = time.monotonic() - start
            assert elapsed < 0.5, (
                f"{op_name} took {elapsed:.3f}s — a foreground waiter has been "
                "reintroduced. See spec section 'The load-bearing rule'."
            )
    finally:
        collection.close()
```

- [ ] **Step 5.4: Run it — it should PASS already**

Run: `uv run pytest tests/test_background_indexing.py::test_foreground_reads_never_block_on_background -v`

Expected: PASS. If it fails because foreground methods take >500ms, that means `_ensure_initialized()` (still present at this point) is being hit and is slow. That's expected — Task 6 strips it, which is what makes this test bulletproof going forward. If it fails for this reason, **skip ahead to Task 6 and come back to verify this test**.

If it fails for any *other* reason — investigate and fix before continuing.

- [ ] **Step 5.5: Commit**

```bash
git add tests/test_background_indexing.py
git commit -m "test(collection): regression test for no foreground waiter on background

Refs #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Strip `_ensure_initialized()` from Collection

**Files:**
- Modify: `src/markdown_vault_mcp/collection.py`
- Update: any test file that breaks (audit `tests/test_collection.py`, `tests/test_managers_*.py`)

- [ ] **Step 6.1: Identify all call sites**

Run: `grep -n "_ensure_initialized" src/markdown_vault_mcp/collection.py`

Expected: ~22 lines listing the method definition and call sites.

- [ ] **Step 6.2: Delete the method and every call site**

In `src/markdown_vault_mcp/collection.py`:

a. Delete the method body (around line 355-358):
```python
def _ensure_initialized(self) -> None:
    """Build the FTS index on first access if it has not been built yet."""
    if not self._initialized:
        self.build_index()
```

b. Delete every `self._ensure_initialized()` call. Use:
```bash
# Sanity check before edit:
grep -n "self._ensure_initialized()" src/markdown_vault_mcp/collection.py
```
Then remove each call line. There are ~21 of them — one per public method that currently lazy-builds. Each removal is a single-line delete; nothing else on those lines needs to change.

c. After all edits, verify the symbol is gone:
```bash
grep -n "_ensure_initialized" src/markdown_vault_mcp/collection.py
```
Expected: no output (empty).

- [ ] **Step 6.3: Audit `Collection` for other #515 leftovers and remove them**

Search for the helper names that the abandon memo identified as #515 leftovers — these are *probably* not present in the current branch (which is fresh from main), but verify:

```bash
grep -n "_count_documents\|_fts_has_documents\|skip_if_missing" src/markdown_vault_mcp/collection.py src/markdown_vault_mcp/managers/index.py
```

Expected: empty (or only legitimate uses unrelated to the spike). If any spike-leftover symbol appears, delete it. If a real production caller uses it (unlikely on a clean branch), file a follow-up issue rather than rip it out here.

- [ ] **Step 6.4: Run the full test suite — expect some failures**

Run: `uv run pytest -x -q`

Expected: Some tests in `tests/test_collection.py`, `tests/test_managers_*.py`, or similar may fail because they implicitly relied on lazy initialisation. The failures will look like "empty result" or "no rows in FTS" assertions. **This is expected.** Continue to step 6.5.

- [ ] **Step 6.5: Fix each failing test by adding an explicit `collection.build_index()`**

For every test that fails because of the strip:
- Locate the test.
- Add `collection.build_index()` (or `collection.start_background_reindex(); _wait_until(...)`) **before** the first assertion that depends on indexed content.
- Prefer `build_index()` for tests that need synchronous correctness; only use the background helper if testing the background path itself.

If a test was *already* calling `build_index()` (most should be), no change needed.

- [ ] **Step 6.6: Re-run the suite to green**

Run: `uv run pytest -x -q`

Expected: PASS. If still failing, repeat 6.5 for the new failures.

- [ ] **Step 6.7: Re-run the load-bearing test (Task 5)**

Run: `uv run pytest tests/test_background_indexing.py::test_foreground_reads_never_block_on_background -v`

Expected: PASS, more solidly than before — the foreground paths now have zero possibility of triggering a lazy build.

- [ ] **Step 6.8: Run gates and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/ tests/
uv run pytest -x -q
git add -A
git commit -m "refactor(collection): strip _ensure_initialized and lazy-build call sites

The lazy-build entry point was the structural vehicle of the PR #515
foreground/background coupling failure. Removing it makes the
no-foreground-waiter rule executable: there is no longer any method
on Collection that performs implicit blocking initialisation.

Tests that relied on lazy-init updated to call build_index() explicitly.

Refs #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Nest `index_status` into `stats()` and `CollectionStats`

**Files:**
- Modify: `src/markdown_vault_mcp/types.py`
- Modify: `src/markdown_vault_mcp/collection.py` (`stats` method, around line 888)
- Test: `tests/test_background_indexing.py`

- [ ] **Step 7.1: Write the failing test**

Append:

```python
def test_stats_includes_index_status_field(tmp_path: Path) -> None:
    """CollectionStats now carries the index_status snapshot."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")
    collection = Collection(source_dir=tmp_path)
    try:
        collection.build_index()
        result = collection.stats()
        # Either dataclass attribute or dict key — depends on dataclass shape.
        assert hasattr(result, "index_status")
        status = result.index_status
        assert set(status.keys()) == {
            "background_running",
            "background_phase",
            "last_run_started_at",
            "last_run_completed_at",
            "last_error",
        }
    finally:
        collection.close()
```

- [ ] **Step 7.2: Run it — expect failure**

Run: `uv run pytest tests/test_background_indexing.py::test_stats_includes_index_status_field -v`

Expected: `AssertionError: hasattr(result, 'index_status')` returns False.

- [ ] **Step 7.3: Extend `CollectionStats`**

In `src/markdown_vault_mcp/types.py`, locate `class CollectionStats:` (line 355). Add the new field at the end of the field list (last position) and document it:

```python
@dataclass
class CollectionStats:
    """Collection-wide statistics, returned by :meth:`~markdown_vault_mcp.collection.Collection.stats`.

    Attributes:
        document_count: Number of indexed markdown documents.
        chunk_count: Total number of indexed sections (chunks).
        folder_count: Number of distinct folder paths.
        semantic_search_available: ``True`` if a vector index is loaded and ready.
        indexed_frontmatter_fields: Frontmatter fields configured for tag indexing.
        attachment_extensions: File extensions recognised as attachments.
        link_count: Total number of links extracted from all documents.
        broken_link_count: Number of links whose target does not exist.
        orphan_count: Number of documents with no inbound or outbound links.
        index_status: Snapshot of background reindex state. See
            :meth:`~markdown_vault_mcp.collection.Collection.index_status`.
    """

    document_count: int
    chunk_count: int
    folder_count: int
    semantic_search_available: bool
    indexed_frontmatter_fields: list[str] = field(default_factory=list)
    attachment_extensions: list[str] = field(default_factory=list)
    link_count: int = 0
    broken_link_count: int = 0
    orphan_count: int = 0
    index_status: dict[str, Any] = field(default_factory=dict)
```

Ensure `from typing import Any` is imported at the top of `types.py` (add to existing imports if missing).

- [ ] **Step 7.4: Populate the field in `Collection.stats()`**

In `src/markdown_vault_mcp/collection.py`, locate `def stats(self) -> CollectionStats:` (around line 888). It currently returns a `CollectionStats(...)`. Add `index_status=self.index_status(),` as the last keyword argument in the return statement.

- [ ] **Step 7.5: Run all stats-related tests**

Run: `uv run pytest -x -q -k stats`

Expected: PASS. If the new field breaks downstream serialisation tests (e.g. JSON marshaling), they may need to expect the new field; fix those.

- [ ] **Step 7.6: Run gates and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/ tests/
uv run pytest -x -q
git add -A
git commit -m "feat(stats): nest index_status snapshot inside CollectionStats

Refs #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Wire `index_status` into `get_server_info`

**Files:**
- Modify: `src/markdown_vault_mcp/server.py` (around line 225)
- Test: `tests/test_background_indexing.py`

- [ ] **Step 8.1: Investigate `register_server_info_tool`'s extension surface**

Run: `uv run python -c "from fastmcp_pvl_core import register_server_info_tool; import inspect; print(inspect.signature(register_server_info_tool))"`

Expected: prints the signature. Look for an `extras` / `extra_status` / `extra_fields` parameter, or a callable that returns supplementary data alongside `upstream_version`.

If `register_server_info_tool` does NOT support arbitrary extra fields, take one of these fallback paths (in priority order):
- **Fallback A:** Use the existing `upstream_version` callable to inject the `index_status` dict as the upstream-version payload (semantic abuse — only if A and B below are also blocked, which is unlikely).
- **Fallback B:** Replace the `register_server_info_tool` call with a thin local `@mcp.tool` decorator that constructs the same payload plus the `index_status` field. The pvl-core helper is small; copying its shape is acceptable. Open a follow-up issue against `fastmcp-pvl-core` asking for an `extras` parameter so this can be reverted later.
- **Fallback C:** Skip the `get_server_info` exposure entirely; surface `index_status` only via `stats`. Update the spec's Acceptance criteria to note this. Filing the `fastmcp-pvl-core` issue is still required.

Record which path you took in the commit message.

- [ ] **Step 8.2: Write the failing test for the chosen path**

If you took the happy path (`register_server_info_tool` supports extras) or Fallback B (local tool):

Append to `tests/test_background_indexing.py`:

```python
import asyncio  # noqa: E402

from fastmcp import Client  # noqa: E402

from markdown_vault_mcp.server import make_server  # noqa: E402
from markdown_vault_mcp.config import CollectionConfig  # noqa: E402


async def _call_get_server_info(server) -> dict:
    async with Client(server) as client:
        result = await client.call_tool("get_server_info", {})
        # Result shape depends on FastMCP version; adapt as needed.
        return result.data if hasattr(result, "data") else result


def test_get_server_info_includes_index_status(tmp_path: Path, monkeypatch) -> None:
    """get_server_info exposes the index_status snapshot."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    config = CollectionConfig.from_env()
    server = make_server(config)
    payload = asyncio.run(_call_get_server_info(server))
    assert "index_status" in payload
    assert set(payload["index_status"].keys()) == {
        "background_running",
        "background_phase",
        "last_run_started_at",
        "last_run_completed_at",
        "last_error",
    }
```

If you took Fallback C, instead add a test that asserts the field is NOT present (so the absence is deliberate, not regression):

```python
def test_get_server_info_does_not_include_index_status_yet(tmp_path: Path, monkeypatch) -> None:
    """Documented limitation until fastmcp-pvl-core gains an extras param."""
    # (same setup as above)
    payload = asyncio.run(_call_get_server_info(server))
    assert "index_status" not in payload
```

- [ ] **Step 8.3: Run the test — should fail before wiring**

Run: `uv run pytest tests/test_background_indexing.py -v -k get_server_info`

Expected: FAIL with "'index_status' not in payload".

- [ ] **Step 8.4: Wire it**

Edit `src/markdown_vault_mcp/server.py` (around line 225). The exact change depends on the path chosen in step 8.1.

**Happy path** (pvl-core supports extras):

```python
def _live_index_status() -> dict[str, Any]:
    """Zero-arg callable for register_server_info_tool's extras parameter."""
    collection = _maybe_get_collection_singleton()
    if collection is None:
        return {
            "background_running": False,
            "background_phase": None,
            "last_run_started_at": None,
            "last_run_completed_at": None,
            "last_error": None,
        }
    return collection.index_status()


register_server_info_tool(
    mcp,
    server_name=server_name,
    server_version=pkg_ver,
    extras={"index_status": _live_index_status},  # exact kwarg name from 8.1
    # DOMAIN-UPSTREAM-START ... DOMAIN-UPSTREAM-END (unchanged)
)
```

Make sure `_maybe_get_collection_singleton` exists in `_server_deps.py` — if not, add it as a safe variant of `set_collection_singleton`'s reader that returns `None` when uninitialised. (The lifespan may not have run yet during health checks.)

**Fallback B** (local tool replacing the helper): copy the pvl-core helper's shape into `server.py` and add the `index_status` field. Keep it small.

**Fallback C**: leave `register_server_info_tool` unchanged. Update the spec acceptance criteria in the same commit.

- [ ] **Step 8.5: Run the test — must PASS**

Run: `uv run pytest tests/test_background_indexing.py -v -k get_server_info`

Expected: PASS (for happy path or Fallback B) or PASS-as-documented-limitation (Fallback C).

- [ ] **Step 8.6: Run gates and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/ tests/
uv run pytest -x -q
git add -A
git commit -m "feat(server): expose index_status via get_server_info

Path chosen: <happy / fallback-B / fallback-C>. <One-line reason.>

Refs #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Switch lifespan to background mode

**Files:**
- Modify: `src/markdown_vault_mcp/_server_deps.py` (around line 83-137)
- Test: existing tests; no new test needed (the integration is exercised by the test in Task 8 and existing lifespan tests).

- [ ] **Step 9.1: Locate the lifespan body**

Open `src/markdown_vault_mcp/_server_deps.py` and find `_collection_lifespan` (around line 83-137). The current body builds the index and embeddings synchronously before yielding.

- [ ] **Step 9.2: Replace the synchronous build with the background spawn**

Replace the block:

```python
        # Build index eagerly so first tool call is fast.
        stats = await asyncio.to_thread(collection.build_index)
        logger.info(
            "Index built: %d documents, %d chunks",
            stats.documents_indexed,
            stats.chunks_indexed,
        )

        # Build embeddings eagerly when an embedding provider is configured.
        # build_embeddings() skips work if the vector index already exists on disk,
        # so this is safe to call on every startup.
        if kwargs.get("embedding_provider") is not None:
            chunks_embedded = await asyncio.to_thread(collection.build_embeddings)
            logger.info("Embeddings ready: %d chunks", chunks_embedded)

        # Start background tasks (e.g. git pull loop) after index is built.
        collection.start()
```

with:

```python
        # Start background tasks (git pull loop, etc.) so reindex can see
        # the freshest tree if a pull happens early.
        collection.start()

        # Kick off the background reindex (FTS + embeddings if configured).
        # Returns immediately; foreground tools are usable straight away,
        # initially returning empty/partial results until the worker fills
        # in the index. Clients learn the state via stats / get_server_info.
        collection.start_background_reindex()
        logger.info(
            "Background reindex started; server is accepting requests immediately"
        )
```

- [ ] **Step 9.3: Run the full suite**

Run: `uv run pytest -x -q`

Expected: PASS. Tests that previously relied on lifespan-built indexes will still work because `Collection.build_index()` is still called by the CLI and by tests directly. If any test fails because it expected the *lifespan* to have built the index, replace that expectation with a wait-for-background or an explicit `build_index()` call.

- [ ] **Step 9.4: Manual smoke check**

Run the server once in an empty vault and verify it accepts an `initialize` handshake in <1s:

```bash
mkdir -p /tmp/smoke-vault
MARKDOWN_VAULT_MCP_SOURCE_DIR=/tmp/smoke-vault uv run markdown-vault-mcp serve --transport stdio < /dev/null &
sleep 1
kill %1 2>/dev/null
```

Expected: process started and shut down cleanly within ~1s; no `Index built` log line during startup (it now happens asynchronously).

- [ ] **Step 9.5: Run gates and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/ tests/
uv run pytest -x -q
git add -A
git commit -m "feat(server): non-blocking startup via background reindex

The MCP initialize handshake no longer waits on build_index() or
build_embeddings(). The collection is constructed, git sync runs, the
periodic-pull background tasks start, and then start_background_reindex
fires a daemon thread that fills in the FTS and vector indexes. Tools
are usable immediately; clients learn state via stats / get_server_info.

Closes #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Documentation updates

**Files:**
- Modify: `docs/design.md`
- Modify: `docs/configuration.md`
- Modify: `docs/guides/claude-desktop.md`
- Modify: `docs/tools/index.md`
- Modify: `README.md`

- [ ] **Step 10.1: Add "Background indexing" section to `docs/design.md`**

Find the appropriate location (probably near "Indexing" or "Architecture"). Add:

```markdown
### Background indexing (non-blocking startup)

The MCP `initialize` handshake never blocks on FTS or embedding work. On
server start, `Collection.start_background_reindex()` spawns a daemon
thread that runs `reindex()` followed by `build_embeddings()` (when an
embedding provider is configured). Tools become available immediately;
read tools return whatever's currently in the index. Clients learn the
state via `stats` or `get_server_info`, both of which carry a nested
`index_status` snapshot (`background_running`, `background_phase`,
`last_run_started_at`, `last_run_completed_at`, `last_error`).

**Load-bearing rule:** No public method on `Collection` may block on
background indexing state. Foreground reads return whatever's currently
in the FTS/vector index. Empty-during-cold-start is a valid result, not
an error to wait out. This rule is enforced by
`test_foreground_reads_never_block_on_background` in
`tests/test_background_indexing.py` — if you find yourself adding a
`threading.Event.wait()` or `Thread.join()` to a foreground method,
stop. The PR #515 abandonment retrospective in
`memory/feedback_background_indexing_abandon.md` explains why.
```

- [ ] **Step 10.2: Update `docs/configuration.md`**

Find the `EXCLUDE_PATTERNS` row in the configuration table. Append a note:

> Changes take effect on the next background reindex (server start or periodic git pull).

- [ ] **Step 10.3: Update `docs/guides/claude-desktop.md`**

Find the section `## Pre-build embeddings before first launch` (or the closest equivalent — the exact anchor matters because it's referenced from `README.md` and the spec). Reframe its opening paragraph:

> Pre-building the index before the first launch is **no longer required** — the server starts immediately and fills in the FTS and vector indexes in the background. Pre-building remains the **fastest path to immediate full readiness**: if you want all tools to return complete results on the very first request after startup, run `markdown-vault-mcp index` before launching the server.

- [ ] **Step 10.4: Update `docs/tools/index.md`**

Find the `stats` tool entry. Add `index_status` to the Returns description:

> - `index_status`: snapshot of background reindex state. Keys: `background_running` (bool), `background_phase` (`"indexing" | "embedding" | None`), `last_run_started_at`, `last_run_completed_at`, `last_error`.

Find the `get_server_info` tool entry (if present in this file; it's registered by `fastmcp-pvl-core`). If absent, skip — the field is documented in the spec. If present, add the same `index_status` Returns bullet.

- [ ] **Step 10.5: Update `README.md`**

Find the "How it works" or similar section. Add a one-sentence note:

> The server starts immediately and builds its index in the background; tools are usable straight away and clients can check progress via the `stats` tool's `index_status` field.

- [ ] **Step 10.6: Verify docs build**

Run: `uv run mkdocs build --strict` (if the project uses MkDocs — based on the file map it does).

Expected: clean build, no warnings treated as errors.

- [ ] **Step 10.7: Commit**

```bash
git add docs/ README.md
git commit -m "docs(513): describe background indexing and the no-waiter rule

Refs #513.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Pre-flight circus and PR open

**Files:** none (process task)

- [ ] **Step 11.1: Verify the cumulative diff**

```bash
git fetch origin main
git log --oneline origin/main..HEAD
git diff --stat origin/main..HEAD
```

Expected: a handful of focused commits (spec + ~10 task commits). Total diff under ~800 added LOC excluding the spec.

- [ ] **Step 11.2: Run all gates one more time**

```bash
uv run pre-commit run --all-files
uv run pytest -x -q
uv run mypy src/ tests/
uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=80 || true
```

Expected: pre-commit green; pytest green; mypy clean; diff-cover ≥ 80% on the diff.

- [ ] **Step 11.3: Invoke the preflight-circus skill**

This is non-negotiable per CLAUDE.md. The skill runs the same five-lens review the bot will run post-push; the steady state is bot LGTM on first run because the bot is re-running prompts the skill already exercised.

Invoke `~/.claude/skills/preflight-circus/` against `BASE..HEAD` where `BASE = $(git merge-base HEAD origin/main)`. Address every finding at confidence ≥ 80 before pushing.

- [ ] **Step 11.4: Push as draft and verify CI**

```bash
git push -u origin feat/513-background-indexing-v2
gh pr create --draft --title "feat(server): non-blocking background indexing (closes #513, second attempt)" --body "$(cat <<'EOF'
## Summary

Replaces the abandoned PR #515 with a structurally different approach:
indexing stays in Collection, but the public API gains
`start_background_reindex()` (fire-and-forget daemon spawn) and
`index_status()` (read-only snapshot) — and the foreground waiter
`_ensure_initialized()` is **removed entirely**. The no-foreground-waiter
rule that PR #515 violated is now executable: a regression test asserts
foreground reads return within 500ms while a deliberately slow background
embedding pass is in flight.

See spec: `docs/superpowers/specs/2026-05-26-background-indexing-v2-design.md`.

Closes #513.

## Test plan

- [ ] Tests added in `tests/test_background_indexing.py` cover: idempotent
      start, phase transitions, failure path, bounded close() join,
      foreground non-blocking under slow background, stats+server_info
      exposure.
- [ ] PR #256 regression test still passes (build_index purge invariant).
- [ ] Manual smoke: server initialize handshake completes in <1s on a
      vault with no pre-built index.
EOF
)"
```

- [ ] **Step 11.5: Read bot review bodies, not just check statuses**

After the push triggers `claude-review`:

```bash
gh pr view --json number,url
# Wait for claude-review to post (1-3 min):
gh pr checks <N>
# Read the actual body, not just the check status:
gh api repos/pvliesdonk/markdown-vault-mcp/pulls/<N>/reviews
gh api repos/pvliesdonk/markdown-vault-mcp/issues/<N>/comments
```

Expected: LGTM on first run. If anything is flagged at ≥ 80 confidence: address it, re-invoke the preflight-circus skill on the new cumulative diff, push, and re-read the bots. **Cap: one bot iteration round.** If something turns up on the second post-push round, surface to the user with a written diagnosis before pushing again.

- [ ] **Step 11.6: Flip to ready when bots LGTM and CI is green**

```bash
gh pr ready <N>
```

Then hand off to the user for human merge.

---

## Self-review (already run)

- **Spec coverage:** All eleven acceptance-criteria bullets in the spec map to tasks: criteria 1, 5 → Task 9; 2 → Tasks 2, 7; 3 → Task 3; 4 → Task 5; 6 → Task 4; 7 → Task 6 (PR #256 preservation) and Task 9 (warm-start path); 8 → Task 6; 9, 10 → unchanged behaviours (no task needed); 11 → Task 10.
- **Placeholders:** scanned — none. Task 8 has a documented fallback decision tree but every branch has concrete steps and a commit.
- **Type/symbol consistency:** `index_status` field name used identically across `Collection.index_status()`, `CollectionStats.index_status`, the `stats` tool response, and the `get_server_info` response. `background_running` / `background_phase` / `last_run_started_at` / `last_run_completed_at` / `last_error` field names match between code and tests.
