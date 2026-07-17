"""Tests for the background summarize job store (#937)."""

from __future__ import annotations

from markdown_vault_mcp.summary_jobs import SummaryJobStore
from markdown_vault_mcp.types import SummaryResult, SummarySource


def _result(text: str = "SUMMARY") -> SummaryResult:
    return SummaryResult(
        summary=text,
        sources=[SummarySource(path="a.md", title="A")],
        mode="synthesis",
        truncated=False,
        notes_included=1,
        notes_omitted=0,
        notes_limit=50,
        hint=None,
    )


class _Clock:
    """Manually-advanced monotonic clock."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class TestSummaryJobStore:
    def test_create_returns_in_progress(self) -> None:
        store = SummaryJobStore()
        job_id = store.create()
        job = store.get(job_id)
        assert job is not None
        assert job.status == "in_progress"
        assert job.result is None and job.error is None

    def test_complete_stores_result(self) -> None:
        store = SummaryJobStore()
        job_id = store.create()
        store.complete(job_id, _result("done"))
        job = store.get(job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.result is not None and job.result.summary == "done"

    def test_fail_stores_error(self) -> None:
        store = SummaryJobStore()
        job_id = store.create()
        store.fail(job_id, "boom")
        job = store.get(job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error == "boom"
        assert job.result is None

    def test_unknown_job_is_none(self) -> None:
        assert SummaryJobStore().get("nope") is None

    def test_complete_unknown_is_noop(self) -> None:
        store = SummaryJobStore()
        store.complete("nope", _result())  # must not raise
        store.fail("nope", "x")  # must not raise

    def test_finished_job_evicted_after_ttl(self) -> None:
        clock = _Clock()
        store = SummaryJobStore(clock=clock, ttl_seconds=100.0)
        job_id = store.create()
        store.complete(job_id, _result())
        clock.t += 99.0
        assert store.get(job_id) is not None  # still within TTL
        clock.t += 2.0
        assert store.get(job_id) is None  # swept

    def test_in_progress_job_not_evicted_by_ttl(self) -> None:
        clock = _Clock()
        store = SummaryJobStore(clock=clock, ttl_seconds=10.0)
        job_id = store.create()
        clock.t += 1_000.0  # long past any TTL
        job = store.get(job_id)
        assert job is not None and job.status == "in_progress"

    def test_cap_evicts_oldest_finished_first(self) -> None:
        clock = _Clock()
        store = SummaryJobStore(clock=clock, ttl_seconds=1e9, max_jobs=2)
        first = store.create()
        store.complete(first, _result("first"))
        clock.t += 1.0
        second = store.create()
        store.complete(second, _result("second"))
        clock.t += 1.0
        # Creating a third exceeds the cap of 2 → oldest finished (first) goes.
        third = store.create()
        assert store.get(first) is None
        assert store.get(second) is not None
        assert store.get(third) is not None
