"""One process writes a built-in vector store at a time (#1505).

A numpy or SQLite writer holds an advisory lock on ``{base}.lock`` (named with
``Path.with_suffix``, #819) while it is open; a second process that opens a
writer on the same base is refused with :class:`StoreBusyError`, so a batch
command cannot write a store a running server is writing. Readers take no
lock. The operating system drops the lock when its process dies, so a killed
server leaves none behind; the file itself is never deleted. Inside one
process every writer on a base shares one lock, counted, so a vault that opens
a fresh store before closing the old one, or two vaults in one process, never
block each other.

Where no lock can be taken (no ``fcntl``, as on Windows, or a lock file that
cannot be created) the writer proceeds as before this lock existed; its own
writes still report a real I/O fault.
"""

from __future__ import annotations

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


class WriterLock:
    """One writer's share of a base path's lock; :meth:`release` is idempotent."""

    def __init__(self, key: str | None) -> None:
        self._key = key

    def release(self) -> None:
        """Give this writer's share back; the last share unlocks the file."""
        with _GUARD:
            key, self._key = self._key, None
            held = _HELD.get(key) if key is not None else None
            if key is None or held is None:
                return
            held.count -= 1
            if held.count == 0:
                del _HELD[key]
                with contextlib.suppress(OSError):
                    fcntl.flock(held.fd, fcntl.LOCK_UN)
                os.close(held.fd)


def hold_writer_lock(base: Path, *, backend: str) -> WriterLock:
    """Take a writer's share of the lock on *base*.

    Args:
        base: The store's base path; the lock file is ``{base}.lock``.
        backend: The backend's registry name, for the refusal.

    Returns:
        The share, to release when the store closes.

    Raises:
        StoreBusyError: If another process holds the lock.
    """
    path = Path(base).with_suffix(LOCK_SUFFIX)
    if fcntl is None:
        return _skipped(path, "unsupported")
    key = os.path.realpath(path)
    with _GUARD:
        held = _HELD.get(key)
        if held is not None:
            held.count += 1
            return WriterLock(key)
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            return _skipped(path, "unwritable")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = _holder(fd)
            os.close(fd)
            raise StoreBusyError(
                f"Vector store at {base} is being written by another process{holder}.",
                backend=backend,
                location=str(base),
            ) from None
        except OSError:
            os.close(fd)
            return _skipped(path, "unwritable")
        with contextlib.suppress(OSError):
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        _HELD[key] = _Held(fd)
        return WriterLock(key)


def _holder(fd: int) -> str:
    """Return `` (pid N)`` for the process the lock file names, else ``""``."""
    with contextlib.suppress(OSError, ValueError):
        return f" (pid {int(os.pread(fd, 32, 0).decode().strip())})"
    return ""


def _skipped(path: Path, reason: str) -> WriterLock:
    """Return a share that locks nothing, saying why."""
    logger.debug("embeddings_writer_lock_skipped path=%s reason=%s", str(path), reason)
    return WriterLock(None)
