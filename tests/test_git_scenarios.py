"""Shared real-repository scenarios for versioned stores (#1307)."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.types import RevisionQuery
from tests.fixtures.git import GitRepoPair, _run_git, isolated_git  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


pytestmark = pytest.mark.usefixtures("isolated_git")


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


def _head(repo: Path) -> str:
    return _run_git(repo, "rev-parse", "HEAD").strip()


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
    unrelated = repo / "unrelated.md"
    unrelated.write_text("original\n")
    _run_git(repo, "add", "unrelated.md")
    _run_git(repo, "commit", "-qm", "seed unrelated")
    unrelated.write_text("staged change\n")
    _run_git(repo, "add", "unrelated.md")

    note = repo / name
    body = "".join(f"line {i}\n" for i in range(30))
    note.write_text(body)
    store(note, body, "write")
    first = _head(repo)
    _bounded(repo, {name})
    note.write_text(body + "updated\n")
    store(note, body + "updated\n", "edit")
    _bounded(repo, {name})
    renamed = repo / name.replace(".md", "-renamed.md")
    note.rename(renamed)
    store(renamed, body + "updated\n", "rename", old_path=note)
    _bounded(repo, {name, renamed.name})

    history = store.get_file_history(repo, renamed, None, 10)
    assert len(history) == 3
    assert history[-1].sha == first
    combined = store.get_file_diff(repo, renamed, first, False)
    per_commit = store.get_file_diff(repo, renamed, first, True)
    assert isinstance(combined, str) and "+updated" in combined
    assert isinstance(per_commit, list) and len(per_commit) == 2
    assert any("+updated" in entry.diff for entry in per_commit)
    revision = store.get_file_at_ref(
        RevisionQuery(repo_path=repo, path=renamed, ref=first, max_bytes=0)
    )
    assert revision.historical_path == name
    assert revision.content == body

    renamed.unlink()
    store(renamed, "", "delete")
    _bounded(repo, {renamed.name})
    assert not renamed.exists()


def _clone(pair: GitRepoPair, tmp_path: Path) -> Path:
    other = tmp_path / "other"
    _run_git(tmp_path, "clone", "-q", str(pair.remote_path), str(other))
    _run_git(other, "config", "user.name", "Test")
    _run_git(other, "config", "user.email", "test@example.com")
    return other


def _commit(repo: Path, name: str, content: str, message: str) -> str:
    (repo / name).write_text(content)
    _run_git(repo, "add", "--", name)
    _run_git(repo, "commit", "-qm", message)
    return _head(repo)


def test_sync_ahead_then_behind(
    git_repo_pair: GitRepoPair, tmp_path: Path, store: GitWriteStrategy
) -> None:
    """An ahead clone is preserved; after publishing it can fast-forward."""
    local = git_repo_pair.local_path
    other = _clone(git_repo_pair, tmp_path)
    local_head = _commit(local, "local.md", "local\n", "local")
    assert store.sync_once(local) is False
    assert _head(local) == local_head
    _run_git(local, "push")
    _run_git(other, "pull", "--ff-only")
    remote_head = _commit(other, "remote.md", "remote\n", "remote")
    _run_git(other, "push")
    assert store.sync_once(local) is True
    assert _head(local) == remote_head
    assert (local / "local.md").read_text() == "local\n"
    assert (local / "remote.md").read_text() == "remote\n"


def test_rename_threshold_agrees_across_queries(
    git_repo: Path, store: GitWriteStrategy
) -> None:
    """A rename below git's default threshold remains one note in every query."""
    repo = git_repo
    old, new = repo / "old.md", repo / "new.md"
    original = "# Title\nOriginal line\n"
    old.write_text(original)
    store(old, original, "write")
    first = _head(repo)
    old.rename(new)
    new.write_text("# Title\nModified line\n")
    store(new, new.read_text(), "rename", old_path=old)
    assert [entry.sha for entry in store.get_file_history(repo, new, None, 10)][
        -1
    ] == first
    combined = store.get_file_diff(repo, new, first, False)
    entries = store.get_file_diff(repo, new, first, True)
    assert (
        isinstance(combined, str)
        and "-Original line" in combined
        and "+Modified line" in combined
    )
    assert isinstance(entries, list) and len(entries) == 1
    assert "-Original line" in entries[0].diff and "+Modified line" in entries[0].diff
    revision = store.get_file_at_ref(
        RevisionQuery(repo_path=repo, path=new, ref=first, max_bytes=0)
    )
    assert revision.historical_path == "old.md"
    assert revision.content == original


@pytest.mark.parametrize("mode", ["clean", "conflict", "union", "dirty"])
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
        _commit(local, ".gitattributes", "README.md merge=union\n", "attributes")
        _run_git(local, "push")
    other = _clone(git_repo_pair, tmp_path)
    if mode == "clean":
        _commit(local, "local.md", "local\n", "local")
        _commit(other, "remote.md", "remote\n", "remote")
    elif mode == "dirty":
        (local / "README.md").write_text("uncommitted local\n")
        _commit(other, "README.md", "remote\n", "remote")
    else:
        _commit(local, "README.md", "local\n", "local")
        _commit(other, "README.md", "remote\n", "remote")
    before = _head(local)
    remote = _head(other)
    _run_git(other, "push")
    with caplog.at_level("INFO", logger="markdown_vault_mcp.git"):
        advanced = store.sync_once(local)
    if mode == "dirty":
        assert not advanced and _head(local) == before
        assert (local / "README.md").read_text() == "uncommitted local\n"
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
            assert (local / "README.md").read_text().endswith("remote")
            siblings = list(local.glob("README.conflict-mcp-*.md"))
            assert len(siblings) == 1 and siblings[0].read_text().endswith("local")
            assert "git_conflict_resolved" in caplog.text
            import frontmatter

            original = frontmatter.loads((local / "README.md").read_text())
            sibling = frontmatter.loads(siblings[0].read_text())
            assert original.metadata["conflict_with"] == siblings[0].name
            assert sibling.metadata["conflict_with"] == "README.md"
            assert "conflict_date" in original.metadata
            assert "conflict_date" in sibling.metadata
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
    _commit(git_repo, sibling, "original\n", "seed sibling")
    (git_repo / sibling).write_text("pending\n")
    note = git_repo / name
    note.write_text("note\n")
    store(note, "note\n", "write")
    assert _paths(
        git_repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD"
    ) == {name}
    assert _run_git(git_repo, "show", f"HEAD:{sibling}") == "original\n"
    assert (git_repo / sibling).read_text() == "pending\n"
    assert _paths(git_repo, "diff", "--cached", "--name-only", "-z") == set()
    assert _paths(git_repo, "diff", "--name-only", "-z") == {sibling}
