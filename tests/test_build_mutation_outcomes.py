"""All indexed mutations share one policy for build outcomes (#1464/#1483)."""

from __future__ import annotations

import threading
from concurrent.futures import CancelledError
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.indexing import ProcessDirtyPaths
from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.types import IndexStats


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/target.md").write_text("# Target\n")
    (tmp_path / "source.md").write_text("See [[target]].\n")
    col = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    return col


def _mutate(col: Vault, operation: str) -> None:
    if operation == "rename":
        assert (
            col.writer.rename(
                "notes/target.md", "notes/new.md", update_links=True
            ).updated_links
            == 1
        )
    elif operation == "move":
        col.writer.move_folder("notes", "moved")
        assert "[[moved/target]]" in (col.source_dir / "source.md").read_text()
    elif operation == "convert":
        assert col.writer.okf_convert_links().links_converted == 1
    else:
        col.writer.okf_generate_index(folder="notes")
        assert "/notes/target.md" in (col.source_dir / "notes/index.md").read_text()


def _files(col: Vault) -> dict[Path, bytes]:
    return {
        p.relative_to(col.source_dir): p.read_bytes()
        for p in col.source_dir.rglob("*.md")
    }


def _failed_build(col: Vault, kind: str) -> None:
    if kind == "legacy":
        col.index.start_background_build_index()
    else:
        with pytest.raises(OSError, match="injected failure"):
            if kind == "sync":
                col.index.build_index()
            else:
                col.index.build_index_async().result(5)
    with pytest.raises(IndexUnavailableError) as error:
        col.index.wait_until_queryable(timeout=5)
    assert error.value.reason == "build_failed"


@pytest.mark.parametrize("kind", ["sync", "async", "legacy"])
@pytest.mark.parametrize("phase", ["clear", "scan", "publish"])
@pytest.mark.parametrize("operation", ["rename", "move", "convert", "generate"])
def test_failed_build_prevents_all_mutations_and_retry_recovers(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, kind: str, phase: str, operation: str
) -> None:
    before = _files(vault)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected failure")

    target, method = {
        "clear": (vault._fts, "clear_build_completed"),
        "scan": (vault._index_mgr, "build_index"),
        "publish": (vault._fts, "set_build_completed"),
    }[phase]
    try:
        with monkeypatch.context() as patch:
            patch.setattr(target, method, fail)
            _failed_build(vault, kind)
            with pytest.raises(
                IndexUnavailableError, match="injected failure"
            ) as error:
                _mutate(vault, operation)
            assert error.value.reason == "build_failed"
            assert _files(vault) == before
        vault.index.build_index()
        _mutate(vault, operation)
    finally:
        vault.close()


@pytest.mark.parametrize("operation", ["rename", "move", "convert", "generate"])
@pytest.mark.parametrize("state", ["cancelled", "timeout"])
def test_pending_build_never_authorizes_mutation(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, operation: str, state: str
) -> None:
    entered, release = threading.Event(), threading.Event()
    original = vault._coordinator.writer._runners["process_dirty_paths"]

    def held_refresh(job: object, ctx: object) -> None:
        entered.set()
        assert release.wait(5)
        original(job, ctx)

    monkeypatch.setattr(
        vault._doc_mgr,
        "_sync_index",
        lambda: vault._coordinator.prepare_index_read(0.02),
    )
    try:
        vault._coordinator.writer._runners["process_dirty_paths"] = held_refresh
        vault._coordinator.writer.submit(ProcessDirtyPaths())
        assert entered.wait(5)
        build = vault.index.build_index_async()
        if state == "cancelled":
            assert build.cancel()
        before = _files(vault)
        error = IndexUnavailableError if state == "cancelled" else TimeoutError
        with pytest.raises(error):
            _mutate(vault, operation)
        assert _files(vault) == before
        release.set()
        assert vault.index.wait_for_drain(timeout=5)
        if state == "cancelled":
            with pytest.raises(CancelledError):
                build.result()
            assert vault._coordinator._readiness.error is None
            assert not vault._fts.is_build_completed()
        else:
            build.result(5)
        vault.index.build_index()
        _mutate(vault, operation)
    finally:
        release.set()
        vault.close()


