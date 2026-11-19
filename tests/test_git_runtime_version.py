"""Report the runtime Git version without making a compatibility promise."""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.git.bootstrap import RepoBootstrap
from markdown_vault_mcp.git.strategy import GitWriteStrategy

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


@pytest.mark.parametrize("managed", [False, True])
def test_startup_reports_actual_git_version_once(
    git_repo_pair: GitRepoPair,
    caplog: pytest.LogCaptureFixture,
    managed: bool,
) -> None:
    """Managed and commit-only startup identify the Git on this process's PATH."""
    expected = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    with caplog.at_level(logging.INFO):
        strategy = GitWriteStrategy(
            repo_path=git_repo_pair.local_path,
            repo_url=str(git_repo_pair.remote_path) if managed else None,
            managed=managed,
            enable_push=False,
            git_lfs=False,
        )
        try:
            strategy.start(repo_path=git_repo_pair.local_path, pull_interval_s=0)
        finally:
            strategy.close()
    records = [r for r in caplog.records if r.msg == "git_runtime_version version=%s"]
    assert len(records) == 1
    assert records[0].args == (expected,)


def test_plain_directory_does_not_probe_version(tmp_path: Path) -> None:
    """No-git startup remains free of a version diagnostic."""
    bootstrap = RepoBootstrap(
        lock=threading.Lock(), token=None, username="git", repo_url=None
    )
    with (
        patch.object(bootstrap, "ensure_git_root", return_value=None),
        patch("markdown_vault_mcp.git.bootstrap.subprocess.run") as run,
    ):
        bootstrap.validate_startup(tmp_path)
    run.assert_not_called()


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.CompletedProcess(
            ["git", "--version"], 0, "git version 2.39.5 (Apple Git-154)\n"
        ),
        FileNotFoundError("git"),
        subprocess.CalledProcessError(1, ["git", "--version"]),
        subprocess.TimeoutExpired(["git", "--version"], 5),
    ],
)
def test_version_probe_is_bounded_and_diagnostic_only(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    outcome: subprocess.CompletedProcess[str] | Exception,
) -> None:
    """Vendor suffixes survive, while probe failures never reject startup."""
    bootstrap = RepoBootstrap(
        lock=threading.Lock(), token=None, username="git", repo_url=None
    )
    with (
        patch.object(bootstrap, "ensure_git_root", return_value=tmp_path),
        patch("markdown_vault_mcp.git.bootstrap.subprocess.run") as run,
        caplog.at_level(logging.DEBUG),
    ):
        if isinstance(outcome, Exception):
            run.side_effect = outcome
        else:
            run.return_value = outcome
        bootstrap.validate_startup(tmp_path)
        bootstrap.validate_startup(tmp_path)
    run.assert_called_once_with(
        ["git", "--version"], capture_output=True, text=True, check=True, timeout=5
    )
    if isinstance(outcome, Exception):
        assert "git_version_unavailable" in caplog.text
        assert "git_runtime_version" not in caplog.text
    else:
        assert (
            "git_runtime_version version=git version 2.39.5 (Apple Git-154)"
            in caplog.text
        )
