"""Real Git coverage for descriptive server commit trailers."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "mode",
    [
        "single",
        "batch",
        "group",
        "ai",
        "multiline",
        "divider",
        "ai_divider",
        "verbatim",
    ],
)
def test_server_commit_trailers(tmp_path: Path, mode: str, isolated_git: None) -> None:
    """Git parses attribution on every commit path without changing its subject."""
    from markdown_vault_mcp import __version__
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.git._subject import SubjectWriter
    from tests.fixtures.git import _run_git as git
    from tests.test_summarize import FakeSummarizer

    del isolated_git
    git(tmp_path, "init", "-q")
    if mode == "verbatim":
        git(tmp_path, "config", "commit.cleanup", "verbatim")

    fake = FakeSummarizer("Update note")
    strategy = GitWriteStrategy(
        enable_pull=False,
        enable_push=False,
        git_lfs=False,
        subject_writer=SubjectWriter(fake) if mode in {"ai", "ai_divider"} else None,
    )
    filename = (
        "part\n\nsecond.md"
        if mode == "multiline"
        else "part\n---\nnote.md"
        if mode in {"divider", "ai_divider"}
        else "note.md "
        if mode == "verbatim"
        else "a.md"
    )
    first = tmp_path / filename
    second = tmp_path / "b.md"
    first.write_text("one", encoding="utf-8")
    try:
        if mode in {
            "single",
            "ai",
            "multiline",
            "divider",
            "ai_divider",
            "verbatim",
        }:
            tool = "convert" if mode == "single" else "write"
            strategy.on_write_batch([(first, "write", None, None)], tool)
            subject = (
                "[AI summary] Update note" if mode == "ai" else f"write: {first.name}"
            )
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
            ["git", "interpret-trailers", "--parse", "--no-divider"],
            input=message,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        if mode == "verbatim":
            assert message.splitlines()[0] == "write: note.md "
        elif mode == "multiline":
            assert message.startswith(f"{subject}\n")
        elif mode in {"divider", "ai_divider"}:
            assert (
                git(tmp_path, "log", "-1", "--format=%s").strip()
                == "write: part --- note.md"
            )
            if mode == "ai_divider":
                assert not fake.calls
        else:
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


@pytest.mark.parametrize("name", ["note.md", "part\n---\nnote.md"])
def test_conflict_save_commit_has_trailers(git_repo: Path, name: str) -> None:
    """Conflict-save commits identify the server operation."""
    from markdown_vault_mcp.git.conflict import write_conflict_files
    from tests.fixtures.git import _run_git as git

    (git_repo / name).write_text("upstream", encoding="utf-8")
    result = write_conflict_files(
        git_repo,
        [(name, "local")],
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
    message = git(git_repo, "log", "-1", "--format=%B")
    trailers = subprocess.run(
        ["git", "interpret-trailers", "--parse", "--no-divider"],
        input=message,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert "Vault-Tool: sync_conflict" in trailers
    assert "Vault-Server: markdown-vault-mcp/" in trailers
