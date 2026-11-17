"""Regression coverage for native Git parity."""

from __future__ import annotations

import pygit2
import pytest

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git.libgit2 import Libgit2Store
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from tests.fixtures.libgit2 import _git
from tests.fixtures.libgit2 import isolated_repo as isolated_repo


def test_diff_against_divergent_commit(isolated_repo):
    repo = isolated_repo
    note = repo / "note.md"
    store = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    note.write_text("base\n")
    store(note, "", "write")
    branch = _git(repo, "branch", "--show-current").strip()
    _git(repo, "checkout", "-qb", "side")
    note.write_text("side\n")
    _git(repo, "commit", "-qam", "side")
    side = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", branch)
    note.write_text("main\n")
    store(note, "", "write")
    expected = cli.get_file_diff(repo, note, side, False)
    assert expected
    assert store.get_file_diff(repo, note, side, False) == expected


def test_linked_worktree_common_hook_refused(isolated_repo, tmp_path):
    repo = isolated_repo
    _git(repo, "config", "--unset", "core.hooksPath")
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    other = tmp_path / "linked"
    _git(repo, "worktree", "add", "-qb", "linkedbranch", str(other))
    with pytest.raises(ConfigurationError):
        Libgit2Store(other)


def test_root_directory_history_matches_vault(isolated_repo):
    store = Libgit2Store(isolated_repo)
    assert store.get_file_history(
        isolated_repo, isolated_repo, None, 20, is_dir=True
    ) == store.get_file_history(isolated_repo, None, None, 20)


def test_symlinked_vault_path(isolated_repo, tmp_path):
    link = tmp_path / "vault"
    link.symlink_to(isolated_repo)
    store = Libgit2Store(link)
    note = link / "note.md"
    note.write_text("one\n")
    store(note, "", "write")
    assert store.get_file_history(link, note, None, 10)


def test_index_refresh_after_external_unstage(isolated_repo):
    note = isolated_repo / "note.md"
    note.write_text("one\n")
    _git(isolated_repo, "add", "note.md")
    _git(isolated_repo, "commit", "-qm", "note")
    store = Libgit2Store(isolated_repo)
    assert store.committed_revision(isolated_repo, note)
    _git(isolated_repo, "rm", "--cached", "-q", "note.md")
    assert store.committed_revision(isolated_repo, note) is None


def test_valueless_promisor_is_refused(isolated_repo):
    with (isolated_repo / ".git/config").open("a") as config:
        config.write('\n[remote "fixture"]\n    promisor\n')
    with pytest.raises(ConfigurationError, match="partial clones"):
        Libgit2Store(isolated_repo)


def test_valueless_signing_is_refused(isolated_repo):
    with (isolated_repo / ".git/config").open("a") as config:
        config.write("\n[commit]\n    gpgsign\n")
    with pytest.raises(ConfigurationError, match="signed commits"):
        Libgit2Store(isolated_repo)


def test_concurrent_index_and_head_writers_are_excluded(isolated_repo, monkeypatch):
    import subprocess

    import markdown_vault_mcp.git.libgit2 as native

    store = Libgit2Store(isolated_repo)
    note = isolated_repo / "note.md"
    note.write_text("new\n")
    late = isolated_repo / "late.md"
    late.write_text("late\n")
    before = _git(isolated_repo, "rev-parse", "HEAD").strip()
    original = native._stage_items

    def interleave(*args):
        with pytest.raises(subprocess.CalledProcessError):
            _git(isolated_repo, "add", "late.md")
        branch = _git(isolated_repo, "symbolic-ref", "HEAD").strip()
        with pytest.raises(subprocess.CalledProcessError):
            _git(isolated_repo, "update-ref", branch, before)
        return original(*args)

    monkeypatch.setattr(native, "_stage_items", interleave)
    store(note, "", "write")
    assert _git(isolated_repo, "show", ":unrelated.md") == "staged\n"
    assert _git(isolated_repo, "show", "HEAD:note.md") == "new\n"
    assert not (isolated_repo / ".git/index.lock").exists()


