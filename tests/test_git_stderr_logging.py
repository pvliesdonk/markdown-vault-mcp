"""Git stderr is safe for line-oriented logs without changing results."""

import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from markdown_vault_mcp.git import GitWriteStrategy, conflict
from tests.fixtures.git import GitRepoPair, _run_git


def test_force_push_logs_one_line_but_preserves_hint(
    git_repo_pair: GitRepoPair, caplog: pytest.LogCaptureFixture
) -> None:
    repo = git_repo_pair.local_path
    (repo / "pending.md").write_text("pending")
    _run_git(repo, "add", "pending.md")
    _run_git(repo, "commit", "-m", "pending")
    strategy = GitWriteStrategy(repo_path=repo, enable_push=True, token="secret-token")
    original_git = strategy._git

    def failing_push(root: Path, *args: str, **kwargs: object) -> str:
        if args[0] == "push":
            raise subprocess.CalledProcessError(
                1, args, stderr="remote: secret-token  \n  denied\taccess\n"
            )
        return original_git(root, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch.object(strategy, "_git", side_effect=failing_push),
        caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
    ):
        result = strategy.force_push()

    record = next(r for r in caplog.records if r.msg.startswith("git_push_failed"))
    assert "remote: *** denied access" in record.getMessage()
    assert "\n" not in record.getMessage()
    assert "secret-token" not in record.getMessage()
    assert result.reason == "push_failed"
    assert result.hint == "remote: ***  \n  denied\taccess"


def test_conflict_abort_logs_one_redacted_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    failed = subprocess.CompletedProcess(
        ["git", "rebase", "--abort"], 1, "", "secret-token\n  abort failed\t now\n"
    )
    with (
        patch.object(conflict, "run_git_capturing", return_value=failed),
        caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git.conflict"),
    ):
        assert not conflict.abort_in_progress_rebase(
            tmp_path, None, token="secret-token"
        )

    record = next(
        r for r in caplog.records if r.msg.startswith("git_conflict_abort_failed")
    )
    assert record.getMessage().endswith("stderr=*** abort failed now")
