"""Unit tests for WriteCallbackDispatcher (issues #599, #601).

Pins the behavioural invariants the dispatcher inherits from the former Vault
callback worker (#175) — on_write=None no-op, lazy+idempotent single worker,
FIFO order, callback-exception isolation, bounded draining close, daemon worker
identity — plus the #601 close/fire lifecycle contract: fire-after-close is a
dropped, logged no-op that does not resurrect the worker; double-close is an
explicit no-op; and the join-timeout warning quantifies the pending count.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from markdown_vault_mcp.write_callback import WriteCallbackDispatcher


def _recorder() -> tuple[list[tuple[Path, str, str]], object]:
    calls: list[tuple[Path, str, str]] = []

    def cb(abs_path: Path, content: str, operation: str) -> None:
        calls.append((abs_path, content, operation))

    return calls, cb


class TestFire:
    def test_fire_invokes_callback(self) -> None:
        calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "body", "write")
        dispatcher.close()  # drains + joins -> callback has run
        assert calls == [(Path("a.md"), "body", "write")]

    def test_fires_in_submission_order(self) -> None:
        calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        for i in range(5):
            dispatcher.fire(Path(f"{i}.md"), str(i), "write")
        dispatcher.close()
        assert [content for _, content, _ in calls] == ["0", "1", "2", "3", "4"]

    def test_noop_when_on_write_none(self) -> None:
        dispatcher = WriteCallbackDispatcher(None)
        dispatcher.fire(Path("a.md"), "body", "write")
        assert dispatcher._worker is None  # no worker started
        dispatcher.close()  # safe with no worker

    def test_worker_is_daemon_named(self) -> None:
        _calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "body", "write")
        assert dispatcher._worker is not None
        assert dispatcher._worker.daemon is True
        assert dispatcher._worker.name == "write-callback"
        dispatcher.close()

    def test_idempotent_worker_start(self) -> None:
        _calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "x", "write")
        first = dispatcher._worker
        dispatcher.fire(Path("b.md"), "y", "write")
        assert dispatcher._worker is first  # not restarted
        dispatcher.close()


class TestCallbackException:
    def test_worker_continues_after_exception(self, caplog) -> None:
        calls: list[str] = []

        def cb(_abs_path: Path, content: str, _operation: str) -> None:
            if content == "bad":
                raise RuntimeError("boom")
            calls.append(content)

        dispatcher = WriteCallbackDispatcher(cb)
        with caplog.at_level(logging.ERROR):
            dispatcher.fire(Path("a.md"), "bad", "write")
            dispatcher.fire(Path("b.md"), "good", "write")
            dispatcher.close()
        assert calls == ["good"]  # second item processed despite first raising
        assert any("Write callback failed" in r.getMessage() for r in caplog.records)


class TestClose:
    def test_close_when_worker_never_started(self) -> None:
        _calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.close()  # no fire -> no worker; must not error or hang
        assert dispatcher._worker is None

    def test_close_is_idempotent(self) -> None:
        calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "body", "write")
        dispatcher.close()
        dispatcher.close()  # second close safe
        assert calls == [(Path("a.md"), "body", "write")]

    def test_close_warns_when_worker_hangs(self, caplog) -> None:
        started = threading.Event()
        release = threading.Event()

        def cb(_abs_path: Path, _content: str, _operation: str) -> None:
            started.set()
            release.wait(5)  # block the worker

        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "body", "write")
        assert started.wait(2)  # worker is now blocked inside the callback
        with caplog.at_level(logging.WARNING):
            dispatcher.close(timeout=0.05)  # join times out -> warn
        assert any("did not finish" in r.getMessage() for r in caplog.records)
        release.set()  # let the daemon worker exit

    def test_close_timeout_warning_quantifies_pending(self, caplog) -> None:
        """The hang warning must report how many commits are at risk (#601)."""
        started = threading.Event()
        release = threading.Event()

        def cb(_abs_path: Path, _content: str, _operation: str) -> None:
            started.set()
            release.wait(5)  # block the worker on the first item

        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "first", "write")
        assert started.wait(2)
        dispatcher.fire(Path("b.md"), "second", "write")  # queued behind the block
        with caplog.at_level(logging.WARNING):
            dispatcher.close(timeout=0.05)
        warning = next(
            r.getMessage() for r in caplog.records if "did not finish" in r.getMessage()
        )
        # Worker is blocked on "first" (in-flight); "second" is queued; close()
        # adds the sentinel. qsize() = [second, sentinel] = 2, which equals the
        # commits genuinely at risk: the in-flight "first" + the queued "second".
        assert "2 pending" in warning, warning
        release.set()


