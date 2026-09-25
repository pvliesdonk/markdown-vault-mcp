"""A revision read tells the caller's mistake apart from a git failure (#1608)."""

from __future__ import annotations

import hashlib
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import DocumentUnreadableError, InvalidRequestError
from tests.test_git_revision import _commit, _git, _init, _read_at, _write

if TYPE_CHECKING:
    from pathlib import Path


def _fail(monkeypatch: pytest.MonkeyPatch, subcommand: str) -> None:
    """Make one git subcommand exit 128, as a broken repository would."""
    real_run = subprocess.run

    def run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        if subcommand in cmd:
            empty, err = (
                ("", "fatal: boom") if kwargs.get("text") else (b"", b"fatal: boom")
            )
            return subprocess.CompletedProcess(cmd, 128, empty, err)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


@pytest.mark.parametrize(
    "subcommand", ["merge-base", "ls-files", "ls-tree", "cat-file"]
)
def test_git_failure_is_a_fault_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, subcommand: str
) -> None:
    _init(tmp_path)
    _write(tmp_path, "note.md", "# v1\n")
    first = _commit(tmp_path, "add")
    _fail(monkeypatch, subcommand)
    with pytest.raises(ValueError, match="boom") as exc:
        _read_at(tmp_path, "note.md", first)
    assert not isinstance(exc.value, InvalidRequestError)


def test_prefix_a_commit_shares_with_a_blob(tmp_path: Path) -> None:
    _init(tmp_path)
    _write(tmp_path, "note.md", "# v1\n")
    first = _commit(tmp_path, "add")
    n = 0
    while True:
        body = f"collide {n}\n".encode()
        header = f"blob {len(body)}\0".encode()
        # git's own object id, not a security use.
        blob_id = hashlib.sha1(header + body, usedforsecurity=False).hexdigest()
        if blob_id.startswith(first[:4]):
            break
        n += 1
    subprocess.run(
        ["git", "-C", str(tmp_path), "hash-object", "-w", "--stdin"],
        input=body,
        capture_output=True,
        check=True,
    )
    _write(tmp_path, "note.md", "# v2\n")
    _commit(tmp_path, "edit")
    assert _read_at(tmp_path, "note.md", first[:4]).content == "# v1\n"


def _repo_with_history(tmp_path: Path) -> str:
    _init(tmp_path)
    _write(tmp_path, "note.md", "# v1\n")
    return _commit(tmp_path, "add")


def test_note_never_at_that_revision_is_a_refusal(tmp_path: Path) -> None:
    first = _repo_with_history(tmp_path)
    with pytest.raises(
        InvalidRequestError, match=f"not present at revision '{first[:7]}'"
    ):
        _read_at(tmp_path, "ghost.md", first[:7])


def test_submodule_at_that_revision_is_a_refusal(tmp_path: Path) -> None:
    """A gitlink replaced by a note is a type change, so the walk reaches it."""
    _init(tmp_path)
    base = _commit_empty(tmp_path)
    _git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{base},note.md")
    _git(tmp_path, "commit", "-qm", "a submodule entry")
    first = _git(tmp_path, "rev-parse", "HEAD").strip()
    _git(tmp_path, "rm", "-q", "--cached", "note.md")
    _write(tmp_path, "note.md", "# now a note\n")
    _commit(tmp_path, "replace it with a note")
    with pytest.raises(InvalidRequestError, match="git records a commit"):
        _read_at(tmp_path, "note.md", first)


def _commit_empty(repo: Path) -> str:
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    return _git(repo, "rev-parse", "HEAD").strip()


def test_note_linked_outside_the_repository_is_a_refusal(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    first = _repo_with_history(repo)
    outside = tmp_path / "outside.md"
    outside.write_text("# elsewhere\n")
    (repo / "link.md").symlink_to(outside)
    with pytest.raises(InvalidRequestError, match="outside the git repository"):
        _read_at(repo, "link.md", first)


def test_revision_on_an_empty_branch_is_a_refusal(tmp_path: Path) -> None:
    first = _repo_with_history(tmp_path)
    _git(tmp_path, "checkout", "-q", "--orphan", "fresh")
    with pytest.raises(InvalidRequestError, match="no commits yet"):
        _read_at(tmp_path, "note.md", first)


def test_unreadable_note_is_named_by_its_current_path(tmp_path: Path) -> None:
    _init(tmp_path)
    (tmp_path / "old.md").write_bytes(b"\xff\xfe not text\n")
    first = _commit(tmp_path, "add")
    _git(tmp_path, "mv", "old.md", "new.md")
    _commit(tmp_path, "rename")
    with pytest.raises(DocumentUnreadableError) as exc:
        _read_at(tmp_path, "new.md", first)
    assert exc.value.path == "new.md"
    assert "old.md" in str(exc.value)


def test_unknown_revision_points_at_get_history(tmp_path: Path) -> None:
    _repo_with_history(tmp_path)
    with pytest.raises(InvalidRequestError, match="get_history"):
        _read_at(tmp_path, "note.md", "0" * 40)
