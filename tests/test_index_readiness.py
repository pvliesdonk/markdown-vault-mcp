"""Unit tests for ReadinessState — the build-readiness state machine."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.indexing.readiness import ReadinessState, _Verdict


def test_fresh_state_not_built_but_done_preset() -> None:
    r = ReadinessState()
    assert r.is_built is False
    assert r.is_queryable() is False
    # done-event is pre-set so a never-built vault does not block waiters
    assert r.wait(timeout=0) is True


def test_mark_built_makes_queryable_and_clears_error() -> None:
    r = ReadinessState()
    r.fail_build(RuntimeError("old"))
    r.mark_built()
    assert r.is_built is True
    assert r.is_queryable() is True
    assert r.error is None


def test_begin_sync_build_clears_built_error_and_done() -> None:
    # #587: sync begin clears the stale error (a fresh build supersedes a prior
    # failure) AND clears the done-event so a concurrent reader blocks/waits for
    # the active build instead of prematurely raising never_built.
    r = ReadinessState()
    r.mark_built()  # built=True, done set, error None
    r.record_error(RuntimeError("stale"))  # leftover error from a prior failure
    r.begin_sync_build()
    assert r.is_built is False
    assert r.error is None  # stale error cleared
    assert r.wait(timeout=0) is False  # done-event cleared so waiters block


def test_status_building_after_begin_sync_build_with_stale_error() -> None:
    # #587: a stale error from a prior failed async build must not make
    # status report "failed" once a fresh sync build has begun.
    r = ReadinessState()
    r.fail_build(RuntimeError("old async failure"))  # error + done set
    assert r.status_fields()["status"] == "failed"
    r.begin_sync_build()
    fields = r.status_fields()
    assert fields["status"] == "building"
    assert fields["error"] is None


def test_begin_async_build_clears_built_error_and_done() -> None:
    r = ReadinessState()
    r.mark_built()
    r.record_error(RuntimeError("kept?"))
    r.begin_async_build()
    assert r.is_built is False
    assert r.error is None
    assert r.wait(timeout=0) is False  # done cleared


def test_fail_build_records_error_sets_done_leaves_built_false() -> None:
    r = ReadinessState()
    r.begin_async_build()
    exc = RuntimeError("boom")
    r.fail_build(exc)
    assert r.is_built is False
    assert r.error is exc
    assert r.wait(timeout=0) is True


def test_captured_error_is_diagnostic_not_a_gate_when_built() -> None:
    # Invariant 1: an error captured AFTER a successful build does not
    # demote queryability — it is diagnostic state only.
    r = ReadinessState()
    r.mark_built()
    r.record_error(RuntimeError("late diagnostic"))
    assert r.is_queryable() is True
    assert r.status_fields()["status"] == "queryable"
    assert "late diagnostic" in r.status_fields()["error"]


def test_status_fields_failed_when_error_and_not_built() -> None:
    r = ReadinessState()
    r.fail_build(RuntimeError("scan exploded"))
    fields = r.status_fields()
    assert fields["status"] == "failed"
    assert "scan exploded" in fields["error"]


def test_status_fields_building_when_done_cleared() -> None:
    r = ReadinessState()
    r.begin_async_build()
    fields = r.status_fields()
    assert fields["status"] == "building"
    assert fields["error"] is None


def test_record_error_sets_error_without_touching_done() -> None:
    r = ReadinessState()
    r.begin_background_build()  # done cleared
    r.record_error(RuntimeError("worker failed"))
    assert isinstance(r.error, RuntimeError)
    assert r.wait(timeout=0) is False  # done still cleared (set later in finally)


def test_require_built_raises_never_built_when_unbuilt() -> None:
    r = ReadinessState()
    with pytest.raises(IndexUnavailableError) as ei:
        r.require_built()
    assert ei.value.reason == "never_built"


def test_require_built_raises_build_failed_when_error() -> None:
    # #586: a build that ran and failed (error captured) is distinguishable
    # from one never scheduled — reason="build_failed", not "never_built".
    r = ReadinessState()
    r.fail_build(RuntimeError("boom"))
    with pytest.raises(IndexUnavailableError) as ei:
        r.require_built()
    assert ei.value.reason == "build_failed"
    assert "boom" in str(ei.value)


def test_require_built_passes_when_built() -> None:
    r = ReadinessState()
    r.mark_built()
    r.require_built()  # no raise


# -- #598: (built, error) read as one atomic snapshot -------------------------


def test_mark_built_publishes_verdict_atomically() -> None:
    # mark_built publishes (True, None) as ONE immutable verdict object, so a
    # reader can never observe built=True paired with a stale error (#598).
    r = ReadinessState()
    r.fail_build(RuntimeError("old"))
    r.mark_built()
    v = r._snapshot()
    assert v.built is True
    assert v.error is None


def test_fail_build_preserves_built_in_published_verdict() -> None:
    # fail_build only records an error; it must leave the built flag untouched
    # and publish the pair as one verdict (#598).
    r = ReadinessState()
    r.mark_built()  # (True, None)
    exc = RuntimeError("late failure")
    r.fail_build(exc)
    v = r._snapshot()
    assert v.built is True  # preserved
    assert v.error is exc


def test_begin_background_build_preserves_built_clears_error() -> None:
    # begin_background_build clears the error but does not reset built — the
    # published verdict must reflect both in one object (#598).
    r = ReadinessState()
    r.mark_built()
    r.record_error(RuntimeError("stale"))  # (True, exc)
    r.begin_background_build()
    v = r._snapshot()
    assert v.built is True  # preserved
    assert v.error is None  # cleared


def test_require_built_decision_uses_single_snapshot_not_live_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #598: require_built must derive (built, error) from ONE snapshot. If a
    # concurrent failure transition lands immediately after the snapshot is
    # captured, the already-captured verdict governs the decision — there is
    # no torn re-read of the newer error.
    r = ReadinessState()
    r.begin_async_build()  # live: (False, None) — building
    captured = _Verdict(built=True, error=None)

    def snapshot_then_concurrent_failure() -> _Verdict:
        # A failure lands right after we capture the snapshot.
        r._verdict = _Verdict(built=False, error=RuntimeError("late"))
        return captured

    monkeypatch.setattr(r, "_snapshot", snapshot_then_concurrent_failure)
    r.require_built()  # must NOT raise: the captured snapshot said built=True


def test_require_built_build_failed_from_snapshot_not_live_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #598 mirror: a captured failed verdict yields build_failed even if a
    # concurrent success lands right after the snapshot — the decision never
    # re-reads live built/error.
    r = ReadinessState()
    r.mark_built()  # live: (True, None)
    captured = _Verdict(built=False, error=RuntimeError("boom"))

    def snapshot_then_concurrent_success() -> _Verdict:
        r._verdict = _Verdict(built=True, error=None)
        return captured

    monkeypatch.setattr(r, "_snapshot", snapshot_then_concurrent_success)
    with pytest.raises(IndexUnavailableError) as ei:
        r.require_built()
    assert ei.value.reason == "build_failed"
    assert "boom" in str(ei.value)


def test_status_fields_uses_single_verdict_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #598: status_fields derives status from one (built, error) snapshot, not
    # from live state re-read mid-call.
    r = ReadinessState()
    r.mark_built()  # live: (True, None), done set
    captured = _Verdict(built=False, error=RuntimeError("scan failed"))

    def snapshot_then_mutate() -> _Verdict:
        r._verdict = _Verdict(built=True, error=None)
        return captured

    monkeypatch.setattr(r, "_snapshot", snapshot_then_mutate)
    fields = r.status_fields()
    assert fields["status"] == "failed"  # from the snapshot, not live state
    assert "scan failed" in str(fields["error"])


def test_status_fields_reads_done_before_verdict_so_failure_is_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #598/#706: status_fields reads the done-event BEFORE the verdict. If a
    # build failure lands between the two reads, reading done first means a
    # True done is paired with the already-published failure verdict — so the
    # failure is reported as "failed", never misreported as "building" from a
    # stale error=None verdict. (Read-verdict-first would yield "building".)
    r = ReadinessState()
    r.begin_async_build()  # building: (False, None), done cleared

    real_is_set = r._done.is_set

    def is_set_landing_failure() -> bool:
        # A build failure completes exactly here, honouring the transition
        # write order (verdict published, then done set).
        r.fail_build(RuntimeError("boom"))
        return real_is_set()

    monkeypatch.setattr(r._done, "is_set", is_set_landing_failure)
    fields = r.status_fields()
    monkeypatch.setattr(r._done, "is_set", real_is_set)

    assert fields["status"] == "failed"
    assert "boom" in str(fields["error"])
