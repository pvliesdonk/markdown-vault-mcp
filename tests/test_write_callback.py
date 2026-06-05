"""Unit tests for WriteCallbackDispatcher (issue #599).

Pins the six behavioural invariants the dispatcher inherits from the former
Vault callback worker (#175): on_write=None no-op, lazy+idempotent single
worker, FIFO order, callback-exception isolation, bounded draining close, and
the daemon worker identity.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

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
