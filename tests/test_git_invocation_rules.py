"""The git runner owns literal scope and rejects accidental index-wide writes."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from markdown_vault_mcp.git._run import run_git, run_git_capturing
from markdown_vault_mcp.git.strategy import _stage_and_commit
from tests.fixtures.git import _run_git


@pytest.mark.parametrize("runner", [run_git, run_git_capturing])
@pytest.mark.parametrize(
    "args",
    [
        ("add",),
        ("commit",),
        ("diff", "--cached"),
        ("diff", "--staged"),
        ("-c", "user.name=test", "commit"),
    ],
)
def test_index_commands_require_scope(runner: Any, args: tuple[str, ...]) -> None:
    with patch("markdown_vault_mcp.git.subprocess.run") as spawn:
        with pytest.raises(ValueError, match="requires pathspecs"):
            runner(Path("/repo"), *args)
        spawn.assert_not_called()


@pytest.mark.parametrize("runner", [run_git, run_git_capturing])
@pytest.mark.parametrize(
    "args,paths",
    [
        (("add",), []),
        (("add",), [""]),
        (("add",), ["valid", ""]),
        (("add", "--"), ["a"]),
    ],
)
def test_invalid_scope_never_spawns(
    runner: Any, args: tuple[str, ...], paths: list[str]
) -> None:
    with patch("markdown_vault_mcp.git.subprocess.run") as spawn:
        with pytest.raises(ValueError, match="nonempty"):
            runner(Path("/repo"), *args, pathspecs=paths)
        spawn.assert_not_called()


def test_scoped_and_legacy_argv() -> None:
    with patch("markdown_vault_mcp.git.subprocess.run") as spawn:
        run_git(Path("/repo"), "add", pathspecs=["b[1].md"])
        assert spawn.call_args.args[0] == [
            "git",
            "-C",
            "/repo",
            "add",
            "--",
            ":(literal)b[1].md",
        ]
        run_git_capturing(Path("/repo"), "log", "--", ":(literal)old.md")
        assert spawn.call_args.args[0] == [
            "git",
            "-C",
            "/repo",
            "log",
            "--",
            ":(literal)old.md",
        ]
        run_git(Path("/repo"), "add", "-u", whole_repo=True)
        assert spawn.call_args.args[0] == ["git", "-C", "/repo", "add", "-u"]


def test_legacy_rename_still_commits_whole_index(git_repo: Path) -> None:
    old = git_repo / "old.md"
    other = git_repo / "other.md"
    old.write_text("old\n")
    other.write_text("before\n")
    _run_git(git_repo, "add", "old.md", "other.md")
    _run_git(git_repo, "commit", "-m", "seed")
    new = git_repo / "new.md"
    old.rename(new)
    other.write_text("after\n")

    _stage_and_commit(git_repo, new, "rename", old_path=None)

    assert _run_git(git_repo, "show", "HEAD:other.md").strip() == "after"
    assert _run_git(git_repo, "show", "HEAD:new.md").strip() == "old"


@pytest.mark.parametrize("runner", [run_git, run_git_capturing])
@pytest.mark.parametrize(
    "args",
    [
        ("--no-pager", "add", "-A"),
        ("-C", ".", "commit"),
        ("-c", "x=y", "--no-pager", "diff", "--cached"),
    ],
)
def test_unsupported_global_options_are_refused(
    runner: Any, args: tuple[str, ...]
) -> None:
    with patch("markdown_vault_mcp.git.subprocess.run") as spawn:
        with pytest.raises(ValueError, match="unsupported global option"):
            runner(Path("/repo"), *args)
        spawn.assert_not_called()


def test_explicit_root_scopes_and_configured_commit() -> None:
    with patch("markdown_vault_mcp.git.subprocess.run") as spawn:
        run_git(
            Path("/repo"),
            "-c",
            "user.name=test",
            "commit",
            "-m",
            "x",
            pathspecs=[".", "/repo"],
        )
        assert spawn.call_args.args[0] == [
            "git",
            "-C",
            "/repo",
            "-c",
            "user.name=test",
            "commit",
            "-m",
            "x",
            "--",
            ":(literal).",
            ":(literal)/repo",
        ]
