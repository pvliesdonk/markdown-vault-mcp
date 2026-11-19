"""The key-value-backed ``TransferStore`` (#2320).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_transfer_store.py``.

The store runs an ``available ↔ in_flight`` machine over an ``AsyncKeyValue``
backend, plus an explicit ``consumed`` burn:

- **release-on-failure**: a failed reservation returns to ``available`` with the
  full remaining TTL, so the link survives for a full retry window (ADR §6.1).
- **grace-settle on success** (``complete``): the reservation returns to
  ``available`` with its TTL shrunk to ``min(remaining, grace_seconds)`` — not
  hard-burned — so a served-but-stalled transfer can re-claim within the grace
  window rather than being stranded. The ``min`` never extends and does not
  slide across retries.
- **explicit hard burn** (``burn``): marks ``consumed`` (strict one-shot) for a
  caller that cannot tolerate a grace-window replay.
- lease reclaim of a crashed/slow handler; a per-claim fence so a superseded
  holder cannot mutate the current reservation; the KV TTL is the security
  bound (ADR §6.3); one in-process ``asyncio.Lock`` serialises the
  read-modify-write.
"""

from __future__ import annotations

import asyncio

import pytest
from key_value.aio.stores.memory import MemoryStore

from markdown_vault_mcp._pvl_core import ServerConfig
from markdown_vault_mcp._transfer_store import (
    _TOKEN_COLLECTION,
    TokenAlreadyConsumedError,
    TokenInFlightError,
    TokenKindMismatchError,
    TokenNotClaimedError,
    TokenNotFoundError,
    TransferStore,
    TransferToken,
)


