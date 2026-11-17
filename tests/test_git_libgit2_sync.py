"""Real repository synchronization with the native backend."""

import os
from pathlib import Path

import pytest

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git.libgit2_sync import Libgit2SyncStore
from tests.fixtures.git import GitRepoPair, _git_clone, _git_commit, _git_head, _run_git

pygit2 = pytest.importorskip("pygit2")


@pytest.fixture(autouse=True)
def isolated_git_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    for level in (
        pygit2.GIT_CONFIG_LEVEL_GLOBAL,
        pygit2.GIT_CONFIG_LEVEL_XDG,
        pygit2.GIT_CONFIG_LEVEL_SYSTEM,
    ):
        original = pygit2.option(pygit2.GIT_OPT_GET_SEARCH_PATH, level)
        pygit2.option(pygit2.GIT_OPT_SET_SEARCH_PATH, level, str(tmp_path))
        request.addfinalizer(
            lambda level=level, original=original: pygit2.option(
                pygit2.GIT_OPT_SET_SEARCH_PATH, level, original
            )
        )
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.mark.parametrize(
    "mode", ["fast_forward", "clean", "conflict", "union", "dirty"]
)
@pytest.mark.parametrize("backend", ["cli", "native"])
def test_two_clone_sync(
    git_repo_pair: GitRepoPair, tmp_path: Path, mode: str, backend: str
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "commit.gpgsign", "false")
    _run_git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    if mode == "union":
        _git_commit(local, ".gitattributes", "README.md merge=union\n", "attributes")
        _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    if mode == "clean":
        _git_commit(local, "local.md", "local\n", "local")
    elif mode in {"conflict", "union"}:
        _git_commit(local, "README.md", "local\n", "local")
    elif mode == "dirty":
        (local / "README.md").write_text("uncommitted\n")
    _git_commit(other, "README.md", "remote\n", "remote")
    _run_git(other, "push")
    before = _git_head(local)
    if backend == "native":
        store = Libgit2SyncStore(repo_path=local, enable_sync=True)
    else:
        from markdown_vault_mcp.git.strategy import GitWriteStrategy

        store = GitWriteStrategy(
            repo_path=local, enable_pull=True, enable_push=False, git_lfs=False
        )
    result = store.force_pull()
    if mode == "dirty":
        assert not result.applied and _git_head(local) == before
        assert (local / "README.md").read_text() == "uncommitted\n"
    else:
        assert result.applied and _git_head(local) != before
        if mode == "clean":
            assert (local / "local.md").read_text() == "local\n"
        if mode == "union":
            assert "local" in (local / "README.md").read_text()
        if mode == "conflict":
            import frontmatter

            sibling = next(local.glob("README.conflict-mcp-*.md"))
            theirs = frontmatter.loads((local / "README.md").read_text())
            ours = frontmatter.loads(sibling.read_text())
            assert (theirs.content, ours.content) == ("remote", "local")
            assert theirs["conflict_with"] == sibling.name
            assert ours["conflict_with"] == "README.md"
    assert not (local / ".git/MERGE_HEAD").exists()
    assert not (local / ".git/rebase-merge").exists()
    store.close()


@pytest.mark.parametrize("overlap", [False, True])
def test_staged_work_is_preserved(
    git_repo_pair: GitRepoPair, tmp_path: Path, overlap: bool
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "commit.gpgsign", "false")
    _run_git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    other = _git_clone(git_repo_pair, tmp_path)
    name = "README.md" if overlap else "unrelated.md"
    (local / name).write_text("staged\n")
    _run_git(local, "add", name)
    _git_commit(other, "README.md", "remote\n", "remote")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(repo_path=local, enable_sync=True)
    result = store.force_pull()
    assert result.applied is not overlap
    assert (_git_head(local) == before) is overlap
    assert _run_git(local, "show", f":{name}") == "staged\n"
    assert (local / name).read_text() == "staged\n"
    store.close()


