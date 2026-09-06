"""Sync-health tracking for a managed-git clone (#1287).

A clone that cannot reach its remote keeps accepting writes: the commit
lands locally, ``read`` serves it back, and nothing in the write path says
the content is going nowhere.  These tests pin the state machine that makes
that visible — what marks the clone unsynced, what clears it, and that the
transition is logged once rather than every cycle.
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

from markdown_vault_mcp.git import conflict
from markdown_vault_mcp.git.health import SyncHealthTracker
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.types import (
    PULL_REASON_CONFLICT_RESOLUTION_FAILED,
    PULL_REASON_FETCH_FAILED,
    PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS,
    PUSH_REASON_NON_FAST_FORWARD,
    PUSH_REASON_PUSH_FAILED,
    REMOTE_STATE_UNSYNCED,
)
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    import threading
    from pathlib import Path

    import pytest

    from tests.fixtures.git import GitRepoPair


def _seed_remote_commit(
    pair: GitRepoPair, *, clone_name: str, file_name: str, body: str
) -> None:
    """Push one new commit to the bare remote from a sibling clone.

    Mirrors the helper in ``test_git_sync.py`` so this module stands alone —
    both drive the same bare-remote fixture at different layers.
    """
    sibling = pair.remote_path.parent / clone_name
    sibling.mkdir()
    _run_git(sibling, "init", "--initial-branch=main")
    _run_git(sibling, "config", "user.email", "other@example.com")
    _run_git(sibling, "config", "user.name", "Other")
    _run_git(sibling, "remote", "add", "origin", str(pair.remote_path))
    _run_git(sibling, "pull", "origin", "main")
    (sibling / file_name).write_text(body)
    _run_git(sibling, "add", file_name)
    _run_git(sibling, "commit", "-m", f"remote commit: {file_name}")
    _run_git(sibling, "push", "origin", "main")


class TestTrackerState:
    """What marks the clone unsynced, and what clears it again."""

    def test_a_fresh_tracker_reports_nothing(self) -> None:
        """No observation yet means no claim either way."""
        assert SyncHealthTracker().snapshot() is None

    def test_a_failed_push_marks_the_clone_unsynced(self) -> None:
        """The push failure is what strands the commits, so it is the signal."""
        tracker = SyncHealthTracker()

        tracker.push_failed(PUSH_REASON_PUSH_FAILED)

        health = tracker.snapshot()
        assert health is not None
        assert health.state == REMOTE_STATE_UNSYNCED
        assert health.reason == PUSH_REASON_PUSH_FAILED
        assert health.since.tzinfo is not None

    def test_a_successful_push_clears_the_state(self) -> None:
        """The commits reached origin, so there is nothing left to warn about."""
        tracker = SyncHealthTracker()
        tracker.push_failed(PUSH_REASON_NON_FAST_FORWARD)

        tracker.push_succeeded()

        assert tracker.snapshot() is None

    def test_an_unresolvable_pull_marks_the_clone_unsynced(self) -> None:
        """Divergence the resolver gave up on is what pushes then break against."""
        tracker = SyncHealthTracker()

        tracker.pull_failed(PULL_REASON_CONFLICT_RESOLUTION_FAILED)

        health = tracker.snapshot()
        assert health is not None
        assert health.reason == PULL_REASON_CONFLICT_RESOLUTION_FAILED

    def test_a_successful_pull_clears_an_unresolvable_pull(self) -> None:
        """The divergence is reconciled; the condition it raised is over."""
        tracker = SyncHealthTracker()
        tracker.pull_failed(PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS)

        tracker.pull_succeeded()

        assert tracker.snapshot() is None

    def test_a_successful_pull_does_not_clear_a_failed_push(self) -> None:
        """Reading from origin is not evidence that anything reached it.

        This is the incident shape in #1287: pulls recover while pushes keep
        being rejected, and every write still lands only on the host.
        """
        tracker = SyncHealthTracker()
        tracker.push_failed(PUSH_REASON_NON_FAST_FORWARD)

        tracker.pull_succeeded()

        health = tracker.snapshot()
        assert health is not None
        assert health.reason == PUSH_REASON_NON_FAST_FORWARD

    def test_the_start_of_the_outage_is_kept_across_repeats(self) -> None:
        """``since`` dates the outage, not the most recent failed attempt."""
        tracker = SyncHealthTracker()
        tracker.push_failed(PUSH_REASON_PUSH_FAILED)
        first = tracker.snapshot()
        assert first is not None

        tracker.push_failed(PUSH_REASON_PUSH_FAILED)

        second = tracker.snapshot()
        assert second is not None
        assert second.since == first.since

    def test_a_push_failure_outranks_a_pull_failure_in_the_reason(self) -> None:
        """A caller's writes are stranded by the push; that is the actionable half."""
        tracker = SyncHealthTracker()
        tracker.pull_failed(PULL_REASON_CONFLICT_RESOLUTION_FAILED)

        tracker.push_failed(PUSH_REASON_NON_FAST_FORWARD)

        health = tracker.snapshot()
        assert health is not None
        assert health.reason == PUSH_REASON_NON_FAST_FORWARD

    def test_both_conditions_must_clear_before_the_state_does(self) -> None:
        """Either condition alone is enough to keep the clone unsynced."""
        tracker = SyncHealthTracker()
        tracker.pull_failed(PULL_REASON_CONFLICT_RESOLUTION_FAILED)
        tracker.push_failed(PUSH_REASON_NON_FAST_FORWARD)

        tracker.push_succeeded()

        health = tracker.snapshot()
        assert health is not None
        assert health.reason == PULL_REASON_CONFLICT_RESOLUTION_FAILED

    def test_a_pull_that_could_not_reach_the_remote_changes_nothing(self) -> None:
        """A failed fetch is not evidence that commits are stranded.

        The deferred push may still be reaching origin fine; only outcomes
        that prove the clone cannot reconcile or cannot send are recorded.
        """
        tracker = SyncHealthTracker()

        tracker.pull_failed(PULL_REASON_FETCH_FAILED)

        assert tracker.snapshot() is None


