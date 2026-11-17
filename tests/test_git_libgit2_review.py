"""Regression coverage for native Git parity."""

from __future__ import annotations

import pygit2
import pytest

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git.libgit2 import Libgit2Store
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from tests.fixtures.libgit2 import _git
from tests.fixtures.libgit2 import isolated_repo as isolated_repo


def test_hook_installed_after_open(isolated_repo):
    repo = isolated_repo
    store = Libgit2Store(repo)
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    note = repo / "note.md"
    note.write_text("new\n")
    with pytest.raises(ConfigurationError):
        store(note, "", "write")


def test_locked_index_does_not_advance_head(isolated_repo):
    repo = isolated_repo
    store = Libgit2Store(repo)
    before = _git(repo, "rev-parse", "HEAD").strip()
    (repo / ".git/index.lock").write_text("fixture lock")
    note = repo / "note.md"
    note.write_text("new\n")
    with pytest.raises((OSError, pygit2.GitError)):
        store(note, "", "write")
    assert _git(repo, "rev-parse", "HEAD").strip() == before


def test_pure_rename_per_commit_patch(isolated_repo):
    repo = isolated_repo
    store = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    old, new = repo / "old.md", repo / "new.md"
    old.write_text("hello\n")
    store(old, "", "write")
    before = _git(repo, "rev-parse", "HEAD").strip()
    old.rename(new)
    store(new, "", "rename", old_path=old)
    got = store.get_file_diff(repo, new, before, True)
    expected = cli.get_file_diff(repo, new, before, True)
    assert got[0].diff == expected[0].diff


def test_branch_range_excludes_all_ancestors_of_ref(isolated_repo):
    repo = isolated_repo
    store = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    note = repo / "note.md"
    note.write_text("base\n")
    store(note, "", "write")
    branch = _git(repo, "branch", "--show-current").strip()
    _git(repo, "checkout", "-qb", "side")
    note.write_text("side\n")
    _git(repo, "add", "note.md")
    _git(repo, "commit", "-qm", "side")
    side = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", branch)
    (repo / "other.txt").write_text("other\n")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-qm", "main")
    _git(repo, "merge", "--no-ff", "-qm", "merge", "side")
    got = store.get_file_diff(repo, note, side, True)
    expected = cli.get_file_diff(repo, note, side, True)
    assert [row.sha for row in got] == [row.sha for row in expected]


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


def test_filemode_false_preserves_mode(isolated_repo):
    repo = isolated_repo
    note = repo / "note.md"
    note.write_text("one\n")
    _git(repo, "add", "note.md")
    _git(repo, "commit", "-qm", "note")
    _git(repo, "config", "core.filemode", "false")
    note.chmod(0o755)
    note.write_text("two\n")
    store = Libgit2Store(repo)
    store(note, "", "edit")
    assert _git(repo, "ls-tree", "HEAD", "note.md").startswith("100644 ")


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


def test_history_git_date_expression(isolated_repo):
    repo = isolated_repo
    note = repo / "note.md"
    store = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    note.write_text("today\n")
    store(note, "", "write")
    expected = cli.get_file_history(repo, note, "yesterday", 20)
    assert expected
    assert store.get_file_history(repo, note, "yesterday", 20) == expected


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


@pytest.mark.parametrize(
    "expression",
    ["yesterday", "2 weeks 3 days ago", "1.week.ago", "last monday", "noon yesterday"],
)
def test_common_git_date_expressions(isolated_repo, expression):
    store = Libgit2Store(isolated_repo)
    assert store.get_file_history(isolated_repo, None, expression, 20)


def test_date_overflow_is_request_error(isolated_repo):
    from markdown_vault_mcp.exceptions import InvalidRequestError

    with pytest.raises(InvalidRequestError):
        Libgit2Store(isolated_repo).get_file_history(
            isolated_repo, None, "99999999999999999999 years ago", 20
        )


def test_valueless_promisor_is_refused(isolated_repo):
    with (isolated_repo / ".git/config").open("a") as config:
        config.write('\n[remote "fixture"]\n    promisor\n')
    with pytest.raises(ConfigurationError, match="partial clones"):
        Libgit2Store(isolated_repo)


def test_bounded_history_and_revision_do_not_walk_whole_history(
    isolated_repo, monkeypatch
):
    import subprocess

    import markdown_vault_mcp.git.libgit2_history as history

    stream = []
    for i in range(80):
        stream.append(
            f"commit refs/heads/long\ncommitter Fixture <fixture@example.com> {1700000000 + i} +0000\ndata 1\nx\nM 100644 inline note.md\ndata {len(str(i))}\n{i}\n"
        )
    subprocess.run(
        ["git", "-C", str(isolated_repo), "fast-import", "--quiet"],
        input="".join(stream),
        text=True,
        check=True,
        capture_output=True,
    )
    _git(isolated_repo, "checkout", "-q", "-f", "long")
    store = Libgit2Store(isolated_repo)
    calls = 0
    original = history._diff

    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(history, "_diff", counted)
    assert len(store.get_file_history(isolated_repo, None, None, 3)) == 3
    assert calls == 3
    calls = 0
    original_entry = history._entry

    def counted_entry(*args):
        nonlocal calls
        calls += 1
        return original_entry(*args)

    monkeypatch.setattr(history, "_entry", counted_entry)
    assert store.committed_revision(isolated_repo, isolated_repo / "note.md")
    assert calls < 5


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
