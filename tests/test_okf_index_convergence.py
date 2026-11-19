"""External changes converge designated OKF indexes on reindex."""

import shutil
import threading
from pathlib import Path

import pytest

from markdown_vault_mcp._file_watcher import VaultFileWatcher
from markdown_vault_mcp._okf_index_checkpoint import IndexCheckpoint
from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.utils.fs import path_exists
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.designated_index import designated_vault
from tests.fixtures.git import GitRepoPair, _git_clone, _git_commit, _git_head, _run_git


def test_external_add_updates_existing_index(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        (root / "concept.md").write_text("# Concept\n", encoding="utf-8")
        vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


@pytest.mark.parametrize("change", ["delete", "move", "metadata"])
def test_external_changes_update_listing(tmp_path: Path, change: str) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    concept = root / "concept.md"
    concept.write_text("---\ndescription: Before\n---\n# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        if change == "delete":
            concept.unlink()
        elif change == "move":
            concept.rename(root / "moved.md")
        else:
            concept.write_text(
                "---\ndescription: After\n---\n# Concept\n", encoding="utf-8"
            )
        vault.index.reindex()
        listing = index.read_text(encoding="utf-8")
        if change == "delete":
            assert "concept.md" not in listing
        elif change == "move":
            assert "concept.md" not in listing
            assert "moved.md" in listing
        else:
            assert "After" in listing
            assert "Before" not in listing
    finally:
        vault.close()


def test_reserved_index_edit_does_not_regenerate(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        edited = '---\nokf_version: "0.2"\n---\n# Hand edited\n'
        index.write_text(edited, encoding="utf-8")
        vault.index.reindex()
        assert index.read_text(encoding="utf-8") == edited
    finally:
        vault.close()


def test_cold_build_then_reindex_repairs_stale_index(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


def test_identical_reindex_does_not_write_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        writes: list[str] = []
        original = vault._doc_mgr.replace_body

        def record(path: str, *args: object, **kwargs: object) -> bool:
            writes.append(path)
            return original(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._doc_mgr, "replace_body", record)
        vault.index.reindex()
        assert writes == []
    finally:
        vault.close()


def test_reserved_change_does_not_mask_concept_change(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        index.write_text('---\nokf_version: "0.2"\n---\n# Edited\n', encoding="utf-8")
        (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
        vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_reindex_completion_includes_convergence(
    tmp_path: Path, asynchronous: bool
) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
        if asynchronous:
            vault.index.reindex_async().result(timeout=10)
        else:
            with vault.pause_writes():
                vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


def test_failed_refresh_retries_without_another_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        original = vault._okf_migrate.generate_index
        calls = 0

        def fail_once(**kwargs: object) -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("temporary failure")
            return original(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._okf_migrate, "generate_index", fail_once)
        (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
        vault.index.reindex()
        assert "concept.md" not in index.read_text(encoding="utf-8")
        vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
        assert calls == 2
    finally:
        vault.close()


def test_prior_observation_sweep_keeps_change_for_reindex(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    concept = tmp_path / "concept.md"
    concept.write_text("---\ndescription: Before\n---\n# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        concept.write_text(
            "---\ndescription: After\n---\n# Concept\n", encoding="utf-8"
        )
        assert vault._index_mgr.observations is not None
        vault._index_mgr.observations.sweep()
        vault.index.reindex()
        assert "After" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


def test_arrival_creates_designated_anchor_after_baseline(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    folder = tmp_path / "projects" / "one" / "deep" / "nested"
    folder.mkdir(parents=True)
    vault = designated_vault(tmp_path, (".", "projects/*"))
    try:
        vault.index.build_index()
        vault.index.reindex()
        project_index = tmp_path / "projects" / "one" / "index.md"
        assert not path_exists(project_index)
        (folder / "concept.md").write_text("# Concept\n")
        vault.index.reindex()
        assert "concept.md" in project_index.read_text()
        assert not path_exists(folder / "index.md")
    finally:
        vault.close()


def test_identical_reindex_creates_no_git_commit(git_repo: Path) -> None:
    (git_repo / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (git_repo / "concept.md").write_text("# Concept\n")
    strategy = GitWriteStrategy(
        enable_pull=False, enable_push=False, git_lfs=False, repo_path=git_repo
    )
    vault = Vault(
        source_dir=git_repo,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert vault._write_callback.drain()
        before = _git_head(git_repo)
        vault.index.reindex()
        assert vault._write_callback.drain()
        assert _git_head(git_repo) == before
    finally:
        vault.close()


def test_two_clones_converge_after_exchanging_concepts(
    tmp_path: Path, git_repo_pair: GitRepoPair
) -> None:
    first = git_repo_pair.local_path
    second = _git_clone(git_repo_pair, tmp_path)
    for root in (first, second):
        (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vaults = [
        Vault(
            source_dir=root,
            settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
        )
        for root in (first, second)
    ]
    try:
        for vault in vaults:
            vault.index.build_index()
            vault.index.reindex()
        first_commit = _git_commit(first, "first.md", "# First\n", "first concept")
        second_commit = _git_commit(second, "second.md", "# Second\n", "second concept")
        for vault in vaults:
            vault.index.reindex()
        _run_git(first, "fetch", str(second), second_commit)
        _run_git(first, "cherry-pick", second_commit)
        _run_git(second, "fetch", str(first), first_commit)
        _run_git(second, "cherry-pick", first_commit)
        for vault in vaults:
            vault.index.reindex()
        listing = (first / "index.md").read_bytes()
        assert listing == (second / "index.md").read_bytes()
        assert b"first.md" in listing and b"second.md" in listing
    finally:
        for vault in vaults:
            vault.close()


def test_persistent_restart_preserves_index_and_creates_offline_arrival(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    for name in ("one", "two"):
        (root / "projects" / name).mkdir(parents=True)
    existing = root / "projects/one/index.md"
    existing.write_text("# Original\n")
    (root / "projects/one/note.md").write_text("# One\n")
    db = tmp_path / "index.sqlite"
    vault = designated_vault(root, ("projects/*",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
    finally:
        vault.close()
    edited = "# Hand-edited\n"
    existing.write_text(edited)
    (root / "projects/two/offline.md").write_text("# Offline\n")
    vault = designated_vault(root, ("projects/*",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert existing.read_text() == edited
        assert path_exists(root / "projects/two/index.md")
    finally:
        vault.close()


def test_noop_does_not_canonicalize_each_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp._okf_convention as convention

    for n in range(20):
        (tmp_path / f"note{n}.md").write_text(f"# Note {n}\n")
    vault = designated_vault(tmp_path, ("**",))
    try:
        vault.index.build_index()
        vault.index.reindex()
        calls: list[str] = []
        original = convention.canonical_path

        def count(path: str, source_dir: Path | None) -> str:
            calls.append(path)
            return original(path, source_dir)

        monkeypatch.setattr(convention, "canonical_path", count)
        vault.index.reindex()
        assert calls == []
    finally:
        vault.close()


def test_external_folder_removal_refreshes_designated_ancestor(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    folder = tmp_path / "projects/one"
    folder.mkdir(parents=True)
    (folder / "index.md").write_text("# One\n")
    (folder / "note.md").write_text("# Note\n")
    vault = designated_vault(tmp_path, (".", "projects/*"))
    try:
        vault.index.build_index()
        vault.index.reindex()
        root_index = tmp_path / "index.md"
        assert "projects/one/index.md" in root_index.read_text()
        shutil.rmtree(folder)
        vault.index.reindex()
        assert "projects/one/index.md" not in root_index.read_text()
    finally:
        vault.close()


def test_log_only_does_not_schedule_index_convergence(tmp_path: Path) -> None:
    capabilities = ContentConfig(
        okf_mode="on", okf_index=False, okf_log="writes"
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", okf_capabilities=capabilities
        ),
    )
    try:
        assert vault._index_facet._convergence_executor is None
    finally:
        vault.close()


def test_checkpoint_corruption_scope_and_future_version(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint = IndexCheckpoint(path, "first")
    concepts = {"note.md": "a" * 64}
    checkpoint.save(concepts)
    assert IndexCheckpoint(path, "first").load() == concepts
    assert IndexCheckpoint(path, "second").load() is None
    path.write_text("not json")
    assert IndexCheckpoint(path, "first").load() is None
    path.write_text('{"version": 2}')
    with pytest.raises(StoreUnsupportedSchemaError):
        IndexCheckpoint(path, "first").load()


def test_failed_checkpoint_save_keeps_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint = IndexCheckpoint(path, "scope")
    before = {"before.md": "a" * 64}
    checkpoint.save(before)
    original = path.read_bytes()

    def fail_replace(_self: Path, _target: Path) -> Path:
        raise OSError("disk failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        checkpoint.save({"after.md": "b" * 64})
    assert checkpoint.load() == before
    assert path.read_bytes() == original


def test_search_admission_policy_change_reconciles_listing(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "note.md").write_text("---\ntitle: Custom title\n---\n# Body\n")
    db = tmp_path / "index.sqlite"
    capabilities = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".",)
    ).okf_capabilities

    def open_vault(required: list[str] | None) -> Vault:
        return Vault(
            source_dir=root,
            settings=VaultSettings(
                read_only=False,
                okf_mode="on",
                okf_capabilities=capabilities,
                index_path=db,
                required_frontmatter=required,
            ),
        )

    vault = open_vault(None)
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert "Custom title" in index.read_text()
    finally:
        vault.close()
    vault = open_vault(["missing"])
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert "Custom title" not in index.read_text()
        assert "note.md" in index.read_text()
    finally:
        vault.close()


def test_removed_folder_preserves_undesignated_boundary(tmp_path: Path) -> None:
    root_index = tmp_path / "index.md"
    root_index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    parent = tmp_path / "projects"
    folder = parent / "one"
    folder.mkdir(parents=True)
    (parent / "index.md").write_text("# Authored boundary\n")
    (folder / "index.md").write_text("# One\n")
    (folder / "note.md").write_text("# Note\n")
    vault = designated_vault(tmp_path, (".", "projects/*"))
    try:
        vault.index.build_index()
        vault.index.reindex()
        root_index.write_text('---\nokf_version: "0.2"\n---\n# Hand edited\n')
        shutil.rmtree(folder)
        vault.index.reindex()
        assert "Hand edited" in root_index.read_text()
        assert (parent / "index.md").read_text() == "# Authored boundary\n"
    finally:
        vault.close()


def test_future_checkpoint_leaves_index_untouched(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    original = '---\nokf_version: "0.2"\n---\n# Authored\n'
    index.write_text(original)
    (root / "note.md").write_text("# Note\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "index.sqlite")
    try:
        assert vault._okf_checkpoint_path is not None
        vault._okf_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        vault._okf_checkpoint_path.write_text('{"version": 2}')
        vault.index.build_index()
        vault.index.reindex()
        assert index.read_text() == original
        assert vault._okf_checkpoint_path.read_text() == '{"version": 2}'
    finally:
        vault.close()


def test_failed_refresh_keeps_persistent_checkpoint_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".",), index_path=tmp_path / "index.sqlite")
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert vault._okf_checkpoint_path is not None
        before = vault._okf_checkpoint_path.read_bytes()
        original = vault._okf_migrate.generate_index

        def fail(**_kwargs: object) -> object:
            raise OSError("temporary failure")

        monkeypatch.setattr(vault._okf_migrate, "generate_index", fail)
        (root / "new.md").write_text("# New\n")
        vault.index.reindex()
        assert vault._okf_checkpoint_path.read_bytes() == before
        monkeypatch.setattr(vault._okf_migrate, "generate_index", original)
        vault.index.reindex()
        assert "new.md" in (root / "index.md").read_text()
        assert vault._okf_checkpoint_path.read_bytes() != before
    finally:
        vault.close()


def test_reserved_alias_does_not_mask_other_observed_arrivals(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    alias = root / "alias.md"
    alias.write_text("# Previously a concept\n")
    db = tmp_path / "index.sqlite"
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert vault._okf_checkpoint_path is not None
        checkpoint = vault._okf_checkpoint_path
    finally:
        vault.close()
    checkpoint.unlink()
    alias.unlink()
    alias.symlink_to("index.md")
    (root / "new.md").write_text("# New\n")
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert "new.md" in index.read_text()
    finally:
        vault.close()


def test_watcher_event_reindexes_and_converges(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(tmp_path, (".",))
    done = threading.Event()

    def on_change() -> None:
        with vault.pause_writes():
            vault.index.reindex()
        done.set()

    watcher = VaultFileWatcher(tmp_path, on_change, debounce_s=0.1)
    try:
        vault.index.build_index()
        vault.index.reindex()
        watcher.start()
        (tmp_path / "external.md").write_text("# External\n")
        assert done.wait(10)
        assert "external.md" in index.read_text()
    finally:
        watcher.stop()
        vault.close()


def test_close_waits_for_async_convergence(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(tmp_path, (".",))
    vault.index.build_index()
    (tmp_path / "external.md").write_text("# External\n")
    pending = vault.index.reindex_async()
    vault.close()
    pending.result(timeout=10)
    assert "external.md" in index.read_text()


def test_head_reconcile_runs_convergence(git_repo: Path) -> None:
    index = git_repo / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    strategy = GitWriteStrategy(
        enable_pull=False, enable_push=False, git_lfs=False, repo_path=git_repo
    )
    vault = Vault(
        source_dir=git_repo,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
        git_strategy=strategy,
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        vault.mark_index_reconciled(vault.git_head())
        _git_commit(git_repo, "external.md", "# External\n", "external edit")
        assert vault.reconcile_index_with_head(source="test") == "reindexed"
        assert "external.md" in index.read_text()
    finally:
        vault.close()


def test_missing_checkpoint_creates_designated_anchor(tmp_path: Path) -> None:
    folder = tmp_path / "projects/one"
    folder.mkdir(parents=True)
    (folder / "note.md").write_text("# Note\n")
    vault = designated_vault(tmp_path, ("projects/*",))
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert "note.md" in (folder / "index.md").read_text()
        assert vault._okf_checkpoint_path is None
    finally:
        vault.close()


def test_persistent_restart_with_no_changes_does_not_generate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "note.md").write_text("# Note\n")
    db = tmp_path / "index.sqlite"
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
    finally:
        vault.close()
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        calls: list[bool] = []

        def unexpected(**_kwargs: object) -> object:
            calls.append(True)
            raise AssertionError("unchanged restart generated an index")

        monkeypatch.setattr(vault._okf_migrate, "generate_index", unexpected)
        vault.index.reindex()
        assert not calls
    finally:
        vault.close()
