"""Index-dependent mutations must observe completed vault writes (#1464)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
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

    def held_refresh(
        paths: set[str], *, on_refreshed: Callable[[str], None] | None = None
    ) -> None:
        started.set()
        assert release.wait(5), "test did not release the index writer"
        original(paths, on_refreshed=on_refreshed)

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


@pytest.mark.parametrize("operation", ["rename", "move"])
def test_disk_mutation_preserves_unbuilt_support(
    tmp_path: Path, operation: str
) -> None:
    col = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        col.writer.write("notes/target.md", "# Target\n")
        col.writer.write("links/source.md", "See [[target]].\n")
        _mutate(col, operation)
        assert not col.index.is_queryable()
    finally:
        col.close()


@pytest.mark.parametrize("failure", ["read", "validation", "tombstone"])
def test_failed_file_refresh_retries_without_mutating(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from typing import Any

    import markdown_vault_mcp.managers.index as index_module

    vault.writer.write("notes/target.md", "# Target\n")
    vault.writer.write("links/source.md", "Old content.\n")
    assert vault.index.wait_for_drain(timeout=5)
    source = vault.source_dir / "links/source.md"
    source.write_text("See [[target]].\n", encoding="utf-8")
    if failure == "tombstone":
        source.write_text("---\ninvalid: [\n---\n", encoding="utf-8")
    good = vault.source_dir / "healthy.md"
    good.write_text("# Healthy\n", encoding="utf-8")
    original = index_module.parse_note

    def fail_read(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == source:
            if failure == "validation":
                raise ValueError("temporary validation failure")
            raise OSError("temporary read failure")
        return original(path, *args, **kwargs)

    def fail_hash(_path: Path) -> str:
        raise OSError("temporary tombstone read failure")

    with monkeypatch.context() as patch:
        if failure == "tombstone":
            patch.setattr(index_module, "compute_file_hash", fail_hash)
        else:
            patch.setattr(index_module, "parse_note", fail_read)
        vault._coordinator.writer.mark_dirty(["links/source.md", "healthy.md"])
        with pytest.raises((OSError, ValueError), match="temporary"):
            vault._coordinator.prepare_index_read()
        # A successful sibling still refreshes, while the job retains work.
        assert vault._fts.get_note("healthy.md") is not None
        assert vault.index.get_index_status()["dirty_paths"] == 2
        with pytest.raises((OSError, ValueError), match="temporary"):
            vault.writer.rename("notes/target.md", "notes/new.md", update_links=True)
        assert (vault.source_dir / "notes/target.md").exists()
        assert not (vault.source_dir / "notes/new.md").exists()
    source.write_text("See [[target]].\n", encoding="utf-8")
    _mutate(vault, "rename")


def test_failed_deletion_retains_dirty_path(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.managers.index as index_module

    vault.writer.write("target.md", "# Target\n")
    assert vault.index.wait_for_drain(timeout=5)
    target = vault.source_dir / "target.md"

    def disappear(*_args: object, **_kwargs: object) -> None:
        target.unlink()
        raise OSError("disappeared")

    def fail_delete(_path: str) -> None:
        raise OSError("delete failed")

    with monkeypatch.context() as patch:
        patch.setattr(index_module, "parse_note", disappear)
        patch.setattr(vault._fts, "delete_by_path", fail_delete)
        vault._coordinator.writer.mark_dirty(["target.md"])
        with pytest.raises(OSError, match="delete failed"):
            vault._coordinator.prepare_index_read()
        assert vault.index.get_index_status()["dirty_paths"] == 1
    vault._coordinator.prepare_index_read()
    assert vault._fts.get_note("target.md") is None


@pytest.mark.parametrize("operation", ["convert", "generate", "maintain"])
def test_okf_refresh_waits_behind_initial_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from typing import Any

    started, release = threading.Event(), threading.Event()
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/target.md").write_text("# Target\n")
    (tmp_path / "links").mkdir()
    (tmp_path / "links/source.md").write_text("See [[target]].\n")
    col = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_write=operation == "maintain"),
    )
    original = col._index_mgr.build_index

    def held_build(*, force: bool = False) -> Any:
        started.set()
        assert release.wait(5)
        return original(force=force)

    monkeypatch.setattr(col._index_mgr, "build_index", held_build)
    try:
        build = col.index.build_index_async()
        assert started.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                if operation == "maintain":
                    mutation = pool.submit(col.writer.write, "notes/new.md", "# New\n")
                else:
                    mutation = pool.submit(_mutate, col, operation)
                with pytest.raises(TimeoutError):
                    mutation.result(timeout=0.1)
                release.set()
                build.result(timeout=5)
                mutation.result(timeout=5)
            finally:
                release.set()
        if operation == "maintain":
            assert "/notes/new.md" in (tmp_path / "notes/index.md").read_text()
            assert "new.md" in (tmp_path / "notes/log.md").read_text()
    finally:
        release.set()
        col.close()


@pytest.mark.parametrize("operation", ["convert", "generate"])
def test_okf_refresh_still_rejects_never_built_index(
    tmp_path: Path, operation: str
) -> None:
    from markdown_vault_mcp.exceptions import IndexUnavailableError

    col = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(IndexUnavailableError, match="Index not built"):
            _mutate(col, operation)
        assert not list(tmp_path.rglob("*.md"))
    finally:
        col.close()


@pytest.mark.parametrize("failure", ["read", "graph"])
def test_failed_refresh_does_not_starve_healthy_embeddings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from typing import Any

    import markdown_vault_mcp.managers.index as index_module
    from markdown_vault_mcp.indexing import FlushDirtyEmbeddings, ProcessDirtyPaths
    from tests.conftest import MockEmbeddingProvider

    provider = MockEmbeddingProvider()
    embedded: list[str] = []
    original_embed = provider.embed

    def record_embed(texts: list[str]) -> list[list[float]]:
        embedded.extend(texts)
        return original_embed(texts)

    monkeypatch.setattr(provider, "embed", record_embed)
    col = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, embeddings_path=tmp_path / ".vectors"),
        embedding_provider=provider,
    )
    original = index_module.parse_note

    def fail_bad(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name.startswith("bad"):
            raise OSError("persistent read failure")
        return original(path, *args, **kwargs)

    def fail_graph() -> int:
        raise OSError("persistent graph failure")

    try:
        col.index.build_index()
        col.index.build_embeddings()
        with monkeypatch.context() as patch:
            if failure == "read":
                patch.setattr(index_module, "parse_note", fail_bad)
            else:
                patch.setattr(col._fts, "resolve_vault_wikilinks", fail_graph)
            for name in ("bad1.md", "bad2.md"):
                (tmp_path / name).write_text("# Bad\n")
                col._coordinator.writer.mark_dirty([name])
            for name in ("healthy1.md", "healthy2.md"):
                (tmp_path / name).write_text(f"# {name}\n")
                col._coordinator.writer.mark_dirty([name])
                with pytest.raises(OSError, match="persistent"):
                    col._coordinator.writer.submit(ProcessDirtyPaths()).result(5)
                # Wait for the follow-up embedding job despite retained FTS work.
                col._coordinator.writer.submit(FlushDirtyEmbeddings()).result(5)
                assert col._vectors is not None
                assert name in {m["path"] for m in col._vectors._metadata}
                assert col.index.get_index_status()["dirty_paths"] >= 2
                assert sum("healthy1.md" in text for text in embedded) == 1
            before_edit = len(embedded)
            (tmp_path / "healthy1.md").write_text("# Changed healthy note\n")
            col._coordinator.writer.mark_dirty(["healthy1.md"])
            with pytest.raises(OSError, match="persistent"):
                col._coordinator.writer.submit(ProcessDirtyPaths()).result(5)
            col._coordinator.writer.submit(FlushDirtyEmbeddings()).result(5)
            assert len(embedded) == before_edit + 1
    finally:
        col.close()


def test_file_disappearing_during_parse_is_successful_deletion(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    import markdown_vault_mcp.managers.index as index_module

    vault.writer.write("source.md", "See [[target]].\n")
    assert vault.index.wait_for_drain(timeout=5)
    source = vault.source_dir / "source.md"
    original = index_module.parse_note

    def disappear(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == source:
            path.unlink()
            raise FileNotFoundError("source disappeared after the existence check")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(index_module, "parse_note", disappear)
    vault._coordinator.writer.mark_dirty(["source.md"])
    vault._coordinator.prepare_index_read()
    assert vault._fts.get_note("source.md") is None
    assert not source.exists()
    assert vault.index.wait_for_drain(timeout=5)
    assert vault.index.get_index_status()["dirty_paths"] == 0
