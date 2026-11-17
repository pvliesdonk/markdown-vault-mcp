"""Commit groups share the existing callback worker and Git strategy."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.types import WriteBatchItem

from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.write_callback import WriteCallbackDispatcher
from tests.fixtures.git import _run_git


def git(repo: Path, *args: str) -> str:
    return _run_git(repo, *args).strip()


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


def test_group_rename_chain_commits_final_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    original, middle, final = (repo / name for name in ("a.md", "b.md", "c.md"))
    original.write_text("note", encoding="utf-8")
    git(repo, "add", "a.md")
    git(repo, "commit", "-m", "seed")
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
        group = dispatcher.open_group("Rename chain", None)
        with dispatcher.bind_group(group.handle, None):
            original.rename(middle)
            dispatcher.fire(middle, "", "rename", original)
            middle.rename(final)
            dispatcher.fire(final, "", "rename", middle)
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "committed"
        assert git(repo, "ls-tree", "--name-only", "HEAD") == "c.md"
    finally:
        dispatcher.close()
        strategy.close()


def test_group_stage_failure_reports_partial_commit(tmp_path: Path) -> None:
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
        group = dispatcher.open_group("Partial", None)
        for path in (repo / "good.md", tmp_path / "outside.md"):
            path.write_text("content", encoding="utf-8")
            with dispatcher.bind_group(group.handle, None):
                dispatcher.fire(path, "content", "write")
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "failed"
        assert result.sha == git(repo, "rev-parse", "HEAD")
        assert result.files == 1
        assert (tmp_path / "outside.md").exists()
    finally:
        dispatcher.close()
        strategy.close()


def test_group_index_lock_reports_failure(tmp_path: Path) -> None:
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
        group = dispatcher.open_group("Locked", None)
        note = repo / "note.md"
        note.write_text("content", encoding="utf-8")
        with dispatcher.bind_group(group.handle, None):
            dispatcher.fire(note, "content", "write")
        (repo / ".git/index.lock").write_text("", encoding="utf-8")
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "failed"
        assert result.sha is None
        assert result.files == 0
    finally:
        (repo / ".git/index.lock").unlink(missing_ok=True)
        dispatcher.close()
        strategy.close()


def test_group_owner_uses_stable_principal_identity() -> None:
    from markdown_vault_mcp._identity import Principal

    dispatcher = WriteCallbackDispatcher(None)
    original = Principal("alice", "Old name", "old@example.com", "human")
    refreshed = Principal("alice", "New name", "new@example.com", "human")
    group = dispatcher.open_group("Owned", original)
    with dispatcher.bind_group(group.handle, refreshed):
        pass
    assert (
        dispatcher.close_group(group.handle, refreshed).result().status == "no_changes"
    )
    dispatcher.close()


def test_subject_rejects_unicode_controls_and_excessive_utf8_bytes() -> None:
    import pytest

    from markdown_vault_mcp._commit_groups import CommitGroupError

    dispatcher = WriteCallbackDispatcher(None)
    for description in (
        "two\u0085lines",
        "two\u2028lines",
        "two\u2029lines",
        "é" * 2049,
    ):
        with pytest.raises(CommitGroupError):
            dispatcher.open_group(description, None)
    dispatcher.close()


def test_vault_group_rename_chain(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections import VaultSettings
    from markdown_vault_mcp.vault import Vault

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "a.md").write_text("# Note\n", encoding="utf-8")
    git(repo, "add", "a.md")
    git(repo, "commit", "-m", "seed")
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
        group = dispatcher.open_group("Move note", None)
        with dispatcher.bind_group(group.handle, None):
            vault.writer.rename("a.md", "b.md")
        with dispatcher.bind_group(group.handle, None):
            vault.writer.rename("b.md", "c.md")
        assert (
            dispatcher.close_group(group.handle, None).result(timeout=10).status
            == "committed"
        )
        assert git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines() == [
            "c.md"
        ]
        assert vault.index.wait_for_drain(timeout=10)
        assert vault.reader.read("c.md") is not None
    finally:
        vault.close()
        strategy.close()


def test_group_rename_then_delete_and_create_then_rename(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    old, middle, created, final = (
        repo / name for name in ("old.md", "middle.md", "created.md", "final.md")
    )
    old.write_text("old", encoding="utf-8")
    git(repo, "add", "old.md")
    git(repo, "commit", "-m", "seed")
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
        group = dispatcher.open_group("Final state", None)
        with dispatcher.bind_group(group.handle, None):
            old.rename(middle)
            dispatcher.fire(middle, "", "rename", old)
            middle.unlink()
            dispatcher.fire(middle, "", "delete")
            created.write_text("new", encoding="utf-8")
            dispatcher.fire(created, "new", "write")
            created.rename(final)
            dispatcher.fire(final, "", "rename", created)
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "committed"
        assert git(repo, "ls-tree", "-r", "--name-only", "HEAD") == "final.md"
    finally:
        dispatcher.close()
        strategy.close()


def test_group_rename_revisits_original_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    original, middle, final = (repo / name for name in ("a.md", "b.md", "c.md"))
    original.write_text("note", encoding="utf-8")
    git(repo, "add", "a.md")
    git(repo, "commit", "-m", "seed")
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
        group = dispatcher.open_group("Revisit", None)
        with dispatcher.bind_group(group.handle, None):
            for source, target in (
                (original, middle),
                (middle, original),
                (original, final),
            ):
                source.rename(target)
                dispatcher.fire(target, "", "rename", source)
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "committed"
        assert git(repo, "ls-tree", "--name-only", "HEAD") == "c.md"
    finally:
        dispatcher.close()
        strategy.close()


def test_group_skips_ignored_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "seed")
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
        group = dispatcher.open_group("Mixed", None)
        with dispatcher.bind_group(group.handle, None):
            for name in ("ignored.md", "good.md"):
                path = repo / name
                path.write_text(name, encoding="utf-8")
                dispatcher.fire(path, name, "write")
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "committed"
        assert result.files == 1
        empty = dispatcher.open_group("Ignored only", None)
        with dispatcher.bind_group(empty.handle, None):
            dispatcher.fire(repo / "ignored.md", "ignored", "write")
        assert (
            dispatcher.close_group(empty.handle, None).result(timeout=5).status
            == "no_changes"
        )
    finally:
        dispatcher.close()
        strategy.close()


def test_group_counts_unicode_separator_filename(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "core.quotePath", "false")
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
        group = dispatcher.open_group("Unicode", None)
        path = repo / "one\u2028two.md"
        path.write_text("note", encoding="utf-8")
        with dispatcher.bind_group(group.handle, None):
            dispatcher.fire(path, "note", "write")
        assert dispatcher.close_group(group.handle, None).result(timeout=5).files == 1
    finally:
        dispatcher.close()
        strategy.close()


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, "git"),
        OSError("unavailable"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    ],
)
def test_group_post_commit_metadata_failure_keeps_sha_and_schedules_push(
    tmp_path: Path,
    error: Exception,
) -> None:
    from unittest.mock import patch

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
        enable_push=True,
        repo_path=repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    original = strategy._git

    def fail_diff(root: Path, *args: str, **kwargs: object) -> str:
        if args[0] == "diff-tree":
            raise error
        return original(root, *args, **kwargs)

    try:
        group = dispatcher.open_group("Commit despite metadata", None)
        path = repo / "note.md"
        path.write_text("note", encoding="utf-8")
        with dispatcher.bind_group(group.handle, None):
            dispatcher.fire(path, "note", "write")
        with (
            patch.object(strategy, "_git", side_effect=fail_diff),
            patch.object(strategy._push_scheduler, "schedule_push") as schedule,
        ):
            result = dispatcher.close_group(group.handle, None).result(timeout=5)
            schedule.assert_called_once()
        assert result.status == "failed"
        assert result.sha == git(repo, "rev-parse", "HEAD")
        assert result.files == 0
    finally:
        dispatcher.close()
        strategy.close()


def test_group_accepts_format_characters() -> None:
    dispatcher = WriteCallbackDispatcher(None)
    for description in ("می‌خواهم", "👩‍💻"):
        group = dispatcher.open_group(description, None)
        assert (
            dispatcher.close_group(group.handle, None).result().status == "no_changes"
        )
    dispatcher.close()


def test_group_opt_in_without_batch_does_not_strand_writes(tmp_path: Path) -> None:
    class Callback:
        accepts_group_commit = True

        def __init__(self) -> None:
            self.paths: list[Path] = []

        def __call__(self, path: Path, _content: str, _operation: str) -> None:
            self.paths.append(path)

    callback = Callback()
    dispatcher = WriteCallbackDispatcher(callback)
    group = dispatcher.open_group("Group", None)
    with dispatcher.bind_group(group.handle, None):
        dispatcher.fire(tmp_path / "note.md", "note", "write")
    assert (
        dispatcher.close_group(group.handle, None).result(timeout=5).status
        == "no_changes"
    )
    dispatcher.close()
    assert callback.paths == [tmp_path / "note.md"]


def test_ordinary_write_during_group_and_group_author(git_repo: Path) -> None:
    from markdown_vault_mcp._identity import Principal, bound_principal

    principal = Principal("alice", "Alice", "alice@example.com", "human")
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=git_repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    try:
        group = dispatcher.open_group("Grouped", principal)
        grouped = git_repo / "grouped.md"
        grouped.write_text("grouped", encoding="utf-8")
        with bound_principal(principal), dispatcher.bind_group(group.handle, principal):
            dispatcher.fire(grouped, "grouped", "write")
        ordinary = git_repo / "ordinary.md"
        ordinary.write_text("ordinary", encoding="utf-8")
        dispatcher.fire(ordinary, "ordinary", "write")
        assert dispatcher.drain()
        assert git(git_repo, "log", "-2", "--format=%s").splitlines() == [
            "Grouped",
            "write: ordinary.md",
        ]
        assert (
            git(git_repo, "log", "-1", "--format=%an <%ae>")
            == "Alice <alice@example.com>"
        )
        assert (
            dispatcher.close_group(group.handle, principal).result(timeout=5).status
            == "committed"
        )
    finally:
        dispatcher.close()
        strategy.close()


def test_ordinary_batch_callback_receives_no_group_keyword(tmp_path: Path) -> None:
    from markdown_vault_mcp._commit_scope import bound_commit_scope

    class Callback:
        accepts_batch = True

        def __init__(self) -> None:
            self.batches: list[tuple[object, str]] = []

        def __call__(self, _path: Path, _content: str, _operation: str) -> None:
            raise AssertionError("ordinary scope should be batched")

        def on_write_batch(self, items: list[WriteBatchItem], tool_name: str) -> None:
            self.batches.append((items, tool_name))

    callback = Callback()
    dispatcher = WriteCallbackDispatcher(callback)
    with bound_commit_scope("ordinary") as scope:
        dispatcher.fire(tmp_path / "note.md", "note", "write")
        dispatcher.end_scope(scope)
    assert dispatcher.drain()
    dispatcher.close()
    assert len(callback.batches) == 1
    assert callback.batches[0][1] == "ordinary"


@pytest.mark.parametrize("mixed", [False, True])
def test_group_commits_tracked_note_in_ignored_directory(
    git_repo: Path, mixed: bool
) -> None:
    directory = git_repo / "ignored"
    directory.mkdir()
    tracked = directory / "tracked.md"
    tracked.write_text("old", encoding="utf-8")
    git(git_repo, "add", "ignored/tracked.md")
    git(git_repo, "commit", "-m", "track note")
    (git_repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    git(git_repo, "add", ".gitignore")
    git(git_repo, "commit", "-m", "ignore directory")
    tracked.write_text("updated", encoding="utf-8")
    ignored = directory / "untracked.md"
    ignored.write_text("ignored", encoding="utf-8")
    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=git_repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    try:
        group = dispatcher.open_group("Update notes", None)
        with dispatcher.bind_group(group.handle, None):
            dispatcher.fire(tracked, "updated", "edit")
            dispatcher.fire(ignored, "ignored", "write")
            if mixed:
                other = git_repo / "other.md"
                other.write_text("other", encoding="utf-8")
                dispatcher.fire(other, "other", "write")
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "committed"
        assert result.files == (2 if mixed else 1)
        assert git(git_repo, "show", "HEAD:ignored/tracked.md") == "updated"
        assert "ignored/untracked.md" not in git(
            git_repo, "ls-tree", "-r", "--name-only", "HEAD"
        )
    finally:
        dispatcher.close()
        strategy.close()


def test_group_close_during_timed_out_shutdown_preserves_subject(
    git_repo: Path,
) -> None:
    import threading

    from markdown_vault_mcp._commit_groups import CommitGroupError

    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=git_repo,
    )
    started, release = threading.Event(), threading.Event()

    class BlockFirst:
        accepts_batch = True
        accepts_group_commit = True
        accepts_primary_paths = True

        def __call__(self, path: Path, content: str, operation: str) -> None:
            started.set()
            assert release.wait(10)
            strategy(path, content, operation)  # type: ignore[arg-type]

        def on_write_batch(self, *args: object, **kwargs: object) -> object:
            return strategy.on_write_batch(*args, **kwargs)  # type: ignore[arg-type]

    dispatcher = WriteCallbackDispatcher(BlockFirst())
    try:
        group = dispatcher.open_group("Organize", None)
        prior = git_repo / "prior.md"
        prior.write_text("prior", encoding="utf-8")
        with dispatcher.bind_group(group.handle, None):
            dispatcher.fire(prior, "prior", "write")
        assert dispatcher.drain()
        previous = group.result
        assert previous.status == "committed"
        assert previous.files == 1
        ordinary = git_repo / "ordinary.md"
        ordinary.write_text("ordinary", encoding="utf-8")
        dispatcher.fire(ordinary, "ordinary", "write")
        assert started.wait(5)
        note = git_repo / "group.md"
        note.write_text("group", encoding="utf-8")
        with dispatcher.bind_group(group.handle, None):
            dispatcher.fire(note, "group", "write")
        dispatcher.close(timeout=0)
        with pytest.raises(CommitGroupError):
            dispatcher.close_group(group.handle, None)
        assert group.result == previous
        release.set()
        assert dispatcher._worker is not None
        dispatcher._worker.join(timeout=10)
        assert not dispatcher._worker.is_alive()
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
        assert result.status == "committed"
        assert result.sha == git(git_repo, "rev-parse", "HEAD")
        assert result.files == 2
        assert git(git_repo, "log", "-1", "--format=%s") == "Organize"
    finally:
        release.set()
        if dispatcher._worker is not None:
            dispatcher._worker.join(timeout=10)
        strategy.close()


def test_git_group_surrounds_per_call_scope(git_repo: Path) -> None:
    from markdown_vault_mcp._commit_scope import bound_commit_scope

    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=git_repo,
    )
    dispatcher = WriteCallbackDispatcher(strategy)
    try:
        group = dispatcher.open_group("Grouped calls", None)
        with dispatcher.bind_group(group.handle, None):
            for name in ("one.md", "two.md"):
                note = git_repo / name
                note.write_text(name, encoding="utf-8")
                with bound_commit_scope("write_note") as scope:
                    dispatcher.fire(note, name, "write")
                    dispatcher.end_scope(scope)
        # A queue barrier proves both per-call endings have been processed,
        # without drain(), which deliberately flushes the group.
        ordinary = git_repo / "ordinary.md"
        ordinary.write_text("ordinary", encoding="utf-8")
        dispatcher.fire(ordinary, "ordinary", "write")
        dispatcher.close_group(group.handle, None).result(timeout=5)
        assert git(git_repo, "log", "-2", "--format=%s").splitlines() == [
            "Grouped calls",
            "write: ordinary.md",
        ]
        assert git(git_repo, "show", "HEAD:one.md") == "one.md"
        assert git(git_repo, "show", "HEAD:two.md") == "two.md"
    finally:
        dispatcher.close()
        strategy.close()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
@pytest.mark.parametrize("fatal", [SystemExit, KeyboardInterrupt])
@pytest.mark.parametrize("flush", ["close_group", "drain", "shutdown"])
def test_interrupted_group_callback_reports_failed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    fatal: type[BaseException],
    flush: str,
) -> None:
    class FatalCallback:
        accepts_batch = True
        accepts_group_commit = True

        def __call__(self, _path: Path, _content: str, _operation: str) -> None:
            raise AssertionError("expected grouped batch")

        def on_write_batch(self, *_args: object, **_kwargs: object) -> None:
            raise fatal("interrupted")

    dispatcher = WriteCallbackDispatcher(FatalCallback())
    try:
        group = dispatcher.open_group("Interrupted", None)
        note = tmp_path / "note.md"
        note.write_text("on disk", encoding="utf-8")
        with dispatcher.bind_group(group.handle, None):
            dispatcher.fire(note, "on disk", "write")
        if flush == "close_group":
            result = dispatcher.close_group(group.handle, None).result(timeout=5)
        elif flush == "drain":
            dispatcher.drain(timeout=0)
            assert dispatcher._worker is not None
            dispatcher._worker.join(timeout=5)
            result = group.result
        else:
            dispatcher.close(timeout=5)
            result = group.result
        assert dispatcher._worker is not None
        dispatcher._worker.join(timeout=5)
        assert not dispatcher._worker.is_alive()
        assert "write_callback_worker_died" in caplog.text
        assert result.status == "failed"
        assert result.sha is None
        assert note.read_text(encoding="utf-8") == "on disk"
    finally:
        dispatcher.close()
