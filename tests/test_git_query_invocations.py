"""Query rendering and literal pathspecs belong to the shared runner."""

from pathlib import Path
from unittest.mock import patch

import pytest

from markdown_vault_mcp.git._run import run_git, run_git_capturing
from markdown_vault_mcp.git.query import (
    committed_revision,
    get_file_at_ref,
    get_file_diff,
)
from markdown_vault_mcp.git.types import RevisionQuery
from tests.fixtures.git import _run_git


@pytest.mark.parametrize("runner", [run_git, run_git_capturing])
def test_runner_rejects_embedded_paths(runner: object) -> None:
    with patch("markdown_vault_mcp.git.subprocess.run") as spawn:
        with pytest.raises(ValueError):
            runner(Path("/repo"), "log", "--", "note.md")  # type: ignore[operator]
        spawn.assert_not_called()


@pytest.mark.parametrize("verb", ["diff", "show"])
def test_rendering_config_is_central(verb: str) -> None:
    with patch("markdown_vault_mcp.git.subprocess.run") as spawn:
        run_git(Path("/repo"), verb, "HEAD")
        assert spawn.call_args.args[0] == [
            "git",
            "-C",
            "/repo",
            "-c",
            "core.quotePath=false",
            verb,
            "HEAD",
        ]


def test_revision_uses_literal_name(git_repo: Path) -> None:
    note = git_repo / "star*note.md"
    sibling = git_repo / "starXnote.md"
    note.write_text("own\n")
    sibling.write_text("sibling\n")
    _run_git(git_repo, "add", "--", note.name, sibling.name)
    _run_git(git_repo, "commit", "-qm", "seed")
    sha = _run_git(git_repo, "rev-parse", "HEAD").strip()
    sibling.write_text("changed\n")
    assert committed_revision(git_repo, note, token=None, username="") == sha
    result = get_file_at_ref(
        git_repo,
        RevisionQuery(repo_path=git_repo, path=note, ref=sha, max_bytes=0),
        token=None,
        username="",
    )
    assert result.content == "own\n"


def test_diff_renders_non_ascii_paths(git_repo: Path) -> None:
    text = git_repo / "ö.md"
    binary = git_repo / "ö.bin"
    text.write_text("before\n")
    binary.write_bytes(b"\x00before")
    _run_git(git_repo, "add", "--", text.name, binary.name)
    _run_git(git_repo, "commit", "-qm", "before")
    base = _run_git(git_repo, "rev-parse", "HEAD").strip()
    text.write_text("after\n")
    binary.write_bytes(b"\x00after")
    _run_git(git_repo, "add", "--", text.name, binary.name)
    _run_git(git_repo, "commit", "-qm", "after")
    patch = get_file_diff(git_repo, text, base, False, token=None, username="")
    stat = get_file_diff(
        git_repo, binary, base, False, token=None, username="", summarize_binary=True
    )
    assert isinstance(patch, str) and "ö.md" in patch and "+after" in patch
    assert isinstance(stat, str) and "ö.bin" in stat and "Bin" in stat
