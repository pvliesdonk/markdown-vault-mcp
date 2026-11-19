"""Key-value-backed capability-link token store.

Ported from ``fastmcp_pvl_core._transfer.store`` 10.2.0 (#2320), whole: the
``consumed`` state and :meth:`TransferStore.burn` have no caller here, but
``claim``, ``release`` and ``complete`` each guard against that state, so it
stays as one state machine.

A ``TransferStore`` persists capability-link tokens over an
:class:`~key_value.aio.protocols.key_value.AsyncKeyValue` backend
(``build_kv_store(config, namespace="transfer")``) and runs an
``available ↔ in_flight`` state machine (plus an explicit ``consumed`` burn) on
them:

- :meth:`TransferStore.mint` creates an ``available`` token whose lifetime is
  the KV entry TTL — there is no sweep loop; an expired token (and any
  abandoned reservation past its TTL) simply vanishes from the backend.
- :meth:`TransferStore.claim` marks a token ``in_flight`` under a short lease.
  A *crashed* handler's reservation auto-frees once the lease lapses, so the
  next claim reclaims it without an explicit release.
- :meth:`TransferStore.release` reverts ``in_flight → available`` on a transient
  failure, keeping the **full remaining TTL** — the link survives for a full
  retry window (delete-on-claim is rejected: mid-serve failures are common and
  must not spend the link).
- :meth:`TransferStore.complete` grace-settles ``in_flight → available`` on
  success with the TTL shrunk to ``min(remaining, grace_seconds)``. It does
  **not** hard-burn: a transfer whose bytes were served but whose delivery then
  stalled can re-claim within the grace window rather than be stranded by a
  spent link. The ``min`` never extends and does not slide across retries, so
  the absolute expiry is pinned at the first settle.
- :meth:`TransferStore.burn` is the strict-one-shot alternative — it marks the
  token ``consumed`` (a later claim raises ``TokenAlreadyConsumedError``), for a
  caller that cannot tolerate even a grace-window replay.

**Why grace-settle over a hard burn.** The TTL is the
security-relevant bound; strict single-use is *hygiene*. A hard burn on success
spends the link the instant the bytes leave the sink — before they reach the
client, so a download stall or a lost ack strands the caller (the failure this
project hit in practice before #979). Shrinking the TTL to a short grace instead
keeps the link briefly reclaimable while still letting normal KV-TTL expiry
remove it, with no sweep loop.

**Correctness boundary.** The KV facade exposes no compare-and-set,
so one in-process :class:`asyncio.Lock` serialises every read-modify-write.
Within the single ``serve --transport http`` process these servers run, that
gives exact semantics *and* restart survival (the record lives in KV). A future
horizontally-scaled deployment sharing one backend would degrade to best-effort
— acceptable by design, because the TTL, not strict single-use, is the
security-relevant bound.

The token carries an **opaque ``sink_handle``** (where bytes land) and opaque
``caps`` that the store never interprets — only ``kind`` is matched on claim.
``sink_handle`` and ``caps`` must be JSON-serialisable: they round-trip through
the KV backend.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, TypedDict, cast

from markdown_vault_mcp._kv_store import build_kv_store

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from key_value.aio.protocols.key_value import AsyncKeyValue

    from markdown_vault_mcp._pvl_core import ServerConfig

_TOKEN_COLLECTION = "tokens"  # noqa: S105, RUF100 - A collection name; S105 is gate-only.
"""KV collection holding token records, under the ``transfer`` namespace."""

_TOKEN_BYTES = 32
"""Entropy of a minted token — an unguessable 32-byte URL-safe secret."""

_FENCE_BYTES = 8
"""Entropy of a per-claim fence — a fresh reservation id each :meth:`claim`."""

_NAMESPACE = "transfer"
"""The KV namespace pinned for transfer tokens."""

# The three token states as a Literal, so mypy checks every comparison and
# assignment — a typo like ``"in-flight"`` fails the type check rather than
# silently creating an unreachable state. The bare ``Final`` constants each
# narrow to their individual literal type and give a single source of truth;
# the stored value stays a plain string (JSON-serialisable).
TokenStatus = Literal["available", "in_flight", "consumed"]
_AVAILABLE: Final = "available"
_IN_FLIGHT: Final = "in_flight"
_CONSUMED: Final = "consumed"


class _TokenRecord(TypedDict):
    """The persisted shape of a token, round-tripped through the KV backend.

    A ``TypedDict`` (not a class) so it stays a plain JSON-serialisable dict at
    rest while mypy checks every field access in this module. ``sink_handle``
    and ``caps`` are opaque passthrough the store never interprets:
    ``sink_handle`` is typed ``object`` (not ``Any``), so a consumer of a claim
    must narrow it before use; ``caps`` is a ``Mapping[str, Any]`` the store
    never inspects.
    """

    status: TokenStatus
    kind: str
    sink_handle: object
    caps: Mapping[str, Any]
    lease_expires_at: float | None
    # A fresh id stamped on each claim (the fencing token). ``release``,
    # ``complete``, and ``burn`` only act when the caller's fence matches this —
    # so a stale holder whose lease was reclaimed cannot mutate the new holder's
    # reservation. ``None`` while ``available``/``consumed``.
    fence: str | None


class TransferTokenError(Exception):
    """Base class for the token-store's claim/complete/burn failures."""


