"""Commit groups share the existing callback worker and Git strategy."""

from __future__ import annotations

import subprocess
import threading
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp._commit_groups import (
    CommitGroupError,
    GroupCommitResult,
    make_group,
)
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


def _commit_group(
    strategy: GitWriteStrategy, items: list[WriteBatchItem]
) -> GroupCommitResult:
    result = strategy.on_write_batch(items, "test", group_subject="Group")
    assert result is not None
    return result


def test_group_commits_multiple_writes_on_close(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    principal = Principal("alice", "Alice", "alice@example.com", "human")
    group = dispatcher.open_group("Organize notes", principal)
    # one.md is written twice: the count is paths in the commit, not writes.
    for index, name in enumerate(("one.md", "two.md", "one.md")):
        note = git_repo / name
        note.write_text(f"{name} {index}", encoding="utf-8")
        with bound_principal(principal), dispatcher.bind_group(group.handle, principal):
            dispatcher.fire(note, f"{name} {index}", "write")
        assert note.read_text(encoding="utf-8") == f"{name} {index}"
    assert git(git_repo, "log", "-1", "--format=%s") == "init"
    result = dispatcher.close_group(group.handle, principal).result(timeout=5)
    assert (
        git(git_repo, "log", "-1", "--format=%an <%ae>") == "Alice <alice@example.com>"
    )
    assert result.status == "committed"
    assert result.sha == git(git_repo, "rev-parse", "HEAD")
    assert result.files == 2
    assert git(git_repo, "log", "-1", "--format=%s") == "Organize notes"


def test_group_handles_reject_nested_foreign_and_unknown() -> None:
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


def test_group_unchanged_file_reports_no_changes(
    git_repo: Path, dispatcher: WriteCallbackDispatcher
) -> None:
    path = _seed(git_repo, "note.md", "one")
    group = dispatcher.open_group("no change", None)
    with dispatcher.bind_group(group.handle, None):
        dispatcher.fire(path, "one", "write")
    result = dispatcher.close_group(group.handle, None).result(timeout=5)
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
        assert (
            dispatcher.close_group(group.handle, None).result(timeout=10).status
            == "committed"
        )
        assert git(
            git_repo, "diff", "--no-renames", "--name-status", "HEAD^", "HEAD"
        ).splitlines() == [
            "D\ta.md",
            "A\tb.md",
            "A\ttest.md",
        ]
        assert vault.index.wait_for_drain(timeout=10)
        assert vault.reader.read("b.md") is not None
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("description", "valid"),
    [
        ("", False),
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
    if valid:
        assert make_group(description, None).description == description
    else:
        with pytest.raises(CommitGroupError):
            make_group(description, None)


@pytest.mark.parametrize("case", ["chain", "revisit", "delete_create"])
def test_group_rename_final_tree(
    git_repo: Path, strategy: GitWriteStrategy, case: str
) -> None:
    _seed(git_repo, "a.md")
    items: list[WriteBatchItem] = []
    if case == "delete_create":
        moves = (("a.md", "b.md"),)
    elif case == "revisit":
        moves = (("a.md", "b.md"), ("b.md", "a.md"), ("a.md", "c.md"))
    else:
        moves = (("a.md", "b.md"), ("b.md", "c.md"))
    for source, target in moves:
        old, new = git_repo / source, git_repo / target
        old.rename(new)
        items.append((new, "rename", old, None))
    if case == "delete_create":
        (git_repo / "b.md").unlink()
        items.append((git_repo / "b.md", "delete", None, None))
        created, final = git_repo / "created.md", git_repo / "c.md"
        created.write_text("new", encoding="utf-8")
        items.append((created, "write", None, None))
        created.rename(final)
        items.append((final, "rename", created, None))
    assert _commit_group(strategy, items).status == "committed"
    assert git(git_repo, "ls-tree", "--name-only", "HEAD").splitlines() == [
        "README.md",
        "c.md",
    ]


def test_group_stage_failure_reports_partial_commit(
    git_repo: Path, strategy: GitWriteStrategy
) -> None:
    nested = git_repo / "nested"
    nested.mkdir()
    git(nested, "init")
    git(nested, "config", "user.name", "Test")
    git(nested, "config", "user.email", "test@example.com")
    git(nested, "commit", "--allow-empty", "-m", "nested")
    git(git_repo, "add", "nested")
    git(git_repo, "commit", "-m", "add nested repository")
    good, invalid = git_repo / "good.md", nested / "note.md"
    good.write_text("good", encoding="utf-8")
    invalid.write_text("nested", encoding="utf-8")
    result = _commit_group(
        strategy, [(good, "write", None, None), (invalid, "write", None, None)]
    )
    assert result.status == "failed"
    assert result.sha == git(git_repo, "rev-parse", "HEAD")
    assert result.files == 1


@pytest.mark.parametrize("failure", ["commit", "stage"])
def test_group_git_failure_reports_failure(
    git_repo: Path, dispatcher: WriteCallbackDispatcher, failure: str
) -> None:
    group = dispatcher.open_group("Failure", None)
    _write_in_group(dispatcher, group.handle, git_repo / "note.md", "content")
    lock = git_repo / ".git/index.lock"
    if failure == "stage":
        lock.write_text("", encoding="utf-8")
    try:
        if failure == "commit":
            with patch(
                "markdown_vault_mcp.git.strategy._commit_staged",
                side_effect=subprocess.CalledProcessError(1, "git"),
            ):
                result = dispatcher.close_group(group.handle, None).result(timeout=5)
        else:
            result = dispatcher.close_group(group.handle, None).result(timeout=5)
    finally:
        if lock.exists():
            lock.unlink()
    assert result.status == "failed"
    assert result.sha is None
    assert result.files == 0


def test_group_counts_unicode_separator_filename(
    git_repo: Path, strategy: GitWriteStrategy
) -> None:
    git(git_repo, "config", "core.quotePath", "false")
    note = git_repo / "one\u2028two.md"
    note.write_text("note", encoding="utf-8")
    assert _commit_group(strategy, [(note, "write", None, None)]).files == 1


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
    original = strategy._git

    def fail_diff(root: Path, *args: str, **kwargs: object) -> str:
        if args[0] == "diff-tree":
            raise error
        return original(root, *args, **kwargs)

    try:
        note = git_repo / "note.md"
        note.write_text("note", encoding="utf-8")
        with (
            patch.object(strategy, "_git", side_effect=fail_diff),
            patch.object(strategy._push_scheduler, "schedule_push") as schedule,
        ):
            result = _commit_group(strategy, [(note, "write", None, None)])
            schedule.assert_called_once()
        assert result.status == "failed"
        assert result.sha == git(git_repo, "rev-parse", "HEAD")
        assert result.files == 0
    finally:
        strategy.close()


def test_non_group_callback_keeps_per_call_commits(tmp_path: Path) -> None:
    class Callback:
        accepts_batch = True

        def __init__(self) -> None:
            self.batches: list[str] = []

        def __call__(self, _path: Path, _content: str, _operation: str) -> None:
            raise AssertionError("expected per-call batch")

        def on_write_batch(self, _items: list[WriteBatchItem], tool_name: str) -> None:
            self.batches.append(tool_name)

    callback = Callback()
    dispatcher = WriteCallbackDispatcher(callback)
    group = dispatcher.open_group("Not opted in", None)
    with (
        dispatcher.bind_group(group.handle, None),
        bound_commit_scope("write") as scope,
    ):
        dispatcher.fire(tmp_path / "note.md", "note", "write")
        dispatcher.end_scope(scope)
    assert (
        dispatcher.close_group(group.handle, None).result(timeout=5).status
        == "no_changes"
    )
    dispatcher.close()
    assert callback.batches == ["write"]


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


@pytest.mark.parametrize("mixed", [False, True])
def test_group_commits_tracked_note_in_ignored_directory(
    git_repo: Path, strategy: GitWriteStrategy, mixed: bool
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
    items: list[WriteBatchItem] = [
        (tracked, "edit", None, None),
        (ignored, "write", None, None),
    ]
    if mixed:
        other = git_repo / "other.md"
        other.write_text("other", encoding="utf-8")
        items.append((other, "write", None, None))
    result = _commit_group(strategy, items)
    assert result.status == "committed"
    assert result.files == (2 if mixed else 1)
    assert git(git_repo, "show", "HEAD:ignored/tracked.md") == "updated"
    assert "ignored/untracked.md" not in git(
        git_repo, "ls-tree", "-r", "--name-only", "HEAD"
    )
    assert (
        _commit_group(strategy, [(ignored, "write", None, None)]).status == "no_changes"
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
    finally:
        dispatcher.close()
