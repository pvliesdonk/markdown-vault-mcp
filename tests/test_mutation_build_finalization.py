"""Mutation refreshes include build finalization on every entry point (#1464)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from pathlib import Path


def _build(col: Vault, kind: str) -> None:
    if kind == "sync":
        col.index.build_index()
    elif kind == "async":
        col.index.build_index_async().result(5)
    else:
        col.index.start_background_build_index()


def _mutate(col: Vault, operation: str) -> None:
    if operation == "convert":
        assert col.writer.okf_convert_links(folder="links").links_converted == 1
    elif operation == "generate":
        assert col.writer.okf_generate_index(folder="notes").entries == 1
    elif operation == "rename":
        assert (
            col.writer.rename(
                "notes/target.md", "notes/new.md", update_links=True
            ).updated_links
            == 1
        )
    elif operation == "move":
        col.writer.move_folder("notes", "moved")
        assert "[[moved/target]]" in (col.source_dir / "links/source.md").read_text()
    else:
        col.writer.write("notes/new.md", "# New\n")


def _vault(root: Path, *, maintain: bool = False) -> Vault:
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "notes").mkdir()
    (root / "notes/target.md").write_text("# Target\n")
    (root / "links").mkdir()
    (root / "links/source.md").write_text("See [[target]].\n")
    return Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_write=maintain),
    )


@pytest.mark.parametrize("kind", ["sync", "async", "legacy"])
@pytest.mark.parametrize(
    "operation", ["convert", "generate", "rename", "move", "maintain"]
)
def test_mutation_waits_for_build_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, operation: str
) -> None:
    col = _vault(tmp_path, maintain=operation == "maintain")
    started, release = threading.Event(), threading.Event()
    original = col._fts.set_build_completed

    def held_finalize() -> None:
        started.set()
        assert release.wait(5)
        original()

    monkeypatch.setattr(col._fts, "set_build_completed", held_finalize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            try:
                build = pool.submit(_build, col, kind)
                assert started.wait(5)
                assert not col.index.is_queryable()
                mutation = pool.submit(_mutate, col, operation)
                with pytest.raises(TimeoutError):
                    mutation.result(0.1)
                release.set()
                build.result(5)
                mutation.result(5)
            finally:
                release.set()
        if operation == "maintain":
            assert "/notes/new.md" in (tmp_path / "notes/index.md").read_text()
    finally:
        release.set()
        col.close()


@pytest.mark.parametrize("kind", ["sync", "async", "legacy"])
def test_failed_finalization_rejects_mutation_with_build_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from markdown_vault_mcp.exceptions import IndexUnavailableError

    col = _vault(tmp_path)
    started, release = threading.Event(), threading.Event()

    def fail_finalize() -> None:
        started.set()
        assert release.wait(5)
        raise OSError("completion marker failed")

    monkeypatch.setattr(col._fts, "set_build_completed", fail_finalize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            try:
                build = pool.submit(_build, col, kind)
                assert started.wait(5)
                mutation = pool.submit(_mutate, col, "convert")
                with pytest.raises(TimeoutError):
                    mutation.result(0.1)
                release.set()
                if kind != "legacy":
                    with pytest.raises(OSError, match="completion marker failed"):
                        build.result(5)
                else:
                    build.result(5)
                with pytest.raises(IndexUnavailableError) as error:
                    mutation.result(5)
                assert error.value.reason == "build_failed"
                assert "completion marker failed" in str(error.value)
                assert (tmp_path / "links/source.md").read_text() == "See [[target]].\n"
            finally:
                release.set()
    finally:
        release.set()
        col.close()


def test_refresh_and_finalization_share_one_timeout_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import Future
    from unittest.mock import Mock

    import markdown_vault_mcp.indexing.coordinator as module

    col = _vault(tmp_path)
    pending: Future[None] = Future()
    pending.set_result(None)
    result = Mock(side_effect=TimeoutError)
    build = Mock()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                col._coordinator._builds,
                "enqueue_refresh",
                lambda _writer: (build, pending),
            )
            patch.setattr(pending, "result", result)
            patch.setattr(
                module.time, "monotonic", Mock(side_effect=[100.0, 100.2, 100.7])
            )
            with pytest.raises(
                TimeoutError, match="dependent mutation was not started"
            ):
                col._coordinator.prepare_index_read(timeout=1)
            build.require_success.assert_called_once_with(pytest.approx(0.8))
            result.assert_called_once_with(timeout=pytest.approx(0.3))
    finally:
        col.close()
