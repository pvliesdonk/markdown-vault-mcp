"""Index-dependent mutations must observe completed vault writes (#1464)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    col = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


def _mutate(vault: Vault, operation: str) -> None:
    if operation == "convert":
        result = vault.writer.okf_convert_links(folder="links")
        assert result.links_converted == 1
        assert result.links_skipped == 0
    elif operation == "generate":
        assert vault.writer.okf_generate_index(folder="notes").entries == 1
    elif operation == "rename":
        assert (
            vault.writer.rename(
                "notes/target.md", "notes/new.md", update_links=True
            ).updated_links
            == 1
        )
    else:
        vault.writer.move_folder("notes", "moved")


@pytest.mark.parametrize("operation", ["convert", "generate", "rename", "move"])
def test_mutation_waits_for_prior_writes(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Hold the actual index worker so the stale window cannot settle by luck."""
    started = threading.Event()
    release = threading.Event()
    original = vault._index_mgr.process_dirty_paths

    def held_refresh(paths: set[str]) -> None:
        started.set()
        assert release.wait(5), "test did not release the index writer"
        original(paths)

    monkeypatch.setattr(vault._index_mgr, "process_dirty_paths", held_refresh)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            vault.writer.write("notes/target.md", "# Target\n")
            assert started.wait(5)
            vault.writer.write("links/source.md", "See [[target]].\n")
            mutation = pool.submit(_mutate, vault, operation)
            with pytest.raises(TimeoutError):
                mutation.result(timeout=0.1)
            release.set()
            mutation.result(timeout=5)
        finally:
            release.set()
    source = (vault.source_dir / "links/source.md").read_text(encoding="utf-8")
    if operation == "convert":
        assert "[target](/notes/target.md)" in source
    elif operation == "generate":
        assert "/notes/target.md" in (vault.source_dir / "notes/index.md").read_text(
            encoding="utf-8"
        )
    elif operation == "rename":
        assert "[[notes/new]]" in source
    else:
        assert "[[moved/target]]" in source


@pytest.mark.parametrize("operation", ["convert", "generate", "rename", "move"])
def test_failed_graph_refresh_aborts_before_mutation(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    vault.writer.write("notes/target.md", "# Target\n")
    vault.writer.write("links/source.md", "See [[target]].\n")
    assert vault.index.wait_for_drain(timeout=5)
    before = {
        p.relative_to(vault.source_dir): p.read_bytes()
        for p in vault.source_dir.rglob("*.md")
    }

    def fail_resolution() -> int:
        raise OSError("graph refresh failed")

    with monkeypatch.context() as patch:
        patch.setattr(vault._fts, "resolve_vault_wikilinks", fail_resolution)
        vault._coordinator.writer.mark_dirty(["links/source.md"])
        with pytest.raises(OSError, match="graph refresh failed"):
            _mutate(vault, operation)
        assert vault.index.get_index_status()["dirty_paths"] == 1
        assert {
            p.relative_to(vault.source_dir): p.read_bytes()
            for p in vault.source_dir.rglob("*.md")
        } == before
    # The failed job retains its work, and the next mutation retries it.
    _mutate(vault, operation)


def test_refresh_timeout_cancels_pending_job(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import Future
    from typing import Any

    pending: Future[Any] = Future()
    monkeypatch.setattr(vault._coordinator.writer, "submit", lambda _job: pending)
    with pytest.raises(TimeoutError, match="dependent mutation was not started"):
        vault._coordinator.prepare_index_read(timeout=0)
    assert pending.cancelled()


def test_graph_recovery_does_not_mask_primary_failure(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_parse(_path: str) -> bool:
        raise RuntimeError("primary parse failure")

    def fail_resolution() -> int:
        raise OSError("secondary graph failure")

    monkeypatch.setattr(vault._index_mgr, "_is_path_excluded", fail_parse)
    monkeypatch.setattr(vault._fts, "resolve_vault_wikilinks", fail_resolution)
    vault._coordinator.writer.mark_dirty(["a.md"])
    with pytest.raises(RuntimeError, match="primary parse failure"):
        vault._coordinator.prepare_index_read()


def test_refresh_does_not_wait_for_followup_embeddings(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()

    def held_embeddings(_paths: set[str]) -> None:
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(vault._index_mgr, "flush_dirty_embeddings", held_embeddings)
    (vault.source_dir / "target.md").write_text("# Target\n", encoding="utf-8")
    vault._coordinator.writer.mark_dirty(["target.md"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            refresh = pool.submit(vault._coordinator.prepare_index_read)
            assert started.wait(5)
            refresh.result(timeout=1)
            assert vault.reader.list_documents()[0].path == "target.md"
        finally:
            release.set()
