"""Regressions for native Git lineage and capability boundaries."""

import pytest

from markdown_vault_mcp.exceptions import ConfigurationError, InvalidRequestError
from markdown_vault_mcp.git.libgit2 import Libgit2Store
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.types import RevisionQuery
from tests.fixtures.libgit2 import _git
from tests.fixtures.libgit2 import isolated_repo as isolated_repo


def commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def test_symlink_replaced_by_regular_file_with_filemode_false(isolated_repo):
    repo = isolated_repo
    link = repo / "note.md"
    link.symlink_to("target")
    commit(repo, "symlink")
    _git(repo, "config", "core.filemode", "false")
    store = Libgit2Store(repo)
    link.unlink()
    link.write_text("regular\n")
    store(link, "", "write")
    assert _git(repo, "ls-tree", "HEAD", "note.md").startswith("100644 ")
    assert _git(repo, "show", "HEAD:note.md") == "regular\n"


def test_core_symlinks_false_refused_at_open_and_write(isolated_repo):
    repo = isolated_repo
    store = Libgit2Store(repo)
    _git(repo, "config", "core.symlinks", "false")
    with pytest.raises(ConfigurationError, match=r"core\.symlinks"):
        Libgit2Store(repo)
    note = repo / "new.md"
    note.write_text("new\n")
    with pytest.raises(ConfigurationError, match=r"core\.symlinks"):
        store(note, "", "write")


@pytest.mark.parametrize("primary", [False, True])
def test_single_batch_subject_matches_cli(isolated_repo, primary):
    repo = isolated_repo
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    note = repo / "note.md"
    note.write_text("native\n")
    kwargs = {"primary_paths": frozenset({note})} if primary else {}
    native.on_write_batch([(note, "write", None, None)], "write", **kwargs)
    subject = _git(repo, "log", "-1", "--format=%s")
    note.write_text("cli\n")
    cli.on_write_batch([(note, "write", None, None)], "write", **kwargs)
    assert subject == _git(repo, "log", "-1", "--format=%s")


def test_exact_rename_with_unrelated_deletion(isolated_repo):
    repo = isolated_repo
    old, new, unrelated = repo / "old.md", repo / "new.md", repo / "unrelated2.md"
    old.write_text("original note\n")
    unrelated.write_text("distinct\n")
    before = commit(repo, "before")
    _git(repo, "mv", "old.md", "new.md")
    _git(repo, "rm", "-q", "unrelated2.md")
    commit(repo, "rename")
    store = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    assert store.get_file_history(repo, new, None, 20) == cli.get_file_history(
        repo, new, None, 20
    )
    assert store.get_file_at_ref(
        RevisionQuery(repo, new, before, 0)
    ) == cli.get_file_at_ref(RevisionQuery(repo, new, before, 0))
    for per_commit in (False, True):
        assert store.get_file_diff(repo, new, before, per_commit) == cli.get_file_diff(
            repo, new, before, per_commit
        )


def test_birth_endpoint_and_range_do_not_inspect_excluded_commit(
    isolated_repo, monkeypatch
):
    import markdown_vault_mcp.git.libgit2_history as history

    repo = isolated_repo
    other = repo / "other.md"
    other.write_text("old\n")
    commit(repo, "old")
    _git(repo, "rm", "-q", "other.md")
    note = repo / "note.md"
    note.write_text("brand new\n")
    birth = commit(repo, "birth")
    note.write_text("updated\n")
    commit(repo, "update")
    store = Libgit2Store(repo)
    original = history._resolved

    def reject(diff, path):
        assert not any(delta.old_file.path == "other.md" for delta in diff.deltas)
        return original(diff, path)

    monkeypatch.setattr(history, "_resolved", reject)
    assert (
        store.get_file_at_ref(RevisionQuery(repo, note, birth, 0)).content
        == "brand new\n"
    )
    assert len(store.get_file_diff(repo, note, birth, True)) == 1
    monkeypatch.setattr(history, "_resolved", original)


