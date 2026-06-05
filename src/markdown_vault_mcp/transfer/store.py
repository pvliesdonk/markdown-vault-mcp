"""In-memory token store for one-time HTTP transfer links (#622)."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

_LEASE_SECONDS = 300.0

_Kind = Literal["download", "upload"]


@dataclass(frozen=True)
class TransferToken:
    """An immutable record describing one transfer capability.

    Attributes:
        token: The unguessable URL-safe token (the capability secret).
        kind: ``"download"`` or ``"upload"``.
        path: The resolved vault-relative path the token operates on.
        is_attachment: ``True`` for a non-.md attachment, ``False`` for a note.
        expires_at: Wall-clock epoch after which the token is invalid.
        max_upload_bytes: Per-upload size cap (upload tokens only; else ``None``).
    """

    token: str
    kind: _Kind
    path: str
    is_attachment: bool
    expires_at: float
    max_upload_bytes: int | None = None


@dataclass
class _Entry:
    """Mutable store entry wrapping a token with its reservation state."""

    record: TransferToken
    status: Literal["available", "in_flight", "consumed"] = "available"
    lease_expires_at: float | None = None


class TransferStore:
    """Thread-safe in-memory store of one-time transfer tokens.

    The state machine per token is ``available → in_flight → consumed``;
    a failed or abandoned ``in_flight`` reservation becomes claimable again —
    explicitly via :meth:`release` (which resets it to ``available``), or
    implicitly once its lease expires (:meth:`claim` treats a stale in-flight
    entry as available) — so a one-time link survives a transient failure.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        lease_seconds: float = _LEASE_SECONDS,
    ) -> None:
        """Initialise the store.

        Args:
            clock: Zero-arg callable returning epoch seconds (injectable for tests).
            lease_seconds: How long an ``in_flight`` reservation is held before
                a crashed handler's token becomes reclaimable.
        """
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def now(self) -> float:
        """Return the current epoch time from the injected clock."""
        return self._clock()

    def create(
        self,
        kind: _Kind,
        path: str,
        is_attachment: bool,
        ttl_seconds: float,
        max_upload_bytes: int | None = None,
    ) -> TransferToken:
        """Mint a new token, insert it as ``available``, and return it.

        Args:
            kind: ``"download"`` or ``"upload"``.
            path: Resolved vault-relative path.
            is_attachment: Whether *path* is a non-.md attachment.
            ttl_seconds: Lifetime from now until expiry.
            max_upload_bytes: Size cap for upload tokens.

        Returns:
            The created :class:`TransferToken`.
        """
        record = TransferToken(
            token=secrets.token_urlsafe(32),
            kind=kind,
            path=path,
            is_attachment=is_attachment,
            expires_at=self._clock() + ttl_seconds,
            max_upload_bytes=max_upload_bytes,
        )
        with self._lock:
            self._sweep_expired_locked()
            self._entries[record.token] = _Entry(record=record)
        return record

    def claim(self, token: str, kind: _Kind) -> TransferToken | None:
        """Atomically reserve a token for use, or return ``None``.

        Returns the record and moves it to ``in_flight`` iff it exists, is
        unexpired, matches *kind*, is not consumed, and is not already
        reserved under a live lease. Otherwise returns ``None``.
        """
        now = self._clock()
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            if entry.record.expires_at <= now:
                return None
            if entry.record.kind != kind:
                return None
            if entry.status == "consumed":
                return None
            if (
                entry.status == "in_flight"
                and entry.lease_expires_at is not None
                and entry.lease_expires_at > now
            ):
                return None
            entry.status = "in_flight"
            entry.lease_expires_at = now + self._lease_seconds
            return entry.record

    def complete(self, token: str) -> None:
        """Mark a token consumed (idempotent)."""
        with self._lock:
            entry = self._entries.get(token)
            if entry is not None:
                entry.status = "consumed"
                entry.lease_expires_at = None

    def release(self, token: str) -> None:
        """Return an ``in_flight`` token to ``available`` (idempotent)."""
        with self._lock:
            entry = self._entries.get(token)
            if entry is not None and entry.status == "in_flight":
                entry.status = "available"
                entry.lease_expires_at = None

    def _sweep_expired_locked(self) -> None:
        """Drop expired entries, keeping in-flight ones under a live lease.

        Caller must hold the lock.
        """
        now = self._clock()
        expired = [
            t
            for t, e in self._entries.items()
            if e.record.expires_at <= now
            and not (
                e.status == "in_flight"
                and e.lease_expires_at is not None
                and e.lease_expires_at > now
            )
        ]
        for t in expired:
            del self._entries[t]
