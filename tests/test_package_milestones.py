"""Exercise release-package retries, partial failures and GitHub API semantics."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import package_milestones as packages


def milestone(number: int, title: str, state: str = "open") -> dict[str, Any]:
    return {"number": number, "title": title, "state": state, "open_issues": 0}


class Tracker:
    def __init__(self) -> None:
        self.milestones = [
            milestone(20, "020 second"),
            milestone(9, "9.0"),
            milestone(10, "010 first"),
        ]
        self.items: list[dict[str, Any]] = [
            {"number": 31, "milestone": {"number": 10}},
            {"number": 32, "milestone": {"number": 10}, "pull_request": {}},
        ]
        self.fail_removal = False
        self.ignore_removal = False
        self.fail_close = False
        self.mutations: list[tuple[str, dict[str, Any]]] = []

    def api(self, path: str, *, fields: dict[str, Any] | None = None) -> Any:
        if fields is None:
            if "/milestones?" in path:
                return copy.deepcopy(self.milestones)
            number = int(path.split("milestone=")[1].split("&")[0])
            return copy.deepcopy(
                [item for item in self.items if item["milestone"] == {"number": number}]
            )
        self.mutations.append((path, fields))
        number = int(path.rsplit("/", 1)[1])
        if "/issues/" in path:
            if self.fail_removal:
                raise subprocess.CalledProcessError(1, ["gh", "api"])
            item = next(item for item in self.items if item["number"] == number)
            if not self.ignore_removal:
                item.update(fields)
            return copy.deepcopy(item)
        if self.fail_close and fields.get("state") == "closed":
            raise subprocess.CalledProcessError(1, ["gh", "api"])
        target = next(m for m in self.milestones if m["number"] == number)
        target.update(fields)
        return copy.deepcopy(target)


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> Tracker:
    instance = Tracker()
    monkeypatch.setattr(packages, "api", instance.api)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)
    return instance


def test_closes_only_current_and_returns_issue_and_pr_to_backlog(
    tracker: Tracker, capsys: pytest.CaptureFixture[str]
) -> None:
    packages.run("close", "owner/repo", "4.3.0")
    assert tracker.milestones[2] == milestone(10, "v4.3.0 first", "closed")
    assert tracker.milestones[0]["state"] == "open"
    assert tracker.milestones[1]["title"] == "9.0"
    assert all(item["milestone"] is None for item in tracker.items)
    assert tracker.mutations[0][1] == {"title": "v4.3.0 first"}
    output = capsys.readouterr().out
    assert "issue #31" in output and "PR #32" in output


def test_completed_retry_never_closes_next_package(tracker: Tracker) -> None:
    packages.run("close", "owner/repo", "4.3.0")
    previous = copy.deepcopy(tracker.mutations)
    packages.run("close", "owner/repo", "4.3.0")
    assert tracker.mutations == previous
    assert tracker.milestones[0]["state"] == "open"


@pytest.mark.parametrize("failure", ["fail_removal", "ignore_removal", "fail_close"])
def test_partial_failure_leaves_package_open_and_retry_resumes(
    tracker: Tracker, failure: str
) -> None:
    setattr(tracker, failure, True)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        packages.run("close", "owner/repo", "4.3.0")
    assert tracker.milestones[2]["state"] == "open"
    assert tracker.milestones[2]["title"] == "v4.3.0 first"
    # A different cut cannot bypass unfinished bookkeeping.
    with pytest.raises(ValueError, match="unfinished"):
        packages.run("close", "owner/repo", "4.4.0")
    setattr(tracker, failure, False)
    packages.run("close", "owner/repo", "4.3.0")
    assert tracker.milestones[2]["state"] == "closed"
    assert tracker.milestones[0]["state"] == "open"


def test_no_package_is_a_noop(tracker: Tracker) -> None:
    tracker.milestones = [milestone(1, "Future"), milestone(2, "v1.0")]
    packages.run("close", "owner/repo", "4.3.0")
    assert not tracker.mutations


@pytest.mark.parametrize("workflow_retry", [False, True])
def test_retry_after_no_package_does_not_consume_new_package(
    tracker: Tracker, monkeypatch: pytest.MonkeyPatch, workflow_retry: bool
) -> None:
    tracker.milestones = []
    packages.run("close", "owner/repo", "4.3.0")
    tracker.milestones = [milestone(10, "010 next-cut")]
    if workflow_retry:
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    packages.run("close", "owner/repo", "4.3.0", resume_only=not workflow_retry)
    assert not tracker.mutations
    assert tracker.milestones[0]["title"] == "010 next-cut"


def test_duplicate_ordinal_refuses_mutations(tracker: Tracker) -> None:
    tracker.milestones.append(milestone(30, "010 ambiguous"))
    with pytest.raises(ValueError, match="Duplicate"):
        packages.run("close", "owner/repo", "4.3.0")
    assert not tracker.mutations


def test_prerelease_refuses_mutations(tracker: Tracker) -> None:
    with pytest.raises(ValueError, match="stable"):
        packages.run("close", "owner/repo", "4.3.0-rc.1")
    assert not tracker.mutations


def test_warning_uses_current_package_even_when_later_one_is_open(
    tracker: Tracker, capsys: pytest.CaptureFixture[str]
) -> None:
    tracker.milestones[0]["open_issues"] = 9
    packages.run("warn", "owner/repo")
    assert capsys.readouterr().out == ""
    tracker.milestones[2]["open_issues"] = 2
    packages.run("warn", "owner/repo")
    assert "Current package 010 first — 2 open item(s)" in capsys.readouterr().out
    assert not tracker.mutations


def test_api_combines_pages_and_sends_json_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def execute(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        payload = [[{"number": 20}], [{"number": 10}]]
        stdout = json.dumps(payload if "--paginate" in command else {"milestone": None})
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", execute)
    assert packages.api("repos/o/r/milestones") == [{"number": 20}, {"number": 10}]
    assert "--paginate" in calls[0][0] and "--slurp" in calls[0][0]
    assert packages.api("repos/o/r/issues/1", fields={"milestone": None}) == {
        "milestone": None
    }
    assert json.loads(calls[1][1]["input"]) == {"milestone": None}
    assert "--input" in calls[1][0]


def test_concurrent_open_member_prevents_closure(
    tracker: Tracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = 0

    def api(path: str, *, fields: dict[str, Any] | None = None) -> Any:
        nonlocal reads
        if "/issues?" in path:
            reads += 1
            if reads == 2:
                return [{"number": 99, "milestone": {"number": 10}}]
        return tracker.api(path, fields=fields)

    monkeypatch.setattr(packages, "api", api)
    with pytest.raises(ValueError, match="Open items remain"):
        packages.run("close", "owner/repo", "4.3.0")
    assert tracker.milestones[2]["state"] == "open"


def test_cli_failure_warns_without_failing_release(
    tracker: Tracker,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker.fail_removal = True
    monkeypatch.setattr(
        sys,
        "argv",
        ["package_milestones.py", "close", "--repo", "o/r", "--version", "1.2.3"],
    )
    assert packages.main() == 0
    assert "::warning::Package bookkeeping incomplete" in capsys.readouterr().out
    assert tracker.milestones[2]["state"] == "open"
