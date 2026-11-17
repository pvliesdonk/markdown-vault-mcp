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
    assert result.reason == "conflict_resolution_failed"
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
    store = Libgit2SyncStore(local, enable_sync=True, push_delay_s=0)
    hook = git_repo_pair.remote_path / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
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
    names = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert "subprocess" not in names
    imported = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "markdown_vault_mcp.git._run"
        for alias in node.names
    ]
    assert set(imported) <= {"_normalize_remote", "redact"}


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


@pytest.mark.parametrize(
    "case",
    [
        ("data.csv", b"value\nbase\n", b"value\nlocal\n", b"value\nremote\n"),
        ("image.png", b"\x89PNG\x00base", b"\x89PNG\x00local", b"\x89PNG\x00remote"),
    ],
)
def test_attachment_conflicts_preserve_bytes_and_push(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    case: tuple[str, bytes, bytes, bytes],
) -> None:
    name, base, ours, theirs = case
    local = git_repo_pair.local_path
    (local / name).write_bytes(base)
    _run_git(local, "add", name)
    _run_git(local, "commit", "-qm", "seed attachment")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    (local / name).write_bytes(ours)
    _run_git(local, "commit", "-qam", "local attachment")
    (other / name).write_bytes(theirs)
    _run_git(other, "commit", "-qam", "remote attachment")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    result = store.force_pull()
    assert result.applied, result.detail
    sibling = next(local.glob(f"{Path(name).stem}.conflict-mcp-*{Path(name).suffix}"))
    assert (local / name).read_bytes() == theirs
    assert sibling.read_bytes() == ours
    assert store.force_push().applied
    remote = pygit2.Repository(str(git_repo_pair.remote_path))
    tree = remote[remote.references["refs/heads/main"].target].tree
    assert remote[tree[name].id].data == theirs
    assert remote[tree[sibling.name].id].data == ours
    store.close()


def test_conflict_outside_served_vault_is_reconciled(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    (local / "vault").mkdir()
    _git_commit(local, "vault/note.md", "note\n", "vault")
    _git_commit(local, "outside.md", "base\n", "seed")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "outside.md", "local\n", "local")
    _git_commit(other, "outside.md", "remote\n", "remote")
    _run_git(other, "push")
    store = Libgit2SyncStore(local / "vault", enable_sync=True)
    result = store.force_pull()
    assert result.applied, result.detail
    assert result.conflict_files == ()
    sibling = next(local.glob("outside.conflict-mcp-*.md"))
    assert "local" in sibling.read_text()
    assert "remote" in (local / "outside.md").read_text()
    assert store.sync_health() is None
    store.close()


@pytest.mark.parametrize("incoming", [False, True])
def test_unconfigured_filter_is_inert(
    git_repo_pair: GitRepoPair, tmp_path: Path, incoming: bool
) -> None:
    local = git_repo_pair.local_path
    if incoming:
        other = _git_clone(git_repo_pair, tmp_path)
        _git_commit(other, ".gitattributes", "*.md filter=missing\n", "attributes")
        _run_git(other, "push")
    else:
        _git_commit(local, ".gitattributes", "*.md filter=missing\n", "attributes")
    store = Libgit2SyncStore(local, enable_sync=True)
    if incoming:
        result = store.force_pull()
        assert result.applied, result.detail
        assert (local / ".gitattributes").read_text() == "*.md filter=missing\n"
    store.close()


def test_noop_pull_skips_incoming_tree_scan(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.git.libgit2_sync as sync

    store = Libgit2SyncStore(git_repo_pair.local_path, enable_sync=True)
    monkeypatch.setattr(
        sync, "_check_tree", lambda *_args: pytest.fail("unchanged tree scanned")
    )
    assert store.force_pull().applied
    store.close()


def test_commit_only_ignores_sync_hooks(git_repo: Path) -> None:
    hook = git_repo / ".git/hooks/pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    store = Libgit2SyncStore(git_repo, enable_sync=False)
    store.close()


def test_ssh_transport_refused_with_action(tmp_path: Path) -> None:
    with pytest.raises(
        ConfigurationError, match=r"host key verification.*GIT_BACKEND=cli"
    ):
        Libgit2SyncStore(
            tmp_path / "clone", repo_url="ssh://git@example.test/repo", managed=True
        )


def test_incoming_configured_filter_is_refused_without_conflict_reason(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "filter.demo.clean", "cat")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, ".gitattributes", "*.md filter=demo\n", "attributes")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    before = _git_head(local)
    result = store.force_pull()
    assert not result.applied and _git_head(local) == before
    assert result.reason == "conflict_resolution_failed"
    assert "filter" in (result.detail or "")
    store.close()


