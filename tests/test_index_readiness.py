"""Unit tests for ReadinessState — the build-readiness state machine."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.indexing.readiness import ReadinessState


def test_fresh_state_not_built_but_done_preset() -> None:
    r = ReadinessState()
    assert r.is_built is False
    assert r.is_queryable() is False
    # done-event is pre-set so a never-built collection does not block waiters
    assert r.wait(timeout=0) is True


def test_mark_built_makes_queryable_and_clears_error() -> None:
    r = ReadinessState()
    r.fail_build(RuntimeError("old"))
    r.mark_built()
    assert r.is_built is True
    assert r.is_queryable() is True
    assert r.error is None


def test_begin_sync_build_only_clears_built() -> None:
    r = ReadinessState()
    r.mark_built()  # built=True, done set, error None
    prior_error = RuntimeError("kept")
    r._error = prior_error  # simulate a leftover error
    r.begin_sync_build()
    assert r.is_built is False
    # sync begin does NOT clear the error nor the done-event
    assert r.error is prior_error
    assert r.wait(timeout=0) is True


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


def test_require_built_passes_when_built() -> None:
    r = ReadinessState()
    r.mark_built()
    r.require_built()  # no raise
