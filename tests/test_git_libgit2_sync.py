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
            assert _run_git(local, "log", "-1", "--format=%s").startswith(
                "conflict: saved "
            )
            assert sibling.name in _run_git(
                local, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
            )
    assert not (local / ".git/MERGE_HEAD").exists()
    assert not (local / ".git/rebase-merge").exists()
    store.close()


def test_staged_work_is_preserved(git_repo_pair: GitRepoPair, tmp_path: Path) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "commit.gpgsign", "false")
    _run_git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    other = _git_clone(git_repo_pair, tmp_path)
    name = "unrelated.md"
    (local / name).write_text("staged\n")
    _run_git(local, "add", name)
    _git_commit(other, "README.md", "remote\n", "remote")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(repo_path=local, enable_sync=True)
    result = store.force_pull()
    assert result.applied
    assert _git_head(local) != before
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

    commit_store, interval = _resolve_git(
        ProjectConfig(source_dir=git_repo_pair.local_path, git_backend="libgit2")
    )
    assert isinstance(commit_store, Libgit2SyncStore) and interval == 0
    assert commit_store.force_pull().reason == "pull_disabled"
    commit_store.close()
    token_config = ProjectConfig(
        source_dir=git_repo_pair.local_path, git_backend="libgit2", git_token="fixture"
    )
    token_store, interval = _resolve_git(token_config)
    assert isinstance(token_store, Libgit2SyncStore) and not token_store.is_managed
    assert interval == token_config.git_pull_interval_s
    assert token_store.force_pull().reason != "pull_disabled"
    token_store.close()
    managed_config = ProjectConfig(
        source_dir=tmp_path / "clone",
        git_backend="libgit2",
        git_repo_url=str(git_repo_pair.remote_path),
    )
    managed_store, interval = _resolve_git(managed_config)
    assert isinstance(managed_store, Libgit2SyncStore) and managed_store.is_managed
    assert interval == managed_config.git_pull_interval_s
    assert managed_store.force_pull().reason != "pull_disabled"
    managed_store.close()


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


@pytest.mark.parametrize(
    "hook", ["pre-push", "post-merge", "post-checkout", "pre-rebase", "post-rewrite"]
)
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

        def push(self, _specs, *, callbacks, proxy=None):
            assert proxy is True
            callbacks.push_update_reference("refs/heads/main", "policy rejected")

        def fetch(self, *, callbacks, proxy=None):
            remote.fetch(callbacks=callbacks, proxy=proxy)

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
    assert result.conflict_files == ()
    assert (local / "renamed.md").read_text() == "local edit\n"
    assert not (local / "README.md").exists()
    assert not list(local.glob("*.conflict-mcp-*"))
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
    store.force_pull(dry_run=True)
    assert store.sync_health() is not None
    assert store.sync_health().reason == "conflict_resolution_failed"
    store.close()


def test_missing_remote_and_disabled_sync(git_repo: Path) -> None:
    import contextlib

    calls = []

    @contextlib.contextmanager
    def pause():
        calls.append("pause")
        yield

    store = Libgit2SyncStore(git_repo, enable_sync=False)
    store.set_write_quiescer(pause, lambda: True)
    result = store.force_pull()
    assert result.reason == "pull_disabled"
    assert result.from_sha == result.to_sha == ""
    assert calls == []
    pushed = store.force_push()
    assert pushed.reason == "no_remote" and pushed.hint
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


def test_ssh_transport_refused_with_action(tmp_path: Path) -> None:
    with pytest.raises(
        ConfigurationError, match=r"host key verification.*GIT_BACKEND=cli"
    ):
        Libgit2SyncStore(
            tmp_path / "clone", repo_url="ssh://git@example.test/repo", managed=True
        )
    assert not (tmp_path / "clone").exists()


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
    assert store.sync_health() is not None
    store.close()


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

        def push(self, _specs, *, callbacks, proxy=None):
            assert proxy is True
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