def test_certificate_callback_never_accepts_invalid_host() -> None:
    from markdown_vault_mcp.git.libgit2_sync import _callbacks

    callback = _callbacks(pygit2, None, "git", "https://example.test/repo")
    with pytest.raises(pygit2.Passthrough):
        callback.certificate_check(None, False, b"example.test")
    with pytest.raises(pygit2.Passthrough):
        callback.certificate_check(None, True, b"example.test")


def test_local_remote_hook_is_refused_at_startup(git_repo_pair: GitRepoPair) -> None:
    hook = git_repo_pair.remote_path / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
        Libgit2SyncStore(git_repo_pair.local_path, enable_sync=True)


def test_push_url_is_validated_at_startup(git_repo_pair: GitRepoPair) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "remote", "set-url", "--push", "origin", "http://example.test/repo")
    with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
        Libgit2SyncStore(local, token="fixture", enable_sync=True)


def test_push_callback_uses_effective_push_host(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = git_repo_pair.local_path
    store = Libgit2SyncStore(local, token="fixture", enable_sync=True)
    _git_commit(local, "new.md", "local\n", "local")
    original = store._origin()

    class Remote:
        url = "https://fetch.example.test/repo"
        push_url = "https://push.example.test/repo"

        def push(self, _specs, *, callbacks):
            credential = callbacks.credentials(
                self.push_url, None, pygit2.enums.CredentialType.USERPASS_PLAINTEXT
            )
            assert credential.credential_tuple == ("x-access-token", "fixture")

    monkeypatch.setattr(store, "_origin", lambda: Remote())
    store._push(local)
    monkeypatch.setattr(store, "_origin", lambda: original)
    store.close()


def test_incoming_attribute_override_is_checked_against_target_commit(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    _git_commit(local, ".gitattributes", "*.md -filter\n", "attributes")
    _run_git(local, "push")
    _run_git(local, "config", "filter.demo.clean", "cat")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, ".gitattributes", "*.md filter=demo\n", "new attributes")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    result = store.force_pull()
    assert not result.applied
    assert "attribute changes" in (result.detail or "")
    assert "GIT_BACKEND=cli" in (result.detail or "")
    store.close()


def test_credentials_stop_after_rejected_attempt() -> None:
    from markdown_vault_mcp.git.libgit2_sync import _callbacks

    url = "https://example.test/repo"
    callback = _callbacks(pygit2, "fixture", "user", url)
    allowed = pygit2.enums.CredentialType.USERPASS_PLAINTEXT
    for _ in range(3):
        assert callback.credentials(url, None, allowed).credential_tuple == (
            "user",
            "fixture",
        )
    with pytest.raises(ValueError, match=r"[Cc]redential"):
        callback.credentials(url, None, allowed)


@pytest.mark.parametrize("failure", ["reference", "partial", "restore"])
def test_failed_checkout_keeps_vault_search_consistent(  # noqa: C901, PLR0915, RUF100 -- exercises failure stages through a real vault.
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from markdown_vault_mcp.vault import Vault

    local = git_repo_pair.local_path
    _git_commit(local, "README.md", "originalquartz\n", "seed")
    _git_commit(local, "staged.txt", "base\n", "stage seed")
    _git_commit(local, "unstaged.txt", "base\n", "unstaged seed")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "README.md", "newcobalt\n", "remote")
    _git_commit(other, "remote.md", "remote new\n", "add remote")
    _run_git(other, "push")
    (local / "staged.txt").write_text("staged\n")
    _run_git(local, "add", "staged.txt")
    (local / "unstaged.txt").write_text("unstaged\n")
    store = Libgit2SyncStore(local, enable_sync=True)
    vault = Vault(source_dir=local, git_strategy=store)
    try:
        vault.index.build_index()
        assert vault.reader.search("originalquartz", mode="keyword")
        reconciler = vault._head_reconciler
        assert reconciler is not None
        before = _git_head(local)
        reconciler.mark_reconciled(before)
        real_repo = store._repo

        class FailedTransaction:
            def __init__(self):
                self.transaction = real_repo.transaction()

            def __enter__(self):
                self.transaction.__enter__()
                return self

            def __exit__(self, *args):
                return self.transaction.__exit__(*args)

            def lock_ref(self, name):
                self.transaction.lock_ref(name)

            def set_target(self, *_args, **_kwargs):
                raise OSError("reference update failed")

        class FailedRepo:
            checkouts = 0

            def checkout_tree(self, *args, **kwargs):
                self.checkouts += 1
                if failure == "restore" and self.checkouts == 2:
                    raise OSError("restore failed")
                if failure in ("partial", "restore") and self.checkouts == 1:
                    real_repo.checkout_tree(
                        args[0], strategy=pygit2.GIT_CHECKOUT_SAFE, paths=["README.md"]
                    )
                    raise OSError("partial checkout failed")
                real_repo.checkout_tree(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(real_repo, name)

            def __getitem__(self, key):
                return real_repo[key]

            def transaction(self):
                return FailedTransaction()

        monkeypatch.setattr(store, "_repo", FailedRepo())
        assert not store.force_pull().applied
        if failure == "restore":
            assert store.sync_health() is not None
            monkeypatch.setattr(store, "_repo", real_repo)
            note = local / "own.md"
            note.write_text("own write\n")
            store(note, "own write\n", "write")
        assert (local / "staged.txt").read_text() == "staged\n"
        assert _run_git(local, "show", ":staged.txt") == "staged\n"
        assert (local / "unstaged.txt").read_text() == "unstaged\n"
        reconciler.reconcile(source="git_sync")
        if failure != "restore":
            assert _git_head(local) == before
        contents = (local / "README.md").read_text()
        assert bool(vault.reader.search("newcobalt", mode="keyword")) == (
            "newcobalt" in contents
        )
        assert bool(vault.reader.search("originalquartz", mode="keyword")) == (
            "originalquartz" in contents
        )
        monkeypatch.setattr(store, "_repo", real_repo)
        retry = store.force_pull()
        if failure == "restore":
            assert not retry.applied and "README.md" in (retry.detail or "")
        else:
            assert retry.applied, retry.detail
    finally:
        vault.close()
        store.close()


@pytest.mark.parametrize("conflict", [False, True])
@pytest.mark.parametrize("query", ["vault", "directory", "revision"])
def test_history_consumers_after_native_divergence(
    git_repo_pair: GitRepoPair, tmp_path: Path, conflict: bool, query: str
) -> None:
    from markdown_vault_mcp.git.types import RevisionQuery

    local = git_repo_pair.local_path
    _run_git(local, "config", "core.abbrev", "7")
    (local / "notes").mkdir()
    birth = _git_commit(local, "notes/note.md", "base\n", "seed")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "notes/note.md" if conflict else "local.md", "local\n", "local")
    _git_commit(other, "notes/note.md", "remote\n", "remote")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        assert store.force_pull().applied
        if query == "revision":
            assert (
                store.get_file_at_ref(
                    RevisionQuery(local, local / "notes/note.md", birth, 0)
                ).content
                == "base\n"
            )
        else:
            path = local / "notes" if query == "directory" else None
            assert store.get_file_history(
                local, path, None, 20, is_dir=query == "directory"
            )
    finally:
        store.close()


@pytest.mark.parametrize("incoming", [False, True])
def test_gitlink_is_supported_or_refused_actionably(
    git_repo_pair: GitRepoPair, tmp_path: Path, incoming: bool
) -> None:
    local = git_repo_pair.local_path
    sub = tmp_path / "subsource"
    sub.mkdir()
    _run_git(sub, "init", "-q", "--initial-branch=main")
    _run_git(sub, "config", "user.name", "test")
    _run_git(sub, "config", "user.email", "test@example.test")
    oid = _git_commit(sub, "note.md", "submodule\n", "seed")
    target = _git_clone(git_repo_pair, tmp_path) if incoming else local
    _run_git(target, "update-index", "--add", "--cacheinfo", f"160000,{oid},sub")
    _run_git(target, "commit", "-qm", "add gitlink")
    if incoming:
        _run_git(target, "push")
    try:
        store = Libgit2SyncStore(local, enable_sync=True)
    except ConfigurationError as exc:
        assert "GIT_BACKEND=cli" in str(exc)
        return
    try:
        if incoming:
            result = store.force_pull()
            assert result.applied or "GIT_BACKEND=cli" in (result.detail or "")
    finally:
        store.close()


def test_incoming_required_unconfigured_filter_refused(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "filter.missing.required", "true")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, ".gitattributes", "*.md filter=missing\n", "required filter")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        assert not result.applied and _git_head(local) == before
        assert "GIT_BACKEND=cli" in (result.detail or "")
    finally:
        store.close()