class TestTrackerPayload:
    """The shape the MCP write path hands to a caller."""

    def test_the_payload_carries_state_reason_since_and_advice(self) -> None:
        """A caller reacts to ``state``; a human reads ``detail``."""
        tracker = SyncHealthTracker()
        tracker.push_failed(PUSH_REASON_PUSH_FAILED)
        health = tracker.snapshot()
        assert health is not None

        payload = health.as_payload()

        assert payload["state"] == REMOTE_STATE_UNSYNCED
        assert payload["reason"] == PUSH_REASON_PUSH_FAILED
        assert payload["since"].endswith("+00:00")
        assert "committed locally" in payload["detail"]


class TestTransitionLogging:
    """One line per state change, not one per failed cycle (#1287, item 3)."""

    def test_entering_the_unsynced_state_logs_once_at_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The transition is the event an operator needs to catch."""
        tracker = SyncHealthTracker()

        with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.git.health"):
            tracker.push_failed(PUSH_REASON_PUSH_FAILED)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "git_remote_unsynced" in errors[0].message

    def test_staying_unsynced_does_not_log_again(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The repeating per-cycle warning is what hid the incident for hours."""
        tracker = SyncHealthTracker()
        tracker.push_failed(PUSH_REASON_PUSH_FAILED)
        caplog.clear()

        with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.git.health"):
            tracker.push_failed(PUSH_REASON_PUSH_FAILED)
            tracker.push_failed(PUSH_REASON_NON_FAST_FORWARD)

        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_recovery_logs_once_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        """The clone reaching origin again closes the incident in the log."""
        tracker = SyncHealthTracker()
        tracker.push_failed(PUSH_REASON_PUSH_FAILED)
        caplog.clear()

        with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.git.health"):
            tracker.push_succeeded()

        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1
        assert "git_remote_resynced" in infos[0].message

    def test_a_healthy_clone_stays_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Every successful push must not narrate itself."""
        tracker = SyncHealthTracker()

        with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.git.health"):
            tracker.push_succeeded()
            tracker.pull_succeeded()

        assert [r for r in caplog.records if r.levelno >= logging.INFO] == []


class TestStrategyRecordsPushOutcomes:
    """The push paths feed the tracker (#1287)."""

    def test_a_fresh_strategy_reports_nothing(self) -> None:
        """A strategy that has not pushed makes no claim about the remote."""
        assert GitWriteStrategy().sync_health() is None

    def test_a_failed_deferred_push_marks_the_clone_unsynced(
        self, tmp_path: Path
    ) -> None:
        """The deferred push is the path that strands ordinary MCP writes."""
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = tmp_path
        strategy._push_pending = True
        rejected = subprocess.CalledProcessError(
            returncode=1,
            cmd=["git", "push", "origin"],
            stderr="! [rejected] main -> main (non-fast-forward)",
        )

        with patch("markdown_vault_mcp.git.push_scheduler._push", side_effect=rejected):
            strategy._push_scheduler.do_push_safe()

        health = strategy.sync_health()
        assert health is not None
        assert health.reason == PUSH_REASON_NON_FAST_FORWARD

    def test_an_unclassifiable_push_failure_is_recorded_as_push_failed(
        self, tmp_path: Path
    ) -> None:
        """Auth and network failures strand commits just as effectively."""
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = tmp_path
        strategy._push_pending = True

        with patch(
            "markdown_vault_mcp.git.push_scheduler._push",
            side_effect=OSError("boom"),
        ):
            strategy._push_scheduler.do_push_safe()

        health = strategy.sync_health()
        assert health is not None
        assert health.reason == PUSH_REASON_PUSH_FAILED

    def test_a_later_successful_push_clears_the_state(self, tmp_path: Path) -> None:
        """Recovery is observable, so a caller stops being warned."""
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = tmp_path
        strategy._push_pending = True
        rejected = subprocess.CalledProcessError(
            returncode=1, cmd=["git", "push", "origin"], stderr="failed"
        )
        with patch("markdown_vault_mcp.git.push_scheduler._push", side_effect=rejected):
            strategy._push_scheduler.do_push_safe()

        strategy._push_pending = True
        with patch("markdown_vault_mcp.git.push_scheduler._push"):
            strategy._push_scheduler.do_push()

        assert strategy.sync_health() is None

    def test_a_failed_startup_push_marks_the_clone_unsynced(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """Commits left over from a previous run are stranded the same way."""
        (git_repo_pair.local_path / "unpushed.md").write_text("unpushed\n")
        _run_git(git_repo_pair.local_path, "add", "unpushed.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "unpushed")
        strategy = GitWriteStrategy(
            token=None, push_delay_s=0, repo_path=git_repo_pair.local_path
        )
        strategy._git_root = git_repo_pair.local_path
        rejected = subprocess.CalledProcessError(
            returncode=1, cmd=["git", "push", "origin"], stderr="fetch first"
        )

        with patch("markdown_vault_mcp.git.push_scheduler._push", side_effect=rejected):
            strategy._push_scheduler.push_if_unpushed()

        health = strategy.sync_health()
        assert health is not None
        assert health.reason == PUSH_REASON_NON_FAST_FORWARD

    def test_a_rejected_force_push_marks_the_clone_unsynced(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """The interactive push observes the same remote as the deferred one."""
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_health_push",
            file_name="remote.md",
            body="# remote\n",
        )
        (git_repo_pair.local_path / "local.md").write_text("# local\n")
        _run_git(git_repo_pair.local_path, "add", "local.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "local edit")
        strategy = GitWriteStrategy(
            enable_pull=True, enable_push=True, repo_path=git_repo_pair.local_path
        )
        _run_git(git_repo_pair.local_path, "fetch", "origin")

        result = strategy.force_push()

        assert result.reason == PUSH_REASON_NON_FAST_FORWARD
        health = strategy.sync_health()
        assert health is not None
        assert health.reason == PUSH_REASON_NON_FAST_FORWARD

    def test_a_clean_force_push_clears_the_state(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """Pushing successfully is the proof the commits left the host."""
        strategy = GitWriteStrategy(
            enable_pull=True, enable_push=True, repo_path=git_repo_pair.local_path
        )
        strategy._health.push_failed(PUSH_REASON_PUSH_FAILED)
        (git_repo_pair.local_path / "local.md").write_text("# local\n")
        _run_git(git_repo_pair.local_path, "add", "local.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "local edit")

        result = strategy.force_push()

        assert result.applied is True
        assert strategy.sync_health() is None


class TestStrategyRecordsPullOutcomes:
    """The pull pipeline feeds the tracker from both entry points (#1287)."""

    def test_an_unresolvable_pull_marks_the_clone_unsynced(
        self, git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Divergence the resolver gives up on is what pushes break against."""
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_health_pull",
            file_name="README.md",
            body="# remote\n",
        )
        (git_repo_pair.local_path / "README.md").write_text("# local\n")
        _run_git(git_repo_pair.local_path, "add", "README.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "local edit")
        strategy = GitWriteStrategy(
            enable_pull=True, enable_push=False, repo_path=git_repo_pair.local_path
        )

        def _raising_resolve(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("simulated conflict resolution failure")

        monkeypatch.setattr(conflict, "resolve_rebase_conflicts", _raising_resolve)

        result = strategy.force_pull()

        assert result.reason == PULL_REASON_CONFLICT_RESOLUTION_FAILED
        health = strategy.sync_health()
        assert health is not None
        assert health.reason == PULL_REASON_CONFLICT_RESOLUTION_FAILED

    def test_a_clean_pull_clears_the_state(self, git_repo_pair: GitRepoPair) -> None:
        """Reconciling with the remote ends the condition the divergence raised."""
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_health_pull_ok",
            file_name="seeded.md",
            body="seeded\n",
        )
        strategy = GitWriteStrategy(
            enable_pull=True, enable_push=False, repo_path=git_repo_pair.local_path
        )
        strategy._health.pull_failed(PULL_REASON_CONFLICT_RESOLUTION_FAILED)

        result = strategy.force_pull()

        assert result.applied is True
        assert strategy.sync_health() is None

    def test_a_dry_run_pull_records_nothing(self, git_repo_pair: GitRepoPair) -> None:
        """A prediction is not an observation of the clone's health."""
        strategy = GitWriteStrategy(
            enable_pull=True, enable_push=False, repo_path=git_repo_pair.local_path
        )
        strategy._health.pull_failed(PULL_REASON_CONFLICT_RESOLUTION_FAILED)

        strategy.force_pull(dry_run=True)

        assert strategy.sync_health() is not None


class TestOutageStartSurvivesPartialRecovery:
    """``since`` dates the outage, not the condition that outlived it."""

    def test_clearing_the_older_condition_keeps_the_outage_start(self) -> None:
        """The clone never became healthy, so the outage never restarted.

        Reported on PR #1300: with both conditions open, closing whichever
        opened first recomputed ``since`` from the survivor, so a payload
        understated how long the writes had been stranded.
        """
        tracker = SyncHealthTracker()
        tracker.pull_failed(PULL_REASON_CONFLICT_RESOLUTION_FAILED)
        first = tracker.snapshot()
        assert first is not None
        tracker.push_failed(PUSH_REASON_NON_FAST_FORWARD)

        tracker.pull_succeeded()

        health = tracker.snapshot()
        assert health is not None
        assert health.since == first.since

    def test_a_new_outage_after_recovery_gets_a_new_start(self) -> None:
        """A clone that recovered and broke again is a second outage."""
        tracker = SyncHealthTracker()
        tracker.push_failed(PUSH_REASON_NON_FAST_FORWARD)
        first = tracker.snapshot()
        assert first is not None
        tracker.push_succeeded()

        tracker.push_failed(PUSH_REASON_NON_FAST_FORWARD)

        health = tracker.snapshot()
        assert health is not None
        assert health.since > first.since


class _LockObservingTracker(SyncHealthTracker):
    """Records whether a lock was held at the moment each outcome landed."""

    def __init__(self, lock: threading.Lock) -> None:
        super().__init__()
        self._observed_lock = lock
        self.held: list[bool] = []

    def push_failed(self, reason: str, detail: str | None = None) -> None:
        self.held.append(self._observed_lock.locked())
        super().push_failed(reason, detail)

    def push_succeeded(self) -> None:
        self.held.append(self._observed_lock.locked())
        super().push_succeeded()

    def pull_failed(self, reason: str) -> None:
        self.held.append(self._observed_lock.locked())
        super().pull_failed(reason)

    def pull_succeeded(self) -> None:
        self.held.append(self._observed_lock.locked())
        super().pull_succeeded()


def _observe_health(strategy: GitWriteStrategy) -> _LockObservingTracker:
    """Swap in a tracker that reports the strategy lock's state per record."""
    tracker = _LockObservingTracker(strategy._lock)
    strategy._health = tracker
    strategy._push_scheduler._health = tracker
    return tracker


class TestOutcomesAreRecordedUnderTheStrategyLock:
    """Concurrent operations must not record their outcomes out of order.

    Pushes and pulls are serialised by the strategy-wide lock, so recording
    an outcome after releasing it lets an older operation publish over a
    newer one — an earlier push's success clearing the failure of the push
    that ran after it, which puts the caller back to being told nothing
    while its commits are stranded. Reported on PR #1300.
    """

    def test_a_deferred_push_records_before_releasing_the_lock(
        self, tmp_path: Path
    ) -> None:
        """The path ordinary MCP writes take."""
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = tmp_path
        strategy._push_pending = True
        tracker = _observe_health(strategy)
        rejected = subprocess.CalledProcessError(
            returncode=1, cmd=["git", "push", "origin"], stderr="fetch first"
        )

        with patch("markdown_vault_mcp.git.push_scheduler._push", side_effect=rejected):
            strategy._push_scheduler.do_push_safe()

        assert tracker.held == [True]

    def test_an_interactive_push_records_before_releasing_the_lock(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """``git_sync(direction="push")`` observes the same remote."""
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_lock_push",
            file_name="remote.md",
            body="# remote\n",
        )
        (git_repo_pair.local_path / "local.md").write_text("# local\n")
        _run_git(git_repo_pair.local_path, "add", "local.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "local edit")
        strategy = GitWriteStrategy(
            enable_pull=True, enable_push=True, repo_path=git_repo_pair.local_path
        )
        tracker = _observe_health(strategy)

        strategy.force_push()

        assert tracker.held == [True]

    def test_a_pull_records_before_releasing_the_lock(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """Two pulls are ordered by the lock; their outcomes must be too."""
        _seed_remote_commit(
            git_repo_pair,
            clone_name="clone_lock_pull",
            file_name="seeded.md",
            body="seeded\n",
        )
        strategy = GitWriteStrategy(
            enable_pull=True, enable_push=False, repo_path=git_repo_pair.local_path
        )
        tracker = _observe_health(strategy)

        strategy.force_pull()

        assert tracker.held == [True]


# ---------------------------------------------------------------------------
# A stranded clone says why, at the level deployments run at (#1330)
# ---------------------------------------------------------------------------


class TestPushFailureIsDiagnosable:
    """What an operator can learn from the log when pushes stop landing.

    Observed on a live vault: the only push-related line in the whole
    container log was the transition into the unsynced state, naming the
    generic ``push_failed`` bucket. Git's own words sat at DEBUG, and the
    deployment ran at INFO.
    """

    @staticmethod
    def _rejected() -> subprocess.CalledProcessError:
        return subprocess.CalledProcessError(
            returncode=1,
            cmd=["git", "push", "origin"],
            stderr=(
                "remote: GitLab: You are not allowed to push code to "
                "protected branches on this project."
            ),
        )

    def test_the_per_attempt_line_carries_git_stderr_at_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = tmp_path
        strategy._push_pending = True

        with (
            patch(
                "markdown_vault_mcp.git.push_scheduler._push",
                side_effect=self._rejected(),
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.git"),
        ):
            strategy._push_scheduler.do_push_safe()

        line = next(r for r in caplog.records if "git_push_failed" in r.message)
        assert line.levelno == logging.WARNING
        assert "protected branches" in line.getMessage()

    def test_every_retry_logs_so_retrying_is_distinguishable_from_stopped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The transition line fires once; the attempts are what repeat."""
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = tmp_path

        with (
            patch(
                "markdown_vault_mcp.git.push_scheduler._push",
                side_effect=self._rejected(),
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.git"),
        ):
            for _ in range(3):
                strategy._push_pending = True
                strategy._push_scheduler.do_push_safe()

        assert len([r for r in caplog.records if "git_push_failed" in r.message]) == 3

    def test_the_pull_loop_retry_of_a_pending_push_stays_at_debug(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A retry fires on the pull timer, so warning per tick is #1287 again."""
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = tmp_path
        strategy._push_pending = True

        with (
            patch(
                "markdown_vault_mcp.git.push_scheduler._push",
                side_effect=self._rejected(),
            ),
            caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.git"),
        ):
            strategy._push_scheduler.do_push_safe(retry=True)

        line = next(r for r in caplog.records if "git_push_failed" in r.message)
        assert line.levelno == logging.DEBUG
        assert "protected branches" in line.getMessage()

    def test_a_whitespace_only_cause_reads_as_unavailable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        tracker = SyncHealthTracker()
        with caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"):
            tracker.push_failed(PUSH_REASON_PUSH_FAILED, "  \n ")

        line = next(r for r in caplog.records if "git_remote_unsynced" in r.message)
        assert line.getMessage().endswith("cause=unavailable")

    def test_the_transition_line_names_the_cause_not_just_the_bucket(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``push_failed`` is the bucket every unrecognised stderr lands in."""
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = tmp_path
        strategy._push_pending = True

        with (
            patch(
                "markdown_vault_mcp.git.push_scheduler._push",
                side_effect=self._rejected(),
            ),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            strategy._push_scheduler.do_push_safe()

        line = next(r for r in caplog.records if "git_remote_unsynced" in r.message)
        assert "protected branches" in line.getMessage()

    def test_the_cause_is_one_line_however_git_wrapped_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Git's stderr spans lines and pads them; a key=value line cannot."""
        tracker = SyncHealthTracker()
        stderr = (
            "remote: GitLab: You are not allowed to push.        \n"
            "To https://example.invalid/vault.git\n"
            " ! [remote rejected] main -> main (pre-receive hook declined)"
        )
        with caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"):
            tracker.push_failed(PUSH_REASON_PUSH_FAILED, stderr)

        line = next(r for r in caplog.records if "git_remote_unsynced" in r.message)
        message = line.getMessage()
        assert "\n" not in message
        assert message.endswith(
            "cause=remote: GitLab: You are not allowed to push. "
            "To https://example.invalid/vault.git "
            "! [remote rejected] main -> main (pre-receive hook declined)"
        )

    def test_a_missing_cause_reads_as_unavailable_not_none(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An outcome with nothing to say still produces a readable line.

        ``OSError()`` with no message makes ``str(exc)`` empty, which is the
        shape ``_push_locked`` turns into a missing detail.
        """
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = tmp_path
        strategy._push_pending = True

        with (
            patch(
                "markdown_vault_mcp.git.push_scheduler._push",
                side_effect=OSError(),
            ),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            strategy._push_scheduler.do_push_safe()

        line = next(r for r in caplog.records if "git_remote_unsynced" in r.message)
        assert "cause=unavailable" in line.getMessage()

    def test_a_push_result_without_a_hint_reads_as_unavailable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The interactive path forwards ``PushResult.hint``, which may be None."""
        tracker = SyncHealthTracker()
        with caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"):
            tracker.push_failed(PUSH_REASON_PUSH_FAILED, None)

        line = next(r for r in caplog.records if "git_remote_unsynced" in r.message)
        assert "cause=unavailable" in line.getMessage()

    def test_the_startup_push_logs_its_rejection_at_warning(
        self, git_repo_pair: GitRepoPair, caplog: pytest.LogCaptureFixture
    ) -> None:
        """On a restart with unpushed commits, nothing was logged at INFO."""
        (git_repo_pair.local_path / "unpushed.md").write_text("unpushed\n")
        _run_git(git_repo_pair.local_path, "add", "unpushed.md")
        _run_git(git_repo_pair.local_path, "commit", "-m", "unpushed")
        strategy = GitWriteStrategy(
            token=None, push_delay_s=0, repo_path=git_repo_pair.local_path
        )
        strategy._git_root = git_repo_pair.local_path

        with (
            patch(
                "markdown_vault_mcp.git.push_scheduler._push",
                side_effect=self._rejected(),
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.git"),
        ):
            strategy._push_scheduler.push_if_unpushed()

        line = next(r for r in caplog.records if "git_startup_push_failed" in r.message)
        assert line.levelno == logging.WARNING
        assert "protected branches" in line.getMessage()
        transition = next(
            r for r in caplog.records if "git_remote_unsynced" in r.message
        )
        assert "protected branches" in transition.getMessage()

    def test_the_token_is_still_redacted_at_the_new_level(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Raising the level must not raise a secret with it."""
        secret = "glpat_secret_abc123"
        strategy = GitWriteStrategy(token=secret, push_delay_s=0)
        strategy._git_root = tmp_path
        strategy._push_pending = True
        exc = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "push", "origin"],
            stderr=f"fatal: authentication failed — token={secret}",
        )

        with (
            patch("markdown_vault_mcp.git.push_scheduler._push", side_effect=exc),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.git"),
        ):
            strategy._push_scheduler.do_push_safe()

        text = " ".join(r.getMessage() for r in caplog.records)
        assert secret not in text
        assert "***" in text