def test_prior_failed_attempt_cannot_be_hidden_by_later_success(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = vault._index_mgr.build_index

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected failure")

    try:
        monkeypatch.setattr(vault._index_mgr, "build_index", fail)
        _failed_build(vault, "async")
        attempt, refresh = vault._coordinator._builds.enqueue_refresh(
            vault._coordinator.writer
        )
        assert attempt is not None
        monkeypatch.setattr(vault._index_mgr, "build_index", original)
        vault.index.build_index()
        refresh.result(5)
        with pytest.raises(IndexUnavailableError, match="injected failure"):
            attempt.require_success(0)
        _mutate(vault, "rename")
    finally:
        vault.close()


def test_older_completion_cannot_publish_newer_build_readiness(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()
    original = vault._index_mgr.build_index
    builds = 0

    def held_build(*, force: bool = False) -> IndexStats:
        nonlocal builds
        builds += 1
        if builds == 1:
            entered.set()
            assert release.wait(5)
        else:
            raise OSError("second build failed")
        return original(force=force)

    monkeypatch.setattr(vault._index_mgr, "build_index", held_build)
    try:
        first = vault.index.build_index_async()
        assert entered.wait(5)
        second = vault.index.build_index_async(force=True)
        observed: list[bool] = []
        first.add_done_callback(lambda _: observed.append(vault.index.is_queryable()))
        release.set()
        first.result(5)
        with pytest.raises(OSError, match="second build failed"):
            second.result(5)
        assert observed == [False]
        assert vault._coordinator._readiness.error is not None
        with pytest.raises(IndexUnavailableError):
            _mutate(vault, "rename")
    finally:
        release.set()
        vault.close()


def test_build_timeout_error_is_failure_not_wait_timeout(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("provider timed out during build")

    monkeypatch.setattr(vault._index_mgr, "build_index", fail)
    try:
        with pytest.raises(TimeoutError):
            vault.index.build_index()
        with pytest.raises(IndexUnavailableError, match="provider timed out") as error:
            _mutate(vault, "rename")
        assert error.value.reason == "build_failed"
    finally:
        vault.close()


def test_warm_check_preserves_queryability_until_reuse_is_confirmed(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    vault.index.build_index()
    entered, release = threading.Event(), threading.Event()
    original = vault._coordinator._builds._is_warm

    def held_warm_check() -> bool:
        entered.set()
        assert release.wait(5)
        return original()

    monkeypatch.setattr(vault._coordinator._builds, "_is_warm", held_warm_check)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            build = pool.submit(vault.index.build_index_async)
            assert entered.wait(5)
            assert vault.index.is_queryable()
            release.set()
            future = build.result(5)
        assert future.done()
        assert future.result().chunks_indexed == 0
        assert vault.index.is_queryable()
    finally:
        release.set()
        vault.close()


@pytest.mark.parametrize("kind", ["sync", "async", "legacy"])
def test_failed_build_keeps_primary_okf_write_and_existing_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "notes").mkdir()
    listing = tmp_path / "notes/index.md"
    listing.write_text("# Existing listing\n")
    col = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=False, okf_write=True)
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected failure")

    try:
        monkeypatch.setattr(col._index_mgr, "build_index", fail)
        _failed_build(col, kind)
        col.writer.write("notes/new.md", "# New\n")
        assert "# New" in (tmp_path / "notes/new.md").read_text()
        assert listing.read_text() == "# Existing listing\n"
        assert "new.md" in (tmp_path / "notes/log.md").read_text()
    finally:
        col.close()


def test_cancelled_later_attempt_does_not_hide_running_build(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()
    original = vault._fts.clear_build_completed
    clears = 0

    def held_clear() -> None:
        nonlocal clears
        clears += 1
        if clears == 1:
            entered.set()
            assert release.wait(5)
        original()

    try:
        vault.index.build_index()
        monkeypatch.setattr(vault._fts, "clear_build_completed", held_clear)
        first = vault.index.build_index_async(force=True)
        assert entered.wait(5)  # The old warm marker is still present.
        second = vault.index.build_index_async(force=True)
        assert second.cancel()
        third = vault.index.build_index_async()
        assert not third.done(), (
            "must not warm-start through a running destructive build"
        )
        release.set()
        first.result(5)
        third.result(5)
        assert clears == 2
        assert vault.index.is_queryable()
    finally:
        release.set()
        vault.close()


@pytest.mark.parametrize("operation", ["rename", "move", "convert", "generate"])
def test_runner_cancellation_is_terminal_without_becoming_scan_failure(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    def cancel(*_args: object, **_kwargs: object) -> None:
        raise CancelledError()

    monkeypatch.setattr(vault._index_mgr, "build_index", cancel)
    before = _files(vault)
    try:
        with pytest.raises(CancelledError):
            vault.index.build_index_async().result(5)
        with pytest.raises(IndexUnavailableError) as error:
            _mutate(vault, operation)
        assert error.value.reason == "never_built"
        assert vault._coordinator._readiness.error is None
        assert _files(vault) == before
    finally:
        vault.close()


def test_raw_writer_build_retains_low_level_semantics(vault: Vault) -> None:
    from markdown_vault_mcp.indexing import BuildIndex

    try:
        stats = vault._coordinator.writer.submit(BuildIndex()).result(5)
        assert stats.documents_indexed == 2
        assert not vault.index.is_queryable()
    finally:
        vault.close()


def test_dispatcher_cannot_silently_drop_build_publication(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        vault._coordinator.writer._runners, "build_index", lambda _job, _ctx: None
    )
    try:
        with pytest.raises(RuntimeError, match="without publishing"):
            vault.index.build_index_async().result(5)
        assert vault.index.get_index_status()["status"] == "failed"
    finally:
        vault.close()


def test_cancel_racing_dispatch_failure_preserves_cancellation(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import Future

    queued: Future[object] = Future()
    monkeypatch.setattr(vault._coordinator.writer, "submit", lambda _job: queued)
    entered, release = threading.Event(), threading.Event()
    build = vault.index.build_index_async()
    original = build.set_running_or_notify_cancel

    def held_claim() -> bool:
        entered.set()
        assert release.wait(5)
        return original()

    monkeypatch.setattr(build, "set_running_or_notify_cancel", held_claim)
    delivery = threading.Thread(
        target=lambda: queued.set_exception(OSError("dispatch failed"))
    )
    try:
        delivery.start()
        assert entered.wait(5)
        assert build.cancel()
        release.set()
        delivery.join(5)
        assert not delivery.is_alive()
        with pytest.raises(CancelledError):
            build.result()
        assert vault._coordinator._readiness.error is None
    finally:
        release.set()
        delivery.join(5)
        vault.close()
