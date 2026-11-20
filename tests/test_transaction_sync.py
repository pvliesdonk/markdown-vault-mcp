"""Transaction settlement at the managed git_sync MCP boundary."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from markdown_vault_mcp._identity import Principal
from tests.fixtures.git import _run_git
from tests.server_factory import make_server
from tests.test_git_sync import _git_managed_env as _git_managed_env

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp._commit_groups import CommitGroup
    from markdown_vault_mcp.write_callback import WriteCallbackDispatcher
    from tests.fixtures.git import GitRepoPair


@pytest.mark.parametrize("explicit", [False, True])
async def test_sync_settles_and_pushes_all_caller_groups(
    git_repo_pair: GitRepoPair, _git_managed_env: Path, explicit: bool
) -> None:
    async with Client(make_server()) as client:
        handles = []
        for i in range(2):
            handle = (
                await client.call_tool(
                    "begin_transaction", {"description": f"Group {i}"}
                )
            ).data["handle"]
            handles.append(handle)
            await client.call_tool(
                "write", {"path": f"{i}.md", "content": "x", "transaction": handle}
            )
        args = {"direction": "push"}
        if explicit:
            args["transaction"] = handles[0]
        result = await client.call_tool("git_sync", args)
        assert result.data["push"]["applied"]
        assert "history_failed" not in result.data
        subjects = _run_git(
            git_repo_pair.remote_path, "log", "--format=%s"
        ).splitlines()
        assert "Group 0" in subjects and "Group 1" in subjects
        for handle in handles:
            with pytest.raises(ToolError):
                await client.call_tool("end_transaction", {"handle": handle})


@pytest.mark.parametrize("explicit", [False, True])
async def test_sync_preview_keeps_group_open(
    git_repo_pair: GitRepoPair, _git_managed_env: Path, explicit: bool
) -> None:
    async with Client(make_server()) as client:
        before = _run_git(git_repo_pair.local_path, "rev-parse", "HEAD")
        remote = _run_git(git_repo_pair.remote_path, "rev-parse", "HEAD")
        handle = (
            await client.call_tool("begin_transaction", {"description": "Preview"})
        ).data["handle"]
        await client.call_tool(
            "write", {"path": "preview.md", "content": "x", "transaction": handle}
        )
        args = {"direction": "push", "dry_run": True}
        if explicit:
            args["transaction"] = handle
            with pytest.raises(ToolError, match="dry-run"):
                await client.call_tool("git_sync", args)
        else:
            await client.call_tool("git_sync", args)
        assert _run_git(git_repo_pair.local_path, "rev-parse", "HEAD") == before
        assert _run_git(git_repo_pair.remote_path, "rev-parse", "HEAD") == remote
        assert (await client.call_tool("end_transaction", {"handle": handle})).data[
            "status"
        ] == "committed"


async def test_sync_refuses_after_failed_commit(
    git_repo_pair: GitRepoPair, _git_managed_env: Path
) -> None:
    hook = git_repo_pair.local_path / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    remote = _run_git(git_repo_pair.remote_path, "rev-parse", "HEAD")
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool("begin_transaction", {"description": "Fails"})
        ).data["handle"]
        await client.call_tool(
            "write", {"path": "failed.md", "content": "x", "transaction": handle}
        )
        with pytest.raises(ToolError, match=r"failed.*staged"):
            await client.call_tool("git_sync", {"direction": "push"})
        assert _run_git(git_repo_pair.remote_path, "rev-parse", "HEAD") == remote
        assert "failed.md" in _run_git(
            git_repo_pair.local_path, "diff", "--cached", "--name-only"
        )


async def test_sync_cannot_close_another_principals_group(
    git_repo_pair: GitRepoPair, _git_managed_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = Principal("alice", None, None, "human")
    bob = Principal("bob", None, None, "human")
    monkeypatch.setattr(
        "markdown_vault_mcp._tools.transactions.resolve_mcp_principal", lambda: alice
    )
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool("begin_transaction", {"description": "Alice"})
        ).data["handle"]
        await client.call_tool(
            "write", {"path": "alice.md", "content": "x", "transaction": handle}
        )
        monkeypatch.setattr(
            "markdown_vault_mcp._tools.transactions.resolve_mcp_principal", lambda: bob
        )
        with pytest.raises(ToolError, match="Unknown"):
            await client.call_tool(
                "git_sync", {"direction": "push", "transaction": handle}
            )
        await client.call_tool("git_sync", {"direction": "push"})
        assert "Alice" not in _run_git(git_repo_pair.remote_path, "log", "--format=%s")
        monkeypatch.setattr(
            "markdown_vault_mcp._tools.transactions.resolve_mcp_principal",
            lambda: alice,
        )
        assert (await client.call_tool("end_transaction", {"handle": handle})).data[
            "status"
        ] == "committed"


def _assert_owner_preflight(
    dispatcher: WriteCallbackDispatcher,
    alice: Principal,
    manual: CommitGroup,
    active: CommitGroup,
    other: CommitGroup,
) -> None:
    """Preflight rejects active and foreign groups without closing earlier ones."""
    from markdown_vault_mcp._commit_groups import CommitGroupError

    with (
        dispatcher.bind_group(active.handle, alice),
        pytest.raises(CommitGroupError, match="active"),
    ):
        dispatcher.close_owner_groups(alice)
    with dispatcher.bind_group(manual.handle, alice):
        pass
    with pytest.raises(CommitGroupError, match="Unknown"):
        dispatcher.close_owner_groups(alice, other.handle)


def test_owner_settlement_preflights_and_selects_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import Future
    from threading import Condition

    from markdown_vault_mcp.write_callback import WriteCallbackDispatcher

    alice = Principal("alice", None, None, "human")
    bob = Principal("bob", None, None, "human")
    dispatcher = WriteCallbackDispatcher(None)
    completions: dict[str, Future[None]] = {}
    condition = Condition()

    def enqueue(_scope: object) -> Future[None]:
        future: Future[None] = Future()
        with condition:
            handle = next(
                g.handle for g in dispatcher._groups.values() if g.scope is _scope
            )
            completions[handle] = future
            condition.notify_all()
        return future

    def complete(handle: str) -> None:
        with condition:
            assert condition.wait_for(lambda: handle in completions, timeout=5)
            completions[handle].set_result(None)

    monkeypatch.setattr(dispatcher, "_enqueue_scope_end", enqueue)
    try:
        manual = dispatcher.open_group("Manual", alice)
        automatic = dispatcher.open_group("Automatic", alice)
        other = dispatcher.open_group("Other", bob)
        active = dispatcher.open_group("Active", alice)
        _assert_owner_preflight(dispatcher, alice, manual, active, other)

        manual_result = dispatcher.close_group(manual.handle, alice)
        auto_result = dispatcher.close_group(automatic.handle, alice, _automatic=True)
        other_result = dispatcher.close_group(other.handle, bob, _automatic=True)
        results = dispatcher.close_owner_groups(alice)
        assert len(results) == 3
        assert {manual_result, auto_result} < set(results)
        assert dispatcher.close_group(manual.handle, alice) is manual_result
        complete(manual.handle)
        complete(automatic.handle)
        complete(active.handle)
        assert dispatcher.close_owner_groups(alice) == []
        later = dispatcher.open_group("Later", alice)
        results = dispatcher.close_owner_groups(alice, automatic.handle)
        assert len(results) == 2 and auto_result in results
        complete(later.handle)
        assert dispatcher.close_group(automatic.handle, alice) is auto_result
        assert dispatcher.close_owner_groups(alice, automatic.handle) == [auto_result]
        assert dispatcher.close_owner_groups(bob) == [other_result]
        complete(other.handle)
        assert other_result.result().status == "no_changes"
    finally:
        for future in list(completions.values()):
            if not future.done():
                future.set_result(None)
        dispatcher.close()


async def test_sync_explicit_closed_and_auto_closed_handles(
    git_repo_pair: GitRepoPair, _git_managed_env: Path
) -> None:
    from markdown_vault_mcp import domain

    async with Client(make_server()) as client:
        closed = (
            await client.call_tool("begin_transaction", {"description": "Closed"})
        ).data["handle"]
        await client.call_tool("end_transaction", {"handle": closed})
        with pytest.raises(ToolError, match="Unknown"):
            await client.call_tool("git_sync", {"transaction": closed})
        handle = (
            await client.call_tool("begin_transaction", {"description": "Auto"})
        ).data["handle"]
        await client.call_tool(
            "write", {"path": "auto.md", "content": "x", "transaction": handle}
        )
        vault = domain.get_vault_singleton()
        assert vault is not None
        auto = vault._write_callback.close_group(handle, None, _automatic=True)
        assert auto.result(timeout=10).status == "committed"
        result = await client.call_tool(
            "git_sync", {"direction": "push", "transaction": handle}
        )
        assert result.data["push"]["applied"]
        assert (await client.call_tool("end_transaction", {"handle": handle})).data[
            "status"
        ] == "committed"
        assert "Auto" in _run_git(git_repo_pair.remote_path, "log", "--format=%s")


@pytest.mark.usefixtures("_mcp_env")
async def test_no_git_result_reports_failed_commit(
    monkeypatch: pytest.MonkeyPatch, vault_path: Path
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(vault_path.parent))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool("begin_transaction", {"description": "No git"})
        ).data["handle"]
        await client.call_tool(
            "write", {"path": "new.md", "content": "text", "transaction": handle}
        )
        result = await client.call_tool("end_transaction", {"handle": handle})
        assert result.data == {
            "status": "failed",
            "sha": None,
            "files": 0,
            "history_failed": False,
        }
        assert (await client.call_tool("read", {"path": "new.md"})).data[
            "content"
        ] == "text"


async def test_group_includes_attachment_and_explicit_auto_failure_blocks_sync(
    git_repo_pair: GitRepoPair, _git_managed_env: Path
) -> None:
    from markdown_vault_mcp import domain

    hook = git_repo_pair.local_path / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool("begin_transaction", {"description": "Attachment"})
        ).data["handle"]
        await client.call_tool(
            "write",
            {"path": "file.png", "content_base64": "eA==", "transaction": handle},
        )
        vault = domain.get_vault_singleton()
        assert vault is not None
        assert (
            vault._write_callback.close_group(handle, None, _automatic=True)
            .result(timeout=10)
            .status
            == "failed"
        )
        with pytest.raises(ToolError, match="commit failed"):
            await client.call_tool(
                "git_sync", {"direction": "push", "transaction": handle}
            )
        assert (await client.call_tool("end_transaction", {"handle": handle})).data[
            "status"
        ] == "failed"
        assert (
            _run_git(git_repo_pair.remote_path, "log", "-1", "--format=%s").strip()
            == "initial"
        )


@pytest.mark.parametrize("fail", [False, True])
async def test_sync_waits_for_pending_automatic_close(
    git_repo_pair: GitRepoPair,
    _git_managed_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail: bool,
) -> None:
    import asyncio
    import threading

    from markdown_vault_mcp import domain
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    if fail:
        hook = git_repo_pair.local_path / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
    entered, release = threading.Event(), threading.Event()
    original = GitWriteStrategy.on_write_batch

    def gated(strategy, items, tool_name, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(strategy, items, tool_name, **kwargs)

    monkeypatch.setattr(GitWriteStrategy, "on_write_batch", gated)
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool("begin_transaction", {"description": "Pending auto"})
        ).data["handle"]
        await client.call_tool(
            "write", {"path": "pending.md", "content": "x", "transaction": handle}
        )
        vault = domain.get_vault_singleton()
        assert vault is not None
        future = vault._write_callback.close_group(handle, None, _automatic=True)
        task = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task = asyncio.create_task(
                client.call_tool("git_sync", {"direction": "push"})
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.2)
            assert (
                _run_git(git_repo_pair.remote_path, "log", "-1", "--format=%s").strip()
                == "initial"
            )
        finally:
            release.set()
        assert task is not None
        if fail:
            with pytest.raises(ToolError, match="commit failed"):
                await task
        else:
            await task
        assert (await asyncio.wrap_future(future)).status == (
            "failed" if fail else "committed"
        )
        assert _run_git(
            git_repo_pair.remote_path, "log", "-1", "--format=%s"
        ).strip() == ("initial" if fail else "Pending auto")


async def test_end_transaction_bounds_pending_commit(
    git_repo_pair: GitRepoPair, _git_managed_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading

    from markdown_vault_mcp import domain
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    entered, release = threading.Event(), threading.Event()
    original = GitWriteStrategy.on_write_batch

    def gated(strategy, items, tool_name, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(strategy, items, tool_name, **kwargs)

    monkeypatch.setattr(GitWriteStrategy, "on_write_batch", gated)
    monkeypatch.setattr(
        "markdown_vault_mcp._server_commit_scope._SCOPE_WAIT_TIMEOUT", 0.1
    )
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool("begin_transaction", {"description": "Bounded"})
        ).data["handle"]
        await client.call_tool(
            "write", {"path": "bounded.md", "content": "x", "transaction": handle}
        )
        task = asyncio.create_task(
            client.call_tool("end_transaction", {"handle": handle})
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(ToolError, match="pending"):
                await asyncio.wait_for(asyncio.shield(task), 0.8)
            vault = domain.get_vault_singleton()
            assert vault is not None
            future = vault._write_callback._finished_groups[handle][1]
            assert not future.cancelled() and not future.done()
            with pytest.raises(ToolError, match="pending"):
                await asyncio.wait_for(
                    client.call_tool("git_sync", {"direction": "push"}), 0.8
                )
            assert not future.cancelled() and not future.done()
            assert (
                _run_git(git_repo_pair.remote_path, "log", "-1", "--format=%s").strip()
                == "initial"
            )
        finally:
            release.set()
            if not task.done():
                await task
        assert (await asyncio.wrap_future(future)).status == "committed"
        assert (await client.call_tool("git_sync", {"direction": "push"})).data["push"][
            "applied"
        ]
        assert (
            _run_git(git_repo_pair.remote_path, "log", "-1", "--format=%s").strip()
            == "Bounded"
        )


@pytest.mark.parametrize("explicit", [False, True])
async def test_sync_joins_pending_manual_close_and_second_end(
    git_repo_pair: GitRepoPair,
    _git_managed_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
) -> None:
    import asyncio
    import threading

    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    entered, release = threading.Event(), threading.Event()
    original = GitWriteStrategy.on_write_batch

    def gated(strategy, items, tool_name, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(strategy, items, tool_name, **kwargs)

    monkeypatch.setattr(GitWriteStrategy, "on_write_batch", gated)
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool("begin_transaction", {"description": "Manual"})
        ).data["handle"]
        await client.call_tool(
            "write", {"path": "manual.md", "content": "x", "transaction": handle}
        )
        first = asyncio.create_task(
            client.call_tool("end_transaction", {"handle": handle})
        )
        second = sync = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            second = asyncio.create_task(
                client.call_tool("end_transaction", {"handle": handle})
            )
            args = {"direction": "push"}
            if explicit:
                args["transaction"] = handle
            sync = asyncio.create_task(client.call_tool("git_sync", args))
            for task in (second, sync):
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.1)
        finally:
            release.set()
        assert second is not None and sync is not None
        assert (await first).data["status"] == "committed"
        assert (await second).data["status"] == "committed"
        assert (await sync).data["push"]["applied"]
        assert (
            _run_git(git_repo_pair.remote_path, "log", "-1", "--format=%s").strip()
            == "Manual"
        )
        with pytest.raises(ToolError, match="Unknown"):
            await client.call_tool("end_transaction", {"handle": handle})


async def _assert_cancelled_write_commits(
    client: Client[Any], pair: GitRepoPair, handle: str, path: str
) -> None:
    import asyncio

    async with asyncio.timeout(5):
        while True:
            try:
                result = await client.call_tool("end_transaction", {"handle": handle})
                break
            except ToolError as exc:
                assert "active" in str(exc)
                await asyncio.sleep(0.01)
    assert result.data["status"] == "committed"
    assert result.data["files"] == 1
    assert _run_git(pair.local_path, "log", "-1", "--format=%s").strip() == (
        "Cancelled write"
    )
    assert _run_git(pair.local_path, "show", f"HEAD:{path}") == "x"
    await client.call_tool("git_sync", {"direction": "push"})
    assert _run_git(pair.remote_path, "show", f"HEAD:{path}") == "x"


@pytest.mark.parametrize("attachment", [False, True])
@pytest.mark.parametrize("after_callback", [False, True])
@pytest.mark.parametrize("settle", ["end_transaction", "git_sync"])
async def test_cancelled_write_keeps_transaction_active_until_worker_finishes(
    git_repo_pair: GitRepoPair,
    _git_managed_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    attachment: bool,
    after_callback: bool,
    settle: str,
) -> None:
    """A cancelled request cannot close a group while its write still runs."""
    import asyncio
    import threading

    from markdown_vault_mcp import domain

    entered, release, completed = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    path = "cancel.png" if attachment else "cancel.md"
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool(
                "begin_transaction", {"description": "Cancelled write"}
            )
        ).data["handle"]
        vault = domain.get_vault_singleton()
        assert vault is not None
        method = "write_attachment" if attachment else "write"
        original = getattr(vault.writer, method)

        def gated(*args, **kwargs):
            try:
                if not after_callback:
                    entered.set()
                    assert release.wait(10)
                result = original(*args, **kwargs)
                if after_callback:
                    entered.set()
                    assert release.wait(10)
                return result
            finally:
                completed.set()

        monkeypatch.setattr(vault.writer, method, gated)
        args = {"path": path, "transaction": handle}
        args.update({"content_base64": "eA=="} if attachment else {"content": "x"})
        task = asyncio.create_task(client.call_tool("write", args))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 1)
            except (asyncio.CancelledError, TimeoutError):
                pass  # Cancellation may be delayed until the worker has finished.
            else:
                pytest.fail("cancelled request unexpectedly returned")
            settlement = (
                {"handle": handle}
                if settle == "end_transaction"
                else {"direction": "push"}
            )
            with pytest.raises(ToolError, match="active"):
                await client.call_tool(settle, settlement)
        finally:
            release.set()
            assert await asyncio.to_thread(completed.wait, 10)
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)
        await _assert_cancelled_write_commits(client, git_repo_pair, handle, path)


async def _prepare_automatic_close(
    vault: Any, handle: str, selection: str, monkeypatch: pytest.MonkeyPatch
) -> Event | None:
    """Arrange a pending automatic close for the sync probe."""
    import asyncio

    if selection != "pending":
        return None
    entered, release = Event(), Event()
    event = vault._write_callback.group_event
    assert event is not None

    def paused(*args: Any) -> None:
        entered.set()
        assert release.wait(5)
        event(*args)

    monkeypatch.setattr(vault._write_callback, "group_event", paused)
    vault._write_callback.close_group(handle, None, _automatic=True)
    assert await asyncio.to_thread(entered.wait, 5)
    return release


@pytest.mark.parametrize("selection", ["implicit", "pending"])
async def test_sync_reports_settled_history_failures(
    git_repo_pair: GitRepoPair,
    _git_managed_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    """Sync surfaces newly settled history failures without stopping Git."""
    import asyncio

    from markdown_vault_mcp import domain

    root = git_repo_pair.local_path

    def prepare_logs() -> None:
        (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
        (root / "bad").mkdir()
        (root / "bad/log.md").write_text("# Log\n\n```\nunclosed\n")

    prepare_logs()
    for key, value in (("MODE", "on"), ("LOG", "events"), ("FOLDERS", "bad")):
        monkeypatch.setenv(f"MARKDOWN_VAULT_MCP_OKF_{key}", value)
    async with Client(make_server()) as client:
        handle = (
            await client.call_tool("begin_transaction", {"description": "Partial"})
        ).data["handle"]
        for path in ("good.md", "bad/note.md"):
            await client.call_tool(
                "write", {"path": path, "content": "# Note\n", "transaction": handle}
            )
        vault = domain.get_vault_singleton()
        assert vault is not None
        if selection == "implicit":
            other = (
                await client.call_tool("begin_transaction", {"description": "Other"})
            ).data["handle"]
            await client.call_tool(
                "write",
                {"path": "other.md", "content": "# Other\n", "transaction": other},
            )
        release = await _prepare_automatic_close(vault, handle, selection, monkeypatch)
        args = {"direction": "push"}
        task = asyncio.create_task(client.call_tool("git_sync", args))
        try:
            if selection == "pending":
                await asyncio.sleep(0.1)
                assert not task.done()
        finally:
            if release is not None:
                release.set()
        result = (await task).data
        assert result["push"]["applied"]
        assert result["history_failed"] is True
