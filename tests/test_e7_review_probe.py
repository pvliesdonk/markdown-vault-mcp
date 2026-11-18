from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair

from markdown_vault_mcp._okf_log_merge import merge_log_texts
from tests.fixtures.conflict_recovery import recovery_vault
from tests.fixtures.git import _git_clone, _git_commit, _run_git


def test_commonmark_date_heading_is_merged() -> None:
    upstream = "# Log\n\n##  2026-11-18\n\n- remote\n"
    local = "# Log\n\n##  2026-11-18\n\n- local\n"
    result = merge_log_texts(upstream, local)
    assert result.text is not None, result.reason
    assert "- remote\n" in result.text and "- local\n" in result.text


@pytest.mark.parametrize("deleted", [False, True])
def test_abort_after_later_clean_log_commit_preserves_latest(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deleted: bool,
) -> None:
    from markdown_vault_mcp.git import conflict

    root = git_repo_pair.local_path
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed log")
    _git_commit(root, "other.md", "base\n", "seed other")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "log.md", base + "- remote\n", "remote log")
    _git_commit(other, "other.md", "remote\n", "remote other")
    _run_git(other, "push", "origin", "main")
    _git_commit(root, "log.md", base + "- local one\n", "local log one")
    latest = base + "- local one\n- local two\n"
    _git_commit(root, "log.md", latest, "local log two")
    if deleted:
        _run_git(root, "rm", "log.md")
        _run_git(root, "commit", "-m", "delete log")
    _git_commit(root, "other.md", "local\n", "local other")
    original_run = subprocess.run
    calls = 0

    def fail_second_continue(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        command = args[0]
        if "rebase" in command and "--continue" in command:
            calls += 1
            if calls >= 2:
                return subprocess.CompletedProcess(command, 1, "", "forced failure")
        return original_run(*args, **kwargs)

    strategy, vault = recovery_vault(root)
    try:
        monkeypatch.setattr(conflict.subprocess, "run", fail_second_continue)
        result = strategy.force_pull()
        if deleted:
            from markdown_vault_mcp.utils.fs import path_exists

            assert result.reason == "conflict_resolution_failed"
            assert not path_exists(root / "log.md")
            return
        all_contents = [p.read_text() for p in root.glob("log*.md")]
        assert any("- remote\n" in content for content in all_contents), result
        assert any(
            "- local one\n" in content and "- local two\n" in content
            for content in all_contents
        ), (result, all_contents, calls)
    finally:
        vault.close()
        strategy.close()


def test_pull_merges_commonmark_date_heading(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    root = git_repo_pair.local_path
    base = "# Log\n\n##  2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "log.md", base + "- remote\n", "remote")
    _run_git(other, "push", "origin", "main")
    _git_commit(root, "log.md", base + "- local\n", "local")
    strategy, vault = recovery_vault(root)
    try:
        result = strategy.force_pull()
        assert result.reason == "rebased", result
        log = (root / "log.md").read_text()
        assert "- remote\n" in log and "- local\n" in log
    finally:
        vault.close()
        strategy.close()
