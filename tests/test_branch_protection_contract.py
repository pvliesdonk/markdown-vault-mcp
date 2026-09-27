"""Guards for the branch-protection posture the rulesets and workflows share.

`bootstrap.yml` applies `.github/rulesets/*.json` verbatim, and a required
check only works when the workflow behind it runs on the branches the
ruleset protects.  The two live in different files, so a change to one can
silently deadlock or unguard the other.  The posture asserted here:

- No branch ruleset requires a pull request to be up to date with its base
  before merging (`docs/deployment/repository-protection.md`).
- `integration/*` branches require `CI Success` alone, and `ci.yml` runs
  on pull requests to them and pushes to them, so an epic's child pull
  requests are gated without waiting on a domain check that never reports
  (`docs/deployment/integration-branches.md`).
- The release workflow never fires for a pull request merged into an
  integration branch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RULESETS = REPO_ROOT / ".github" / "rulesets"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _branch_rulesets() -> list[Path]:
    return sorted(
        path
        for path in RULESETS.glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["target"] == "branch"
    )


def _status_rule(ruleset: dict[str, Any]) -> dict[str, Any]:
    for rule in ruleset["rules"]:
        if rule["type"] == "required_status_checks":
            return dict(rule["parameters"])
    raise AssertionError(f"{ruleset['name']} has no required_status_checks rule")


def _triggers(workflow: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    return dict(data.get("on", data.get(True)))


@pytest.mark.parametrize("path", _branch_rulesets(), ids=lambda p: p.name)
def test_branch_rulesets_do_not_require_up_to_date_branches(path: Path) -> None:
    ruleset = json.loads(path.read_text(encoding="utf-8"))
    assert _status_rule(ruleset)["strict_required_status_checks_policy"] is False, (
        f"{path.name} requires pull requests to be up to date with their base; "
        "every merge would then force an update-branch round trip on each "
        "open pull request"
    )


def test_integration_branches_require_ci_success_alone() -> None:
    ruleset = json.loads(
        (RULESETS / "protect-integration-branches.json").read_text(encoding="utf-8")
    )
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/integration/**"]
    contexts = [c["context"] for c in _status_rule(ruleset)["required_status_checks"]]
    assert contexts == ["CI Success"]


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_ci_runs_on_integration_branches(event: str) -> None:
    branches = _triggers("ci.yml")[event]["branches"]
    assert "integration/**" in branches, (
        f"ci.yml's {event} trigger must cover integration/**: the ruleset "
        "requires CI Success there, which never reports otherwise"
    )


def test_release_ignores_integration_branches() -> None:
    data = yaml.safe_load((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
    guards = " ".join(str(job.get("if", "")) for job in data["jobs"].values())
    assert "github.event.repository.default_branch" in guards
    assert "startsWith(github.event.pull_request.base.ref, 'release/')" in guards
    assert "integration" not in guards