class TestThreadContract:
    """#601: enforce the close/fire lifecycle contract by the type, not by
    caller convention."""

    def test_fire_after_close_is_dropped(self, caplog) -> None:
        calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "before", "write")
        dispatcher.close()
        assert calls == [(Path("a.md"), "before", "write")]
        worker_after_close = dispatcher._worker

        with caplog.at_level(logging.WARNING):
            dispatcher.fire(Path("b.md"), "after", "write")
        dispatcher.close()  # idempotent; nothing new to drain

        # The post-close fire must NOT run the callback...
        assert calls == [(Path("a.md"), "before", "write")]
        # ...nor resurrect a fresh worker thread.
        assert dispatcher._worker is worker_after_close
        assert any("after close" in r.getMessage().lower() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_double_close_is_idempotent_noop(self) -> None:
        calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "x", "write")
        dispatcher.close()
        worker = dispatcher._worker
        dispatcher.close()  # second close: explicit no-op via the closed flag
        assert dispatcher._worker is worker  # unchanged
        assert calls == [(Path("a.md"), "x", "write")]


class TestDrain:
    def test_drain_waits_for_all_queued_items(self) -> None:
        calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        for i in range(5):
            dispatcher.fire(Path(f"{i}.md"), str(i), "write")
        assert dispatcher.drain() is True  # must block until all 5 have run
        assert [content for _, content, _ in calls] == ["0", "1", "2", "3", "4"]
        dispatcher.close()

    def test_drain_keeps_worker_alive(self) -> None:
        calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "a", "write")
        dispatcher.drain()
        worker_after_drain = dispatcher._worker
        dispatcher.fire(Path("b.md"), "b", "write")
        dispatcher.drain()
        assert dispatcher._worker is worker_after_drain
        assert [content for _, content, _ in calls] == ["a", "b"]
        dispatcher.close()

    def test_drain_noop_when_worker_never_started(self) -> None:
        _calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        assert (
            dispatcher.drain() is True
        )  # no fire -> no worker; must return immediately, not hang
        assert dispatcher._worker is None
        dispatcher.close()

    def test_drain_noop_when_closed(self) -> None:
        calls, cb = _recorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "a", "write")
        dispatcher.close()
        assert dispatcher.drain() is True  # after close: immediate no-op, no hang
        assert calls == [(Path("a.md"), "a", "write")]

    def test_drain_noop_when_on_write_none(self) -> None:
        dispatcher = WriteCallbackDispatcher(None)
        assert dispatcher.drain() is True  # no callback configured -> immediate no-op
        assert dispatcher._worker is None

    def test_drain_timeout_warns(self, caplog) -> None:
        started = threading.Event()
        release = threading.Event()

        def cb(_abs_path: Path, _content: str, _operation: str) -> None:
            started.set()
            release.wait(5)  # block the worker

        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "first", "write")
        assert started.wait(2)  # worker blocked on the in-flight item
        dispatcher.fire(Path("b.md"), "second", "write")  # queued behind the block
        with caplog.at_level(logging.WARNING):
            drained = dispatcher.drain(
                timeout=0.05
            )  # cannot drain -> warn, return False
        assert drained is False
        warning = next(
            r.getMessage()
            for r in caplog.records
            if "drain did not finish" in r.getMessage()
        )
        assert "2 pending" in warning, warning
        release.set()
        dispatcher.close()

    @pytest.mark.filterwarnings(
        # The SystemExit intentionally terminates the write-callback worker.
        "ignore::pytest.PytestUnhandledThreadExceptionWarning"
    )
    def test_drain_returns_false_when_worker_died(self, caplog) -> None:
        """If the worker thread dies on a BaseException, drain() must report
        False (not silently succeed) and the death must have been logged (#571)."""

        def cb(_abs_path: Path, _content: str, _operation: str) -> None:
            raise SystemExit("worker dies")  # BaseException kills the thread

        dispatcher = WriteCallbackDispatcher(cb)
        with caplog.at_level(logging.ERROR):
            dispatcher.fire(Path("a.md"), "x", "write")
            assert dispatcher._worker is not None
            dispatcher._worker.join(timeout=2)  # wait for the worker to die
            assert not dispatcher._worker.is_alive()
            assert dispatcher.drain(timeout=0.5) is False
        messages = [r.getMessage() for r in caplog.records]
        assert any("write_callback_worker_died" in m for m in messages), messages
        # The dead-worker drain reports the stranded backlog including the
        # in-flight commit the worker died on (already dequeued, so qsize()+1).
        assert any("found a dead worker" in m and "1 pending" in m for m in messages), (
            messages
        )
        dispatcher.close()
