"""The index follows git HEAD even when a post-pull reindex is lost (#1532).

Real repositories throughout: a bare remote, the vault's clone, and a sibling
clone standing in for another machine that pushes renames and deletions.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
from markdown_vault_mcp.git import GitWriteStrategy
from markdown_vault_mcp.vault import Vault
from tests.fixtures.git import GitRepoPair, _run_git

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _paths(vault: Vault) -> set[str]:
    return {note.path for note in vault.reader.list_documents()}


def _sibling(pair: GitRepoPair, name: str = "sibling") -> Path:
    """Clone the remote as another machine would."""
    sibling = pair.remote_path.parent / name
    _run_git(pair.remote_path.parent, "clone", str(pair.remote_path), name)
    _run_git(sibling, "config", "user.email", "other@example.com")
    _run_git(sibling, "config", "user.name", "Other")
    return sibling


@pytest.fixture
def vault(git_repo_pair: GitRepoPair, tmp_path: Path) -> Iterator[Vault]:
    local = git_repo_pair.local_path
    (local / "keep.md").write_text("# Keep\n")
    (local / "gone.md").write_text("# Gone\n")
    (local / "old-name.md").write_text("# Moved\n")
    _run_git(local, "add", ".")
    _run_git(local, "commit", "-m", "notes")
    _run_git(local, "push", "origin", "main")

    strategy = GitWriteStrategy(
        token=None, push_delay_s=0, git_lfs=False, repo_path=local
    )
    (tmp_path / "state").mkdir()
    v = Vault(
        source_dir=local,
        settings=VaultSettings(
            read_only=False,
            index_path=tmp_path / "state" / "fts.db",
            state_path=tmp_path / "state" / "state.json",
        ),
        git_strategy=strategy,
        on_write=strategy,
    )
    v.index.build_index()
    assert v.reconcile_index_with_head(source="test") == "reindexed"
    yield v
    v.close()


def _push_rename_and_delete(pair: GitRepoPair) -> None:
    sibling = _sibling(pair)
    _run_git(sibling, "rm", "-q", "gone.md")
    _run_git(sibling, "mv", "old-name.md", "new-name.md")
    _run_git(sibling, "commit", "-m", "rename and delete elsewhere")
    _run_git(sibling, "push", "origin", "main")


def test_a_lost_reindex_after_a_pull_is_retried(
    vault: Vault, git_repo_pair: GitRepoPair
) -> None:
    """The #1532 shape: the pull fast-forwards, its one reindex is lost, and
    every later pull finds nothing to pull.  Reconcile must still catch up."""
    _push_rename_and_delete(git_repo_pair)

    pulled = vault.force_pull()
    assert pulled is not None and pulled.applied
    assert pulled.from_sha != pulled.to_sha

    with patch.object(
        vault.index, "reindex", side_effect=RuntimeError("simulated loss")
    ):
        assert vault.reconcile_index_with_head(source="test") == "failed"
    assert "gone.md" in _paths(vault)

    again = vault.force_pull()
    assert again is not None and again.from_sha == again.to_sha

    assert vault.reconcile_index_with_head(source="test") == "reindexed"
    assert _paths(vault) == {"README.md", "keep.md", "new-name.md"}


def test_a_head_moved_outside_any_pull_is_reconciled(
    vault: Vault, git_repo_pair: GitRepoPair
) -> None:
    """A commit made in the vault's clone by another process moves HEAD with
    no pull at all; the next reconcile picks it up."""
    local = git_repo_pair.local_path
    _run_git(local, "rm", "-q", "gone.md")
    _run_git(local, "commit", "-m", "deleted by hand")

    assert vault.reconcile_index_with_head(source="test") == "reindexed"
    assert "gone.md" not in _paths(vault)


def test_the_servers_own_commit_does_not_force_a_rescan(vault: Vault) -> None:
    vault.writer.write("fresh.md", "# Fresh\n")
    assert vault._write_callback.drain()

    with patch.object(vault.index, "reindex") as reindex:
        assert vault.reconcile_index_with_head(source="test") == "current"
    reindex.assert_not_called()
    assert "fresh.md" in _paths(vault)


def test_the_strategy_reports_its_own_commits(git_repo_pair: GitRepoPair) -> None:
    local = git_repo_pair.local_path
    strategy = GitWriteStrategy(
        token=None, push_delay_s=0, git_lfs=False, enable_push=False, repo_path=local
    )
    seen: list[tuple[str, str]] = []
    strategy.set_commit_observer(lambda parent, new: seen.append((parent, new)))
    before = _run_git(local, "rev-parse", "HEAD").strip()

    note = local / "n.md"
    note.write_text("# N\n")
    strategy(note, "# N\n", "write")

    after = _run_git(local, "rev-parse", "HEAD").strip()
    assert seen == [(before, after)]
    strategy.close()


def test_the_pull_loop_ticks_even_when_head_does_not_move(
    git_repo_pair: GitRepoPair,
) -> None:
    strategy = GitWriteStrategy(
        token=None,
        push_delay_s=0,
        git_lfs=False,
        enable_push=False,
        repo_path=git_repo_pair.local_path,
    )
    ticked = threading.Event()
    pulled: list[str] = []

    with patch.object(strategy, "sync_once", return_value=False):
        strategy.start(
            repo_path=git_repo_pair.local_path,
            pull_interval_s=3600,
            on_pull=lambda: pulled.append("x"),
            on_tick=ticked.set,
        )
        try:
            assert ticked.wait(timeout=5.0)
        finally:
            strategy.close()
    assert pulled == []


def test_a_vault_without_git_has_nothing_to_reconcile(tmp_path: Path) -> None:
    from concurrent.futures import Future

    (tmp_path / "state").mkdir()
    v = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(index_path=tmp_path / "state" / "fts.db"),
    )
    try:
        boot: Future[object] = Future()
        v.adopt_boot_reindex(boot, "abc")
        v.mark_index_reconciled("abc")

        assert v.git_head() is None
        assert v.reconcile_index_with_head(source="test") == "current"
    finally:
        v.close()


def test_an_unreadable_head_reads_as_none(tmp_path: Path) -> None:
    """A strategy pointed at a directory git cannot read yields no HEAD."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    (tmp_path / "state").mkdir()
    strategy = GitWriteStrategy(token=None, git_lfs=False, enable_push=False)
    v = Vault(
        source_dir=not_a_repo,
        settings=VaultSettings(index_path=tmp_path / "state" / "fts.db"),
        git_strategy=strategy,
    )
    try:
        assert v.git_head() is None
        assert v.reconcile_index_with_head(source="test") == "current"
    finally:
        v.close()