def test_failed_ref_publication_leaves_operation_staged(isolated_repo, monkeypatch):
    import pygit2.transaction

    store = Libgit2Store(isolated_repo)
    observed = []
    store.set_commit_observer(lambda old, new: observed.append((old, new)))
    before = _git(isolated_repo, "rev-parse", "HEAD")
    note = isolated_repo / "note.md"
    note.write_text("new\n")

    def fail(_transaction):
        raise OSError("injected ref failure")

    monkeypatch.setattr(pygit2.transaction.ReferenceTransaction, "commit", fail)
    with pytest.raises(OSError, match="injected ref failure"):
        store(note, "", "write")
    assert _git(isolated_repo, "rev-parse", "HEAD") == before
    assert _git(isolated_repo, "show", ":note.md") == "new\n"
    assert _git(isolated_repo, "show", ":unrelated.md") == "staged\n"
    assert observed == []


def test_index_publication_failure_does_not_move_head(isolated_repo, monkeypatch):
    import markdown_vault_mcp.git.libgit2 as native

    store = Libgit2Store(isolated_repo)
    before = _git(isolated_repo, "rev-parse", "HEAD")
    note = isolated_repo / "note.md"
    note.write_text("new\n")

    def fail(*_args):
        raise OSError("injected index rename failure")

    monkeypatch.setattr(native.os, "replace", fail)
    with pytest.raises(OSError, match="injected index rename failure"):
        store(note, "", "write")
    assert _git(isolated_repo, "rev-parse", "HEAD") == before
    assert _git(isolated_repo, "show", ":unrelated.md") == "staged\n"
    assert not (isolated_repo / ".git/index.lock").exists()


def test_write_requires_no_user_git_identity(isolated_repo):
    _git(isolated_repo, "config", "--unset", "user.name")
    _git(isolated_repo, "config", "--unset", "user.email")
    note = isolated_repo / "note.md"
    note.write_text("new\n")
    Libgit2Store(isolated_repo)(note, "", "write")
    assert _git(isolated_repo, "show", "HEAD:note.md") == "new\n"


def test_in_progress_merge_is_refused(isolated_repo):
    import subprocess

    note = isolated_repo / "conflict.md"
    note.write_text("base\n")
    _git(isolated_repo, "add", "conflict.md")
    _git(isolated_repo, "commit", "-qm", "base")
    branch = _git(isolated_repo, "branch", "--show-current").strip()
    _git(isolated_repo, "checkout", "-qb", "side")
    note.write_text("side\n")
    _git(isolated_repo, "commit", "-qam", "side")
    _git(isolated_repo, "checkout", "-q", branch)
    note.write_text("main\n")
    _git(isolated_repo, "commit", "-qam", "main")
    with pytest.raises(subprocess.CalledProcessError):
        _git(isolated_repo, "merge", "side")
    before = _git(isolated_repo, "rev-parse", "HEAD")
    other = isolated_repo / "other.md"
    other.write_text("new\n")
    with pytest.raises(ConfigurationError, match="GitWriteStrategy"):
        Libgit2Store(isolated_repo)(other, "", "write")
    assert _git(isolated_repo, "rev-parse", "HEAD") == before


@pytest.mark.usefixtures("isolated_repo")
def test_publication_supports_unborn_and_detached_head(tmp_path):
    new_repo = tmp_path / "unborn"
    new_repo.mkdir()
    _git(new_repo, "init", "-q")
    note = new_repo / "note.md"
    note.write_text("one\n")
    store = Libgit2Store(new_repo)
    store(note, "", "write")
    first = _git(new_repo, "rev-parse", "HEAD").strip()
    _git(new_repo, "checkout", "-q", "--detach")
    note.write_text("two\n")
    store(note, "", "write")
    assert _git(new_repo, "rev-parse", "HEAD^").strip() == first
    assert _git(new_repo, "show", "HEAD:note.md") == "two\n"


def test_held_branch_lock_leaves_index_and_head_unchanged(isolated_repo):
    store = Libgit2Store(isolated_repo)
    branch = _git(isolated_repo, "symbolic-ref", "HEAD").strip()
    lock = isolated_repo / ".git" / (branch + ".lock")
    lock.write_text("fixture")
    before = _git(isolated_repo, "rev-parse", "HEAD")
    note = isolated_repo / "note.md"
    note.write_text("new\n")
    with pytest.raises((OSError, pygit2.GitError)):
        store(note, "", "write")
    assert _git(isolated_repo, "rev-parse", "HEAD") == before
    assert _git(isolated_repo, "show", ":unrelated.md") == "staged\n"
    assert lock.read_text() == "fixture"
