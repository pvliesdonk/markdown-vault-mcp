"""The live runtime owns only its supplied vault, never process publication."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, Mock

import pytest

from markdown_vault_mcp import _identity, domain
from markdown_vault_mcp._file_watcher import VaultFileWatcher
from markdown_vault_mcp._vault_runtime import _VaultRuntime
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections._assembly import VaultInstances
from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.git import GitWriteStrategy
from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from pathlib import Path


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _runtime(root: Path, word: str) -> _VaultRuntime:
    root.mkdir()
    (root / "same.md").write_text(f"# Note\n\n{word}\n")
    _git(root, "init")
    _git(root, "config", "user.name", "Synthetic Test")
    _git(root, "config", "user.email", "synthetic@example.test")
    _git(root, "add", "same.md")
    _git(root, "commit", "-m", "Initial synthetic note")
    settings = VaultSettings(
        index_path=root.parent / f"{root.name}.db",
        state_path=root.parent / f"{root.name}.json",
        read_only=False,
    )
    strategy = GitWriteStrategy(enable_pull=False, enable_push=False, repo_path=root)
    instances = VaultInstances(None, None, strategy, strategy, 0)
    vault = Vault(
        source_dir=root, settings=settings, git_strategy=strategy, on_write=strategy
    )
    return _VaultRuntime(
        vault, settings, instances, ProjectConfig(source_dir=root), "stdio"
    )


def _drain(vault: Vault) -> None:
    assert vault.index.wait_for_drain(timeout=10)
    assert vault._write_callback.drain(timeout=10)


def _assert_note(vault: Vault, word: str, absent: str) -> None:
    note = vault.reader.read("same.md")
    assert note is not None and word in note.content
    assert [hit.path for hit in vault.reader.search(word, mode="keyword")] == [
        "same.md"
    ]
    assert vault.reader.search(absent, mode="keyword") == []


async def test_runtimes_isolate_storage_git_watchers_and_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _runtime(tmp_path / "a", "apricot"), _runtime(tmp_path / "b", "blueberry")
    watchers: list[VaultFileWatcher] = []
    monkeypatch.setattr(
        VaultFileWatcher, "start", lambda watcher: watchers.append(watcher)
    )
    stopped: list[Path] = []
    real_stop = VaultFileWatcher.stop

    def stop(watcher: VaultFileWatcher) -> None:
        stopped.append(watcher._source_dir)
        real_stop(watcher)

    monkeypatch.setattr(VaultFileWatcher, "stop", stop)
    try:
        await a.start()
        await b.start()
        _drain(a.vault)
        _drain(b.vault)
        _assert_note(a.vault, "apricot", "blueberry")
        _assert_note(b.vault, "blueberry", "apricot")
        (tmp_path / "a" / "same.md").write_text("# Note\n\nfig\n")
        watchers[0]._on_change()
        _assert_note(a.vault, "fig", "apricot")
        _assert_note(b.vault, "blueberry", "fig")
        b_head = b.vault.git_head()
        a_head = a.vault.git_head()
        a.vault.writer.write("same.md", "# Note\n\ncranberry\n")
        _drain(a.vault)
        _assert_note(a.vault, "cranberry", "apricot")
        _assert_note(b.vault, "blueberry", "cranberry")
        assert a.vault.git_head() != a_head
        assert b.vault.git_head() == b_head
        assert (tmp_path / "a.db").is_file() and (tmp_path / "b.db").is_file()
        assert (tmp_path / "a.json").is_file() and (tmp_path / "b.json").is_file()

        a.close()
        assert stopped == [tmp_path / "a"]
        assert a.vault._coordinator.writer.is_closed()
        assert not b.vault._coordinator.writer.is_closed()
        b.vault.writer.write("same.md", "# Note\n\ndamson\n")
        _drain(b.vault)
        assert b.vault.git_head() != b_head
        _assert_note(b.vault, "damson", "cranberry")
        # Each watcher callback closes over its own vault, even after A stops.
        (tmp_path / "b" / "same.md").write_text("# Note\n\nelderberry\n")
        watchers[1]._on_change()
        _assert_note(b.vault, "elderberry", "damson")
    finally:
        if tmp_path / "a" not in stopped:
            a.close()
        b.close()
    assert stopped == [tmp_path / "a", tmp_path / "b"]


async def test_blocked_writer_does_not_block_another_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _runtime(tmp_path / "a", "apricot"), _runtime(tmp_path / "b", "blueberry")
    monkeypatch.setattr(VaultFileWatcher, "start", lambda _self: None)
    entered, release = threading.Event(), threading.Event()
    reindex = a.vault._index_mgr.reindex

    def blocked() -> object:
        entered.set()
        assert release.wait(timeout=10)
        return reindex()

    try:
        await a.start()
        await b.start()
        _drain(a.vault)
        _drain(b.vault)
        monkeypatch.setattr(a.vault._index_mgr, "reindex", blocked)
        pending = a.vault.index.reindex_async()
        assert await asyncio.to_thread(entered.wait, 10)
        (tmp_path / "b" / "same.md").write_text("# Note\n\ncranberry\n")
        await asyncio.wait_for(asyncio.wrap_future(b.vault.index.reindex_async()), 5)
        _assert_note(b.vault, "cranberry", "blueberry")
        assert not pending.done()
        release.set()
        await asyncio.wait_for(asyncio.wrap_future(pending), 5)
    finally:
        release.set()
        a.close()
        b.close()


@pytest.mark.parametrize("boot_reindex", [True, False])
@pytest.mark.parametrize("embeddings", [True, False])
@pytest.mark.parametrize("watcher_enabled", [True, False])
async def test_boot_jobs_and_close_remain_ordered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boot_reindex: bool,
    embeddings: bool,
    watcher_enabled: bool,
) -> None:
    events: list[str] = []
    vault = MagicMock(spec=Vault)
    instances = MagicMock(spec=VaultInstances)
    instances.embedding_provider = object() if embeddings else None
    instances.git_pull_interval_s = 0
    vault.sync_from_remote_before_index.side_effect = lambda: events.append("sync")
    vault.index.build_index_async.side_effect = lambda: events.append("build")
    vault.git_head.side_effect = lambda: events.append("head") or "boot-head"
    vault.index.reindex_async.side_effect = lambda: events.append("reindex") or "job"
    vault.adopt_boot_reindex.side_effect = lambda *_args: events.append("adopt")
    vault.mark_index_reconciled.side_effect = lambda *_args: events.append("accept")
    vault.index.build_embeddings_async.side_effect = lambda: events.append("embeddings")
    vault.start.side_effect = lambda: events.append("start")
    vault.close.side_effect = lambda: events.append("vault-close")
    instances.close.side_effect = lambda: events.append("instances-close")
    monkeypatch.setattr(VaultFileWatcher, "start", lambda _self: events.append("watch"))
    monkeypatch.setattr(
        VaultFileWatcher, "stop", lambda _self: events.append("unwatch")
    )
    runtime = _VaultRuntime(
        vault,
        VaultSettings(),
        instances,
        ProjectConfig(
            source_dir=tmp_path, boot_reindex=boot_reindex, file_watcher=watcher_enabled
        ),
        "stdio",
    )
    await runtime.start()
    runtime.close()
    expected = ["sync", "build", "head"]
    expected += ["reindex", "adopt"] if boot_reindex else ["accept"]
    expected += ["embeddings"] if embeddings else []
    expected += ["start"]
    expected += ["watch", "unwatch"] if watcher_enabled else []
    assert events == [
        *expected,
        "vault-close",
        "instances-close",
    ]
    if boot_reindex:
        vault.adopt_boot_reindex.assert_called_once_with("job", "boot-head")
    else:
        vault.mark_index_reconciled.assert_called_once_with("boot-head")


@pytest.mark.parametrize(
    ("failure", "event"),
    [
        (
            IndexUnavailableError("not ready", reason="busy"),
            "file_watcher_reindex_skipped reason=index_not_queryable",
        ),
        (RuntimeError("scan failed"), "file_watcher_reindex_failed"),
    ],
)
async def test_watcher_callback_logs_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
    event: str,
) -> None:
    runtime = _runtime(tmp_path / "a", "apricot")
    monkeypatch.setattr(VaultFileWatcher, "start", lambda _self: None)
    try:
        await runtime.start()
        _drain(runtime.vault)
        reindex = Mock(side_effect=failure)
        monkeypatch.setattr(runtime.vault.index, "reindex", reindex)
        assert runtime._file_watcher is not None
        with caplog.at_level("INFO"):
            runtime._file_watcher._on_change()
        reindex.assert_called_once_with()
        records = [record for record in caplog.records if record.msg == event]
        assert len(records) == 1
        if isinstance(failure, IndexUnavailableError):
            assert records[0].levelname == "INFO"
            assert records[0].exc_info is None
        else:
            assert records[0].levelname == "ERROR"
            assert records[0].exc_info is not None
            assert records[0].exc_info[1] is failure
    finally:
        runtime.close()


async def test_service_owns_partial_start_and_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = domain.Service(ProjectConfig(source_dir=tmp_path))
    vault, instances = MagicMock(spec=Vault), MagicMock(spec=VaultInstances)
    events: list[str] = []

    async def fail(runtime: _VaultRuntime) -> None:
        assert service.vault is runtime.vault
        assert domain.get_vault_singleton() is vault
        raise RuntimeError("startup interrupted")

    def close_vault() -> None:
        with pytest.raises(RuntimeError, match="not initialised"):
            domain.get_vault_singleton()
        events.append("vault")

    monkeypatch.setattr(domain, "_vault_singleton", None)
    monkeypatch.setattr(domain, "_vault_unavailable", None)
    monkeypatch.setattr(_VaultRuntime, "start", fail)
    vault.close.side_effect = close_vault
    instances.close.side_effect = lambda: events.append("instances")
    # Before _serve, Service owns the constructed collaborators.
    service._instances = instances
    with pytest.raises(RuntimeError, match="startup interrupted"):
        await service._serve(vault, VaultSettings(), instances)
    await service.stop()
    await service.stop()
    assert events == ["vault", "instances"]


async def test_runtime_does_not_assemble_or_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path / "a", "apricot")
    sentinel = Mock(spec=Vault)
    monkeypatch.setattr(domain, "_vault_singleton", sentinel)
    monkeypatch.setattr(_identity, "_name_claim", "custom_name")
    monkeypatch.setattr(_identity, "_email_claim", "custom_email")
    monkeypatch.setattr(_identity, "_service_subjects", frozenset({"sentinel"}))
    assembly = Mock(side_effect=AssertionError("runtime must not assemble"))
    monkeypatch.setattr(domain, "to_vault_instances", assembly)
    monkeypatch.setattr(VaultFileWatcher, "start", lambda _self: None)
    try:
        await runtime.start()
        _drain(runtime.vault)
    finally:
        runtime.close()
    assert domain.get_vault_singleton() is sentinel
    assert (_identity._name_claim, _identity._email_claim) == (
        "custom_name",
        "custom_email",
    )
    assert _identity._service_subjects == frozenset({"sentinel"})
    assembly.assert_not_called()
