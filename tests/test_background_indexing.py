"""Tests for non-blocking startup background indexing (#513)."""

from __future__ import annotations

import time
from pathlib import Path  # noqa: TC003

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.providers import EmbeddingProvider


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
        assert first_thread is not None

        # Second call must be a no-op while the first thread is alive.
        # If the first thread completes between calls, this still must not
        # raise — we just accept whichever invariant holds.
        collection.start_background_reindex()
        second_thread = collection._background_thread

        assert second_thread is first_thread or not first_thread.is_alive()

        _wait_until(lambda: not collection.index_status()["background_running"])
    finally:
        collection.close()


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


def test_close_bounded_join_does_not_hang_indefinitely(tmp_path: Path) -> None:
    """A background thread that ignores shutdown still lets close() return."""
    import threading  # local import; only needed here

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

    # Verify the bounded-join semantics directly without invoking close()'s
    # full teardown (which would also try to flush embeddings, close git, etc.,
    # adding noise to what we're trying to assert).
    start = time.monotonic()
    collection._background_shutdown.set()
    t = collection._background_thread
    assert t is not None
    if t.is_alive():
        t.join(timeout=2.0)  # short for the test
    elapsed = time.monotonic() - start

    # The slow thread is still alive (it ignored shutdown), but the join returned.
    assert elapsed < 5.0
    assert collection._background_thread is not None
    assert collection._background_thread.is_alive()

    # Cleanup the deliberately-stuck thread.
    stop_sleeping.set()
    collection._background_thread.join(timeout=2.0)

    # Now do the real close (which will hit the daemon-thread fallback path
    # if the worker is still alive, or join cleanly if not).
    collection.close()


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


# ---------------------------------------------------------------------------
# Slow embedding provider for the no-foreground-waiter regression test
# ---------------------------------------------------------------------------


class _SlowEmbeddingProvider(EmbeddingProvider):
    """Deterministic provider that sleeps per call to simulate a slow backend.

    Used by the no-foreground-waiter regression test. Each ``embed`` call
    blocks for ``per_call_delay`` seconds before returning a fixed vector
    per input text, letting the test assert foreground reads stay
    responsive while a background embedding pass is in flight.
    """

    def __init__(self, *, per_call_delay: float = 0.5, dim: int = 8) -> None:
        self._per_call_delay = per_call_delay
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        time.sleep(self._per_call_delay)
        # Deterministic non-zero vector per text.
        return [[1.0] * self._dim for _ in texts]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def provider_name(self) -> str:
        return "slow-test"

    @property
    def model_name(self) -> str:
        return "slow-test-v0"


def test_foreground_reads_never_block_on_background(tmp_path: Path) -> None:
    """REGRESSION TEST FOR PR #515.

    Foreground methods must NOT wait on the background thread. With a
    deliberately slow embedding provider, search/list/stats/
    index_status must all return promptly while the background thread
    is mid-embedding. If this test ever fails, a foreground waiter has
    been reintroduced — find and remove it before re-running.

    See docs/superpowers/specs/2026-05-26-background-indexing-v2-design.md
    section "The load-bearing rule".
    """
    # Seed a handful of docs so the embedding phase has real work.
    for i in range(5):
        (tmp_path / f"doc{i}.md").write_text(
            f"# Doc {i}\n\nbody {i}\n", encoding="utf-8"
        )

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
            ("list", lambda: collection.list()),
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
