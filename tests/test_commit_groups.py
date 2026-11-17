"""Commit groups share the existing callback worker and Git strategy."""

from __future__ import annotations

import subprocess
import threading
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp._commit_groups import CommitGroupError
from markdown_vault_mcp._commit_scope import bound_commit_scope
from markdown_vault_mcp._identity import Principal, bound_principal
from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.vault import Vault
from markdown_vault_mcp.write_callback import WriteCallbackDispatcher
from tests.fixtures.git import _run_git

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from markdown_vault_mcp.types import WriteBatchItem


def git(repo: Path, *args: str) -> str:
    return _run_git(repo, *args).strip()


def _strategy(repo: Path, *, enable_push: bool = False) -> GitWriteStrategy:
    return GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=enable_push,
        repo_path=repo,
    )


@pytest.fixture
def strategy(git_repo: Path) -> Iterator[GitWriteStrategy]:
    strategy = _strategy(git_repo)
    yield strategy
    strategy.close()


@pytest.fixture
def dispatcher(strategy: GitWriteStrategy) -> Iterator[WriteCallbackDispatcher]:
    dispatcher = WriteCallbackDispatcher(strategy)
    yield dispatcher
    dispatcher.close()


def _seed(repo: Path, name: str, content: str = "note") -> Path:
    path = repo / name
    path.write_text(content, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", "seed")
    return path


def _write_in_group(
    dispatcher: WriteCallbackDispatcher, handle: str, path: Path, content: str
) -> None:
    path.write_text(content, encoding="utf-8")
    with dispatcher.bind_group(handle, None):
        dispatcher.fire(path, content, "write")


def test_group_commits_multiple_writes_on_close(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    group = dispatcher.open_group("Organize notes", None)
    # one.md is written twice: the count is paths in the commit, not writes.
    for index, name in enumerate(("one.md", "two.md", "one.md")):
        note = git_repo / name
        _write_in_group(dispatcher, group.handle, note, f"{name} {index}")
        assert note.read_text(encoding="utf-8") == f"{name} {index}"
    assert git(git_repo, "log", "-1", "--format=%s") == "init"
    result = dispatcher.close_group(group.handle, None).result(timeout=5)
    assert result.status == "committed"
    assert result.sha == git(git_repo, "rev-parse", "HEAD")
    assert result.files == 2
    assert git(git_repo, "log", "-1", "--format=%s") == "Organize notes"


def test_group_handles_reject_nested_foreign_and_closed() -> None:
    dispatcher = WriteCallbackDispatcher(None)
    alice = Principal("alice", "Old name", "old@example.com", "human")
    refreshed = Principal("alice", "New name", "new@example.com", "human")
    bob = Principal("bob", None, None, "human")
    group = dispatcher.open_group("first", alice)
    other = dispatcher.open_group("second", alice)
    with dispatcher.bind_group(group.handle, refreshed):
        with pytest.raises(CommitGroupError):
            dispatcher.open_group("nested", alice)
        with pytest.raises(CommitGroupError):
            dispatcher.bind_group(other.handle, alice).__enter__()
        with pytest.raises(CommitGroupError):
            dispatcher.close_group(group.handle, alice)
    with pytest.raises(CommitGroupError):
        dispatcher.close_group(group.handle, bob)
    assert (
        dispatcher.close_group(group.handle, refreshed).result().status == "no_changes"
    )
    with pytest.raises(CommitGroupError):
        dispatcher.close_group(group.handle, alice)
    dispatcher.close_group(other.handle, alice)
    dispatcher.close()


def test_single_group_no_changes_and_drain(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    group = dispatcher.open_group("One subject", None)
    path = git_repo / "note.md"
    _write_in_group(dispatcher, group.handle, path, "one")
    assert dispatcher.drain()
    assert git(git_repo, "log", "-1", "--format=%s") == "One subject"
    _write_in_group(dispatcher, group.handle, path, "two")
    result = dispatcher.close_group(group.handle, None).result(timeout=5)
    assert result.status == "committed"
    assert result.files == 2
    assert git(git_repo, "log", "-2", "--format=%s").splitlines() == [
        "One subject",
        "One subject",
    ]
    unchanged = dispatcher.open_group("no change", None)
    with dispatcher.bind_group(unchanged.handle, None):
        dispatcher.fire(path, "two", "write")
    result = dispatcher.close_group(unchanged.handle, None).result(timeout=5)
    assert result.status == "no_changes"
    assert result.sha is None


def test_vault_group_indexes_before_close_and_renames(
    git_repo: Path, strategy: GitWriteStrategy
) -> None:
    _seed(git_repo, "a.md", "# Note\n")
    vault = Vault(
        source_dir=git_repo, settings=VaultSettings(read_only=False), on_write=strategy
    )
    try:
        dispatcher = vault._write_callback
        group = dispatcher.open_group("Move note", None)
        with dispatcher.bind_group(group.handle, None):
            vault.writer.write("test.md", "# Quasar\n\nuniquewordquasar")
        assert vault.reader.read("test.md") is not None
        assert vault.index.wait_for_drain(timeout=10)
        assert vault.reader.search("uniquewordquasar", mode="keyword")
        assert git(git_repo, "log", "-1", "--format=%s") == "seed"
        with dispatcher.bind_group(group.handle, None):
            vault.writer.rename("a.md", "b.md")
        with dispatcher.bind_group(group.handle, None):
            vault.writer.rename("b.md", "c.md")
        assert (
            dispatcher.close_group(group.handle, None).result(timeout=10).status
            == "committed"
        )
        assert git(git_repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines() == [
            "README.md",
            "c.md",
            "test.md",
        ]
        assert vault.index.wait_for_drain(timeout=10)
        assert vault.reader.read("c.md") is not None
    finally:
        vault.close()


def test_failed_group_reports_failure(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    group = dispatcher.open_group("fails", None)
    _write_in_group(dispatcher, group.handle, git_repo / "note.md", "content")
    with patch(
        "markdown_vault_mcp.git.strategy._commit_staged",
        side_effect=subprocess.CalledProcessError(1, "git"),
    ):
        assert (
            dispatcher.close_group(group.handle, None).result(timeout=5).status
            == "failed"
        )


def test_non_group_callback_keeps_per_call_commits(tmp_path: Path) -> None:
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


@pytest.mark.parametrize(
    ("description", "valid"),
    [
        ("", False),
        (" ", False),
        ("two\nlines", False),
        (" leading", False),
        ("trailing ", False),
        ("two\u0085lines", False),
        ("two\u2028lines", False),
        ("two\u2029lines", False),
        ("é" * 2049, False),
        ("می‌خواهم", True),
        ("👩‍💻", True),
    ],
)
def test_group_description_rules(description: str, valid: bool) -> None:
    dispatcher = WriteCallbackDispatcher(None)
    if valid:
        group = dispatcher.open_group(description, None)
        assert (
            dispatcher.close_group(group.handle, None).result().status == "no_changes"
        )
    else:
        with pytest.raises(CommitGroupError):
            dispatcher.open_group(description, None)
    dispatcher.close()


@pytest.mark.parametrize(
    "moves",
    [
        (("a.md", "b.md"), ("b.md", "c.md")),
        (("a.md", "b.md"), ("b.md", "a.md"), ("a.md", "c.md")),
    ],
)
def test_group_rename_chain_commits_final_tree(
    git_repo: Path,
    dispatcher: WriteCallbackDispatcher,
    moves: tuple[tuple[str, str], ...],
) -> None:
    _seed(git_repo, "a.md")
    group = dispatcher.open_group("Rename chain", None)
    with dispatcher.bind_group(group.handle, None):
        for source, target in moves:
            (git_repo / source).rename(git_repo / target)
            dispatcher.fire(git_repo / target, "", "rename", git_repo / source)
    result = dispatcher.close_group(group.handle, None).result(timeout=5)
    assert result.status == "committed"
    assert git(git_repo, "ls-tree", "--name-only", "HEAD").splitlines() == [
        "README.md",
        "c.md",
    ]


def test_group_stage_failure_reports_partial_commit(
    tmp_path: Path, git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    group = dispatcher.open_group("Partial", None)
    for path in (git_repo / "good.md", tmp_path / "outside.md"):
        _write_in_group(dispatcher, group.handle, path, "content")
    result = dispatcher.close_group(group.handle, None).result(timeout=5)
    assert result.status == "failed"
    assert result.sha == git(git_repo, "rev-parse", "HEAD")
    assert result.files == 1
    assert (tmp_path / "outside.md").exists()


def test_group_index_lock_reports_failure(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    group = dispatcher.open_group("Locked", None)
    _write_in_group(dispatcher, group.handle, git_repo / "note.md", "content")
    lock = git_repo / ".git/index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        result = dispatcher.close_group(group.handle, None).result(timeout=5)
    finally:
        lock.unlink()
    assert result.status == "failed"
    assert result.sha is None
    assert result.files == 0


def test_group_rename_then_delete_and_create_then_rename(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    old = _seed(git_repo, "old.md", "old")
    middle, created, final = (
        git_repo / name for name in ("middle.md", "created.md", "final.md")
    )
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
    assert git(git_repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines() == [
        "README.md",
        "final.md",
    ]


def test_group_counts_unicode_separator_filename(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    git(git_repo, "config", "core.quotePath", "false")
    group = dispatcher.open_group("Unicode", None)
    _write_in_group(dispatcher, group.handle, git_repo / "one\u2028two.md", "note")
    assert dispatcher.close_group(group.handle, None).result(timeout=5).files == 1


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, "git"),
        OSError("unavailable"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    ],
)
def test_group_post_commit_metadata_failure_keeps_sha_and_schedules_push(
    git_repo: Path,
    error: Exception,
) -> None:
    strategy = _strategy(git_repo, enable_push=True)
    dispatcher = WriteCallbackDispatcher(strategy)
    original = strategy._git

    def fail_diff(root: Path, *args: str, **kwargs: object) -> str:
        if args[0] == "diff-tree":
            raise error
        return original(root, *args, **kwargs)

    try:
        group = dispatcher.open_group("Commit despite metadata", None)
        _write_in_group(dispatcher, group.handle, git_repo / "note.md", "note")
        with (
            patch.object(strategy, "_git", side_effect=fail_diff),
            patch.object(strategy._push_scheduler, "schedule_push") as schedule,
        ):
            result = dispatcher.close_group(group.handle, None).result(timeout=5)
            schedule.assert_called_once()
        assert result.status == "failed"
        assert result.sha == git(git_repo, "rev-parse", "HEAD")
        assert result.files == 0
    finally:
        dispatcher.close()
        strategy.close()


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


def test_ordinary_write_during_group_and_group_author(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    principal = Principal("alice", "Alice", "alice@example.com", "human")
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
        git(git_repo, "log", "-1", "--format=%an <%ae>") == "Alice <alice@example.com>"
    )
    assert (
        dispatcher.close_group(group.handle, principal).result(timeout=5).status
        == "committed"
    )


def test_ordinary_batch_callback_receives_no_group_keyword(tmp_path: Path) -> None:
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
    git_repo: Path, dispatcher: WriteCallbackDispatcher, mixed: bool
) -> None:
    directory = git_repo / "ignored"
    directory.mkdir()
    tracked = _seed(git_repo, "ignored/tracked.md", "old")
    (git_repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    git(git_repo, "add", ".gitignore")
    git(git_repo, "commit", "-m", "ignore directory")
    tracked.write_text("updated", encoding="utf-8")
    ignored = directory / "untracked.md"
    ignored.write_text("ignored", encoding="utf-8")
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
    only_ignored = dispatcher.open_group("Ignored only", None)
    with dispatcher.bind_group(only_ignored.handle, None):
        dispatcher.fire(ignored, "ignored", "write")
    assert (
        dispatcher.close_group(only_ignored.handle, None).result(timeout=5).status
        == "no_changes"
    )


def test_group_close_during_timed_out_shutdown_preserves_subject(
    git_repo: Path, strategy: GitWriteStrategy
) -> None:
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
        _write_in_group(dispatcher, group.handle, git_repo / "prior.md", "prior")
        assert dispatcher.drain()
        previous = group.result
        assert previous.status == "committed"
        assert previous.files == 1
        ordinary = git_repo / "ordinary.md"
        ordinary.write_text("ordinary", encoding="utf-8")
        dispatcher.fire(ordinary, "ordinary", "write")
        assert started.wait(5)
        _write_in_group(dispatcher, group.handle, git_repo / "group.md", "group")
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


def test_git_group_surrounds_per_call_scope(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
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
