"""A bounded record of the link rewrites this server made to notes (#1492).

Renaming a note rewrites the links to it in other notes, which changes
those notes' etags. A client that sent a conditional rename of one of
them in the same breath holds an etag that is now stale, though nothing
but the server's own rewrite changed the note. The record lets
:meth:`DocumentManager.rename` tell that case apart from every other
change: each rewrite stores the note's hash before and after, and an
etag is accepted only when an unbroken chain of recorded rewrites leads
from it to the note's current hash.

The record is evidence, not history. It lives in memory, holds a bounded
number of entries for a bounded time, and an entry it no longer holds
simply makes the rename strict again: a restart, an expiry or an
eviction can only refuse a rename, never accept one.

Callers hold the vault's file write lock around both :meth:`record` and
:meth:`rewrites_since`, the same lock every write takes, so the record
needs no lock of its own.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["LinkRewriteLedger"]

_CAPACITY = 256
_TTL_S = 300.0


class LinkRewriteLedger:
    """Recent link rewrites, keyed by note path and the hash they produced.

    Args:
        capacity: Most entries held; recording past it evicts the oldest.
        ttl_s: Seconds an entry counts as evidence after it is recorded.
        clock: Monotonic clock, replaceable in tests.
    """

    def __init__(
        self,
        *,
        capacity: int = _CAPACITY,
        ttl_s: float = _TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._ttl_s = ttl_s
        self._clock = clock
        # (path, hash_after) -> (hash_before, recorded_at), oldest first.
        self._entries: OrderedDict[tuple[str, str], tuple[str, float]] = OrderedDict()

    def record(self, path: str, before: str, after: str) -> None:
        """Record that a link rewrite changed *path* from *before* to *after*.

        Args:
            path: Vault-relative path of the rewritten note.
            before: Hash of the note's bytes before the rewrite.
            after: Hash of the note's bytes after the rewrite.
        """
        now = self._clock()
        self._expire(now)
        key = (path, after)
        self._entries.pop(key, None)
        self._entries[key] = (before, now)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def rewrites_since(self, path: str, *, etag: str, current: str) -> int | None:
        """Count the recorded rewrites that lead from *etag* to *current*.

        Args:
            path: Vault-relative path of the note.
            etag: The hash the caller last read.
            current: The note's hash now.

        Returns:
            The number of rewrites crossed (``0`` when the hashes are equal),
            or ``None`` when a step from *etag* to *current* is not a
            recorded, unexpired rewrite of *path*.
        """
        self._expire(self._clock())
        crossed = 0
        step = current
        while step != etag:
            entry = self._entries.get((path, step))
            if entry is None or crossed >= len(self._entries):
                # No evidence for this step, or the walk is going round a
                # cycle (a note renamed away and back) without reaching etag.
                return None
            step = entry[0]
            crossed += 1
        return crossed

    def _expire(self, now: float) -> None:
        """Drop entries recorded more than ``ttl_s`` before *now*."""
        while self._entries:
            _key, (_before, recorded_at) = next(iter(self._entries.items()))
            if now - recorded_at <= self._ttl_s:
                return
            self._entries.popitem(last=False)
