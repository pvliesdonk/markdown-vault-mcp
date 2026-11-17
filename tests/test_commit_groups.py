"""Commit groups share the existing callback worker and Git strategy."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.write_callback import WriteCallbackDispatcher


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_group_commits_multiple_writes_on_close(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "commit", "--allow-empty", "-m", "seed")
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    try:
        group = dispatcher.open_group("Organize notes", None)
        for name in ("one.md", "two.md"):
            with dispatcher.bind_group(group.handle, None):
                note = repo / name
                note.write_text(name, encoding="utf-8")
                dispatcher.fire(note, name, "write")
                assert note.read_text(encoding="utf-8") == name
        assert git(repo, "log", "-1", "--format=%s") == "seed"
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "committed"
        assert result.sha == git(repo, "rev-parse", "HEAD")
        assert result.files == 2
        assert git(repo, "log", "-1", "--format=%s") == "Organize notes"
    finally:
        dispatcher.close()
        strategy.close()


def test_group_handles_reject_nested_foreign_and_closed() -> None:
    import pytest

    from markdown_vault_mcp._commit_groups import CommitGroupError
    from markdown_vault_mcp._identity import Principal

    dispatcher = WriteCallbackDispatcher(None)
    alice = Principal("alice", None, None, "human")
    bob = Principal("bob", None, None, "human")
    group = dispatcher.open_group("first", alice)
    other = dispatcher.open_group("second", alice)
    with dispatcher.bind_group(group.handle, alice):
        with pytest.raises(CommitGroupError):
            dispatcher.open_group("nested", alice)
        with pytest.raises(CommitGroupError):
            dispatcher.bind_group(other.handle, alice).__enter__()
        with pytest.raises(CommitGroupError):
            dispatcher.close_group(group.handle, alice)
    with pytest.raises(CommitGroupError):
        dispatcher.close_group(group.handle, bob)
    assert dispatcher.close_group(group.handle, alice).result().status == "no_changes"
    with pytest.raises(CommitGroupError):
        dispatcher.close_group(group.handle, alice)
    dispatcher.close_group(other.handle, alice)
    dispatcher.close()


def test_single_group_no_changes_and_drain(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "commit", "--allow-empty", "-m", "seed")
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    try:
        group = dispatcher.open_group("One subject", None)
        path = repo / "note.md"
        with dispatcher.bind_group(group.handle, None):
            path.write_text("one", encoding="utf-8")
            dispatcher.fire(path, "one", "write")
        assert dispatcher.drain()
        assert git(repo, "log", "-1", "--format=%s") == "One subject"
        with dispatcher.bind_group(group.handle, None):
            path.write_text("two", encoding="utf-8")
            dispatcher.fire(path, "two", "write")
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "committed"
        assert result.files == 2
        assert git(repo, "log", "-2", "--format=%s").splitlines() == [
            "One subject",
            "One subject",
        ]
        unchanged = dispatcher.open_group("no change", None)
        with dispatcher.bind_group(unchanged.handle, None):
            dispatcher.fire(path, "two", "write")
        result = dispatcher.close_group(unchanged.handle, None).result(timeout=5)
        assert result.status == "no_changes"
        assert result.sha is None
    finally:
        dispatcher.close()
        strategy.close()


def test_vault_indexes_writes_before_group_close(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections import VaultSettings
    from markdown_vault_mcp.vault import Vault

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "commit", "--allow-empty", "-m", "seed")
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=repo,
    )
    vault = Vault(
        source_dir=repo, settings=VaultSettings(read_only=False), on_write=strategy
    )
    try:
        dispatcher = vault._write_callback
        group = dispatcher.open_group("Indexed write", None)
        with dispatcher.bind_group(group.handle, None):
            vault.writer.write("test.md", "# Quasar\n\nuniquewordquasar")
        assert vault.reader.read("test.md") is not None
        assert vault.index.wait_for_drain(timeout=10)
        assert vault.reader.search("uniquewordquasar", mode="keyword")
        assert git(repo, "log", "-1", "--format=%s") == "seed"
        assert (
            dispatcher.close_group(group.handle, None).result(timeout=5).status
            == "committed"
        )
    finally:
        vault.close()
        strategy.close()


def test_failed_group_reports_failure(tmp_path: Path) -> None:
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    try:
        group = dispatcher.open_group("fails", None)
        note = repo / "note.md"
        note.write_text("content", encoding="utf-8")
        with dispatcher.bind_group(group.handle, None):
            dispatcher.fire(note, "content", "write")
        with patch(
            "markdown_vault_mcp.git.strategy._commit_staged",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            assert (
                dispatcher.close_group(group.handle, None).result(timeout=5).status
                == "failed"
            )
    finally:
        dispatcher.close()
        strategy.close()


@given(st.lists(st.integers(min_value=0, max_value=3), min_size=1, max_size=8))
@settings(
    max_examples=12,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
)
def test_repeated_paths_count_once(tmp_path: Path, names: list[int]) -> None:
    import uuid

    repo = tmp_path / uuid.uuid4().hex
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "commit", "--allow-empty", "-m", "seed")
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    try:
        group = dispatcher.open_group("Many writes", None)
        for index, name in enumerate(names):
            with dispatcher.bind_group(group.handle, None):
                path = repo / f"{name}.md"
                path.write_text(str(index), encoding="utf-8")
                dispatcher.fire(path, str(index), "write")
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.files == len(set(names))
        assert result.sha == git(repo, "rev-parse", "HEAD")
        assert git(repo, "log", "-1", "--format=%s") == "Many writes"
    finally:
        dispatcher.close()
        strategy.close()


def test_non_group_callback_keeps_per_call_commits(tmp_path: Path) -> None:
    from markdown_vault_mcp._commit_scope import bound_commit_scope

    seen: list[Path] = []
    dispatcher = WriteCallbackDispatcher(lambda path, _content, _op: seen.append(path))
    group = dispatcher.open_group("non-git", None)
    for name in ("a.md", "b.md"):
        with (
            dispatcher.bind_group(group.handle, None),
            bound_commit_scope("write") as scope,
        ):
            dispatcher.fire(tmp_path / name, name, "write")
            dispatcher.end_scope(scope)
    assert dispatcher.close_group(group.handle, None).result().status == "no_changes"
    dispatcher.close()
    assert seen == [tmp_path / "a.md", tmp_path / "b.md"]


def test_invalid_descriptions_rejected() -> None:
    import pytest

    from markdown_vault_mcp._commit_groups import CommitGroupError

    dispatcher = WriteCallbackDispatcher(None)
    for description in ("", " ", "two\nlines", " leading", "trailing "):
        with pytest.raises(CommitGroupError):
            dispatcher.open_group(description, None)