class TokenNotFoundError(TransferTokenError):
    """The token does not exist — never minted, or its TTL has lapsed."""


class TokenKindMismatchError(TransferTokenError):
    """The token exists but was minted for a different link kind."""


class TokenAlreadyConsumedError(TransferTokenError):
    """The token was hard-burned by :meth:`TransferStore.burn` and cannot be reused."""


class TokenInFlightError(TransferTokenError):
    """The token is claimed and its lease is still live — a concurrent holder."""


class TokenNotClaimedError(TransferTokenError):
    """The token is not currently claimed.

    Raised by :meth:`TransferStore.complete` / :meth:`TransferStore.burn` when
    the token is ``available`` — never claimed, or already settled/released.
    """


@dataclass(frozen=True)
class TransferToken:
    """A successful claim: the opaque routing info the caller needs to proceed.

    ``sink_handle`` and ``caps`` are exactly what :meth:`TransferStore.mint`
    stored — the store never interprets them. ``fence`` identifies *this*
    reservation; pass it back to :meth:`TransferStore.release`,
    :meth:`TransferStore.complete`, or :meth:`TransferStore.burn` so a
    reservation superseded by a lease reclaim cannot mutate the current holder's
    state.
    """

    token: str
    fence: str
    kind: str
    sink_handle: object
    caps: Mapping[str, Any]


