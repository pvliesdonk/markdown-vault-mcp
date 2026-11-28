"""The key-value-backed ``JobStore`` (#2307).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_jobs_store.py``. Covers the ``working → completed | failed | cancelled`` lifecycle, TTL
pinning (retention never slides on settle), subject-scope isolation, and
the lazily-pruned per-subject cap.
"""

from __future__ import annotations

import pytest
from key_value.aio.stores.memory import MemoryStore

from markdown_vault_mcp._job_store import (
    _INDEX_COLLECTION,
    _RECORD_COLLECTION,
    COLLECTED_GRACE_S,
    JobLimitExceededError,
    JobNotFoundError,
    JobStore,
    _scope_key,
)


class _Clock:
    """A hand-cranked wall clock for deterministic timestamp tests."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _store(
    *,
    result_ttl_s: float = 3600.0,
    max_per_subject: int = 4,
    clock: _Clock | None = None,
) -> tuple[JobStore, MemoryStore]:
    kv = MemoryStore()
    store = JobStore(
        kv,
        result_ttl_s=result_ttl_s,
        max_per_subject=max_per_subject,
        clock=clock or _Clock(),
    )
    return store, kv


class TestLifecycle:
    async def test_create_yields_working_record(self):
        store, _kv = _store()
        record = await store.create("alice")
        assert record.status == "working"
        assert record.result is None and record.error is None
        assert record.finished_at is None
        fetched = await store.get("alice", record.job_id)
        assert fetched == record

    async def test_finish_settles_completed_with_result(self):
        store, _kv = _store()
        record = await store.create("alice")
        await store.finish("alice", record.job_id, {"answer": 42})
        settled = await store.get("alice", record.job_id)
        assert settled.status == "completed"
        assert settled.result == {"answer": 42}
        assert settled.finished_at is not None

    async def test_fail_settles_failed_with_error(self):
        store, _kv = _store()
        record = await store.create("alice")
        await store.fail("alice", record.job_id, "backend exploded")
        settled = await store.get("alice", record.job_id)
        assert settled.status == "failed"
        assert settled.error == "backend exploded"
        assert settled.result is None

    async def test_cancel_settles_cancelled(self):
        store, _kv = _store()
        record = await store.create("alice")
        await store.cancel("alice", record.job_id)
        settled = await store.get("alice", record.job_id)
        assert settled.status == "cancelled"
        assert settled.result is None and settled.error is None

    async def test_settle_of_expired_record_is_noop(self):
        # A record that vanished (TTL) mid-run: finish must not resurrect it.
        store, _kv = _store()
        record = await store.create("alice")
        await _kv.delete(
            _scope_key("alice", record.job_id), collection=_RECORD_COLLECTION
        )
        await store.finish("alice", record.job_id, {"late": True})
        with pytest.raises(JobNotFoundError):
            await store.get("alice", record.job_id)

    async def test_started_at_override_is_stored(self):
        clock = _Clock(2_000.0)
        store, _kv = _store(clock=clock)
        record = await store.create("alice", started_at=1_990.0)
        assert record.started_at == 1_990.0
        assert record.created_at == 2_000.0


class TestTtl:
    async def test_record_carries_result_ttl(self):
        store, kv = _store(result_ttl_s=600.0)
        record = await store.create("alice")
        _value, remaining = await kv.ttl(
            _scope_key("alice", record.job_id), collection=_RECORD_COLLECTION
        )
        assert remaining is not None and remaining <= 600.0

    async def test_settle_keeps_remaining_ttl(self):
        # Retention is pinned at creation: settling must never extend it.
        store, kv = _store(result_ttl_s=600.0)
        record = await store.create("alice")
        await store.finish("alice", record.job_id, {"ok": True})
        _value, remaining = await kv.ttl(
            _scope_key("alice", record.job_id), collection=_RECORD_COLLECTION
        )
        assert remaining is not None and remaining <= 600.0

    async def test_settle_fails_closed_on_ttl_less_record(self):
        # Anomalous state: a live record with no TTL info. create() always
        # writes with a TTL, so this was not minted here; settling it must
        # not re-mint a full retention window (the "never slides"
        # invariant) — fail closed, leaving the record untouched.
        store, kv = _store(result_ttl_s=600.0)
        record = await store.create("alice")
        key = _scope_key("alice", record.job_id)
        raw, _remaining = await kv.ttl(key, collection=_RECORD_COLLECTION)
        assert raw is not None
        await kv.put(key, dict(raw), collection=_RECORD_COLLECTION)  # no ttl
        await store.finish("alice", record.job_id, {"late": True})
        after = await store.get("alice", record.job_id)
        assert after.status == "working"  # untouched — not resurrected
        _value, remaining = await kv.ttl(key, collection=_RECORD_COLLECTION)
        assert remaining is None  # and no fresh TTL was minted


class TestScopeIsolation:
    async def test_other_scope_cannot_see_job(self):
        store, _kv = _store()
        record = await store.create("alice")
        with pytest.raises(JobNotFoundError):
            await store.get("bob", record.job_id)

    async def test_unknown_id_raises(self):
        store, _kv = _store()
        with pytest.raises(JobNotFoundError):
            await store.get("alice", "nope")

    async def test_scope_with_delimiter_cannot_forge_keys(self):
        # A subject containing ":" must not resolve into another scope's
        # keyspace — the scope is quoted into the key.
        store, _kv = _store()
        record = await store.create("alice")
        with pytest.raises(JobNotFoundError):
            await store.get("ali", f"ce:{record.job_id}")


class TestPerSubjectCap:
    async def test_cap_rejects_when_full(self):
        store, _kv = _store(max_per_subject=2)
        await store.create("alice")
        await store.create("alice")
        with pytest.raises(JobLimitExceededError):
            await store.create("alice")

    async def test_cap_is_per_subject(self):
        store, _kv = _store(max_per_subject=1)
        await store.create("alice")
        await store.create("bob")  # different scope: unaffected

    async def test_expired_records_are_pruned_before_rejecting(self):
        store, kv = _store(max_per_subject=2)
        first = await store.create("alice")
        await store.create("alice")
        # First record expires from the backend; the index still lists it.
        await kv.delete(
            _scope_key("alice", first.job_id), collection=_RECORD_COLLECTION
        )
        # The prune pass frees the slot instead of rejecting.
        await store.create("alice")


class TestCollect:
    """A job whose outcome the caller received stops counting (#2530)."""

    async def test_collected_jobs_free_their_slots(self):
        store, _kv = _store(max_per_subject=2)
        for _ in range(2):
            record = await store.create("alice")
            await store.finish("alice", record.job_id, {"ok": True})
            await store.collect("alice", record.job_id)
        await store.create("alice")

    async def test_settled_but_uncollected_jobs_still_count(self):
        store, _kv = _store(max_per_subject=1)
        record = await store.create("alice")
        await store.finish("alice", record.job_id, {"ok": True})
        with pytest.raises(JobLimitExceededError):
            await store.create("alice")

    async def test_working_job_is_not_collected(self):
        store, _kv = _store(max_per_subject=1)
        record = await store.create("alice")
        await store.collect("alice", record.job_id)
        with pytest.raises(JobLimitExceededError):
            await store.create("alice")

    async def test_collected_record_stays_readable_for_the_grace_period(self):
        store, kv = _store(result_ttl_s=3600.0)
        record = await store.create("alice")
        await store.fail("alice", record.job_id, "boom")
        await store.collect("alice", record.job_id)
        fetched = await store.get("alice", record.job_id)
        assert fetched.status == "failed" and fetched.error == "boom"
        _value, remaining = await kv.ttl(
            _scope_key("alice", record.job_id), collection=_RECORD_COLLECTION
        )
        assert remaining is not None and remaining <= COLLECTED_GRACE_S

    async def test_collect_never_lengthens_a_shorter_lifetime(self):
        store, kv = _store(result_ttl_s=60.0)
        record = await store.create("alice")
        await store.cancel("alice", record.job_id)
        await store.collect("alice", record.job_id)
        _value, remaining = await kv.ttl(
            _scope_key("alice", record.job_id), collection=_RECORD_COLLECTION
        )
        assert remaining is not None and remaining <= 60.0

    async def test_collect_is_idempotent_and_keeps_other_ids(self):
        store, kv = _store()
        done = await store.create("alice")
        running = await store.create("alice")
        await store.finish("alice", done.job_id, {"ok": True})
        await store.collect("alice", done.job_id)
        await store.collect("alice", done.job_id)
        index = await kv.get(_scope_key("alice"), collection=_INDEX_COLLECTION)
        assert index == {"ids": [running.job_id]}

    async def test_collecting_the_last_id_drops_the_index(self):
        store, kv = _store()
        record = await store.create("alice")
        await store.finish("alice", record.job_id, {"ok": True})
        await store.collect("alice", record.job_id)
        assert await kv.get(_scope_key("alice"), collection=_INDEX_COLLECTION) is None

    async def test_collect_of_unknown_or_expired_id_is_noop(self):
        store, kv = _store()
        record = await store.create("alice")
        await store.collect("alice", "nope")
        await kv.delete(
            _scope_key("alice", record.job_id), collection=_RECORD_COLLECTION
        )
        await store.collect("alice", record.job_id)
        index = await kv.get(_scope_key("alice"), collection=_INDEX_COLLECTION)
        assert index == {"ids": [record.job_id]}
