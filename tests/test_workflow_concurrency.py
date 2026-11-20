"""Pull-request runs cancel superseded ones; branch runs are never cancelled (#2390)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
NAMES = ["ci.yml", "codeql.yml", "spa-source-check.yml"]


def _concurrency(name: str) -> dict[str, Any]:
    loaded: dict[Any, Any] = yaml.safe_load(
        (WORKFLOWS / name).read_text(encoding="utf-8")
    )
    block: dict[str, Any] = loaded["concurrency"]
    return block


@pytest.mark.parametrize("name", NAMES)
def test_pull_request_runs_share_a_group_per_pull_request(name: str) -> None:
    group = _concurrency(name)["group"]
    assert "github.event_name == 'pull_request' && github.ref" in group


@pytest.mark.parametrize("name", NAMES)
def test_other_runs_are_grouped_by_commit(name: str) -> None:
    assert "|| github.sha" in _concurrency(name)["group"]


@pytest.mark.parametrize("name", NAMES)
def test_only_pull_request_runs_cancel_in_progress(name: str) -> None:
    cancel = _concurrency(name)["cancel-in-progress"]
    assert cancel == "${{ github.event_name == 'pull_request' }}"