@pytest.mark.parametrize("managed", [True, False], ids=["managed", "token"])
def test_empty_remote_first_commit_can_be_pushed(tmp_path: Path, managed: bool) -> None:
    remote = tmp_path / "empty.git"
    remote.mkdir()
    _run_git(remote, "init", "--bare", "--initial-branch=main")
    local = tmp_path / "local"
    if managed:
        store = Libgit2SyncStore(
            local, managed=True, repo_url=str(remote), enable_sync=True
        )
    else:
        _run_git(tmp_path, "clone", str(remote), str(local))
        store = Libgit2SyncStore(local, token="fixture", enable_sync=True)
    try:
        _run_git(local, "config", "user.name", "Test")
        _run_git(local, "config", "user.email", "test@example.test")
        note = local / "note.md"
        note.write_text("first\n")
        store(note, "first\n", "write")
        store.flush()
        assert _run_git(remote, "show", "main:note.md") == "first\n"
        assert store._tracking() is not None
    finally:
        store.close()


def test_empty_remote_starts_sync_loop(tmp_path: Path) -> None:
    import threading

    remote = tmp_path / "empty.git"
    remote.mkdir()
    _run_git(remote, "init", "--bare", "--initial-branch=main")
    local = tmp_path / "local"
    store = Libgit2SyncStore(
        local, managed=True, repo_url=str(remote), enable_sync=True
    )
    ticked = threading.Event()
    try:
        store.start(repo_path=local, pull_interval_s=1, on_tick=ticked.set)
        assert ticked.wait(2), "sync loop did not start for an empty remote"
    finally:
        store.stop()
        store.close()