def test_push_and_reject_unfetched_remote(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "commit.gpgsign", "false")
    _run_git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    store = Libgit2SyncStore(repo_path=local, enable_sync=True, push_delay_s=0)
    note = local / "note.md"
    note.write_text("local\n")
    store(note, "local\n", "write")
    assert store.force_push().applied
    assert _run_git(git_repo_pair.remote_path, "show", "main:note.md") == "local\n"
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "remote.md", "remote\n", "remote")
    _run_git(other, "push")
    note.write_text("new local\n")
    store(note, "new local\n", "edit")
    result = store.force_push()
    assert not result.applied
    assert result.reason == "non_fast_forward"
    store.close()


def test_managed_clone_and_origin_validation(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    path = tmp_path / "managed"
    url = str(git_repo_pair.remote_path)
    store = Libgit2SyncStore(path, managed=True, repo_url=url, enable_sync=True)
    assert store.is_managed
    assert (path / "README.md").exists()
    store.close()
    with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
        Libgit2SyncStore(path, managed=True, repo_url=str(tmp_path / "other"))


def test_config_constructs_sync_modes(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.config_sections._assembly import _resolve_git

    token_config = ProjectConfig(
        source_dir=git_repo_pair.local_path, git_backend="libgit2", git_token="fixture"
    )
    token_store, interval = _resolve_git(token_config)
    assert isinstance(token_store, Libgit2SyncStore) and not token_store.is_managed
    assert interval == token_config.git_pull_interval_s
    token_store.close()
    managed_config = ProjectConfig(
        source_dir=tmp_path / "clone",
        git_backend="libgit2",
        git_repo_url=str(git_repo_pair.remote_path),
    )
    managed_store, interval = _resolve_git(managed_config)
    assert isinstance(managed_store, Libgit2SyncStore) and managed_store.is_managed
    assert interval == managed_config.git_pull_interval_s
    managed_store.close()


@pytest.mark.parametrize(
    "url",
    ["ssh://git@example.com/repo", "git@example.com:repo", "http://example.com/repo"],
)
def test_token_transport_refused_before_clone(tmp_path: Path, url: str) -> None:
    with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
        Libgit2SyncStore(tmp_path / "clone", repo_url=url, managed=True, token="secret")
    assert not (tmp_path / "clone").exists()


def test_incoming_filter_refused_before_checkout(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, ".gitattributes", "*.md filter=lfs\n", "filter")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    result = store.force_pull()
    assert not result.applied and _git_head(local) == before
    assert "filter" in (result.detail or "")
    assert not (local / ".gitattributes").exists()
    store.close()


@pytest.mark.parametrize("hook", ["pre-push", "post-merge", "post-checkout"])
def test_startup_refuses_hooks(git_repo: Path, hook: str) -> None:
    path = git_repo / ".git/hooks" / hook
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
        Libgit2SyncStore(git_repo, enable_sync=True)


def test_incoming_info_attributes_filter_is_refused(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    (local / ".git/info/attributes").write_text("new.md filter=lfs\n")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "new.md", "remote\n", "remote")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    before = _git_head(local)
    result = store.force_pull()
    assert not result.applied and _git_head(local) == before
    assert "LFS" in (result.detail or "")
    store.close()


def test_ref_failure_reports_dirty_worktree(
    git_repo_pair: GitRepoPair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "README.md", "remote\n", "remote")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)

    def fail_after_checkout(repo, _before, target):
        repo.checkout_tree(repo[target], strategy=pygit2.GIT_CHECKOUT_SAFE)
        raise OSError("reference update failed")

    monkeypatch.setattr(store, "_apply", fail_after_checkout)
    result = store.force_pull()
    assert not result.applied and result.to_sha == before == _git_head(local)
    assert (local / "README.md").read_text() == "remote\n"
    assert store.sync_health().reason == "uncommitted_changes"
    store.close()


def test_remote_delete_preserves_local_change_as_sibling(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "README.md", "local\n", "local")
    _run_git(other, "rm", "README.md")
    _run_git(other, "commit", "-qm", "delete")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    result = store.force_pull()
    assert result.applied
    assert not (local / "README.md").exists()
    sibling = next(local.glob("README.conflict-mcp-*.md"))
    assert "local" in sibling.read_text()
    assert result.conflict_files == (sibling.name,)
    store.close()


def test_remote_hook_rejection_is_not_reported_as_success(
    git_repo_pair: GitRepoPair,
) -> None:
    local = git_repo_pair.local_path
    hook = git_repo_pair.remote_path / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    store = Libgit2SyncStore(local, enable_sync=True, push_delay_s=0)
    note = local / "note.md"
    note.write_text("local\n")
    store(note, "local\n", "write")
    result = store.force_push()
    assert not result.applied and result.reason == "push_failed"
    assert store.sync_health() is not None
    hook.unlink()
    store.flush()
    assert _run_git(git_repo_pair.remote_path, "show", "main:note.md") == "local\n"
    store.close()


def test_push_callback_rejection_is_not_treated_as_success(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = git_repo_pair.local_path
    store = Libgit2SyncStore(local, enable_sync=True, push_delay_s=0)
    note = local / "note.md"
    note.write_text("local\n")
    store(note, "local\n", "write")
    origin_method = store._origin
    remote = origin_method()

    class RejectingRemote:
        url = remote.url
        push_url = remote.push_url

        def push(self, _specs, *, callbacks):
            callbacks.push_update_reference("refs/heads/main", "policy rejected")

        def fetch(self, *, callbacks):
            remote.fetch(callbacks=callbacks)

    monkeypatch.setattr(store, "_origin", lambda: RejectingRemote())
    result = store.force_push()
    assert not result.applied and result.reason == "push_failed"
    assert "policy rejected" in (result.hint or "")
    monkeypatch.setattr(store, "_origin", origin_method)
    store.close()


def test_incoming_file_does_not_overwrite_ignored_local_file(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    (local / ".git/info/exclude").write_text("secret.md\n")
    (local / "secret.md").write_text("local\n")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "secret.md", "remote\n", "remote")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    result = store.force_pull()
    assert not result.applied and _git_head(local) == before
    assert (local / "secret.md").read_text() == "local\n"
    store.close()


def test_pull_is_not_reported_as_a_local_commit(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "README.md", "remote\n", "remote")
    _run_git(other, "push")
    seen: list[tuple[str, str]] = []
    store = Libgit2SyncStore(local, enable_sync=True)
    store.set_commit_observer(lambda before, after: seen.append((before, after)))
    assert store.force_pull().applied
    assert seen == []
    store.close()


def test_pull_quiesces_before_lock_and_native_operations_skip_git_cli(
    git_repo_pair: GitRepoPair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib
    import subprocess

    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "README.md", "remote\n", "remote")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    events: list[str] = []

    @contextlib.contextmanager
    def pause():
        events.append("pause")
        yield
        events.append("resume")

    def drain() -> bool:
        events.append("drain")
        assert store._lock.acquire(blocking=False)
        store._lock.release()
        return True

    store.set_write_quiescer(pause, drain)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Git CLI called")
    )
    assert store.force_pull().applied
    assert events == ["pause", "drain", "resume"]
    store.close()


def test_native_sync_has_no_git_runner_imports() -> None:
    import ast

    source = Path(__file__).parents[1] / "src/markdown_vault_mcp/git/libgit2_sync.py"
    tree = ast.parse(source.read_text())
    modules = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    names = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert "subprocess" not in names
    assert "markdown_vault_mcp.git._run" not in modules


def test_remote_rename_and_local_edit_keep_both_versions(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "README.md", "local edit\n", "edit")
    _run_git(other, "mv", "README.md", "renamed.md")
    _run_git(other, "commit", "-qm", "rename")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    result = store.force_pull()
    assert result.applied
    assert (local / "renamed.md").exists()
    assert "local edit" in (local / "renamed.md").read_text() or any(
        "local edit" in path.read_text() for path in local.glob("*.conflict-mcp-*.md")
    )
    store.close()


def test_conflict_metadata_is_vault_relative(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    import frontmatter

    local = git_repo_pair.local_path
    (local / "vault").mkdir()
    _git_commit(local, "vault/note.md", "base\n", "seed")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "vault/note.md", "local\n", "local")
    _git_commit(other, "vault/note.md", "remote\n", "remote")
    _run_git(other, "push")
    store = Libgit2SyncStore(local / "vault", enable_sync=True)
    result = store.force_pull()
    sibling = next((local / "vault").glob("*.conflict-mcp-*.md"))
    assert result.conflict_files == (sibling.name,)
    assert frontmatter.loads(sibling.read_text())["conflict_with"] == "note.md"
    assert (
        frontmatter.loads((local / "vault/note.md").read_text())["conflict_with"]
        == sibling.name
    )
    store.close()


def test_credentials_are_scoped_to_the_configured_https_host() -> None:
    from markdown_vault_mcp.git.libgit2_sync import _callbacks, _safe

    callback = _callbacks(pygit2, "fixture-secret", "user", "https://example.test/repo")
    credential = callback.credentials(
        "https://example.test/repo",
        None,
        pygit2.enums.CredentialType.USERPASS_PLAINTEXT,
    )
    assert credential.credential_tuple == ("user", "fixture-secret")
    with pytest.raises(ValueError):
        callback.credentials(
            "https://elsewhere.test/repo",
            None,
            pygit2.enums.CredentialType.USERPASS_PLAINTEXT,
        )
    url = "https://" + "user:" + "fixture-secret" + "@example.test/"
    assert "fixture-secret" not in _safe(url, "fixture-secret")
    assert "user:" not in _safe(url, "fixture-secret")


def test_ssh_credentials_support_username_negotiation() -> None:
    from markdown_vault_mcp.git.libgit2_sync import _callbacks

    callback = _callbacks(pygit2, None, "ignored", "ssh://git@example.test/repo")
    credential = callback.credentials(
        "ssh://git@example.test/repo", "git", pygit2.enums.CredentialType.USERNAME
    )
    assert credential.credential_tuple == ("git",)


def test_dry_run_fetches_and_classifies_divergence(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "local.md", "local\n", "local")
    remote = _git_commit(other, "remote.md", "remote\n", "remote")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    result = store.force_pull(dry_run=True)
    assert (
        not result.applied and result.reason == "diverged" and result.to_sha == remote
    )
    assert _git_head(local) == before
    assert _run_git(local, "rev-parse", "origin/main").strip() == remote
    store.close()


def test_dry_run_does_not_clear_existing_pull_failure(
    git_repo_pair: GitRepoPair,
) -> None:
    local = git_repo_pair.local_path
    store = Libgit2SyncStore(local, enable_sync=True)
    store._health.pull_failed("conflict_resolution_failed", "fixture")
    assert store.force_pull(dry_run=True).applied
    assert store.sync_health() is not None
    assert store.sync_health().reason == "conflict_resolution_failed"
    store.close()


def test_missing_remote_and_disabled_sync(git_repo: Path) -> None:
    store = Libgit2SyncStore(git_repo, enable_sync=False)
    assert store.force_pull().reason == "pull_disabled"
    assert store.force_push().reason == "no_remote"
    store.close()
    store = Libgit2SyncStore(git_repo, enable_sync=True)
    assert store.force_pull().reason == "no_remote"
    store.close()


def test_background_loop_calls_pull_and_tick(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    import contextlib
    import threading

    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "README.md", "remote\n", "remote")
    _run_git(other, "push")
    ticked = threading.Event()
    calls: list[str] = []
    store = Libgit2SyncStore(local, enable_sync=True)

    @contextlib.contextmanager
    def pause():
        calls.append("pause")
        yield
        calls.append("resume")

    store.set_write_quiescer(pause, lambda: True)
    store.start(
        repo_path=local,
        pull_interval_s=30,
        on_pull=lambda: calls.append("pull"),
        on_tick=ticked.set,
    )
    assert ticked.wait(5)
    store.stop()
    store.close()
    store.close()
    assert calls == ["pause", "resume", "pause", "pull", "resume"]
