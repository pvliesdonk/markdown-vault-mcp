"""Real Git coverage for descriptive server commit trailers."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["single", "batch", "group", "ai"])
def test_server_commit_trailers(tmp_path: Path, mode: str) -> None:
    """Git parses attribution on every commit path without changing its subject."""
    from markdown_vault_mcp import __version__
    from markdown_vault_mcp.git import GitWriteStrategy
    from tests.fixtures.git import _run_git as git

    git(tmp_path, "init", "-q")

    def describe(_root: str, subject: str, _paths: object) -> str:
        return f"[AI summary] Update note\n\nMechanical-Subject: {subject}"

    strategy = GitWriteStrategy(
        enable_pull=False,
        enable_push=False,
        git_lfs=False,
        subject_writer=describe if mode == "ai" else None,
    )
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    first.write_text("one", encoding="utf-8")
    try:
        if mode in {"single", "ai"}:
            strategy.on_write_batch([(first, "write", None, None)], "write")
            subject = "[AI summary] Update note" if mode == "ai" else "write: a.md"
            tool = "write"
        else:
            second.write_text("two", encoding="utf-8")
            tool = "convert" if mode == "batch" else "write_group"
            strategy.on_write_batch(
                [(first, "write", None, None), (second, "write", None, None)],
                tool,
                group_subject="Group description" if mode == "group" else None,
            )
            subject = "convert: 2 files" if mode == "batch" else "Group description"
        message = git(tmp_path, "log", "-1", "--format=%B")
        parsed = subprocess.run(
            ["git", "interpret-trailers", "--parse"],
            input=message,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert git(tmp_path, "log", "-1", "--format=%s").strip() == subject
        assert f"Vault-Tool: {tool}\n" in parsed
        assert f"Vault-Server: markdown-vault-mcp/{__version__}\n" in parsed
        if mode == "ai":
            assert "Mechanical-Subject: write: a.md\n" in parsed
    finally:
        strategy.close()