class _Clock:
    """A hand-cranked monotonic clock for deterministic lease tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _store(
    *,
    lease_seconds: float = 30.0,
    grace_seconds: float = 60.0,
    clock: _Clock | None = None,
) -> TransferStore:
    return TransferStore(
        MemoryStore(),
        lease_seconds=lease_seconds,
        grace_seconds=grace_seconds,
        clock=clock or _Clock(),
    )


async def _mint(
    store: TransferStore, *, kind: str = "download", ttl: float = 3600.0
) -> str:
    return await store.mint(
        kind=kind, sink_handle={"path": "a/b"}, caps={"max_bytes": 10}, ttl_seconds=ttl
    )


# --------------------------------------------------------------------------- #
# state machine — claim / complete (grace-settle)
# --------------------------------------------------------------------------- #


async def test_claim_returns_opaque_handle_and_caps() -> None:
    store = _store()
    token = await _mint(store)
    claim = await store.claim(token, "download")
    assert isinstance(claim, TransferToken)
    assert claim.token == token
    assert claim.kind == "download"
    assert claim.sink_handle == {"path": "a/b"}
    assert claim.caps == {"max_bytes": 10}


async def test_complete_grace_settles_link_reclaimable_within_grace() -> None:
    # complete() does NOT burn — it grace-settles back to available, so a
    # served-but-stalled transfer can re-claim within the grace window.
    store = _store(grace_seconds=60.0)
    token = await _mint(store)
    claim = await store.claim(token, "download")
    await store.complete(token, claim.fence)
    reclaim = await store.claim(token, "download")
    assert reclaim.token == token


async def test_complete_after_settle_without_reclaim_raises() -> None:
    # After grace-settle the token is available (not in_flight); completing it
    # again without re-claiming is completing something you no longer hold.
    store = _store()
    token = await _mint(store)
    claim = await store.claim(token, "download")
    await store.complete(token, claim.fence)
    with pytest.raises(TokenNotClaimedError):
        await store.complete(token, claim.fence)


async def test_complete_without_claim_raises() -> None:
    store = _store()
    token = await _mint(store)
    with pytest.raises(TokenNotClaimedError):
        await store.complete(token, "no-fence")


# --------------------------------------------------------------------------- #
# grace-settle TTL shrink (the security bound — ADR §6)
# --------------------------------------------------------------------------- #


async def test_complete_shrinks_ttl_to_grace() -> None:
    # A long-lived link's TTL is cut to the grace window on success.
    kv = MemoryStore()
    store = TransferStore(kv, lease_seconds=30.0, grace_seconds=5.0, clock=_Clock())
    token = await store.mint(
        kind="download", sink_handle={}, caps={}, ttl_seconds=3600.0
    )
    claim = await store.claim(token, "download")
    await store.complete(token, claim.fence)
    _value, remaining = await kv.ttl(token, collection=_TOKEN_COLLECTION)
    assert remaining is not None
    assert 0.0 < remaining <= 5.0


async def test_complete_never_extends_ttl_below_grace() -> None:
    # min(remaining, grace): a link already shorter than grace keeps its
    # remaining life — complete must never *extend* it up to the grace window.
    kv = MemoryStore()
    store = TransferStore(kv, lease_seconds=30.0, grace_seconds=3600.0, clock=_Clock())
    token = await store.mint(kind="download", sink_handle={}, caps={}, ttl_seconds=2.0)
    claim = await store.claim(token, "download")
    await store.complete(token, claim.fence)
    _value, remaining = await kv.ttl(token, collection=_TOKEN_COLLECTION)
    assert remaining is not None
    assert 0.0 < remaining <= 2.0


async def test_grace_settle_does_not_slide_absolute_expiry() -> None:
    # The absolute expiry is pinned at the first settle: re-claiming and
    # re-completing within the grace window keeps the shrinking remaining TTL
    # (min never resets it back up to the full grace), so retries can't extend
    # the link indefinitely.
    kv = MemoryStore()
    store = TransferStore(kv, lease_seconds=30.0, grace_seconds=1.0, clock=_Clock())
    token = await store.mint(
        kind="download", sink_handle={}, caps={}, ttl_seconds=3600.0
    )
    c1 = await store.claim(token, "download")
    await store.complete(token, c1.fence)  # remaining ≈ 1.0 (grace)
    await asyncio.sleep(0.2)
    c2 = await store.claim(token, "download")  # still within grace
    await store.complete(token, c2.fence)
    _value, remaining = await kv.ttl(token, collection=_TOKEN_COLLECTION)
    assert remaining is not None
    assert remaining < 0.95  # elapsed time survived; NOT reset back up to 1.0


# --------------------------------------------------------------------------- #
# release-on-failure (ADR §6.1) — keeps the full remaining TTL
# --------------------------------------------------------------------------- #


async def test_claim_release_keeps_the_link_claimable() -> None:
    store = _store()
    token = await _mint(store)
    claim = await store.claim(token, "download")
    await store.release(token, claim.fence)
    # The link survived a transient failure — claimable again.
    reclaim = await store.claim(token, "download")
    assert reclaim.token == token


async def test_release_on_unclaimed_token_is_a_noop() -> None:
    store = _store()
    token = await _mint(store)
    await store.release(token, "no-fence")  # never claimed — no-op, no raise
    claim = await store.claim(token, "download")
    assert claim.token == token


# --------------------------------------------------------------------------- #
# burn — the explicit strict one-shot
# --------------------------------------------------------------------------- #


async def test_burn_consumes_and_second_claim_raises() -> None:
    store = _store()
    token = await _mint(store)
    claim = await store.claim(token, "download")
    await store.burn(token, claim.fence)
    with pytest.raises(TokenAlreadyConsumedError):
        await store.claim(token, "download")


async def test_burn_is_idempotent() -> None:
    store = _store()
    token = await _mint(store)
    claim = await store.claim(token, "download")
    await store.burn(token, claim.fence)
    await store.burn(token, claim.fence)  # already consumed — no-op


async def test_burn_preserves_remaining_ttl_not_grace() -> None:
    # burn keeps the full remaining TTL as a consumed tombstone — unlike
    # complete, it does not shrink to the (shorter) grace window.
    kv = MemoryStore()
    store = TransferStore(kv, lease_seconds=30.0, grace_seconds=5.0, clock=_Clock())
    token = await store.mint(
        kind="download", sink_handle={}, caps={}, ttl_seconds=3600.0
    )
    claim = await store.claim(token, "download")
    await store.burn(token, claim.fence)
    _value, remaining = await kv.ttl(token, collection=_TOKEN_COLLECTION)
    assert remaining is not None
    assert remaining > 5.0  # NOT shrunk to grace
    assert remaining <= 3600.0


async def test_late_release_after_burn_does_not_resurrect() -> None:
    store = _store()
    token = await _mint(store)
    claim = await store.claim(token, "download")
    await store.burn(token, claim.fence)
    await store.release(token, claim.fence)  # late release — must NOT resurrect
    with pytest.raises(TokenAlreadyConsumedError):
        await store.claim(token, "download")


# --------------------------------------------------------------------------- #
# lease reclaim vs live lease
# --------------------------------------------------------------------------- #


async def test_live_lease_blocks_a_second_claim() -> None:
    clock = _Clock()
    store = _store(lease_seconds=30.0, clock=clock)
    token = await _mint(store)
    await store.claim(token, "download")
    clock.advance(10.0)  # still within the 30s lease
    with pytest.raises(TokenInFlightError):
        await store.claim(token, "download")


async def test_lapsed_lease_is_reclaimable_without_release() -> None:
    clock = _Clock()
    store = _store(lease_seconds=30.0, clock=clock)
    token = await _mint(store)
    await store.claim(token, "download")  # handler then "crashes" — no release
    clock.advance(31.0)  # lease has lapsed
    reclaim = await store.claim(token, "download")
    assert reclaim.token == token


# --------------------------------------------------------------------------- #
# fencing — a superseded holder must not mutate the current reservation
# --------------------------------------------------------------------------- #


async def test_superseded_holder_cannot_mutate_reclaimed_reservation() -> None:
    # A slow-but-alive handler A whose lease lapsed and was reclaimed by B must
    # not be able to revert (release) or settle (complete) B's active
    # reservation with its now-stale fence — even single-process.
    clock = _Clock()
    store = _store(lease_seconds=30.0, clock=clock)
    token = await _mint(store)
    a = await store.claim(token, "download")  # A holds fence a.fence
    clock.advance(31.0)  # A's lease lapses — A is slow, not crashed
    b = await store.claim(token, "download")  # B reclaims → B is the holder now
    assert b.fence != a.fence

    # A's late release with its stale fence must NOT revert B's reservation.
    await store.release(token, a.fence)
    with pytest.raises(TokenInFlightError):
        await store.claim(token, "download")

    # A's late complete with its stale fence must NOT settle B's reservation.
    await store.complete(token, a.fence)
    with pytest.raises(TokenInFlightError):
        await store.claim(token, "download")

    # B settles its own reservation with its live fence — grace-settled, so it
    # is reclaimable within the grace window (not burned).
    await store.complete(token, b.fence)
    reclaim = await store.claim(token, "download")
    assert reclaim.token == token


# --------------------------------------------------------------------------- #
# wrong kind
# --------------------------------------------------------------------------- #


async def test_wrong_kind_claim_rejected_and_not_consumed() -> None:
    store = _store()
    token = await _mint(store, kind="download")
    with pytest.raises(TokenKindMismatchError):
        await store.claim(token, "upload")
    # Rejection must not settle the link — the right kind still claims it.
    claim = await store.claim(token, "download")
    assert claim.token == token


# --------------------------------------------------------------------------- #
# unknown token / TTL expiry
# --------------------------------------------------------------------------- #


async def test_claim_unknown_token_raises_not_found() -> None:
    store = _store()
    with pytest.raises(TokenNotFoundError):
        await store.claim("no-such-token", "download")


async def test_ttl_expiry_removes_the_token() -> None:
    store = _store()
    token = await _mint(store, ttl=0.05)
    await asyncio.sleep(0.1)  # let the KV entry TTL lapse
    with pytest.raises(TokenNotFoundError):
        await store.claim(token, "download")


async def test_complete_on_expired_token_is_a_noop() -> None:
    store = _store()
    token = await _mint(store, ttl=0.05)
    claim = await store.claim(token, "download")
    await asyncio.sleep(0.1)
    # expired mid-transfer — no-op, no raise
    await store.complete(token, claim.fence)


# --------------------------------------------------------------------------- #
# TTL preservation on claim / release (the immortal-token failure mode)
# --------------------------------------------------------------------------- #


async def test_claim_preserves_the_token_ttl() -> None:
    # A mutating op must re-put with the remaining TTL. If it dropped the TTL,
    # the link would live forever — defeating the security bound.
    kv = MemoryStore()
    store = TransferStore(kv, lease_seconds=30.0, grace_seconds=60.0, clock=_Clock())
    token = await store.mint(
        kind="download", sink_handle={}, caps={}, ttl_seconds=3600.0
    )
    await store.claim(token, "download")
    _value, remaining = await kv.ttl(token, collection=_TOKEN_COLLECTION)
    assert remaining is not None
    assert 0.0 < remaining <= 3600.0


async def test_release_preserves_the_token_ttl() -> None:
    kv = MemoryStore()
    store = TransferStore(kv, lease_seconds=30.0, grace_seconds=60.0, clock=_Clock())
    token = await store.mint(
        kind="download", sink_handle={}, caps={}, ttl_seconds=3600.0
    )
    claim = await store.claim(token, "download")
    await store.release(token, claim.fence)
    _value, remaining = await kv.ttl(token, collection=_TOKEN_COLLECTION)
    assert remaining is not None
    assert 0.0 < remaining <= 3600.0


# --------------------------------------------------------------------------- #
# concurrency — the in-process lock serialises the read-modify-write
# --------------------------------------------------------------------------- #


class _YieldingKV:
    """A KV wrapper that suspends the event loop *between* the read and the
    write of a claim's read-modify-write.

    A plain ``MemoryStore`` never awaits anything real, so two gathered claims
    serialise on their own — the first runs to completion before the second is
    stepped — and the test would pass even if the lock were deleted. Yielding
    after the ``ttl()`` read forces the real interleave a production backend
    (redis/disk, whose awaits genuinely suspend) would produce, so the test now
    fails if the ``asyncio.Lock`` around the read-modify-write is removed.
    """

    def __init__(self, inner: MemoryStore) -> None:
        self._inner = inner

    async def ttl(self, *args, **kwargs):
        result = await self._inner.ttl(*args, **kwargs)
        await asyncio.sleep(0)  # let a concurrent claim interleave post-read
        return result

    async def put(self, *args, **kwargs):
        return await self._inner.put(*args, **kwargs)


async def test_racing_claims_yield_exactly_one_holder() -> None:
    # Uses _YieldingKV so the two claims genuinely interleave; without the
    # lock this yields two holders (double-claim of the link).
    store = TransferStore(
        _YieldingKV(MemoryStore()),
        lease_seconds=30.0,
        grace_seconds=60.0,
        clock=_Clock(),
    )
    token = await store.mint(
        kind="download", sink_handle={}, caps={}, ttl_seconds=3600.0
    )
    results = await asyncio.gather(
        store.claim(token, "download"),
        store.claim(token, "download"),
        return_exceptions=True,
    )
    holders = [r for r in results if isinstance(r, TransferToken)]
    rejected = [r for r in results if isinstance(r, TokenInFlightError)]
    assert len(holders) == 1
    assert len(rejected) == 1


# --------------------------------------------------------------------------- #
# minting
# --------------------------------------------------------------------------- #


async def test_mint_returns_distinct_unguessable_tokens() -> None:
    store = _store()
    a = await _mint(store)
    b = await _mint(store)
    assert a != b
    assert len(a) >= 32  # token_urlsafe(32) → ~43 chars


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [0.0, -1.0])
async def test_mint_rejects_non_positive_ttl(bad: float) -> None:
    store = _store()
    with pytest.raises(ValueError):
        await store.mint(kind="download", sink_handle={}, caps={}, ttl_seconds=bad)


async def test_mint_rejects_empty_kind() -> None:
    store = _store()
    with pytest.raises(ValueError):
        await store.mint(kind="", sink_handle={}, caps={}, ttl_seconds=60.0)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_constructor_rejects_non_positive_lease(bad: float) -> None:
    with pytest.raises(ValueError):
        TransferStore(MemoryStore(), lease_seconds=bad, grace_seconds=60.0)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_constructor_rejects_non_positive_grace(bad: float) -> None:
    with pytest.raises(ValueError):
        TransferStore(MemoryStore(), lease_seconds=30.0, grace_seconds=bad)


# --------------------------------------------------------------------------- #
# from_config — the store pins namespace="transfer" (shape owned by pvl-core)
# --------------------------------------------------------------------------- #


async def test_from_config_wires_a_working_store() -> None:
    config = ServerConfig(kv_store_url="memory://")
    store = TransferStore.from_config(config, lease_seconds=30.0, grace_seconds=60.0)
    token = await store.mint(
        kind="download", sink_handle={"h": 1}, caps={}, ttl_seconds=60.0
    )
    claim = await store.claim(token, "download")
    assert claim.sink_handle == {"h": 1}
    await store.complete(token, claim.fence)
    # grace-settled → reclaimable within the window (not burned).
    reclaim = await store.claim(token, "download")
    assert reclaim.token == token


async def test_from_config_pins_the_transfer_namespace(monkeypatch) -> None:
    # The keyspace is fixed, not the caller's: from_config
    # must always request namespace="transfer" from build_kv_store.
    seen: dict[str, object] = {}

    def _spy(_config, *, namespace):
        seen["namespace"] = namespace
        return MemoryStore()

    monkeypatch.setattr("markdown_vault_mcp._transfer_store.build_kv_store", _spy)
    TransferStore.from_config(
        ServerConfig(kv_store_url="memory://"), lease_seconds=30.0, grace_seconds=60.0
    )
    assert seen["namespace"] == "transfer"


async def test_opaque_fields_round_trip_through_a_serialising_backend(tmp_path) -> None:
    # The docstring promises sink_handle/caps "round-trip through the KV
    # backend". MemoryStore keeps live objects; a file:// backend actually
    # serialises, so this proves the JSON round-trip the contract advertises.
    config = ServerConfig(kv_store_url=f"file://{tmp_path}/kv")
    store = TransferStore.from_config(config, lease_seconds=30.0, grace_seconds=60.0)
    sink_handle = {"bucket": "b", "key": "k", "parts": [1, 2, 3]}
    caps = {"max_bytes": 1024, "content_types": ["image/png"]}
    token = await store.mint(
        kind="upload", sink_handle=sink_handle, caps=caps, ttl_seconds=60.0
    )
    claim = await store.claim(token, "upload")
    assert claim.sink_handle == sink_handle
    assert claim.caps == caps
