"""Query rendering and literal pathspecs belong to the shared runner."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from markdown_vault_mcp.git._run import run_git, run_git_capturing
from markdown_vault_mcp.git.query import committed_revision, get_file_diff
from tests.fixtures.git import _run_git


@pytest.mark.parametrize("runner", [run_git, run_git_capturing])
@pytest.mark.parametrize("verb", ["diff", "show"])
@pytest.mark.parametrize(
    "prefix", [(), ("-c", "user.name=test"), ("-c", "core.quotePath=true")]
)
def test_rendering_config_is_central(
    runner: Any, verb: str, prefix: tuple[str, ...]
) -> None:
    with patch("markdown_vault_mcp.git.subprocess.run") as spawn:
        runner(Path("/repo"), *prefix, verb, "HEAD")
        assert spawn.call_args.args[0] == [
            "git",
            "-C",
            "/repo",
            *prefix,
            "-c",
            "core.quotePath=false",
            verb,
            "HEAD",
        ]


def test_breadcrumb_uses_literal_name(git_repo: Path) -> None:
    note = git_repo / "star*note.md"
    sibling = git_repo / "starXnote.md"
    note.write_text("own\n")
    sibling.write_text("sibling\n")
    _run_git(git_repo, "add", "--", note.name, sibling.name)
    _run_git(git_repo, "commit", "-qm", "seed")
    sha = _run_git(git_repo, "rev-parse", "HEAD").strip()
    sibling.write_text("changed\n")
    assert committed_revision(git_repo, note, token=None, username="") == sha


def test_binary_stat_renders_non_ascii_path(git_repo: Path) -> None:
    binary = git_repo / "ö.bin"
    binary.write_bytes(b"\x00before")
    _run_git(git_repo, "add", "--", binary.name)
    _run_git(git_repo, "commit", "-qm", "before")
    base = _run_git(git_repo, "rev-parse", "HEAD").strip()
    binary.write_bytes(b"\x00after")
    _run_git(git_repo, "add", "--", binary.name)
    _run_git(git_repo, "commit", "-qm", "after")
    stat = get_file_diff(
        git_repo, binary, base, False, token=None, username="", summarize_binary=True
    )
    assert isinstance(stat, str) and "ö.bin" in stat and "Bin" in stat
