"""Shared real-repository scenarios for versioned stores (#1307)."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.types import RevisionQuery
from tests.fixtures.git import GitRepoPair, _git_clone, _git_commit, _git_head, _run_git

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def store() -> Iterator[GitWriteStrategy]:
    """Create the CLI store; add backends here when they exist."""
    strategy = GitWriteStrategy(enable_push=False, git_lfs=False)
    yield strategy
    strategy.close()


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
    git_repo: Path, store: GitWriteStrategy, name: str
) -> None:
    """Writes, reads and renames retain exact names and never commit other work."""
    if os.name == "nt" and (name.endswith(" ") or any(c in name for c in '*?\r\n"\t')):
        pytest.skip("Windows cannot create these filename characters")
    repo = git_repo
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
    _run_git(repo, "commit", "-qm", "seed unrelated")
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
    store: GitWriteStrategy,
    paths: tuple[Path, Path],
    first: str,
    body: str,
) -> None:
    note, renamed = paths
    history = store.get_file_history(repo, renamed, None, 10)
    assert len(history) == 3
    assert history[-1].sha == first
    combined = store.get_file_diff(repo, renamed, first, False)
    per_commit = store.get_file_diff(repo, renamed, first, True)
    assert isinstance(combined, str) and "+updated" in combined
    assert "sibling-only" not in combined and "other-only" not in combined
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


def test_sync_ahead_keeps_local_commit(
    git_repo_pair: GitRepoPair, store: GitWriteStrategy
) -> None:
    """An ahead clone is preserved when there is nothing to pull."""
    local = git_repo_pair.local_path
    original_remote = _git_head(local)
    published = _git_commit(local, "published.md", "published\n", "published")
    _run_git(local, "push")
    local_head = _git_commit(local, "local.md", "local\n", "local")
    # A stale tracking ref lets the test observe the fetch even when ahead.
    _run_git(local, "update-ref", "refs/remotes/origin/main", original_remote)
    assert store.sync_once(local) is False
    assert _git_head(local) == local_head
    assert _run_git(local, "rev-parse", "origin/main").strip() == published
    assert _run_git(local, "rev-list", "--count", "origin/main..HEAD").strip() == "1"
    assert (local / "local.md").read_bytes() == b"local\n"


def _assert_dirty_sync(
    local: Path,
    store: GitWriteStrategy,
    mode: str,
    caplog: pytest.LogCaptureFixture,
    outcome: tuple[str, str, bool],
) -> None:
    before, remote, advanced = outcome
    assert not advanced and _git_head(local) == before
    assert "git_worktree_dirty" in caplog.text
    assert _run_git(local, "rev-parse", "origin/main").strip() == remote
    assert _run_git(local, "rev-list", "--count", "HEAD..origin/main").strip() == "1"
    assert (local / "README.md").read_bytes() == b"uncommitted local\n"
    if mode == "diverged_dirty":
        assert (local / "local.md").read_bytes() == b"committed local\n"
    _run_git(local, "stash", "push", "-qm", "preserve dirty edit")
    assert store.sync_once(local) is True
    _run_git(local, "merge-base", "--is-ancestor", remote, "HEAD")
    assert (local / "README.md").read_bytes() == b"remote\n"
    assert _run_git(local, "show", "stash@{0}:README.md") == "uncommitted local\n"


@pytest.mark.parametrize(
    "mode", ["clean", "conflict", "union", "dirty", "diverged_dirty"]
)
def test_two_clone_divergence(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    store: GitWriteStrategy,
    mode: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The remote transition preserves both sides, or refuses a dirty tree."""
    local = git_repo_pair.local_path
    if mode == "union":
        _git_commit(local, ".gitattributes", "README.md merge=union\n", "attributes")
        _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    if mode == "clean":
        _git_commit(local, "local.md", "local\n", "local")
        _git_commit(other, "remote.md", "remote\n", "remote")
    elif mode in {"dirty", "diverged_dirty"}:
        if mode == "diverged_dirty":
            _git_commit(local, "local.md", "committed local\n", "local")
        (local / "README.md").write_bytes(b"uncommitted local\n")
        _git_commit(other, "README.md", "remote\n", "remote")
    else:
        _git_commit(local, "README.md", "local\n", "local")
        _git_commit(other, "README.md", "remote\n", "remote")
    before = _git_head(local)
    remote = _git_head(other)
    _run_git(other, "push")
    with caplog.at_level("INFO", logger="markdown_vault_mcp.git"):
        advanced = store.sync_once(local)
    if mode in {"dirty", "diverged_dirty"}:
        _assert_dirty_sync(local, store, mode, caplog, (before, remote, advanced))
    else:
        assert advanced
        _run_git(local, "merge-base", "--is-ancestor", remote, "HEAD")
        if mode == "clean":
            assert "git_rebase_succeeded" in caplog.text
            assert (local / "local.md").read_text() == "local\n"
            assert (local / "remote.md").read_text() == "remote\n"
        elif mode == "union":
            text = (local / "README.md").read_text()
            assert "local\n" in text and "remote\n" in text
            assert not list(local.glob("*.conflict-mcp-*.md"))
        else:
            siblings = list(local.glob("README.conflict-mcp-*.md"))
            assert len(siblings) == 1
            assert "git_conflict_resolved" in caplog.text
            import frontmatter

            original = frontmatter.loads((local / "README.md").read_text())
            sibling = frontmatter.loads(siblings[0].read_text())
            assert original.content == "remote"
            assert sibling.content == "local"
            assert (
                original.metadata["x-markdown-vault-mcp-conflict-with"]
                == siblings[0].name
            )
            assert sibling.metadata["x-markdown-vault-mcp-conflict-with"] == "README.md"
            assert "x-markdown-vault-mcp-conflict-date" in original.metadata
            assert "x-markdown-vault-mcp-conflict-date" in sibling.metadata
    assert not (local / ".git/rebase-merge").exists()
    assert not (local / ".git/rebase-apply").exists()


@pytest.mark.parametrize(
    ("name", "sibling"),
    [
        ("star*.md", "starX.md"),
        ("question?.md", "questionX.md"),
        ("bracket[1].md", "bracket1.md"),
    ],
)
def test_glob_name_does_not_commit_matching_sibling(
    git_repo: Path, store: GitWriteStrategy, name: str, sibling: str
) -> None:
    """An unstaged matching sibling is not selected as a pathspec pattern."""
    if os.name == "nt" and any(c in name for c in "*?"):
        pytest.skip("Windows cannot create these filename characters")
    _git_commit(git_repo, sibling, "original\n", "seed sibling")
    (git_repo / sibling).write_bytes(b"pending\n")
    note = git_repo / name
    note.write_bytes(b"note\n")
    store(note, "note\n", "write")
    assert _paths(
        git_repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD"
    ) == {name}
    assert _run_git(git_repo, "show", f"HEAD:{sibling}") == "original\n"
    assert (git_repo / sibling).read_text() == "pending\n"
    assert _paths(git_repo, "diff", "--cached", "--name-only", "-z") == set()
    assert _paths(git_repo, "diff", "--name-only", "-z") == {sibling}