def test_scoped_history_refuses_merge_but_limit_can_finish(isolated_repo):
    repo = isolated_repo
    branch = _git(repo, "branch", "--show-current").strip()
    _git(repo, "checkout", "-qb", "side")
    (repo / "side.md").write_text("side\n")
    commit(repo, "side")
    _git(repo, "checkout", "-q", branch)
    (repo / "main.md").write_text("main\n")
    commit(repo, "main")
    _git(repo, "merge", "--no-ff", "-qm", "merge", "side")
    (repo / "later.md").write_text("later\n")
    commit(repo, "later")
    store = Libgit2Store(repo)
    assert len(store.get_file_history(repo, None, None, 1)) == 1
    with pytest.raises(InvalidRequestError, match=r"merge.*GitWriteStrategy"):
        store.get_file_history(repo, None, None, 20)


@pytest.mark.parametrize("edited_rename", [False, True])
def test_vault_overwrite_keeps_latest_breadcrumb(isolated_repo, edited_rename):
    from markdown_vault_mcp.vault import Vault, VaultSettings

    repo = isolated_repo
    old = repo / "old.md"
    old.write_text("old content\n")
    commit(repo, "old")
    if edited_rename:
        _git(repo, "mv", "old.md", "note.md")
    else:
        _git(repo, "rm", "-q", "old.md")
    note = repo / "note.md"
    note.write_text("new content\n")
    newest = commit(repo, "new")
    store = Libgit2Store(repo)
    vault = Vault(
        source_dir=repo,
        settings=VaultSettings(read_only=False),
        git_strategy=store,
        on_write=store,
    )
    try:
        assert store.committed_revision(repo, note) == newest
        result = vault.writer.write("note.md", "replacement\n")
        assert result.previous_revision == newest
        assert vault._write_callback.drain()
    finally:
        vault.close()
        store.close()


def test_core_filemode_false_new_and_existing_regular(isolated_repo):
    repo = isolated_repo
    original = repo / "regular.md"
    original.write_text("old\n")
    original.chmod(0o755)
    commit(repo, "regular")
    _git(repo, "config", "core.filemode", "false")
    store = Libgit2Store(repo)
    original.chmod(0o644)
    original.write_text("new\n")
    store(original, "", "edit")
    assert _git(repo, "ls-tree", "HEAD", "regular.md").startswith("100755 ")
    new = repo / "executable.md"
    new.write_text("new\n")
    new.chmod(0o755)
    store(new, "", "write")
    assert _git(repo, "ls-tree", "HEAD", "executable.md").startswith("100644 ")


def test_scoped_history_skips_merge_older_than_since(isolated_repo):
    repo = isolated_repo
    branch = _git(repo, "branch", "--show-current").strip()
    _git(repo, "checkout", "-qb", "side")
    (repo / "side.md").write_text("side\n")
    commit(repo, "side")
    _git(repo, "checkout", "-q", branch)
    (repo / "main.md").write_text("main\n")
    commit(repo, "main")
    _git(repo, "merge", "--no-ff", "-qm", "merge", "side")
    assert (
        Libgit2Store(repo).get_file_history(repo, None, "2100-01-01T00:00:00+00:00", 20)
        == []
    )


def test_revision_at_head_does_not_resolve_parent(isolated_repo, monkeypatch):
    import markdown_vault_mcp.git.libgit2_history as history

    repo = isolated_repo
    (repo / "old.md").write_text("old\n")
    commit(repo, "old")
    _git(repo, "rm", "-q", "old.md")
    note = repo / "note.md"
    note.write_text("new\n")
    head = commit(repo, "birth")
    store = Libgit2Store(repo)

    def fail(*_args):
        raise AssertionError("resolved HEAD parent")

    monkeypatch.setattr(history, "_resolved", fail)
    assert store.get_file_at_ref(RevisionQuery(repo, note, head, 0)).content == "new\n"


