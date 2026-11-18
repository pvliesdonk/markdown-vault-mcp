"""Real Git coverage for descriptive server commit trailers."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["single", "batch", "group", "ai"])
def test_server_commit_trailers(tmp_path: Path, mode: str, isolated_git: None) -> None:
    """Git parses attribution on every commit path without changing its subject."""
    from markdown_vault_mcp import __version__
    from markdown_vault_mcp.git import GitWriteStrategy
    from tests.fixtures.git import _run_git as git

    del isolated_git
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


def test_unscoped_write_uses_operation(git_repo: Path) -> None:
    """A callback outside a tool scope records its operation."""
    from markdown_vault_mcp.git import GitWriteStrategy
    from tests.fixtures.git import _run_git as git

    note = git_repo / "note.md"
    note.write_text("content", encoding="utf-8")
    strategy = GitWriteStrategy(enable_pull=False, enable_push=False, git_lfs=False)
    try:
        strategy(note, "content", "edit")
        assert "Vault-Tool: edit" in git(git_repo, "log", "-1", "--format=%(trailers)")
    finally:
        strategy.close()


def test_blank_line_filename_has_parseable_trailers(git_repo: Path) -> None:
    """A filename with a blank line still produces a Git trailer block."""
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.fixtures.git import _run_git as git

    strategy = GitWriteStrategy(enable_pull=False, enable_push=False, git_lfs=False)
    vault = Vault(
        source_dir=git_repo, on_write=strategy, settings=VaultSettings(read_only=False)
    )
    try:
        vault.writer.write("part\n\nsecond.md", "content")
        vault._write_callback.drain()
        message = git(git_repo, "log", "-1", "--format=%B")
        assert message.startswith("write: part\n\nsecond.md\n")
        trailers = git(git_repo, "log", "-1", "--format=%(trailers)")
        assert "Vault-Tool: write" in trailers
        assert "Vault-Server: markdown-vault-mcp/" in trailers
    finally:
        vault.close()
        strategy.close()


def test_conflict_save_commit_has_trailers(git_repo: Path) -> None:
    """Conflict-save commits identify the server operation."""
    from markdown_vault_mcp.git.conflict import write_conflict_files
    from tests.fixtures.git import _run_git as git

    (git_repo / "note.md").write_text("upstream", encoding="utf-8")
    result = write_conflict_files(
        git_repo,
        [("note.md", "local")],
        None,
        commit_name="Server",
        commit_email="server@example.com",
        token=None,
    )
    assert result
    assert (
        git(git_repo, "log", "-1", "--format=%s").strip()
        == "conflict: saved 1 MCP version(s) for manual reconciliation"
    )
    trailers = git(git_repo, "log", "-1", "--format=%(trailers)")
    assert "Vault-Tool: sync_conflict" in trailers
    assert "Vault-Server: markdown-vault-mcp/" in trailers