class TransferStore:
    """Capability-link token store backed by an ``AsyncKeyValue``.

    Args:
        kv: The backend store, typically from
            ``build_kv_store(config, namespace="transfer")``. Injected so the
            store is testable against an in-memory backend.
        lease_seconds: Reclaim window for an ``in_flight`` reservation — a
            crashed handler's token auto-frees this long after :meth:`claim`.
            Operator-tunable timing (wired from config by ``_transfer_links``);
            must be positive.
        grace_seconds: Post-success grace window. :meth:`complete` settles a
            token back to ``available`` with its TTL shrunk to
            ``min(remaining, grace_seconds)``, so a stalled/retried transfer can
            re-claim within this window rather than being stranded by a hard
            burn (the TTL, not strict single-use, is the security bound).
            Operator-tunable; must be positive.
        clock: Wall-clock source for lease timestamps. Defaults to
            :func:`time.time`; injected only for deterministic tests. Wall
            clock (not monotonic) so a lease persisted in KV stays meaningful
            across a process restart.
    """

    def __init__(
        self,
        kv: AsyncKeyValue,
        *,
        lease_seconds: float,
        grace_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError(f"lease_seconds must be positive, got {lease_seconds}")
        if grace_seconds <= 0:
            raise ValueError(f"grace_seconds must be positive, got {grace_seconds}")
        self._kv = kv
        self._lease_seconds = float(lease_seconds)
        self._grace_seconds = float(grace_seconds)
        self._clock = clock
        self._lock = asyncio.Lock()

    @classmethod
    def from_config(
        cls,
        config: ServerConfig,
        *,
        lease_seconds: float,
        grace_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> TransferStore:
        """Build a store from operator config, pinning ``namespace="transfer"``.

        The namespace is fixed, not the caller's: every transfer link shares one
        keyspace.

        Args:
            config: Server configuration; its ``kv_store_url`` selects the store.
            lease_seconds: Reclaim window for an ``in_flight`` reservation.
            grace_seconds: Post-success grace window.
            clock: Wall-clock source, injected only for deterministic tests.

        Returns:
            A store over the ``transfer`` namespace of the configured backend.
        """
        return cls(
            build_kv_store(config, namespace=_NAMESPACE),
            lease_seconds=lease_seconds,
            grace_seconds=grace_seconds,
            clock=clock,
        )

    async def mint(
        self,
        *,
        kind: str,
        sink_handle: object,
        caps: Mapping[str, Any],
        ttl_seconds: float,
    ) -> str:
        """Create an ``available`` token and return its unguessable id.

        Args:
            kind: Link kind matched on :meth:`claim` (e.g. ``"download"``).
            sink_handle: Opaque routing info the store never interprets.
            caps: Opaque caps the store never interprets.
            ttl_seconds: Token lifetime — the KV entry TTL, the security bound.

        ``sink_handle`` and ``caps`` must be JSON-serialisable so they round-trip
        through the KV backend. That is the caller's responsibility, enforced by
        the backend at persist time (a serialising backend raises on a bad
        value) — this method does not probe it.

        Raises:
            ValueError: On empty ``kind`` or non-positive ``ttl_seconds``.
        """
        if not kind:
            raise ValueError("kind must be a non-empty string")
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        # A fresh 32-byte secret cannot collide, so minting needs no lock.
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        record: _TokenRecord = {
            "status": _AVAILABLE,
            "kind": kind,
            "sink_handle": sink_handle,
            "caps": caps,
            "lease_expires_at": None,
            "fence": None,
        }
        await self._kv.put(
            token, record, collection=_TOKEN_COLLECTION, ttl=float(ttl_seconds)
        )
        return token

    async def claim(self, token: str, kind: str) -> TransferToken:
        """Reserve *token* for *kind*, marking it ``in_flight`` under a lease.

        Reclaims an ``in_flight`` token whose lease has lapsed (a crashed *or
        slow* handler) with a fresh :attr:`TransferToken.fence`. Preserves the
        token's remaining TTL on the re-put — dropping it would make the link
        immortal.

        Raises:
            TokenNotFoundError: The token is missing or its TTL has lapsed.
            TokenKindMismatchError: The token was minted for a different kind.
            TokenAlreadyConsumedError: The token was already burned.
            TokenInFlightError: The token is claimed and its lease is still live.
        """
        async with self._lock:
            record, remaining = await self._read(token)
            if record["kind"] != kind:
                raise TokenKindMismatchError(
                    f"token is for kind {record['kind']!r}, not {kind!r}"
                )
            status = record["status"]
            if status == _CONSUMED:
                raise TokenAlreadyConsumedError("token was already consumed")
            if status == _IN_FLIGHT:
                lease = record["lease_expires_at"]
                if lease is not None and self._clock() < lease:
                    raise TokenInFlightError("token is claimed and its lease is live")
                # Lease lapsed → the previous holder crashed or is too slow;
                # reclaim below under a fresh fence so its late
                # release/complete/burn (carrying the old fence) can no longer
                # mutate this reservation.
            fence = secrets.token_urlsafe(_FENCE_BYTES)
            record["status"] = _IN_FLIGHT
            record["lease_expires_at"] = self._clock() + self._lease_seconds
            record["fence"] = fence
            await self._kv.put(
                token, record, collection=_TOKEN_COLLECTION, ttl=remaining
            )
            return TransferToken(
                token=token,
                fence=fence,
                kind=record["kind"],
                sink_handle=record["sink_handle"],
                caps=record["caps"],
            )

    async def release(self, token: str, fence: str) -> None:
        """Revert *this* reservation to ``available`` (release-on-failure).

        *fence* is the :attr:`TransferToken.fence` from the claim being
        released. Idempotent and safe: a no-op if the token is missing, already
        ``available`` or ``consumed`` (a late release after a :meth:`burn` must
        never resurrect a hard-burned link), or if *fence* does not match the
        record's current fence — i.e. this reservation was superseded by a lease
        reclaim, so reverting it would corrupt the new holder's in-flight state.
        """
        async with self._lock:
            try:
                record, remaining = await self._read(token)
            except TokenNotFoundError:
                return  # expired/gone — nothing to release
            if record["status"] != _IN_FLIGHT:
                return  # available or consumed — nothing to revert
            if record["fence"] != fence:
                return  # superseded — this is no longer our reservation
            record["status"] = _AVAILABLE
            record["lease_expires_at"] = None
            record["fence"] = None
            await self._kv.put(
                token, record, collection=_TOKEN_COLLECTION, ttl=remaining
            )

    async def complete(self, token: str, fence: str) -> None:
        """Grace-settle *this* reservation on a successful transfer.

        The token reverts to ``available`` with its TTL shrunk to
        ``min(remaining, grace_seconds)`` — **not** hard-burned. So a transfer
        whose bytes were served but whose delivery then stalled (a client drop
        mid-download, a lost upload ack) can re-claim within the grace window
        instead of being stranded by a spent link (the TTL is the security
        bound; strict single-use is hygiene). Use :meth:`burn` when a
        caller genuinely needs strict one-shot.

        The ``min`` never *extends* the TTL, and once ``remaining <=
        grace_seconds`` it keeps the shrinking ``remaining`` — so the absolute
        expiry is pinned at the first settle and does not slide across retries.

        *fence* is the :attr:`TransferToken.fence` from the claim being
        completed. A no-op if the token expired mid-transfer, if it was already
        hard-burned (``consumed``), or if *fence* does not match the record's
        current fence (a reservation superseded by a lease reclaim must not
        disturb the new holder's live in-flight link).

        Raises:
            TokenNotClaimedError: The token is not currently claimed (it is
                ``available`` — never claimed, or already settled/released).
        """
        await self._finish(token, fence, new_status=_AVAILABLE, shrink_to_grace=True)

    async def burn(self, token: str, fence: str) -> None:
        """Hard-burn *this* reservation (``in_flight → consumed``) — strict one-shot.

        The stricter alternative to :meth:`complete`: the token becomes
        ``consumed`` and can never be claimed again (a later claim raises
        :class:`TokenAlreadyConsumedError`), for a caller that cannot tolerate
        even a grace-window replay (e.g. a sensitive one-time secret). The
        remaining TTL is preserved as a consumed tombstone until it lapses.

        Same fence/expiry/idempotency no-op rules as :meth:`complete`.

        Raises:
            TokenNotClaimedError: The token is not currently claimed.
        """
        await self._finish(token, fence, new_status=_CONSUMED, shrink_to_grace=False)

    async def _finish(
        self,
        token: str,
        fence: str,
        *,
        new_status: TokenStatus,
        shrink_to_grace: bool,
    ) -> None:
        """Shared terminal transition for :meth:`complete` / :meth:`burn`.

        Both move an ``in_flight`` reservation out of flight under the lock and
        the fence guard; they differ only in the resulting status and whether
        the TTL is shrunk to the grace window.
        """
        async with self._lock:
            try:
                record, remaining = await self._read(token)
            except TokenNotFoundError:
                return  # expired mid-transfer — nothing left to settle
            status = record["status"]
            if status == _CONSUMED:
                return  # already hard-burned — idempotent
            if status != _IN_FLIGHT:
                raise TokenNotClaimedError("token is not currently claimed")
            if record["fence"] != fence:
                return  # superseded — the current reservation is not ours
            record["status"] = new_status
            record["lease_expires_at"] = None
            record["fence"] = None
            ttl = min(remaining, self._grace_seconds) if shrink_to_grace else remaining
            await self._kv.put(token, record, collection=_TOKEN_COLLECTION, ttl=ttl)

    async def _read(self, token: str) -> tuple[_TokenRecord, float]:
        """Read a live token record and its remaining TTL, or raise.

        A single ``ttl()`` call returns both the value and its remaining
        lifetime. A token with no positive remaining TTL is treated as gone —
        this both handles expiry and fails closed on the anomalous no-TTL
        record we never mint, so the remaining value is always safe to re-apply
        on the caller's put.

        The value is cast to :class:`_TokenRecord`: the store is the only writer
        to this keyspace, so a live record is trusted to have the minted shape.
        """
        record, remaining = await self._kv.ttl(token, collection=_TOKEN_COLLECTION)
        if record is None or remaining is None or remaining <= 0:
            raise TokenNotFoundError("token not found or expired")
        return cast("_TokenRecord", record), remaining
