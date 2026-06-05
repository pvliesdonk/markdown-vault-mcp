"""Unit tests for ReadinessState — the build-readiness state machine."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.indexing.readiness import ReadinessState


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
    r._error = RuntimeError("stale")  # leftover error from a prior failure
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
    r._error = RuntimeError("kept?")
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
    r._error = RuntimeError("late diagnostic")
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
