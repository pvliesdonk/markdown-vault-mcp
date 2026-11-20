"""Local lifecycle scenarios from the shared real-Git contract."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git.libgit2 import Libgit2Store
from markdown_vault_mcp.git.libgit2_sync import Libgit2SyncStore
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.types import RevisionQuery
from tests.fixtures.git import _git_commit, _git_head
from tests.fixtures.libgit2 import _git as _run_git
from tests.fixtures.libgit2 import isolated_repo as isolated_repo

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(params=["cli", "libgit2", "libgit2-sync"])
def store(
    request: pytest.FixtureRequest, isolated_repo: Path
) -> Iterator[GitWriteStrategy | Libgit2Store]:
    """Run each contract against the CLI and local native store."""
    if request.param == "cli":
        value = GitWriteStrategy(
            enable_push=False, git_lfs=False, repo_path=isolated_repo
        )
    elif request.param == "libgit2-sync":
        value = Libgit2SyncStore(isolated_repo, enable_sync=True)
    else:
        value = Libgit2Store(isolated_repo)
    yield value
    value.close()


def _paths(repo: Path, *args: str) -> set[str]:
    raw = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    ).stdout
    return {os.fsdecode(path) for path in raw.split(b"\0") if path}


def _bounded(repo: Path, expected: set[str]) -> None:
    assert (
        _paths(
            repo,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--no-renames",
            "-r",
            "-z",
            "HEAD",
        )
        == expected
    )
    assert _paths(repo, "diff", "--cached", "--name-only", "-z") == {"unrelated.md"}
    assert _run_git(repo, "show", ":unrelated.md") == "staged change\n"
    assert _run_git(repo, "show", "HEAD:unrelated.md") == "original\n"


_NAMES = [
    "star*.md",
    "a*[b].md",
    ":name.md",
    "question?.md",
    "bracket[1].md",
    "close].md",
    "return\r.md",
    "newline\n.md",
    'quote".md',
    "tab\t.md",
    "café.md",
    "-dash.md",
    "trailing.md ",
]


@pytest.mark.parametrize("name", _NAMES)
def test_literal_lifecycle_keeps_unrelated_index_entry(
    isolated_repo: Path, store: GitWriteStrategy | Libgit2Store, name: str
) -> None:
    """Writes, reads and renames retain exact names and never commit other work."""
    if os.name == "nt" and (name.endswith(" ") or any(c in name for c in '*?\r\n"\t')):
        pytest.skip("Windows cannot create these filename characters")
    repo = isolated_repo
    matching = {
        "star*.md": "starX.md",
        "question?.md": "questionX.md",
        "bracket[1].md": "bracket1.md",
    }
    sibling_name = matching.get(name)
    if sibling_name is not None:
        _git_commit(repo, sibling_name, "sibling original\n", "seed sibling")
        _git_commit(
            repo,
            sibling_name.replace(".md", "-renamed.md"),
            "other original\n",
            "seed other sibling",
        )
    unrelated = repo / "unrelated.md"
    unrelated.write_bytes(b"original\n")
    _run_git(repo, "add", "unrelated.md")
    _run_git(repo, "commit", "--allow-empty", "-qm", "seed unrelated")
    unrelated.write_bytes(b"staged change\n")
    _run_git(repo, "add", "unrelated.md")

    note = repo / name
    body = "".join(f"line {i}\n" for i in range(30))
    note.write_bytes(body.encode("utf-8"))
    store(note, body, "write")
    first = _git_head(repo)
    _bounded(repo, {name})
    if sibling_name is not None:
        (repo / sibling_name).write_bytes(b"sibling-only change\n")
        _run_git(repo, "commit", "-qm", "sibling edit", "--only", "--", sibling_name)
    note.write_bytes((body + "updated\n").encode("utf-8"))
    store(note, body + "updated\n", "edit")
    _bounded(repo, {name})
    renamed = repo / name.replace(".md", "-renamed.md")
    note.rename(renamed)
    store(renamed, body + "updated\n", "rename", old_path=note)
    _bounded(repo, {name, renamed.name})
    if sibling_name is not None:
        other_name = sibling_name.replace(".md", "-renamed.md")
        (repo / other_name).write_bytes(b"other-only change\n")
        _run_git(
            repo, "commit", "-qm", "other sibling edit", "--only", "--", other_name
        )

    _assert_history(repo, store, (note, renamed), first, body)

    renamed.unlink()
    store(renamed, "", "delete")
    _bounded(repo, {renamed.name})
    assert not renamed.exists()
    deleted_revision = store.get_file_at_ref(
        RevisionQuery(repo_path=repo, path=renamed, ref=first, max_bytes=0)
    )
    assert deleted_revision.historical_path == name
    assert deleted_revision.content == body


def _assert_history(
    repo: Path,
    store: GitWriteStrategy | Libgit2Store,
    paths: tuple[Path, Path],
    first: str,
    body: str,
) -> None:
    note, renamed = paths
    history = store.get_file_history(repo, renamed, None, 10)
    assert len(history) == 3
    assert history[-1].sha == first
    if isinstance(store, Libgit2Store):
        from markdown_vault_mcp.exceptions import InvalidRequestError

        with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
            store.get_file_diff(repo, renamed, first, False)
    else:
        combined = store.get_file_diff(repo, renamed, first, False)
        assert isinstance(combined, str) and "+updated" in combined
        assert "sibling-only" not in combined and "other-only" not in combined
    if isinstance(store, Libgit2Store) and not renamed.name.isascii():
        with pytest.raises(InvalidRequestError, match=r"non-ASCII.*GitWriteStrategy"):
            store.get_file_diff(repo, renamed, first, True)
    else:
        per_commit = store.get_file_diff(repo, renamed, first, True)
        assert isinstance(per_commit, list) and len(per_commit) == 2
        assert any("+updated" in entry.diff for entry in per_commit)
        assert all(
            "sibling-only" not in entry.diff and "other-only" not in entry.diff
            for entry in per_commit
        )
    revision = store.get_file_at_ref(
        RevisionQuery(repo_path=repo, path=renamed, ref=first, max_bytes=0)
    )
    assert revision.historical_path == note.name
    assert revision.content == body