def test_independent_merge_birth_refuses_uncertain_revision(isolated_repo, monkeypatch):
    repo = isolated_repo
    branch = _git(repo, "branch", "--show-current").strip()
    _git(repo, "checkout", "-qb", "side")
    (repo / "note.md").write_text("side\n")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2099-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2099-01-01T00:00:00+00:00")
    commit(repo, "side birth")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    _git(repo, "checkout", "-q", branch)
    (repo / "note.md").write_text("main\n")
    original = commit(repo, "main birth")
    _git(repo, "merge", "-s", "ours", "--no-ff", "-qm", "merge", "side")
    store = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    query = RevisionQuery(repo, repo / "note.md", original, 0)
    assert cli.get_file_at_ref(query).content == "main\n"
    with pytest.raises(InvalidRequestError, match=r"merge.*GitWriteStrategy"):
        store.get_file_at_ref(query)


@pytest.mark.parametrize("directory", [False, True])
def test_scoped_until_excludes_later_merge(isolated_repo, directory):
    repo = isolated_repo
    branch = _git(repo, "branch", "--show-current").strip()
    _git(repo, "checkout", "-qb", "side")
    (repo / "side.md").write_text("side\n")
    commit(repo, "side")
    _git(repo, "checkout", "-q", branch)
    (repo / "main.md").write_text("main\n")
    commit(repo, "main")
    _git(repo, "merge", "--no-ff", "-qm", "merge", "side")
    store = Libgit2Store(repo)
    assert (
        store.get_file_history(
            repo,
            repo if directory else None,
            None,
            20,
            "2000-01-01T00:00:00+00:00",
            is_dir=directory,
        )
        == []
    )


@pytest.mark.parametrize("bound", ["since", "until"])
def test_scoped_time_bounds_skip_tree_diffs(isolated_repo, monkeypatch, bound):
    import markdown_vault_mcp.git.libgit2_history as history

    store = Libgit2Store(isolated_repo)

    def fail(*_args):
        raise AssertionError("out-of-window tree diff")

    monkeypatch.setattr(history, "_diff", fail)
    dates = {"since": "2100-01-01T00:00:00+00:00", "until": "2000-01-01T00:00:00+00:00"}
    assert (
        store.get_file_history(
            isolated_repo,
            None,
            dates.get("since") if bound == "since" else None,
            20,
            dates.get("until") if bound == "until" else None,
        )
        == []
    )


def test_ignored_path_already_in_real_index_is_committed(isolated_repo):
    repo = isolated_repo
    (repo / ".gitignore").write_text("ignored.md\n")
    commit(repo, "ignore")
    note = repo / "ignored.md"
    note.write_text("staged\n")
    _git(repo, "add", "-f", "ignored.md")
    note.write_text("updated\n")
    Libgit2Store(repo)(note, "", "write")
    assert _git(repo, "show", "HEAD:ignored.md") == "updated\n"


@pytest.mark.parametrize("tracked", [False, True])
def test_local_force_push_dry_run_matches_cli_tracking(isolated_repo, tracked):
    repo = isolated_repo
    if tracked:
        branch = _git(repo, "branch", "--show-current").strip()
        _git(repo, "update-ref", f"refs/remotes/origin/{branch}", "HEAD")
    native = Libgit2Store(repo).force_push(dry_run=True)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False).force_push(
        dry_run=True
    )
    assert (native.reason, native.remote_sha_before, native.remote_sha_after) == (
        cli.reason,
        cli.remote_sha_before,
        cli.remote_sha_after,
    )


def test_noon_day_composite_is_actionably_refused(isolated_repo):
    with pytest.raises(
        InvalidRequestError, match=r"Unsupported date.*GitWriteStrategy"
    ):
        Libgit2Store(isolated_repo).get_file_history(
            isolated_repo, None, "noon yesterday", 20
        )