def test_unrelated_remote_history_has_actionable_outcome(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _run_git(other, "checkout", "--orphan", "replacement")
    _run_git(other, "rm", "-rf", ".")
    _git_commit(other, "replacement.md", "replacement\n", "replacement")
    _run_git(other, "push", "--force", "origin", "HEAD:main")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        assert result.applied or result.detail, result
    finally:
        store.close()


def test_commit_only_pull_result_matches_cli(git_repo: Path) -> None:
    store = Libgit2SyncStore(git_repo, enable_sync=False)
    try:
        result = store.force_pull()
        assert result.reason == "pull_disabled"
        assert result.from_sha == result.to_sha == ""
    finally:
        store.close()


def test_no_remote_push_has_actionable_hint(git_repo: Path) -> None:
    store = Libgit2SyncStore(git_repo, enable_sync=True)
    try:
        result = store.force_push()
        assert result.reason == "no_remote"
        assert result.hint
    finally:
        store.close()


def test_filter_refusal_is_visible_in_sync_health(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "filter.demo.clean", "cat")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, ".gitattributes", "*.md filter=demo\n", "attributes")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        assert not store.force_pull().applied
        assert store.sync_health() is not None
    finally:
        store.close()


def test_dry_run_push_has_actionable_hint(git_repo_pair: GitRepoPair) -> None:
    store = Libgit2SyncStore(git_repo_pair.local_path, enable_sync=True)
    try:
        result = store.force_push(dry_run=True)
        assert result.reason == "dry_run_unsupported"
        assert result.hint
    finally:
        store.close()


