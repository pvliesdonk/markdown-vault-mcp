"""What the harness reads from Linux about a process (#1895).

Memory is split the way the kernel splits resident pages. Anonymous pages
(heap, arrays, shared memory) are the process's own and count as its memory.
File-backed pages are the index and store held in page cache, which the
kernel can reclaim; they are reported apart and never counted as process
memory. The parsers take the files' text, so they are tested without
``/proc``.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import TracebackType

_KIB = 1024
_STATUS_KEYS = ("VmHWM", "VmRSS", "RssAnon", "RssFile", "RssShmem")


@dataclass(frozen=True)
class Memory:
    """A process's resident memory, in bytes.

    Attributes:
        anon: Its own pages: ``Pss_Anon`` plus ``Pss_Shmem``.
        file: Page-cache pages mapped into it: ``Pss_File``.
    """

    anon: int
    file: int


def _kib_fields(text: str) -> dict[str, int]:
    """Read ``Key: <n> kB`` lines into bytes; other lines are skipped."""
    fields = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if len(parts) == 2 and parts[1] == "kB" and parts[0].isdigit():
            fields[key.strip()] = int(parts[0]) * _KIB
    return fields


def parse_smaps_rollup(text: str) -> Memory:
    """Read ``/proc/<pid>/smaps_rollup``.

    Args:
        text: The file's contents.

    Returns:
        The process's anonymous and file-backed proportional set sizes.
    """
    fields = _kib_fields(text)
    return Memory(
        anon=fields["Pss_Anon"] + fields.get("Pss_Shmem", 0),
        file=fields["Pss_File"],
    )


def parse_status(text: str) -> dict[str, int]:
    """Read the resident counters of ``/proc/<pid>/status``.

    Args:
        text: The file's contents.

    Returns:
        ``VmHWM``, ``VmRSS``, ``RssAnon``, ``RssFile`` and ``RssShmem`` in
        bytes, those present.
    """
    fields = _kib_fields(text)
    return {key: fields[key] for key in _STATUS_KEYS if key in fields}


def parse_io(text: str) -> dict[str, int]:
    """Read ``/proc/<pid>/io``.

    Args:
        text: The file's contents.

    Returns:
        Each counter, such as ``wchar`` (bytes passed to write calls) and
        ``write_bytes`` (bytes sent to the block layer).
    """
    counters = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if value.strip().isdigit():
            counters[key.strip()] = int(value)
    return counters


def read_memory(pid: int) -> Memory:
    """Return *pid*'s resident memory now.

    Args:
        pid: The process.

    Returns:
        Its anonymous and file-backed memory.
    """
    return parse_smaps_rollup(Path(f"/proc/{pid}/smaps_rollup").read_text())


def read_io(pid: int) -> dict[str, int]:
    """Return *pid*'s I/O counters now.

    Args:
        pid: The process.

    Returns:
        The counters of ``/proc/<pid>/io``.
    """
    return parse_io(Path(f"/proc/{pid}/io").read_text())


class PeakSampler:
    """Track this process's highest resident memory while it is entered.

    A background thread reads ``/proc/self/status`` every *interval_s*
    seconds, so a peak that is freed before the block ends is still seen.

    Attributes:
        peak_anon: The highest ``RssAnon`` plus ``RssShmem`` seen, in bytes.
        peak_resident: The highest ``VmRSS`` seen, in bytes.
    """

    def __init__(self, interval_s: float = 0.01) -> None:
        """Sample every *interval_s* seconds.

        Args:
            interval_s: The pause between samples.
        """
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.peak_anon = 0
        self.peak_resident = 0

    def sample(self) -> None:
        """Take one sample now."""
        status = parse_status(Path("/proc/self/status").read_text())
        anon = status.get("RssAnon", 0) + status.get("RssShmem", 0)
        with self._lock:
            self.peak_anon = max(self.peak_anon, anon)
            self.peak_resident = max(self.peak_resident, status.get("VmRSS", 0))

    def wait_for_sample(self) -> bool:
        """Take a sample now, on the calling thread.

        A sample the background thread read before this call could land
        after it, so waiting on the thread could return stale figures; a
        sample taken here cannot.

        Returns:
            Always ``True``, so callers can assert it.
        """
        self.sample()
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self._interval_s)

    def __enter__(self) -> Self:
        """Start sampling, with one sample taken before the block runs."""
        self.sample()
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop sampling, after one last sample."""
        self._stop.set()
        self._thread.join()
        self.sample()


def evict(paths: Iterable[Path]) -> list[Path]:
    """Drop the clean page-cache pages of each existing file.

    Each file is synced, then advised with
    ``posix_fadvise(POSIX_FADV_DONTNEED)``, which needs no privileges but
    drops only clean, unmapped pages, and none at all on tmpfs; whether they
    left the cache is what :func:`resident_bytes` checks.

    Args:
        paths: The files; missing ones are skipped.

    Returns:
        The files advised.
    """
    advised = []
    for path in paths:
        try:
            fd = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            continue
        try:
            os.fsync(fd)
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
        advised.append(path)
    return advised


def resident_bytes(paths: Iterable[Path]) -> int | None:
    """Return how many bytes of *paths* sit in page cache.

    Args:
        paths: Existing files.

    Returns:
        The total from util-linux ``fincore``, or ``None`` when it is not
        installed or cannot read them.
    """
    fincore = shutil.which("fincore")
    names = [str(p) for p in paths]
    if fincore is None or not names:
        return None
    run = subprocess.run(
        [fincore, "--bytes", "--noheadings", "--output", "RES", *names],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if run.returncode:
        return None
    return sum(int(line) for line in run.stdout.split())


def parse_process_age(stat: str, uptime: str, ticks_per_second: int) -> float:
    """Return a process's age from ``/proc/<pid>/stat`` and ``/proc/uptime``.

    Args:
        stat: The stat file's contents; field 22 is the start time in clock
            ticks after boot.
        uptime: ``/proc/uptime``'s contents; its first field is seconds
            since boot.
        ticks_per_second: The kernel's clock ticks per second.

    Returns:
        The seconds since the process started.
    """
    after_name = stat.rsplit(")", 1)[1].split()  # the name may hold spaces
    started = int(after_name[19]) / ticks_per_second  # field 22 overall
    return float(uptime.split()[0]) - started


def process_age() -> float:
    """Return this process's age in seconds, from interpreter start.

    Returns:
        The age, to the kernel's clock-tick resolution.
    """
    return parse_process_age(
        Path("/proc/self/stat").read_text(),
        Path("/proc/uptime").read_text(),
        os.sysconf("SC_CLK_TCK"),
    )


def percentiles(values: Iterable[float]) -> dict[str, float]:
    """Return the nearest-rank p50 and p95 of *values*.

    Args:
        values: The samples.

    Returns:
        ``{"p50": ..., "p95": ...}``.

    Raises:
        ValueError: If there are no samples.
    """
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no samples")

    def rank(p: float) -> float:
        return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]

    return {"p50": rank(50), "p95": rank(95)}
