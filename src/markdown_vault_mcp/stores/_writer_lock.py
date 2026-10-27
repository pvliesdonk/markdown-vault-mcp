"""One process writes a built-in vector store, or a keyword index, at a time.

A numpy or SQLite vector writer holds an advisory lock on ``{base}.lock``
(named with ``Path.with_suffix``, #819) while it is open (#1505); an owning
keyword index holds one on ``{index}.lock``, its full name plus ``.lock``
(#1890). A second process that opens a writer on the same file is refused
with :class:`StoreBusyError`, so a batch command cannot write a store a
running server is writing. Readers take no
lock. The operating system drops the lock when its process dies, so a killed
server leaves none behind; the file itself is never deleted, and the pid it
names is cleared on the last release. Inside one process every writer on a
base shares one lock, counted, so two vaults in one process never block each
other; a caller that closes one store and opens another on the same base
holds a share across the swap (the backends' ``hold_writer``), so another
process cannot take the lock in between. A store collected unclosed gives its
share back through a finalizer that never waits for the guard, and a forked
child holds nothing.

Where no lock can be taken (no ``fcntl``, as on Windows, or a lock file that
cannot be created) the writer proceeds as before this lock existed; its own
writes still report a real I/O fault. A lock file this user cannot write is
locked read-only.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import os
import threading
from pathlib import Path

from markdown_vault_mcp.exceptions import StoreBusyError

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

LOCK_SUFFIX = ".lock"


class _Held:
    """A lock file this process holds, and how many writers share it."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.count = 1


_HELD: dict[str, _Held] = {}
_GUARD = threading.Lock()
# Shares a garbage-collected store gave back while _GUARD was taken: the
# finalizer may run inside a lock operation on the same thread, so it never
# waits for _GUARD; the next operation returns them.
_PENDING: collections.deque[WriterLock] = collections.deque()


class WriterLock:
    """One writer's share of a base path's lock; :meth:`release` is idempotent."""

    def __init__(self, key: str | None) -> None:
        self._key = key

    def release(self) -> None:
        """Give this writer's share back; the last share unlocks the file."""
        with _GUARD:
            self._give_back()
            _drain()

    def _give_back(self) -> None:
        """Return the share; called with ``_GUARD`` held."""
        key, self._key = self._key, None
        held = _HELD.get(key) if key is not None else None
        if key is None or held is None:
            return
        held.count -= 1
        if held.count == 0:
            del _HELD[key]
            _unlock(held.fd)


def release_from_finalizer(share: WriterLock) -> None:
    """Give *share* back from a store's finalizer, never waiting for the guard.

    Args:
        share: The collected store's share.
    """
    _PENDING.append(share)
    if _GUARD.acquire(blocking=False):
        try:
            _drain()
        finally:
            _GUARD.release()


def hold_writer_lock(
    base: Path,
    *,
    backend: str,
    lock_file: Path | None = None,
    label: str = "Vector store",
) -> WriterLock:
    """Take a writer's share of the lock on *base*.

    The store's directory is created if need be, as its first write would,
    so the lock covers a store from its first open.

    Args:
        base: The store's base path, named in the refusal.
        backend: The backend's registry name, for the refusal.
        lock_file: The lock file; ``None`` means ``{base}.lock`` by
            ``Path.with_suffix``, the vector stores' naming.
        label: What the refusal says is being written.

    Returns:
        The share, to release when the store closes.

    Raises:
        StoreBusyError: If another process holds the lock.
    """
    path = (
        Path(lock_file)
        if lock_file is not None
        else Path(base).with_suffix(LOCK_SUFFIX)
    )
    if fcntl is None:
        return _skipped(path, "unsupported")
    key = os.path.realpath(path)
    with _GUARD:
        _drain()
        held = _HELD.get(key)
        if held is not None:
            held.count += 1
            return WriterLock(key)
        fd = _open_lock_file(path)
        if fd is None:
            return _skipped(path, "unwritable")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = _holder(fd)
            os.close(fd)
            raise StoreBusyError(
                f"{label} at {base} is being written by another process"
                f"{holder}; stop that process first.",
                backend=backend,
                location=str(base),
            ) from None
        except OSError:
            os.close(fd)
            return _skipped(path, "unwritable")
        with contextlib.suppress(OSError):  # a read-only lock file names no pid
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        _HELD[key] = _Held(fd)
        return WriterLock(key)


def _open_lock_file(path: Path) -> int | None:
    """Open the lock file, creating it and its directory; ``None`` if impossible.

    A lock file this user may not write (another user's, say root's through
    ``docker exec``) is opened read-only: ``flock`` needs no write access.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except PermissionError:
        with contextlib.suppress(OSError):
            return os.open(path, os.O_RDONLY)
    except OSError:
        pass
    return None


def _unlock(fd: int) -> None:
    """Clear the pid, then unlock and close: a stale pid is never named."""
    with contextlib.suppress(OSError):
        os.ftruncate(fd, 0)
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _drain() -> None:
    """Return the shares finalizers left; called with ``_GUARD`` held."""
    while _PENDING:
        _PENDING.popleft()._give_back()


def _holder(fd: int) -> str:
    """Return `` (pid N)`` for the other process the lock file names, else ``""``."""
    with contextlib.suppress(OSError, ValueError):
        pid = int(os.pread(fd, 32, 0).decode().strip())
        if pid != os.getpid():
            return f" (pid {pid})"
    return ""


def _skipped(path: Path, reason: str) -> WriterLock:
    """Return a share that locks nothing, saying why."""
    logger.debug("writer_lock_skipped path=%s reason=%s", str(path), reason)
    return WriterLock(None)


def _forget_after_fork() -> None:  # pragma: no cover - runs in a forked child
    """In a forked child, hold nothing: the parent still holds its locks."""
    global _GUARD
    _GUARD = threading.Lock()
    for held in _HELD.values():
        with contextlib.suppress(OSError):
            os.close(held.fd)  # the parent's copy keeps the lock
    _HELD.clear()
    _PENDING.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_forget_after_fork)
