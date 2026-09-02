"""Tests for per-tool-call commit scoping (issue #1264).

Pins the four properties the grouping depends on: writes fired under one scope
reach the callback as a single batch named after the tool; concurrent scopes
never merge; a write with no scope keeps the previous per-write contract; and a
drain flushes still-open scopes rather than reporting success over work no
commit contains.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import pytest

from markdown_vault_mcp._commit_scope import (
    CommitScope,
    CommitScopeMiddleware,
    bound_commit_scope,
    current_commit_scope,
)
from markdown_vault_mcp.write_callback import WriteCallbackDispatcher

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from markdown_vault_mcp.types import WriteBatchItem


class _BatchRecorder:
    """A write callback that opts into batching and records both routes."""

    accepts_batch = True

    def __init__(self) -> None:
        self.batches: list[tuple[str, list[Path]]] = []
        self.singles: list[tuple[Path, str]] = []

    def __call__(
        self,
        abs_path: Path,
        content: str,  # noqa: ARG002 - required by the WriteCallback signature
        operation: str,
    ) -> None:
        self.singles.append((abs_path, operation))

    def on_write_batch(self, items: Sequence[WriteBatchItem], tool_name: str) -> None:
        self.batches.append((tool_name, [item[0] for item in items]))


class _PlainRecorder:
    """A callback that has NOT opted into batching."""

    def __init__(self) -> None:
        self.singles: list[tuple[Path, str]] = []

    def __call__(
        self,
        abs_path: Path,
        content: str,  # noqa: ARG002 - required by the WriteCallback signature
        operation: str,
    ) -> None:
        self.singles.append((abs_path, operation))


class TestScopeBinding:
    def test_no_scope_outside_a_tool_call(self) -> None:
        assert current_commit_scope() is None

    def test_scope_is_reset_afterward(self) -> None:
        with bound_commit_scope("write") as scope:
            assert current_commit_scope() is scope
        assert current_commit_scope() is None

    def test_each_binding_gets_a_distinct_token(self) -> None:
        with bound_commit_scope("write") as first:
            pass
        with bound_commit_scope("write") as second:
            pass
        assert first.token != second.token


class TestGrouping:
    def test_one_scope_produces_one_batch(self) -> None:
        cb = _BatchRecorder()
        dispatcher = WriteCallbackDispatcher(cb)
        with bound_commit_scope("okf_convert_links") as scope:
            for i in range(5):
                dispatcher.fire(Path(f"{i}.md"), str(i), "write")
            dispatcher.end_scope(scope)
        dispatcher.close()

        assert cb.singles == []
        assert cb.batches == [
            ("okf_convert_links", [Path(f"{i}.md") for i in range(5)])
        ]

    def test_a_scope_that_wrote_nothing_produces_no_batch(self) -> None:
        cb = _BatchRecorder()
        dispatcher = WriteCallbackDispatcher(cb)
        with bound_commit_scope("search") as scope:
            dispatcher.end_scope(scope)
        dispatcher.close()

        assert cb.batches == []
        assert cb.singles == []

    def test_writes_without_a_scope_dispatch_individually(self) -> None:
        cb = _BatchRecorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "body", "write")
        dispatcher.fire(Path("b.md"), "body", "write")
        dispatcher.close()

        assert cb.batches == []
        assert cb.singles == [(Path("a.md"), "write"), (Path("b.md"), "write")]

    def test_concurrent_scopes_do_not_merge(self) -> None:
        """Two tool calls interleaving in the queue stay separate commits.

        Grouping is keyed by token, not by queue position — position-based
        grouping would fold these two unrelated calls into one commit.
        """
        cb = _BatchRecorder()
        dispatcher = WriteCallbackDispatcher(cb)
        started = threading.Barrier(2)

        def worker(tool: str, name: str) -> None:
            with bound_commit_scope(tool) as scope:
                started.wait(timeout=5)
                dispatcher.fire(Path(name), "body", "write")
                dispatcher.end_scope(scope)

        threads = [
            threading.Thread(target=worker, args=("write", "one.md")),
            threading.Thread(target=worker, args=("edit", "two.md")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        dispatcher.close()

        assert len(cb.batches) == 2
        assert {tool for tool, _ in cb.batches} == {"write", "edit"}
        for _tool, paths in cb.batches:
            assert len(paths) == 1


class TestBatchOptIn:
    def test_a_callback_without_the_opt_in_is_never_buffered(self) -> None:
        """Grouping costs nothing to a callback that cannot receive a group.

        Buffering a non-batch callback's writes would hold them for the length
        of the tool call and then replay them one by one — the delay and the
        retained content of grouping, with none of the benefit. So the scope is
        attached only for a callback that opted in, and these writes reach the
        callback *while the call is still running*, exactly as before.
        """
        cb = _PlainRecorder()
        arrived = threading.Event()
        cb_call = cb.__call__

        def recording(*args: object) -> None:
            cb_call(*args)  # type: ignore[arg-type]
            arrived.set()

        dispatcher = WriteCallbackDispatcher(recording)
        with bound_commit_scope("okf_convert_links") as scope:
            dispatcher.fire(Path("a.md"), "body", "write")
            assert arrived.wait(timeout=10), "write was buffered instead of dispatched"
            assert dispatcher._open_scopes == {}
            dispatcher.fire(Path("b.md"), "body", "delete")
            dispatcher.end_scope(scope)
        dispatcher.close()

        assert cb.singles == [(Path("a.md"), "write"), (Path("b.md"), "delete")]


class TestDrainFlushesOpenScopes:
    def test_drain_commits_a_still_open_scope(self) -> None:
        """``drain`` is what a git pull waits on before merging.

        Returning with a group still buffered would let the merge run over
        writes that are on disk but in no commit.
        """
        cb = _BatchRecorder()
        dispatcher = WriteCallbackDispatcher(cb)
        with bound_commit_scope("okf_convert_links"):
            dispatcher.fire(Path("a.md"), "body", "write")
            # Deliberately no end_scope: the scope is still open.
            assert dispatcher.drain(timeout=10) is True
            assert cb.batches == [("okf_convert_links", [Path("a.md")])]
        dispatcher.close()

    def test_close_flushes_a_still_open_scope(self) -> None:
        cb = _BatchRecorder()
        dispatcher = WriteCallbackDispatcher(cb)
        with bound_commit_scope("move_folder"):
            dispatcher.fire(Path("a.md"), "body", "write")
        dispatcher.close()

        assert cb.batches == [("move_folder", [Path("a.md")])]

    def test_end_scope_after_a_drain_is_a_noop(self) -> None:
        cb = _BatchRecorder()
        dispatcher = WriteCallbackDispatcher(cb)
        with bound_commit_scope("write") as scope:
            dispatcher.fire(Path("a.md"), "body", "write")
            dispatcher.drain(timeout=10)
            dispatcher.end_scope(scope)
        dispatcher.close()

        assert cb.batches == [("write", [Path("a.md")])]


class TestFailureIsolation:
    def test_a_raising_batch_callback_does_not_kill_the_worker(self) -> None:
        class _Exploding(_BatchRecorder):
            def on_write_batch(
                self, items: Sequence[WriteBatchItem], tool_name: str
            ) -> None:
                if tool_name == "boom":
                    raise RuntimeError("batch failed")
                super().on_write_batch(items, tool_name)

        cb = _Exploding()
        dispatcher = WriteCallbackDispatcher(cb)
        with bound_commit_scope("boom") as first:
            dispatcher.fire(Path("a.md"), "body", "write")
            dispatcher.end_scope(first)
        with bound_commit_scope("write") as second:
            dispatcher.fire(Path("b.md"), "body", "write")
            dispatcher.end_scope(second)
        dispatcher.close()

        assert cb.batches == [("write", [Path("b.md")])]

    def test_end_scope_is_a_noop_when_no_callback_is_configured(self) -> None:
        dispatcher = WriteCallbackDispatcher(None)
        with bound_commit_scope("write") as scope:
            dispatcher.end_scope(scope)
        dispatcher.close()

    def test_end_scope_after_close_is_a_noop(self) -> None:
        cb = _BatchRecorder()
        dispatcher = WriteCallbackDispatcher(cb)
        dispatcher.fire(Path("a.md"), "body", "write")
        dispatcher.close()
        with bound_commit_scope("write") as scope:
            dispatcher.end_scope(scope)
        assert cb.batches == []


class _FakeMessage:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeMiddlewareContext:
    def __init__(self, tool: str, fastmcp_context: object | None) -> None:
        self.message = _FakeMessage(tool)
        self.fastmcp_context = fastmcp_context


class TestMiddleware:
    @staticmethod
    async def _run(
        middleware: CommitScopeMiddleware,
        context: object,
        seen: list[CommitScope | None],
        *,
        raises: bool = False,
    ) -> str:
        async def call_next(_ctx: object) -> str:
            seen.append(current_commit_scope())
            if raises:
                raise RuntimeError("tool blew up")
            return "ok"

        return await middleware.on_call_tool(context, call_next)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_scope_is_bound_during_the_call_and_reset_after(self) -> None:
        middleware = CommitScopeMiddleware()
        context = _FakeMiddlewareContext("write", None)
        seen: list[CommitScope | None] = []

        result = await self._run(middleware, context, seen)

        assert result == "ok"
        assert seen[0] is not None
        assert seen[0].tool_name == "write"
        assert current_commit_scope() is None

    @pytest.mark.asyncio
    async def test_scope_closes_even_when_the_tool_raises(self) -> None:
        """A tool that writes then fails must still commit what it wrote."""
        closed: list[CommitScope] = []

        class _Vault:
            def end_commit_scope(self, scope: CommitScope) -> None:
                closed.append(scope)

        middleware = CommitScopeMiddleware()
        context = _FakeMiddlewareContext("write", object())
        seen: list[CommitScope | None] = []

        with (
            mock.patch("markdown_vault_mcp.domain.get_vault", return_value=_Vault()),
            pytest.raises(RuntimeError, match="tool blew up"),
        ):
            await self._run(middleware, context, seen, raises=True)

        assert len(closed) == 1
        assert closed[0].tool_name == "write"
        assert current_commit_scope() is None

    @pytest.mark.asyncio
    async def test_close_is_skipped_without_a_fastmcp_context(self) -> None:
        middleware = CommitScopeMiddleware()
        context = _FakeMiddlewareContext("write", None)
        seen: list[CommitScope | None] = []

        # No vault lookup happens, so an unpatched get_vault must not be hit.
        assert await self._run(middleware, context, seen) == "ok"

    @pytest.mark.asyncio
    async def test_an_unexpected_close_failure_does_not_destroy_the_result(
        self,
    ) -> None:
        """Bookkeeping runs in a ``finally``; anything it raises replaces the
        tool's result. Only RuntimeError/AttributeError were caught at first,
        so a ValueError from get_vault destroyed a successful call."""
        middleware = CommitScopeMiddleware()
        context = _FakeMiddlewareContext("write", object())
        seen: list[CommitScope | None] = []

        with mock.patch(
            "markdown_vault_mcp.domain.get_vault",
            side_effect=ValueError("something unexpected"),
        ):
            assert await self._run(middleware, context, seen) == "ok"

    @pytest.mark.asyncio
    async def test_a_vault_that_is_not_up_does_not_fail_the_tool_call(self) -> None:
        """Tool listing before startup must not raise out of the middleware."""
        middleware = CommitScopeMiddleware()
        context = _FakeMiddlewareContext("write", object())
        seen: list[CommitScope | None] = []

        with mock.patch(
            "markdown_vault_mcp.domain.get_vault",
            side_effect=RuntimeError("Vault not initialised"),
        ):
            assert await self._run(middleware, context, seen) == "ok"


def _repo(tmp_path: Path) -> Path:
    """A git repo holding one committed note."""
    import subprocess

    repo = tmp_path / "vault"
    repo.mkdir()
    for cmd in (
        ["git", "-C", str(repo), "init"],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "T"],
        ["git", "-C", str(repo), "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, capture_output=True, check=True)
    (repo / "note.md").write_text("# Note\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], capture_output=True, check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "seed"],
        capture_output=True,
        check=True,
    )
    return repo


def _subjects(repo: Path) -> list[str]:
    """Commit subjects, newest first."""
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.splitlines()


def _files_in_head(repo: Path) -> set[str]:
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in out.stdout.splitlines() if line}


class TestBatchCommitsForReal:
    """End-to-end against a real repo: the dispatcher plus GitWriteStrategy.

    The dispatcher tests above use a recorder, so they never exercise the code
    that actually stages and commits. These do.
    """

    @staticmethod
    def _strategy(repo: Path):
        from markdown_vault_mcp.git.strategy import GitWriteStrategy

        return GitWriteStrategy(
            token=None,
            repo_url=None,
            managed=False,
            enable_pull=False,
            enable_push=False,
            repo_path=repo,
        )

    def test_one_tool_call_makes_one_commit(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        dispatcher = WriteCallbackDispatcher(strategy)
        before = len(_subjects(repo))

        with bound_commit_scope("okf_convert_links") as scope:
            for i in range(4):
                target = repo / f"n{i}.md"
                target.write_text(f"# {i}\n", encoding="utf-8")
                dispatcher.fire(target, f"# {i}\n", "write")
            dispatcher.end_scope(scope)
        dispatcher.close()
        strategy.close()

        subjects = _subjects(repo)
        assert len(subjects) == before + 1, subjects
        assert subjects[0] == "okf_convert_links: 4 files"
        assert _files_in_head(repo) == {f"n{i}.md" for i in range(4)}

    def test_writes_with_no_owning_tool_call_commit_individually(
        self, tmp_path: Path
    ) -> None:
        """Background and startup writes have no scope, so nothing groups them."""
        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        dispatcher = WriteCallbackDispatcher(strategy)
        before = len(_subjects(repo))

        for i in range(3):
            target = repo / f"n{i}.md"
            target.write_text(f"# {i}\n", encoding="utf-8")
            dispatcher.fire(target, f"# {i}\n", "write")
        dispatcher.close()
        strategy.close()

        assert len(_subjects(repo)) == before + 3
        assert _subjects(repo)[0] == "write: n2.md"

    def test_a_single_file_call_still_commits_under_its_path(
        self, tmp_path: Path
    ) -> None:
        """#1264 is explicit that per-file granularity is right for one file.

        Grouping must not turn every ordinary ``write`` into ``write: 1 file``
        and cost ``git log`` the path it has always carried.
        """
        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        dispatcher = WriteCallbackDispatcher(strategy)

        with bound_commit_scope("write") as scope:
            target = repo / "one.md"
            target.write_text("# One\n", encoding="utf-8")
            dispatcher.fire(target, "# One\n", "write")
            dispatcher.end_scope(scope)
        dispatcher.close()
        strategy.close()

        assert _subjects(repo)[0] == "write: one.md"

    def test_a_batch_of_identical_content_commits_nothing(self, tmp_path: Path) -> None:
        """Rewriting the same bytes stages no diff, so there is nothing to commit."""
        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        dispatcher = WriteCallbackDispatcher(strategy)
        before = len(_subjects(repo))

        with bound_commit_scope("okf_convert_links") as scope:
            dispatcher.fire(repo / "note.md", "# Note\n", "write")
            dispatcher.end_scope(scope)
        dispatcher.close()
        strategy.close()

        assert len(_subjects(repo)) == before

    def test_a_multi_file_batch_of_identical_content_commits_nothing(
        self, tmp_path: Path
    ) -> None:
        """The same no-diff guard, on the batch path rather than the delegated one.

        A one-item batch delegates to the single-write path and never reaches
        the batch's own ``git diff --cached`` check, so this needs two files:
        both stage successfully and neither changes a byte.
        """
        repo = _repo(tmp_path)
        (repo / "second.md").write_text("# Second\n", encoding="utf-8")
        import subprocess

        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "second"],
            capture_output=True,
            check=True,
        )
        strategy = self._strategy(repo)
        before = len(_subjects(repo))

        strategy.on_write_batch(
            [
                (repo / "note.md", "write", None, None),
                (repo / "second.md", "write", None, None),
            ],
            "okf_convert_links",
        )
        strategy.close()

        assert len(_subjects(repo)) == before

    def test_a_delete_in_a_batch_stages_its_own_removal(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        dispatcher = WriteCallbackDispatcher(strategy)

        with bound_commit_scope("delete") as scope:
            (repo / "note.md").unlink()
            dispatcher.fire(repo / "note.md", "", "delete")
            dispatcher.end_scope(scope)
        dispatcher.close()
        strategy.close()

        assert _subjects(repo)[0] == "delete: note.md"
        assert _files_in_head(repo) == {"note.md"}

    def test_an_unstageable_path_does_not_cost_the_batch_its_commit(
        self, tmp_path: Path
    ) -> None:
        """One bad path is logged; the files that did land still get committed."""
        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        dispatcher = WriteCallbackDispatcher(strategy)

        with bound_commit_scope("okf_convert_links") as scope:
            good = repo / "good.md"
            good.write_text("# Good\n", encoding="utf-8")
            dispatcher.fire(good, "# Good\n", "write")
            # Never created on disk: `git add` exits non-zero on it.
            dispatcher.fire(repo / "ghost.md", "", "write")
            dispatcher.end_scope(scope)
        dispatcher.close()
        strategy.close()

        assert _subjects(repo)[0] == "okf_convert_links: 1 file"
        assert _files_in_head(repo) == {"good.md"}

    def test_a_closed_strategy_ignores_a_batch(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        before = len(_subjects(repo))
        strategy.close()

        (repo / "after.md").write_text("# After\n", encoding="utf-8")
        strategy.on_write_batch([(repo / "after.md", "write", None, None)], "write")

        assert len(_subjects(repo)) == before

    def test_an_empty_batch_is_a_noop(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        before = len(_subjects(repo))

        strategy.on_write_batch([], "write")
        strategy.close()

        assert len(_subjects(repo)) == before

    def test_a_path_outside_any_repo_is_logged_not_raised(self, tmp_path: Path) -> None:
        """No git repository above the path: git is simply disabled."""
        from markdown_vault_mcp.git.strategy import GitWriteStrategy

        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        note = outside / "n.md"
        note.write_text("# N\n", encoding="utf-8")
        strategy = GitWriteStrategy(
            token=None,
            repo_url=None,
            managed=False,
            enable_pull=False,
            enable_push=False,
            repo_path=outside,
        )

        # Must not raise.
        strategy.on_write_batch([(note, "write", None, None)], "write")
        strategy.close()

    def test_a_failing_commit_is_logged_not_raised(
        self, tmp_path: Path, caplog
    ) -> None:
        """A CalledProcessError from the commit must not escape into the tool."""
        import logging
        import subprocess

        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        # Two files: one item would delegate to the single-write path, and this
        # is about the batch commit's own error handling.
        notes = []
        for name in ("x.md", "y.md"):
            note = repo / name
            note.write_text(f"# {name}\n", encoding="utf-8")
            notes.append(note)

        real_run = subprocess.run

        def _explode(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "commit" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="boom")
            return real_run(cmd, *args, **kwargs)

        with (
            mock.patch("markdown_vault_mcp.git.strategy.subprocess.run", _explode),
            caplog.at_level(logging.ERROR),
        ):
            strategy.on_write_batch(
                [(note, "write", None, None) for note in notes], "okf_convert_links"
            )
        strategy.close()

        assert any("git_batch_failed" in r.message for r in caplog.records)

    def test_a_batch_where_nothing_stages_commits_nothing(self, tmp_path: Path) -> None:
        """Every path unstageable: no commit, and no crash."""
        repo = _repo(tmp_path)
        strategy = self._strategy(repo)
        before = len(_subjects(repo))

        strategy.on_write_batch(
            [
                (repo / "ghost-a.md", "write", None, None),
                (repo / "ghost-b.md", "write", None, None),
            ],
            "okf_convert_links",
        )
        strategy.close()

        assert len(_subjects(repo)) == before


class TestScopeSurvivesARealToolCall:
    """The grouping through the real MCP path, not a hand-bound scope (#1264).

    Every other test in this file binds the scope and calls ``fire`` on one
    thread, which is the case that works trivially. The design's load-bearing
    claim is the one those tests cannot reach: ``CommitScopeMiddleware`` binds
    the contextvar in ``on_call_tool``, the tool body runs in
    ``asyncio.to_thread``, and ``fire`` reads the scope back out of that
    thread's copied context. If that chain ever breaks, the scope silently
    reads as ``None`` and every commit goes back to being per file — the exact
    silent-fallback shape of #1218 — with no other test noticing.

    ``SOURCE_DIR`` being a git repository is enough: with no token and no
    repo URL the assembly still builds a commit-only ``GitWriteStrategy``.
    """

    @staticmethod
    def _vault_repo(tmp_path: Path) -> Path:
        import subprocess

        vault = tmp_path / "vault"
        (vault / "src").mkdir(parents=True)
        for cmd in (
            ["git", "-C", str(vault), "init"],
            ["git", "-C", str(vault), "config", "user.email", "t@t.com"],
            ["git", "-C", str(vault), "config", "user.name", "T"],
            ["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
        ):
            subprocess.run(cmd, capture_output=True, check=True)
        for name in ("a", "b", "c"):
            (vault / "src" / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(vault), "add", "-A"], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(vault), "commit", "-m", "seed"],
            capture_output=True,
            check=True,
        )
        return vault

    @staticmethod
    async def _drain() -> None:
        """Wait for the queued scope-end marker to reach the git callback."""
        from markdown_vault_mcp.domain import get_vault_singleton

        vault = get_vault_singleton()
        assert await asyncio.to_thread(vault._write_callback.drain, 30.0) is True

    @pytest.fixture
    def _git_vault_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[Path]:
        for key in [k for k in os.environ if k.startswith("MARKDOWN_VAULT_MCP_")]:
            monkeypatch.delenv(key, raising=False)
        repo = self._vault_repo(tmp_path)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(repo))
        yield repo

    async def test_a_bulk_tool_call_lands_in_one_commit(
        self, _git_vault_env: Path
    ) -> None:
        """``move_folder`` moves three files and commits once, named for the tool."""
        from fastmcp import Client

        from tests.server_factory import make_server

        repo = _git_vault_env
        before = len(_subjects(repo))

        server = make_server()
        async with Client(server) as client:
            await client.call_tool("move_folder", {"old_dir": "src", "new_dir": "dst"})
            await self._drain()

        subjects = _subjects(repo)
        assert len(subjects) == before + 1, subjects
        assert subjects[0] == "move_folder: 3 files"
        assert _files_in_head(repo) == {f"dst/{n}.md" for n in ("a", "b", "c")}

    async def test_a_single_file_tool_call_still_names_its_path(
        self, _git_vault_env: Path
    ) -> None:
        """The common case keeps the subject it has always had."""
        from fastmcp import Client

        from tests.server_factory import make_server

        repo = _git_vault_env
        before = len(_subjects(repo))

        server = make_server()
        async with Client(server) as client:
            await client.call_tool("write", {"path": "note.md", "content": "# Note\n"})
            await self._drain()

        subjects = _subjects(repo)
        assert len(subjects) == before + 1, subjects
        assert subjects[0] == "write: note.md"

    async def test_a_read_only_tool_call_commits_nothing(
        self, _git_vault_env: Path
    ) -> None:
        """A scope that wrote nothing closes without producing a commit."""
        from fastmcp import Client

        from tests.server_factory import make_server

        repo = _git_vault_env
        before = len(_subjects(repo))

        server = make_server()
        async with Client(server) as client:
            await client.call_tool("list_documents", {})
            await self._drain()

        assert len(_subjects(repo)) == before
