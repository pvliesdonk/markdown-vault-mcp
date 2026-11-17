"""Regressions for native store identity and effective capabilities."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git.libgit2 import Libgit2Store
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.libgit2 import _git
from tests.fixtures.libgit2 import isolated_repo as isolated_repo


def test_vault_reused_name_diff(isolated_repo):
    repo = isolated_repo
    store = Libgit2Store(repo)
    vault = Vault(
        source_dir=repo,
        settings=VaultSettings(read_only=False),
        git_strategy=store,
        on_write=store,
    )
    try:
        vault.writer.write("note.md", "OLD DIFFERENT NOTE\n")
        vault._write_callback.drain()
        old = _git(repo, "rev-parse", "HEAD").strip()
        vault.writer.delete("note.md")
        vault._write_callback.drain()
        vault.writer.write("note.md", "NEW NOTE\n")
        vault._write_callback.drain()
        patch = vault.reader.get_diff("note.md", since_sha=old)
        assert "OLD DIFFERENT NOTE" not in patch
        assert "+NEW NOTE" in patch
    finally:
        vault.close()
        store.close()


def test_existing_custom_filter_refused_at_open(isolated_repo):
    repo = isolated_repo
    _git(repo, "config", "filter.upper.clean", "tr a-z A-Z")
    (repo / ".gitattributes").write_text("*.md filter=upper\n")
    with pytest.raises(ConfigurationError, match="filter"):
        Libgit2Store(repo)


def test_vault_revert_to_head_clears_stale_staged_content(isolated_repo):
    repo = isolated_repo
    note = repo / "note.md"
    note.write_text("head\n")
    _git(repo, "add", "--", "note.md")
    _git(repo, "commit", "-qm", "seed")
    note.write_text("operator staged\n")
    _git(repo, "add", "--", "note.md")
    store = Libgit2Store(repo)
    vault = Vault(
        source_dir=repo,
        settings=VaultSettings(read_only=False),
        git_strategy=store,
        on_write=store,
    )
    try:
        vault.writer.write("note.md", "head\n")
        vault._write_callback.drain()
        assert _git(repo, "show", ":note.md") == "head\n"
    finally:
        vault.close()
        store.close()


def test_reference_transaction_hook_refused_at_open(isolated_repo):
    hook = isolated_repo / ".git/hooks/reference-transaction"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(ConfigurationError, match="reference-transaction"):
        Libgit2Store(isolated_repo)


def test_head_aggregate_diff_does_not_scan_ancient_history(isolated_repo, monkeypatch):
    import markdown_vault_mcp.git.libgit2_history as history

    repo = isolated_repo
    note = repo / "note.md"
    store = Libgit2Store(repo)
    for n in range(40):
        note.write_text(f"{n}\n")
        store(note, "", "write")
    head = _git(repo, "rev-parse", "HEAD").strip()
    original = history._entry
    seen = []

    def count(*args):
        seen.append(1)
        return original(*args)

    monkeypatch.setattr(history, "_entry", count)
    assert store.get_file_diff(repo, note, head, False) == ""
    assert len(seen) < 15


def test_unconfigured_filter_allows_write(isolated_repo):
    (isolated_repo / ".gitattributes").write_text("*.md filter=nothing\n")
    store = Libgit2Store(isolated_repo)
    note = isolated_repo / "note.md"
    note.write_text("note\n")
    store(note, "", "write")
    assert _git(isolated_repo, "show", "HEAD:note.md") == "note\n"


@pytest.mark.parametrize("limit", [-1, 0, 1, 100, 1000])
def test_limits_match_cli(isolated_repo, limit):
    note = isolated_repo / "note.md"
    native = Libgit2Store(isolated_repo)
    cli = GitWriteStrategy(repo_path=isolated_repo, enable_push=False, git_lfs=False)
    before = _git(isolated_repo, "rev-parse", "HEAD").strip()
    for n in range(4):
        note.write_text(f"{n}\n")
        native(note, "", "write")
    assert len(native.get_file_history(isolated_repo, note, None, limit)) == len(
        cli.get_file_history(isolated_repo, note, None, limit)
    )
    assert len(
        native.get_file_diff(isolated_repo, note, before, True, limit=limit)
    ) == len(cli.get_file_diff(isolated_repo, note, before, True, limit=limit))


@pytest.mark.parametrize("hook_name", ["reference-transaction", "post-index-change"])
def test_hook_added_after_open_is_refused(isolated_repo, hook_name):
    store = Libgit2Store(isolated_repo)
    hook = isolated_repo / ".git/hooks" / hook_name
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    note = isolated_repo / "note.md"
    note.write_text("note\n")
    before = _git(isolated_repo, "rev-parse", "HEAD")
    with pytest.raises(ConfigurationError, match=hook_name):
        store(note, "", "write")
    assert _git(isolated_repo, "rev-parse", "HEAD") == before


def test_failed_health_refresh_preserves_original_exception(isolated_repo, monkeypatch):
    store = Libgit2Store(isolated_repo)
    note = isolated_repo / "note.md"
    note.write_text("note\n")
    (isolated_repo / ".git/index.lock").write_text("held")

    def fail():
        raise RuntimeError("health failed")

    monkeypatch.setattr(store, "_observe", fail)
    with pytest.raises(FileExistsError):
        store(note, "", "write")


def _both(repo):
    return (
        Libgit2Store(repo),
        GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False),
    )


def _commit(repo, msg):
    _git(repo, "add", "-A", "--", "note.md")
    _git(repo, "commit", "-qm", msg, "--", "note.md")
    return _git(repo, "rev-parse", "HEAD").strip()


def test_reuse_linear_matches_cli(isolated_repo):
    r = isolated_repo
    n = r / "note.md"
    native, cli = _both(r)
    n.write_text("OLD DIFFERENT NOTE\n")
    first = _commit(r, "a")
    n.unlink()
    _commit(r, "b")
    n.write_text("NEW NOTE\n")
    _commit(r, "c")
    a = native.get_file_diff(r, n, first, False)
    b = cli.get_file_diff(r, n, first, False)
    assert a == b and "OLD DIFFERENT" not in a


def test_divergent_same_note_matches_cli(isolated_repo):
    r = isolated_repo
    n = r / "note.md"
    native, cli = _both(r)
    n.write_text("shared\n")
    _commit(r, "a")
    _git(r, "branch", "side")
    n.write_text("shared\nmain\n")
    _commit(r, "m")
    _git(r, "checkout", "-q", "side")
    n.write_text("shared\nside\n")
    side = _commit(r, "s")
    _git(r, "checkout", "-q", "-")
    assert native.get_file_diff(r, n, side, False) == cli.get_file_diff(
        r, n, side, False
    )


def test_divergent_independently_born_name_matches_cli(isolated_repo):
    r = isolated_repo
    n = r / "note.md"
    native, cli = _both(r)
    _git(r, "branch", "side")
    n.write_text("main note\n")
    _commit(r, "m")
    _git(r, "checkout", "-q", "side")
    n.write_text("side note\n")
    side = _commit(r, "s")
    _git(r, "checkout", "-q", "-")
    a = native.get_file_diff(r, n, side, False)
    b = cli.get_file_diff(r, n, side, False)
    print("DIVERGENT-BORN native:", repr(a)[:150], "\nCLI:", repr(b)[:150])
    assert a == b


def test_rename_then_reuse_matches_cli(isolated_repo):
    r = isolated_repo
    n = r / "note.md"
    old = r / "old.md"
    native, cli = _both(r)
    old.write_text("# Title\nbody body body\n")
    _git(r, "add", "old.md")
    _git(r, "commit", "-qm", "o")
    first = _git(r, "rev-parse", "HEAD").strip()
    _git(r, "mv", "old.md", "note.md")
    _git(r, "commit", "-qm", "mv")
    n.unlink()
    _git(r, "commit", "-qam", "rm")
    n.write_text("# Other\nreused name\n")
    _commit(r, "re")
    assert native.get_file_diff(r, n, first, False) == cli.get_file_diff(
        r, n, first, False
    )
    assert len(native.get_file_diff(r, n, first, True)) == len(
        cli.get_file_diff(r, n, first, True)
    )


def test_effective_empty_override_not_refused(isolated_repo):
    r = isolated_repo
    cfg = r / ".git" / "config"
    cfg.write_text(
        cfg.read_text()
        + '[filter "x"]\n\tclean = tr a-z A-Z\n[filter "x"]\n\tclean =\n'
    )
    (r / ".gitattributes").write_text("*.md filter=x\n")
    store = Libgit2Store(r)
    n = r / "note.md"
    n.write_text("low\n")
    store(n, "", "write")
    assert _git(r, "show", "HEAD:note.md") == "low\n"


@pytest.mark.parametrize("key", ["clean", "process"])
def test_effective_driver_refused_open_and_write(isolated_repo, key):
    r = isolated_repo
    (r / ".gitattributes").write_text("*.md filter=x\n")
    (r / "t.md").write_text("t\n")
    _git(r, "add", "--", ".gitattributes", "t.md")
    _git(r, "commit", "-qm", "t")
    store = Libgit2Store(r)  # no driver configured: allowed
    _git(r, "config", f"filter.x.{key}", "cat")
    with pytest.raises(ConfigurationError, match="filter"):
        Libgit2Store(r)
    n = r / "note.md"
    n.write_text("n\n")
    with pytest.raises(ConfigurationError, match="filter"):
        store(n, "", "write")


def test_refused_vault_write_updates_and_recovers_health(isolated_repo):
    _git(isolated_repo, "reset", "--hard", "-q", "HEAD")
    store = Libgit2Store(isolated_repo)
    vault = Vault(
        source_dir=isolated_repo,
        settings=VaultSettings(read_only=False),
        git_strategy=store,
        on_write=store,
    )
    try:
        attrs = isolated_repo / ".gitattributes"
        attrs.write_text("*.md filter=upper\n")
        _git(isolated_repo, "config", "filter.upper.clean", "tr a-z A-Z")
        vault.writer.write("note.md", "first\n")
        assert vault._write_callback.drain()
        assert store.sync_health().reason == "uncommitted_changes"
        attrs.unlink()
        vault.writer.write("note.md", "second\n")
        assert vault._write_callback.drain()
        assert store.sync_health() is None
    finally:
        vault.close()
        store.close()


def test_noop_reconciles_index_without_observer(isolated_repo):
    note = isolated_repo / "note.md"
    note.write_text("head\n")
    _git(isolated_repo, "add", "note.md")
    _git(isolated_repo, "commit", "-qm", "note", "--", "note.md")
    note.write_text("staged\n")
    _git(isolated_repo, "add", "note.md")
    store = Libgit2Store(isolated_repo)
    observed = []
    store.set_commit_observer(lambda before, after: observed.append((before, after)))
    before = _git(isolated_repo, "rev-parse", "HEAD")
    note.write_text("head\n")
    store(note, "", "write")
    assert _git(isolated_repo, "rev-parse", "HEAD") == before
    assert _git(isolated_repo, "show", ":note.md") == "head\n"
    assert _git(isolated_repo, "show", ":unrelated.md") == "staged\n"
    assert not observed


def test_linked_worktree_effective_filter_override(isolated_repo, tmp_path):
    _git(isolated_repo, "config", "extensions.worktreeConfig", "true")
    _git(isolated_repo, "config", "filter.x.clean", "cat")
    linked = tmp_path / "linked"
    _git(isolated_repo, "worktree", "add", "-qb", "side", str(linked))
    _git(linked, "config", "--worktree", "filter.x.clean", "")
    (linked / ".gitattributes").write_text("*.md filter=x\n")
    store = Libgit2Store(linked)
    note = linked / "note.md"
    note.write_text("unchanged\n")
    store(note, "", "write")
    assert _git(linked, "show", "HEAD:note.md") == "unchanged\n"
    _git(linked, "config", "--worktree", "filter.x.clean", "cat")
    note.write_text("changed\n")
    with pytest.raises(ConfigurationError, match="filter"):
        store(note, "", "write")


def test_symlink_note_history_and_revision_match_cli(isolated_repo):
    from markdown_vault_mcp.git.types import RevisionQuery

    target = isolated_repo / "target.md"
    target.write_text("first\n")
    link = isolated_repo / "link.md"
    link.symlink_to("target.md")
    _git(isolated_repo, "add", "link.md", "target.md")
    _git(isolated_repo, "commit", "-qm", "add links")
    ref = _git(isolated_repo, "rev-parse", "HEAD").strip()
    target.write_text("second\n")
    _git(isolated_repo, "commit", "-qam", "edit target")
    native, cli = _both(isolated_repo)
    for store in (native, cli):
        revision = store.get_file_at_ref(RevisionQuery(isolated_repo, link, ref, 0))
        assert revision.content == "first\n"
        assert revision.historical_path == "target.md"
    assert native.committed_revision(isolated_repo, link) == cli.committed_revision(
        isolated_repo, link
    )
    assert native.get_file_history(
        isolated_repo, link, None, 10
    ) == cli.get_file_history(isolated_repo, link, None, 10)
    assert native.get_file_diff(isolated_repo, link, ref, False) == cli.get_file_diff(
        isolated_repo, link, ref, False
    )


def test_symlink_outside_repository_revision_is_refused(isolated_repo, tmp_path):
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.git.types import RevisionQuery

    outside = tmp_path / "outside.md"
    outside.write_text("outside\n")
    link = isolated_repo / "link.md"
    link.symlink_to(outside)
    ref = _git(isolated_repo, "rev-parse", "HEAD").strip()
    for store in _both(isolated_repo):
        with pytest.raises(InvalidRequestError, match="outside"):
            store.get_file_at_ref(RevisionQuery(isolated_repo, link, ref, 0))
        with pytest.raises(InvalidRequestError, match="outside"):
            store.committed_revision(isolated_repo, link)


def test_large_and_unbounded_limits_match_cli(isolated_repo):
    import subprocess

    stream = []
    for i in range(105):
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
    native, cli = _both(isolated_repo)
    note = isolated_repo / "note.md"
    for limit in (100, 1000):
        assert (
            len(native.get_file_history(isolated_repo, note, None, limit))
            == len(cli.get_file_history(isolated_repo, note, None, limit))
            == 100
        )
    first = _git(isolated_repo, "rev-list", "--max-parents=0", "HEAD").strip()
    for limit, count in ((1000, 100), (None, 104)):
        assert (
            len(native.get_file_diff(isolated_repo, note, first, True, limit=limit))
            == len(cli.get_file_diff(isolated_repo, note, first, True, limit=limit))
            == count
        )


def test_symlink_target_outside_vault_is_refused(isolated_repo):
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.git.types import RevisionQuery

    vault = isolated_repo / "vault"
    vault.mkdir()
    target = isolated_repo / "target.md"
    target.write_text("outside vault\n")
    link = vault / "link.md"
    link.symlink_to("../target.md")
    _git(isolated_repo, "add", "target.md", "vault/link.md")
    _git(isolated_repo, "commit", "-qm", "links")
    ref = _git(isolated_repo, "rev-parse", "HEAD").strip()
    for store in _both(isolated_repo):
        with pytest.raises(InvalidRequestError):
            store.get_file_at_ref(RevisionQuery(vault, link, ref, 0))