def test_push_failure_hint_is_bounded(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.git.push_scheduler import PushBackendError

    local = git_repo_pair.local_path
    store = Libgit2SyncStore(local, enable_sync=True)
    _git_commit(local, "note.md", "local\n", "local")

    def fail(_path: Path) -> None:
        raise PushBackendError("x" * 300)

    monkeypatch.setattr(store, "_push", fail)
    try:
        result = store.force_push()
        assert result.reason == "push_failed"
        assert result.hint and len(result.hint) <= 200
    finally:
        store.close()


def test_policy_tree_scan_does_not_load_blob_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import markdown_vault_mcp.git.libgit2_sync as sync

    class Repo:
        def __getitem__(self, _oid):
            pytest.fail("policy type scan loaded a blob")

        def get_attr(self, *_args):
            return None

    monkeypatch.setattr(sync, "_filter_guard", lambda *_args: None)
    entry = SimpleNamespace(name="note.md", id="blob", type_str="blob")
    commit = SimpleNamespace(tree=[entry], id="commit")
    sync._check_tree(Repo(), commit)


def test_path_limited_checkout_restores_target_added_file(git_repo: Path) -> None:
    before = _git_head(git_repo)
    target = _git_commit(git_repo, "added.md", "new\n", "add")
    _run_git(git_repo, "reset", "--hard", before)
    repo = pygit2.Repository(str(git_repo))
    repo.checkout_tree(repo[target], strategy=pygit2.GIT_CHECKOUT_SAFE)
    assert (git_repo / "added.md").exists()
    repo.checkout_tree(
        repo[before], strategy=pygit2.GIT_CHECKOUT_FORCE, paths=["added.md"]
    )
    assert not (git_repo / "added.md").exists()


def test_conflict_siblings_have_dedicated_final_commit(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "README.md", "local\n", "local edit")
    _git_commit(other, "README.md", "remote\n", "remote edit")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        assert result.applied
        assert _run_git(local, "log", "-1", "--format=%s").startswith(
            "conflict: saved "
        )
        sibling = next(local.glob("README.conflict-mcp-*.md"))
        assert sibling.name in _run_git(
            local, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
        )
    finally:
        store.close()


def test_empty_clone_receives_remote_first_commit(tmp_path: Path) -> None:
    remote = tmp_path / "empty.git"
    remote.mkdir()
    _run_git(remote, "init", "--bare", "--initial-branch=main")
    local = tmp_path / "local"
    store = Libgit2SyncStore(
        local, managed=True, repo_url=str(remote), enable_sync=True
    )
    other = tmp_path / "writer"
    _run_git(tmp_path, "clone", str(remote), str(other))
    _run_git(other, "config", "user.name", "Test")
    _run_git(other, "config", "user.email", "test@example.test")
    _git_commit(other, "first.md", "remote first\n", "first")
    _run_git(other, "push", "origin", "main")
    try:
        result = store.force_pull()
        assert result.applied, result
        assert (local / "first.md").read_text() == "remote first\n"
    finally:
        store.close()


def test_empty_clone_loop_receives_later_first_commit(tmp_path: Path) -> None:
    import threading

    remote = tmp_path / "empty.git"
    remote.mkdir()
    _run_git(remote, "init", "--bare", "--initial-branch=main")
    local = tmp_path / "local"
    store = Libgit2SyncStore(
        local, managed=True, repo_url=str(remote), enable_sync=True
    )
    pulled = threading.Event()
    try:
        store.start(repo_path=local, pull_interval_s=1, on_pull=pulled.set)
        other = tmp_path / "writer"
        _run_git(tmp_path, "clone", str(remote), str(other))
        _run_git(other, "config", "user.name", "Test")
        _run_git(other, "config", "user.email", "test@example.test")
        _git_commit(other, "first.md", "remote first\n", "first")
        _run_git(other, "push", "origin", "main")
        assert pulled.wait(5)
        assert (local / "first.md").read_text() == "remote first\n"
    finally:
        store.stop()
        store.close()


@pytest.mark.parametrize(
    "override_false", [False, True], ids=["valueless", "false-override"]
)
def test_incoming_valueless_required_filter(
    git_repo_pair: GitRepoPair, tmp_path: Path, override_false: bool
) -> None:
    local = git_repo_pair.local_path
    with (local / ".git/config").open("a", encoding="utf-8") as config:
        config.write('\n[filter "missing"]\n\trequired\n')
        if override_false:
            config.write("\trequired = false\n")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, ".gitattributes", "*.md filter=missing\n", "required filter")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        if override_false:
            assert result.applied, result.detail
        else:
            assert not result.applied and _git_head(local) == before
            assert "GIT_BACKEND=cli" in (result.detail or "")
    finally:
        store.close()


@pytest.mark.parametrize("backend", ["cli", "native"])
def test_merge_resolution_survives_remote_pull(
    git_repo_pair: GitRepoPair, tmp_path: Path, backend: str
) -> None:
    import subprocess

    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "note.md", "base\n", "base")
    _run_git(local, "push")
    _run_git(other, "pull", "--ff-only")
    _run_git(local, "checkout", "-b", "side")
    _git_commit(local, "note.md", "side\n", "side")
    _run_git(local, "checkout", "main")
    _git_commit(local, "note.md", "main\n", "main")
    merge = subprocess.run(["git", "merge", "side"], cwd=local, capture_output=True)
    assert merge.returncode != 0
    (local / "note.md").write_text("carefully resolved\n")
    _run_git(local, "add", "note.md")
    _run_git(local, "commit", "-qm", "merge resolution")
    _git_commit(other, "remote.md", "remote\n", "remote")
    _run_git(other, "push")
    before = _git_head(local)
    if backend == "cli":
        store = GitWriteStrategy(
            repo_path=local, enable_pull=True, enable_push=False, git_lfs=False
        )
    else:
        store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        if backend == "cli":
            assert result.applied
            assert "main" in (local / "note.md").read_text()
        else:
            assert not result.applied and "GIT_BACKEND=cli" in (result.detail or "")
            assert _git_head(local) == before
            assert (local / "note.md").read_text() == "carefully resolved\n"
            assert _run_git(local, "status", "--porcelain") == ""
    finally:
        store.close()


