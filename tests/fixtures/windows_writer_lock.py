"""Fake the Windows CRT byte-lock API for writer-lock tests."""

import os


class FakeMsvcrt:
    """Record byte-range operations and inject lock errors."""

    LK_UNLCK = 0
    LK_LOCK = 1
    LK_NBLCK = 2

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int]] = []
        self.modes: list[int] = []
        self.error: int | None = None

    def setmode(self, _fd: int, flags: int) -> int:
        """Record a translation-mode change.

        Args:
            _fd: Open lock-file descriptor, unused.
            flags: Requested translation mode.

        Returns:
            The previous mode, which the caller ignores.
        """
        self.modes.append(flags)
        return 0

    def locking(self, fd: int, mode: int, size: int) -> None:
        """Record a lock request and raise the configured error.

        Args:
            fd: Open lock-file descriptor.
            mode: Requested locking operation.
            size: Number of bytes in the range.
        """
        self.calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), size))
        if mode == self.LK_NBLCK and self.error is not None:
            raise OSError(self.error, "lock failed")