def test_history_consumers_after_native_divergence(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    from markdown_vault_mcp.git.types import RevisionQuery

    local = git_repo_pair.local_path
    _run_git(local, "config", "core.abbrev", "7")
    (local / "notes").mkdir()
    birth = _git_commit(local, "notes/note.md", "base\n", "seed")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "notes/note.md", "local\n", "local")
    _git_commit(other, "notes/note.md", "remote\n", "remote")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        assert store.force_pull().applied
        assert (
            store.get_file_at_ref(
                RevisionQuery(local, local / "notes/note.md", birth, 0)
            ).content
            == "base\n"
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
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        if incoming:
            result = store.force_pull()
            assert result.applied
            assert (local / "sub").is_dir()
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
        assert result.applied and result.reason == "rebased", result
        assert (local / "replacement.md").exists()
        assert (local / "README.md").exists()
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


def test_merge_resolution_survives_remote_pull(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    import subprocess

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
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
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


@pytest.mark.parametrize("state", ["changed", "unchanged", "offline"])
def test_initial_sync_attribute_capability_and_network_failure(
    git_repo_pair: GitRepoPair, tmp_path: Path, state: str
) -> None:
    from markdown_vault_mcp.vault import Vault

    local = git_repo_pair.local_path
    _git_commit(local, ".gitattributes", "*.md -filter\n", "inert attributes")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    if state == "changed":
        _run_git(local, "config", "filter.demo.required", "true")
        _git_commit(other, ".gitattributes", "*.md filter=demo\n", "attributes")
        _run_git(other, "push")
    elif state == "offline":
        _run_git(local, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    vault = Vault(source_dir=local, git_strategy=store)
    vault._git_pull_interval_s = 1
    try:
        if state == "changed":
            with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
                vault.sync_from_remote_before_index()
        else:
            vault.sync_from_remote_before_index()
        assert _git_head(local) == before
    finally:
        vault.close()


def test_configured_pre_push_hook_policy(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    import shlex

    local = git_repo_pair.local_path
    marker = tmp_path / "configured-hook-marker"
    _run_git(local, "config", "hook.fixture.event", "pre-push")
    _run_git(
        local, "config", "hook.fixture.command", f"touch {shlex.quote(str(marker))}"
    )
    _git_commit(local, "hook-proof.md", "proof\n", "proof")
    _run_git(local, "push")
    if not marker.exists():
        pytest.skip("installed Git does not execute configured pre-push hooks")
    marker.unlink()
    _git_commit(local, "hook-proof.md", "second\n", "second")
    with pytest.raises(ConfigurationError, match=r"pre-push.*GIT_BACKEND=cli"):
        Libgit2SyncStore(local, enable_sync=True)


def test_conflict_sibling_retains_later_clean_local_edit(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    import frontmatter

    local = git_repo_pair.local_path
    base = "start\n" + "middle\n" * 20 + "end\n"
    _git_commit(local, "note.md", base, "base")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "note.md", base.replace("start", "local"), "local conflict")
    final_local = base.replace("start", "local") + "later clean local\n"
    _git_commit(local, "note.md", final_local, "later local")
    _git_commit(other, "note.md", base.replace("start", "remote"), "remote conflict")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        assert store.force_pull().applied
        siblings = list(local.glob("note.conflict-mcp-*.md"))
        assert len(siblings) == 1
        content = frontmatter.loads(siblings[0].read_text()).content
        original = frontmatter.loads((local / "note.md").read_text()).content
        assert "remote" in original and "later clean local" in original
        assert content == final_local.strip()
        assert store.force_push().applied
    finally:
        store.close()


def test_delete_modify_refusal_has_manual_guidance(
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
        assert _git_head(local) == before
        assert not (local / "README.md").exists()
        assert "manual" in (result.detail or "").lower()
        assert "GIT_BACKEND=cli" not in (result.detail or "")
    finally:
        store.close()


def test_incoming_union_attribute_merges_both_versions(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    _git_commit(local, "union.md", "base\n", "base")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "union.md", "local\n", "local")
    _git_commit(other, ".gitattributes", "*.md merge=union\n", "union")
    _git_commit(other, "union.md", "remote\n", "remote")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        assert result.applied
        assert not list(local.glob("union.conflict-mcp-*.md"))
        assert "local" in (local / "union.md").read_text()
        assert "remote" in (local / "union.md").read_text()
    finally:
        store.close()


def test_shallow_repository_refused_at_startup(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    source = git_repo_pair.local_path
    _git_commit(source, "history.md", "history\n", "history")
    _run_git(source, "push")
    shallow = tmp_path / "shallow"
    _run_git(
        tmp_path,
        "clone",
        "--depth",
        "1",
        git_repo_pair.remote_path.as_uri(),
        str(shallow),
    )
    _git_commit(source, "next.md", "next\n", "next")
    _run_git(source, "push")
    with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
        Libgit2SyncStore(shallow, enable_sync=True)


def test_sparse_repository_refused_at_startup(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    (local / "in").mkdir()
    (local / "out").mkdir()
    _git_commit(local, "in/a.md", "in\n", "in")
    _git_commit(local, "out/b.md", "out\n", "out")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _run_git(local, "sparse-checkout", "init", "--cone")
    _run_git(local, "sparse-checkout", "set", "in")
    _git_commit(other, "out/b.md", "remote\n", "remote")
    _run_git(other, "push")
    with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
        Libgit2SyncStore(local, enable_sync=True)


@pytest.mark.parametrize("conflict", [False, True])
def test_incoming_existing_attributes_changes_with_and_without_conflict(
    git_repo_pair: GitRepoPair, tmp_path: Path, conflict: bool
) -> None:
    local = git_repo_pair.local_path
    _git_commit(local, ".gitattributes", "*.md merge=text -filter\n", "attributes")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    if conflict:
        _git_commit(local, "README.md", "local\n", "local")
        _git_commit(other, "README.md", "remote\n", "remote")
    _git_commit(other, ".gitattributes", "*.md merge=union\n", "attributes")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        if conflict:
            assert not result.applied and "GIT_BACKEND=cli" in (result.detail or "")
            assert _git_head(local) == before
        else:
            assert result.applied
            assert (local / ".gitattributes").read_text() == "*.md merge=union\n"
    finally:
        store.close()


def test_origin_head_fallback_for_pull(git_repo_pair: GitRepoPair) -> None:
    local = git_repo_pair.local_path
    _run_git(
        local, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"
    )
    _run_git(local, "checkout", "-b", "other")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        assert store._tracking().name == "refs/remotes/origin/main"
    finally:
        store.close()


@pytest.mark.parametrize("shape", ["shallow", "sparse", "hook", "remote"])
def test_special_repositories_allowed_in_commit_only_mode(
    git_repo_pair: GitRepoPair, tmp_path: Path, shape: str
) -> None:
    local = git_repo_pair.local_path
    if shape == "shallow":
        _git_commit(local, "seed.md", "seed\n", "seed")
        _run_git(local, "push")
        path = tmp_path / "shallow-commit-only"
        _run_git(
            tmp_path,
            "clone",
            "--depth",
            "1",
            git_repo_pair.remote_path.as_uri(),
            str(path),
        )
    elif shape == "sparse":
        path = local
        (local / "in").mkdir()
        _git_commit(local, "in/a.md", "a\n", "a")
        _run_git(local, "sparse-checkout", "init", "--cone")
        _run_git(local, "sparse-checkout", "set", "in")
    else:
        path = local
        if shape == "hook":
            hook = local / ".git/hooks/pre-push"
            hook.write_text("#!/bin/sh\nexit 0\n")
            hook.chmod(0o755)
        else:
            _run_git(local, "remote", "set-url", "origin", "git://example.test/repo")
    store = Libgit2SyncStore(path, enable_sync=False)
    store.close()


@pytest.mark.parametrize(
    "url",
    [
        "git://example.test/repo",
        "custom://example.test/repo",
        "alice@example.test:repo",
        "example.test:repo",
    ],
)
def test_unsupported_sync_transport_refused_with_fallback(
    git_repo_pair: GitRepoPair, url: str
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "remote", "set-url", "origin", url)
    with pytest.raises(ConfigurationError, match="GIT_BACKEND=cli"):
        Libgit2SyncStore(local, enable_sync=True)


def test_path_limited_restore_treats_metacharacters_literally(
    git_repo_pair: GitRepoPair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = git_repo_pair.local_path
    _git_commit(local, "note[1].md", "original\n", "literal")
    _git_commit(local, "note1.md", "tracked sibling\n", "sibling")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "note[1].md", "remote\n", "remote")
    _run_git(other, "push")
    (local / "note1.md").write_text("local sibling edit\n")
    store = Libgit2SyncStore(local, enable_sync=True)
    real = store._repo

    class FailedTransaction:
        def __init__(self):
            self.transaction = real.transaction()

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
        def __getattr__(self, name):
            return getattr(real, name)

        def __getitem__(self, key):
            return real[key]

        def transaction(self):
            return FailedTransaction()

    monkeypatch.setattr(store, "_repo", FailedRepo())
    try:
        assert not store.force_pull().applied
        assert (local / "note[1].md").read_text() == "original\n"
        assert (local / "note1.md").read_text() == "local sibling edit\n"
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("promisor", ["true", "false"])
def test_mutable_promisor_is_checked_before_sync_transport(
    git_repo_pair: GitRepoPair, tmp_path: Path, operation: str, promisor: str
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    other = _git_clone(git_repo_pair, tmp_path)
    store = Libgit2SyncStore(repo_path=local, enable_sync=True)
    try:
        if operation == "pull":
            _git_commit(other, "remote.md", "remote\n", "remote")
            _run_git(other, "push")
        else:
            _git_commit(local, "local.md", "local\n", "local")
        _run_git(local, "config", "remote.origin.promisor", promisor)
        local_refs = _run_git(local, "show-ref")
        remote_refs = _run_git(git_repo_pair.remote_path, "show-ref")
        if operation == "pull":
            result = store.force_pull()
            applied, detail = result.applied, result.detail
        else:
            pushed = store.force_push()
            applied, detail = pushed.applied, pushed.hint
        if promisor == "true":
            assert not applied
            assert detail is not None
            assert "partial" in detail.lower()
            assert "GIT_BACKEND=cli" in detail
            assert _run_git(local, "show-ref") == local_refs
            assert _run_git(git_repo_pair.remote_path, "show-ref") == remote_refs
        else:
            assert applied
    finally:
        store.close()


def test_conflict_preserves_remote_symlink_and_local_regular_bytes(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "README.md", "local body\n", "local edit")
    (other / "README.md").unlink()
    (other / "README.md").symlink_to("target.md")
    _run_git(other, "add", "README.md")
    _run_git(other, "commit", "-m", "remote symlink")
    _run_git(other, "push")
    store = Libgit2SyncStore(repo_path=local, enable_sync=True)
    try:
        result = store.force_pull()
        assert result.applied
        assert _run_git(local, "ls-tree", "HEAD", "README.md").startswith(
            "120000 blob "
        )
        assert _run_git(local, "show", "HEAD:README.md") == "target.md"
        assert (local / "README.md").is_symlink()
        assert (local / "README.md").readlink() == Path("target.md")
        sibling = next(local.glob("README.conflict-mcp-*.md"))
        assert sibling.read_bytes() == b"local body\n"
    finally:
        store.close()


@pytest.mark.parametrize(
    "old_name,new_name", [("README.md", "renamed.md"), ("a [x].md", "b [y].md")]
)
def test_remote_rename_and_conflicting_edit_preserves_reciprocal_versions(
    git_repo_pair: GitRepoPair, tmp_path: Path, old_name: str, new_name: str
) -> None:
    import frontmatter

    local = git_repo_pair.local_path
    _run_git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    base = "alpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\ntheta\niota\nkappa\n"
    _git_commit(local, old_name, base, "base")
    _git_commit(local, "z-clean.md", "clean rename\n", "second base")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, old_name, base.replace("gamma", "local"), "local edit")
    _git_commit(
        local,
        old_name,
        base.replace("gamma", "local").replace("kappa", "later"),
        "later edit",
    )
    _git_commit(local, "local-add.md", "local addition\n", "local add")
    _run_git(other, "mv", old_name, new_name)
    _run_git(other, "mv", "z-clean.md", "z-renamed.md")
    (other / new_name).write_text(base.replace("gamma", "remote"))
    (other / "remote-add.md").write_text("remote addition\n")
    _run_git(other, "add", new_name, "remote-add.md")
    _run_git(other, "commit", "-m", "rename and edit")
    _run_git(other, "push")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        assert result.applied
        original = frontmatter.loads((local / new_name).read_text())
        sibling = local / result.conflict_files[0]
        saved = frontmatter.loads(sibling.read_text())
        assert (
            "remote" in original.content
            and "local" in saved.content
            and "later" in saved.content
        )
        assert original["conflict_with"] == sibling.name
        assert saved["conflict_with"] == new_name
        assert (local / "local-add.md").read_text() == "local addition\n"
        assert (local / "remote-add.md").read_text() == "remote addition\n"
        assert (local / "z-renamed.md").read_text() == "clean rename\n"
        assert store.force_push().applied
        _run_git(other, "pull", "--ff-only")
        assert (other / sibling.name).read_bytes() == sibling.read_bytes()
    finally:
        store.close()


@pytest.mark.parametrize("hook", ["pre-rebase", "post-rewrite"])
def test_configured_rebase_hook_policy_matches_cli(
    git_repo_pair: GitRepoPair, tmp_path: Path, hook: str
) -> None:
    import shlex

    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "local.md", "local\n", "local")
    _git_commit(other, "remote.md", "remote\n", "remote")
    _run_git(other, "push")
    _run_git(local, "fetch")
    marker = tmp_path / "rebase-hook-marker"
    _run_git(local, "config", "hook.fixture.event", hook)
    command = shlex.quote(f"touch {shlex.quote(str(marker))}")
    _run_git(local, "config", "hook.fixture.command", f"sh -c {command}")
    _run_git(local, "rebase", "origin/main")
    assert marker.exists()
    with pytest.raises(ConfigurationError, match=r"GIT_BACKEND=cli"):
        Libgit2SyncStore(local, enable_sync=True)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_untracked_directory_file_collision_has_actionable_detail(
    git_repo_pair: GitRepoPair, tmp_path: Path, kind: str
) -> None:
    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    if kind == "file":
        (local / "clash").write_text("keep local\n")
        (other / "clash").mkdir()
        _git_commit(other, "clash/note.md", "remote\n", "remote directory")
    else:
        (local / "clash").mkdir()
        (local / "clash/keep.md").write_text("keep local\n")
        _git_commit(other, "clash", "remote\n", "remote file")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        assert not result.applied and _git_head(local) == before
        assert "untracked" in (result.detail or "").lower()
        assert "move" in (result.detail or "").lower()
        kept = local / ("clash" if kind == "file" else "clash/keep.md")
        assert kept.read_text() == "keep local\n"
    finally:
        store.close()


def test_rename_with_actual_local_deletion_refuses_without_state_loss(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    local = git_repo_pair.local_path
    _run_git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    base = "alpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\ntheta\niota\nkappa\n"
    _git_commit(local, "README.md", base, "base")
    _git_commit(local, "deleted.md", "original\n", "base deletion")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _run_git(local, "rm", "README.md")
    _run_git(local, "commit", "-m", "local changes")
    _run_git(other, "mv", "README.md", "renamed.md")
    (other / "renamed.md").write_text(base.replace("gamma", "remote"))
    (other / "deleted.md").write_text("remote change\n")
    _run_git(other, "add", "renamed.md", "deleted.md")
    _run_git(other, "commit", "-m", "remote changes")
    _run_git(other, "push")
    before = _git_head(local)
    index_before = _run_git(local, "ls-files", "--stage")
    status_before = _run_git(local, "status", "--porcelain")
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        result = store.force_pull()
        assert not result.applied and "deletion" in (result.detail or "").lower()
        assert _git_head(local) == before
        assert _run_git(local, "ls-files", "--stage") == index_before
        assert _run_git(local, "status", "--porcelain") == status_before
    finally:
        store.close()


def test_explicit_sync_discovers_checkout_created_after_startup(
    git_repo_pair: GitRepoPair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit sync discovers a checkout created in an existing source directory."""
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    import markdown_vault_mcp.git.libgit2 as native

    with monkeypatch.context() as patch:
        patch.setattr(native, "_open", lambda _path, _pg: None)
        store = Libgit2SyncStore(source, token="fixture", enable_sync=True)
    try:
        _run_git(tmp_path, "clone", str(git_repo_pair.remote_path), str(source))
        _run_git(source, "config", "user.name", "Fixture")
        _run_git(source, "config", "user.email", "fixture@example.invalid")
        (source / "untracked.md").write_text("untracked\n")
        before_health = store.sync_health()
        dry_push = store.force_push(dry_run=True)
        assert not dry_push.applied and dry_push.reason == "dry_run_unsupported"
        assert store.sync_health() == before_health
        other = _git_clone(git_repo_pair, tmp_path)
        _git_commit(other, "remote.md", "remote body\n", "remote")
        _run_git(other, "push")
        result = store.force_pull()
        assert result.applied and result.reason is None
        assert (source / "remote.md").read_text() == "remote body\n"
        assert (health := store.sync_health()) is not None
        assert health.reason == "uncommitted_changes"
        pushed = store.force_push()
        assert pushed.applied and pushed.reason is None
        assert (health := store.sync_health()) is not None
        assert health.reason == "uncommitted_changes"
        before = _git_head(source)
        note = source / "local.md"
        note.write_text("local body\n")
        store(note, "local body\n", "write")
        assert _git_head(source) != before
    finally:
        store.close()


def test_dry_run_attribute_guard_survives_breadcrumb_reader(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A breadcrumb lookup must not change incoming attribute evaluation."""
    import markdown_vault_mcp.git.libgit2_sync as sync

    local = git_repo_pair.local_path
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, ".gitattributes", "*.md filter=lfs\n", "LFS policy")
    _run_git(other, "push")
    before = _git_head(local)
    store = Libgit2SyncStore(local, enable_sync=True)
    try:
        original_check = sync._check_tree

        def with_reader(repo, commit, baseline=None):
            assert store.committed_revision(local, local / "README.md") is not None
            return original_check(repo, commit, baseline)

        monkeypatch.setattr(sync, "_check_tree", with_reader)
        result = store.force_pull(dry_run=True)
        assert result.reason == "conflict_resolution_failed"
        assert "filter" in (result.detail or "")
        assert _git_head(local) == before
    finally:
        store.close()


@pytest.mark.parametrize("dry_run", [False, True])
def test_push_late_unsupported_checkout_preserves_dry_run_health(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dry_run: bool,
) -> None:
    import markdown_vault_mcp.git.libgit2 as native

    source = tmp_path / "late-source"
    source.mkdir()
    with monkeypatch.context() as patch:
        patch.setattr(native, "_open", lambda _path, _pg: None)
        store = Libgit2SyncStore(source, token="fixture", enable_sync=True)
    try:
        _run_git(tmp_path, "clone", str(git_repo_pair.remote_path), str(source))
        _run_git(source, "config", "commit.gpgsign", "true")
        before = store.sync_health()
        result = store.force_push(dry_run=dry_run)
        assert not result.applied and result.reason == "push_failed"
        assert "GIT_BACKEND=cli" in (result.hint or "")
        if dry_run:
            assert store.sync_health() == before
        else:
            assert store.sync_health() is not None
            assert store.sync_health().reason == "push_failed"
    finally:
        store.close()


def test_malformed_frontmatter_conflict_is_logged_and_kept(
    git_repo_pair: GitRepoPair, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    local = git_repo_pair.local_path
    _run_git(local, "config", "commit.gpgsign", "false")
    _run_git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    other = _git_clone(git_repo_pair, tmp_path)
    malformed = "---\nbad: [unclosed\n---\n"
    _git_commit(local, "README.md", malformed + "local body\n", "local")
    _git_commit(other, "README.md", malformed + "remote body\n", "remote")
    _run_git(other, "push")
    store = Libgit2SyncStore(repo_path=local, enable_sync=True)
    try:
        with caplog.at_level(logging.WARNING):
            result = store.force_pull()
    finally:
        store.close()
    assert result.reason == "conflicts_resolved_with_siblings"
    sibling = local / result.conflict_files[0]
    assert "local body" in sibling.read_text() and "unclosed" in sibling.read_text()
    assert "remote body" in (local / "README.md").read_text()
    assert "unclosed" in (local / "README.md").read_text()
    events = [
        r
        for r in caplog.records
        if str(r.msg).startswith("git_conflict_frontmatter_parse_failed")
    ]
    assert sorted(r.args[0] for r in events) == sorted(
        [result.conflict_files[0], "README.md"]
    )
    assert all(r.exc_info for r in events)


def _assert_refused_checkout_unchanged(
    local: Path, head: str, index: str, status: str
) -> None:
    assert _git_head(local) == head
    assert _run_git(local, "ls-files", "--stage") == index
    assert _run_git(local, "status", "--porcelain") == status


def _assert_conflict_published(
    store: Libgit2SyncStore, local: Path, other: Path, sibling: Path, original: Path
) -> None:
    assert original.is_file()
    assert store.force_push().applied
    _run_git(other, "pull", "--ff-only")
    assert (other / sibling.relative_to(local)).read_bytes() == sibling.read_bytes()
    assert (other / original.relative_to(local)).read_bytes() == original.read_bytes()


@pytest.mark.parametrize("ending", ["rename", "multiple", "delete"])
@pytest.mark.parametrize("check", ["content", "link"])
def test_conflict_then_local_rename_preserves_final_version(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
    check: str,
) -> None:
    """A later rename must not lose the final local conflict version."""
    import frontmatter

    local = git_repo_pair.local_path
    base = "start\n" + "middle\n" * 20 + "end\n"
    _git_commit(local, "note.md", base, "base")
    _run_git(local, "push")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(local, "note.md", base.replace("start", "local"), "local edit")
    _run_git(local, "mv", "note.md", "renamed.md")
    final_local = base.replace("start", "local").replace("end", "final edit")
    (local / "renamed.md").write_text(final_local)
    _run_git(local, "add", "renamed.md")
    _run_git(local, "commit", "-qm", "rename and finish")
    if ending == "multiple":
        _run_git(local, "mv", "renamed.md", "final.md")
        _run_git(local, "commit", "-qm", "rename again")
    elif ending == "delete":
        _run_git(local, "rm", "renamed.md")
        _run_git(local, "commit", "-qm", "delete renamed note")
    _git_commit(other, "note.md", base.replace("start", "remote"), "remote edit")
    _run_git(other, "push")
    before = _git_head(local)
    index_before = _run_git(local, "ls-files", "--stage")
    status_before = _run_git(local, "status", "--porcelain")
    store = Libgit2SyncStore(local, enable_sync=True)
    checkouts = []
    apply = store._apply
    monkeypatch.setattr(
        store, "_apply", lambda *args: checkouts.append(args) or apply(*args)
    )
    try:
        result = store.force_pull()
        if not result.applied:
            assert not checkouts
            detail = result.detail or ""
            assert "manual" in detail.lower() or "GIT_BACKEND=cli" in detail
            _assert_refused_checkout_unchanged(
                local, before, index_before, status_before
            )
            return
        assert ending != "delete", "deletion must be refused if identity is uncertain"
        assert len(result.conflict_files) == 1
        sibling = local / result.conflict_files[0]
        saved = frontmatter.loads(sibling.read_text())
        original = local / saved["conflict_with"]
        if check == "content":
            assert saved.content == final_local.strip()
        else:
            assert original.is_file()
            assert (
                frontmatter.loads(original.read_text())["conflict_with"] == sibling.name
            )
        _assert_conflict_published(store, local, other, sibling, original)
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["clone", "pull", "push", "rejection_refresh"])
def test_native_transport_requests_proxy_auto_detection(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Every native transport entry point asks pygit2 to discover proxies."""
    calls = []
    if operation == "clone":
        original_clone = pygit2.clone_repository

        def clone(url, path, **kwargs):
            calls.append(("clone", kwargs.get("proxy")))
            return original_clone(url, path, **kwargs)

        monkeypatch.setattr(pygit2, "clone_repository", clone)
        store = Libgit2SyncStore(
            tmp_path / "managed", repo_url=str(git_repo_pair.remote_path), managed=True
        )
        store.close()
    else:
        local = git_repo_pair.local_path
        if operation in ("push", "rejection_refresh"):
            _git_commit(local, "local.md", "local\n", "local")
        store = Libgit2SyncStore(local, enable_sync=True)
        remote = store._origin()
        assert remote is not None

        class CapturedRemote:
            def __getattr__(self, name):
                return getattr(remote, name)

            def fetch(self, *args, **kwargs):
                calls.append(("fetch", kwargs.get("proxy")))
                return remote.fetch(*args, **kwargs)

            def push(self, *args, **kwargs):
                calls.append(("push", kwargs.get("proxy")))
                if operation == "rejection_refresh":
                    raise RuntimeError("synthetic rejection")
                return remote.push(*args, **kwargs)

        monkeypatch.setattr(store, "_origin", lambda: CapturedRemote())
        try:
            if operation == "pull":
                store.force_pull()
            else:
                store.force_push()
        finally:
            store.close()
    expected = {
        "clone": [("clone", True)],
        "pull": [("fetch", True)],
        "push": [("push", True)],
        "rejection_refresh": [("push", True), ("fetch", True)],
    }
    assert calls == expected[operation]