@pytest.mark.parametrize("managed", [False, True], ids=["existing", "managed"])
def test_initial_lfs_pointer_is_refused(
    git_repo_pair: GitRepoPair, tmp_path: Path, managed: bool
) -> None:
    local = git_repo_pair.local_path
    _git_commit(
        local,
        ".gitattributes",
        "*.bin filter=lfs diff=lfs merge=lfs -text\n",
        "attributes",
    )
    pointer = (
        "version https://git-lfs.github.com/spec/v1\noid sha256:"
        + "a" * 64
        + "\nsize 123\n"
    )
    _git_commit(local, "asset.bin", pointer, "pointer")
    _run_git(local, "push")
    path = tmp_path / "managed" if managed else local
    with pytest.raises(
        ConfigurationError, match=r"(?i)(lfs|filter).*(GIT_BACKEND=cli)"
    ):
        store = Libgit2SyncStore(
            path,
            managed=managed,
            repo_url=str(git_repo_pair.remote_path) if managed else None,
            enable_sync=True,
        )
        store.close()


@pytest.mark.parametrize("backend", ["cli", "native"])
def test_repeated_conflicts_have_reciprocal_sibling_links(
    git_repo_pair: GitRepoPair, tmp_path: Path, backend: str
) -> None:
    import frontmatter

    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "README.md", "first local\n", "local one")
    _git_commit(local, "README.md", "final local\n", "local two")
    _git_commit(other, "README.md", "remote\n", "remote")
    _run_git(other, "push")
    if backend == "cli":
        from markdown_vault_mcp.git.strategy import GitWriteStrategy

        store = GitWriteStrategy(
            repo_path=local, enable_pull=True, enable_push=False, git_lfs=False
        )
    else:
        store = Libgit2SyncStore(local, enable_sync=True)
    try:
        assert store.force_pull().applied
        siblings = list(local.glob("README.conflict-mcp-*.md"))
        assert siblings
        assert any(
            frontmatter.loads(path.read_text()).content == "final local"
            for path in siblings
        )
        original = frontmatter.loads((local / "README.md").read_text())
        for path in siblings:
            assert frontmatter.loads(path.read_text())["conflict_with"] == "README.md"
            assert original["conflict_with"] == path.name
    finally:
        store.close()


@pytest.mark.parametrize("dry_run", [False, True])
def test_force_push_without_tracking_ref_is_no_remote(
    git_repo_pair: GitRepoPair, dry_run: bool
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "update-ref", "-d", "refs/remotes/origin/main")
    _git_commit(local, "note.md", "local\n", "local")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_push(dry_run=dry_run)
        assert not result.applied and result.reason == "no_remote"
        assert result.hint and "git push -u origin <branch>" in result.hint
    finally:
        store.close()


def test_disabled_pull_skips_quiescer(git_repo: Path) -> None:
    import contextlib

    calls = []
    store = Libgit2SyncStore(git_repo, enable_sync=False)

    @contextlib.contextmanager
    def pause():
        calls.append("pause")
        yield

    store.set_write_quiescer(pause, lambda: True)
    try:
        assert store.force_pull().reason == "pull_disabled"
        assert calls == []
    finally:
        store.close()


def test_local_delete_remote_modify_is_refused(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _run_git(local, "rm", "README.md")
    _run_git(local, "commit", "-qm", "delete")
    _git_commit(other, "README.md", "remote edit\n", "remote")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        assert not result.applied and result.reason == "conflict_resolution_failed"
        assert _git_head(local) == before and not (local / "README.md").exists()
    finally:
        store.close()


def test_missing_libgit2_has_operator_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.git.libgit2_sync as sync

    def missing():
        raise ConfigurationError(
            'Install "markdown-vault-mcp[libgit2]" or use GitWriteStrategy'
        )

    monkeypatch.setattr(sync, "_pygit2", missing)
    with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
        Libgit2SyncStore(tmp_path)
